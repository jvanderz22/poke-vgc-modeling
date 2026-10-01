#!/usr/bin/env node
/**
 * Endgame solver: the value of a 1v1 doubles endgame, by search over the real simulator.
 *
 *   node sidecar/showdown/endgame-solver.js <showdown_dir> < position.json
 *
 * A position is two teams of four (the Pokémon left first, then the ones that have fainted) and
 * the state to put them in. `active: {p1, p2}` says how many each side has left, one or two (one
 * if absent). A field about a Pokémon takes a list, one entry per active Pokémon in team order; a
 * single value is the first one's, which is how every 1v1 position is written:
 *   {format, p1: "<team text>", p2: "<team text>",
 *    setup: {hp: {p1, p2} (percent), mega: {p1: bool, p2: bool}, fainted: {p1: n, p2: n},
 *            weather: [id, turns], terrain: [id, turns], trickroom: turns,
 *            boosts: {p1: {...}, p2: {...}}, consumed: {p1: bool, p2: bool},
 *            choicelock: {p1: moveid, p2: moveid}, timesAttacked: {p1: n, p2: n},
 *            fresh: {p1: bool, p2: bool} (just switched in; otherwise it has been out a while),
 *            status: {p1: 'brn' | 'par' | 'psn', p2: ...},
 *            stall: {p1: n, p2: ...} (Protect and its kin used n turns running: the next works
 *            one time in 3^n),
 *            sides: {p1: {tailwind | reflect | lightscreen | auroraveil: turns}, p2: {...}}},
 *    search: {depth, rolls (damage rolls per hit, default 2), rare (chance events below this
 *             probability do not happen, default 0: every crit counts), cutoff (a line whose
 *             probability falls below this is scored by HP share instead of searched on,
 *             default 1e-3), min_prob (branches within a turn below this are dropped, 1e-7),
 *             prune (keep this many choices a Pokémon, the ones realistic play would consider;
 *             0, the default, keeps them all: see `prune`), ko_extend (false: a KO uses up its
 *             turn of the depth like any other; on by default), sample (play each pair of choices
 *             this many times with the simulator's own dice instead of enumerating chance: see
 *             `sampled`), stats (report the search's size)}}
 * Output: {value: P(p1 wins), moves: {p1: [...], p2: [...]}, matrix, strategy: {p1, p2},
 *          leaf_mass, dropped_mass, nodes (turns simulated), ms}
 *
 * The game is simultaneous-move with chance, so each node is a matrix game: every pair of
 * choices is played down every branch of its chance events (`ScriptedPRNG`), the children's
 * values weighted by their probabilities, and the matrix solved for its minimax value. A battle
 * that has not ended by `depth` turns is scored by HP share and counted in `leaf_mass`, the
 * probability that the answer rests on that guess rather than on the engine.
 *
 * Setting a position up is done on a started battle, not by constructing one: the fillers are
 * fainted in place (so Last Respects counts them, via `totalFainted`), switch-in effects from the
 * leads are wiped, and the field, stat stages, items and locks are then written directly.
 */
'use strict';

// The solver's behaviour version, which the answers are cached under (`vgc.wp.solver`): bump it
// with any change that could move any answer (the rules of a position, the choices offered, chance,
// the search). A change that cannot (a comment, pruning that is switched off, speed that leaves
// every answer as it was) keeps it, and so keeps every cached answer. A forgotten bump is caught
// by `vgc wp cache-check`, which solves a sample of cached answers again without the cache.
const VERSION = 1;

const path = require('path');
const crypto = require('crypto');
const SD = path.resolve(process.argv[2]);
const {Battle, Teams} = require(path.join(SD, 'dist/sim'));
const {State} = require(path.join(SD, 'dist/sim/state'));
const {PRNG} = require(path.join(SD, 'dist/sim/prng'));
const {BattleActions} = require(path.join(SD, 'dist/sim/battle-actions'));

/**
 * Secondary effects and self stat drops, as the simulator has them, but asked of the PRNG as one
 * chance instead of `random(100) < chance`. The distribution is the same. Under `ScriptedPRNG`
 * the roll was ten branches, all ten identical when the chance is 100% or absent (a self drop
 * rolls even then), and the rolls of both moves in a turn multiplied: 68% of the replays in the
 * slow positions were spawned here, and 95% of those that finished were duplicates. As one chance
 * it is two branches, or none, and exact for chances that are not a multiple of ten.
 * Gen 9 only: the gen ≤ 8 overflow of boost chances is not carried over.
 */
BattleActions.prototype.selfDrops = function (targets, source, move, moveData, isSecondary) {
	for (const target of targets) {
		if (target === false) continue;
		if (moveData.self && !move.selfDropped) {
			if (!isSecondary && moveData.self.boosts) {
				if (moveData.self.chance === undefined || this.battle.randomChance(moveData.self.chance, 100)) {
					this.moveHit(source, source, move, moveData.self, isSecondary, true);
				}
				if (!move.multihit) move.selfDropped = true;
			} else {
				this.moveHit(source, source, move, moveData.self, isSecondary, true);
			}
		}
	}
};
BattleActions.prototype.secondaries = function (targets, source, move, moveData, isSelf) {
	if (!moveData.secondaries) return;
	for (const target of targets) {
		if (target === false) continue;
		const secondaries = this.battle.runEvent('ModifySecondaries', target, source, moveData, moveData.secondaries.slice());
		for (const secondary of secondaries) {
			if (secondary.chance === undefined || this.battle.randomChance(secondary.chance, 100)) {
				this.moveHit(target, source, move, secondary, true, isSelf);
			}
		}
	}
};

const pack = text => Teams.pack(Teams.import(text).map(s => ({...s, level: 50})));

// A field about a Pokémon, for the active Pokémon `i` of `side`: a list has one entry each, and a
// single value belongs to the first.
function per(field, side, i) {
	const v = (field || {})[side.id];
	return Array.isArray(v) ? v[i] : i === 0 ? v : undefined;
}

// How many Pokémon a side has left: one or two, the first ones in its team.
function actives(pos, side) {
	return Math.max(1, Math.min(2, (pos.active || {})[side.id] || 1));
}

function setUp(pos) {
	const b = new Battle({formatid: pos.format, seed: 'sodium,' + '0'.repeat(64)});
	b.setPlayer('p1', {name: 'p1', team: pack(pos.p1)});
	b.setPlayer('p2', {name: 'p2', team: pack(pos.p2)});
	b.choose('p1', 'team 1234');
	b.choose('p2', 'team 1234');
	const s = pos.setup || {};
	const f = b.field;
	f.clearWeather(); f.clearTerrain();
	for (const id of Object.keys(f.pseudoWeather)) f.removePseudoWeather(id);
	for (const side of b.sides) {
		for (const id of Object.keys(side.sideConditions)) side.removeSideCondition(id);
		const n = actives(pos, side);
		for (const p of side.pokemon.slice(n)) {
			p.hp = 0; p.fainted = true; p.status = 'fnt'; p.switchFlag = false;
		}
		side.pokemonLeft = n;
		side.totalFainted = (s.fainted || {})[side.id] ?? 4 - n;
		side.pokemon.slice(0, n).forEach((me, i) => {
			me.clearBoosts();
			// On the field for a while unless the position says it just arrived: Fake Out and First
			// Impression fail after a Pokémon's first turn out, and a hand reading of the benchmark
			// assumed they would.
			if (!per(s.fresh, side, i)) { me.activeTurns = 3; me.activeMoveActions = 3; }
			const pct = per(s.hp, side, i) ?? 100;
			me.sethp(Math.max(1, Math.round(me.maxhp * pct / 100)));
			if (per(s.mega, side, i)) b.actions.runMegaEvo(me);
		});
	}
	if (s.weather) { f.setWeather(s.weather[0], 'debug'); f.weatherState.duration = s.weather[1]; }
	if (s.terrain) { f.setTerrain(s.terrain[0], 'debug'); f.terrainState.duration = s.terrain[1]; }
	if (s.trickroom) { f.addPseudoWeather('trickroom', 'debug'); f.pseudoWeather.trickroom.duration = s.trickroom; }
	for (const side of b.sides) side.pokemon.slice(0, actives(pos, side)).forEach((me, i) => {
		for (const [stat, n] of Object.entries(per(s.boosts, side, i) || {})) me.boosts[stat] = n;
		if (per(s.consumed, side, i)) {
			me.setItem('');
			if (me.hasAbility('unburden')) me.addVolatile('unburden');
		}
		const lock = per(s.choicelock, side, i);
		if (lock) {
			b.activeMove = b.dex.getActiveMove(lock);
			me.addVolatile('choicelock');
			b.activeMove = null;
			// An ActiveMove, as the simulator leaves it: a plain Move does not survive
			// `State.serializeBattle` (it comes back as the string "[DataMove:id]"), and Encore
			// then read `.flags` off that string and crashed.
			me.lastMove = b.dex.getActiveMove(lock);
		}
		if (per(s.timesAttacked, side, i)) me.timesAttacked = per(s.timesAttacked, side, i);
		// Only the statuses whose effect is fixed once set. Sleep and bad poison carry a counter the
		// cartridge does not show, so a position with either is not built (vgc.wp.endgame says so).
		const status = per(s.status, side, i);
		if (status) me.setStatus(status, me, null, true);
		// Protect used `stall` turns running: the simulator's own counter, which triples each time.
		const stall = per(s.stall, side, i);
		if (stall) { me.addVolatile('stall'); me.volatiles.stall.counter = 3 ** stall; }
	});
	for (const side of b.sides) {
		for (const [id, turns] of Object.entries((s.sides || {})[side.id] || {})) {
			side.addSideCondition(id, side.pokemon[0]);
			side.sideConditions[id].duration = turns;
		}
	}
	// What `endTurn` does before every later request: disable the moves a lock (or Taunt, or a
	// move that cannot be used twice) rules out. Without it the root offered a choice-locked
	// Pokémon all four moves, and choosing another one did nothing but fail.
	for (const side of b.sides) for (const me of side.pokemon.slice(0, actives(pos, side))) {
		for (const slot of me.moveSlots) { slot.disabled = false; slot.disabledSource = ''; }
		b.runEvent('DisableMove', me);
		for (const slot of me.moveSlots) {
			const move = b.dex.getActiveMove(slot.id);
			b.singleEvent('DisableMove', move, null, me);
			if (move.flags['cantusetwice'] && me.lastMove?.id === slot.id) me.disableMove(slot.id);
		}
	}
	b.makeRequest('move');
	return b;
}

// The choices one side can make: for each of its active Pokémon, each usable move, aimed at each
// live foe, or at its ally slot for a move that needs an ally (Helping Hand, Coaching), which the
// cartridge lets you choose even when the slot is empty and then fails. Without a target the
// simulator rejects the choice. The target is the request's, not the dex's: a move locked in (the
// second turn of Phantom Force, Outrage) comes with none, and the simulator rejects one given. A
// side's choice is one for each slot, a fainted or empty slot passing: 'move 2 1, pass' in a 1v1.
function slotOptions(b, side, slot) {
	const req = side.activeRequest;
	const p = side.active[slot];
	if (!p || p.fainted || !req.active[slot]) return ['pass'];
	const foes = b.sides[1 - side.n].active.map((q, i) => (q && !q.fainted ? i : -1)).filter(i => i >= 0);
	const out = [];
	// A move Imprison blocks is disabled 'hidden': the last Pokémon a side has is shown it as
	// usable, and the simulator refuses it when chosen. A player would be told and choose again,
	// so it is no choice at all. With every move blocked, any choice is Struggle.
	const hidden = new Set(p.moveSlots.filter(s => s.disabled === 'hidden').map(s => s.id));
	if (req.active[slot].moves.every(m => m.disabled || hidden.has(m.id))) return ['move 1'];
	req.active[slot].moves.forEach((m, i) => {
		if (m.disabled || hidden.has(m.id)) return;
		if (['normal', 'any', 'adjacentFoe'].includes(m.target) && foes.length) {
			for (const f of foes) out.push(`move ${i + 1} ${f + 1}`);
		} else if (m.target === 'adjacentAlly') {
			out.push(`move ${i + 1} -${2 - slot}`);
		} else if (m.target === 'adjacentAllyOrSelf') {
			out.push(`move ${i + 1} -${slot + 1}`);
		} else {
			out.push(`move ${i + 1}`);
		}
	});
	return out.length ? out : ['pass'];
}

function options(b, side) {
	const req = side.activeRequest;
	if (!req || req.wait || !req.active) return ['pass'];
	const [a, c] = [0, 1].map(slot => slotOptions(b, side, slot));
	const out = [];
	for (const x of a) for (const y of c) if (!(x === 'pass' && y === 'pass')) out.push(`${x}, ${y}`);
	return out.length ? out : ['pass'];
}

/**
 * Realistic play, for `search.prune`: each Pokémon keeps the few choices a good player would
 * weigh, on both sides, so the answer is best play within realistic play. What is kept, in order:
 *   1. its best attack at each live foe, and its best attack with priority: moving first is what
 *      Sucker Punch and Extreme Speed are for, whatever their damage
 *   2. Protect and its kin (a second in a row still works one time in three), and Fake Out while
 *      it works: the flinch is the point, not the 40 base power
 *   3. the rest by score, to `prune` in all
 * Checked against the exact answers of 543 real 1v1 positions (PLAN-endgame-doubles, stage 1).
 * An attack's score is the share of the foe's HP it takes at the middle damage roll with no crit,
 * times its accuracy, plus 1 if that KOs. Setting up and healing are what a player does when the
 * foe cannot punish it: a self-boost scores 0.6 when the foe's best attack takes under half of
 * this Pokémon's HP and 0.15 when it does not, and a heal scores 0.6 when it is also under 70%
 * HP (0.2 when there is not much to heal). Any other status move scores 0.25, so it outranks an
 * attack that does less than a quarter. Damage comes from the simulator itself, played on a copy of the
 * position whose PRNG is fixed (`ScoringPRNG`), so nothing is drawn from the search's own.
 */
class ScoringPRNG {
	getSeed() { return 'sodium,' + '0'.repeat(64); }
	clone() { return this; }
	random(from, to) {
		if (from === undefined) return 0.5;
		const lo = to ? Math.floor(from) : 0, hi = to ? Math.floor(to) : Math.floor(from);
		return lo + Math.floor((hi - lo) / 2);
	}
	randomChance() { return false; }
	sample(items) { return items[0]; }
	shuffle() {}
}

function scorer(snap) {
	const copy = State.deserializeBattle(snap);
	copy.restart(() => {});
	copy.prng = new ScoringPRNG();
	return copy;
}

// One choice's parts: the move slot (1-based) and target, from 'move 2 1' or 'move 3 -2'.
function parse(part) {
	const m = /^move (\d+)(?: (-?\d+))?$/.exec(part.trim());
	return m ? {move: +m[1], target: m[2] === undefined ? null : +m[2]} : null;
}

// A move as the simulator has it when it hits: type and power changed by the user's ability
// (Pixilate, Aerilate, Liquid Voice), the weather (Weather Ball) and the move itself. Without
// this an -ate Double-Edge was a Normal move, and read as doing nothing to a Ghost.
function prepared(copy, me, foe, id) {
	let move = copy.dex.getActiveMove(id);
	copy.singleEvent('ModifyType', move, null, me, foe, move, move);
	copy.singleEvent('ModifyMove', move, null, me, foe, move, move);
	move = copy.runEvent('ModifyType', me, foe, move, move);
	move = copy.runEvent('ModifyMove', me, foe, move, move);
	return move;
}

function damage(copy, me, foe, id) {
	let d;
	try { d = copy.actions.getDamage(me, foe, prepared(copy, me, foe, id), true); } catch (e) { d = 0; }
	return typeof d === 'number' && d > 0 ? d : 0;
}

// The largest share of this Pokémon's HP any live foe's move takes, by the same calculation.
function threat(copy, side, slot) {
	const me = copy.sides[side].active[slot];
	let worst = 0;
	for (const foe of copy.sides[1 - side].active) {
		if (!foe || foe.fainted) continue;
		for (const ms of foe.moveSlots) {
			const move = copy.dex.moves.get(ms.id);
			if (move.category === 'Status') continue;
			const d = damage(copy, foe, me, ms.id);
			if (d > 0) worst = Math.max(worst, Math.min(1, d / me.hp));
		}
	}
	return worst;
}

function score(copy, side, slot, part, danger) {
	const c = parse(part);
	if (!c) return {score: 0, protect: false};
	const me = copy.sides[side].active[slot];
	const req = copy.sides[side].activeRequest;
	const id = req && req.active && req.active[slot] ? req.active[slot].moves[c.move - 1].id : me.moveSlots[c.move - 1].id;
	const move = copy.dex.moves.get(id);
	if (move.stallingMove || move.id === 'fakeout') return {score: 0.5, protect: true};
	if (move.category === 'Status') {
		const safe = danger() < 0.5;
		const boosts = move.boosts && move.target === 'self' && Object.values(move.boosts).some(v => v > 0);
		if (boosts) return {score: safe ? 0.6 : 0.15, protect: false};
		if (move.heal || move.flags.heal) return {score: safe ? (me.hp / me.maxhp < 0.7 ? 0.6 : 0.2) : 0.15, protect: false};
		return {score: 0.25, protect: false};
	}
	const foes = copy.sides[1 - side].active.map((p, i) => ({p, i})).filter(x => x.p && !x.p.fainted)
		.filter(x => c.target === null || c.target === x.i + 1);
	let best = 0, ko = false, foe = null;
	for (const {p, i} of foes) {
		const d = damage(copy, me, p, id);
		if (!(d > 0)) continue;
		const hits = Array.isArray(move.multihit) ? 3 : (move.multihit || 1);
		const share = Math.min(1, d * hits / p.hp);
		if (share > best) { best = share; ko = d * hits >= p.hp; foe = i; }
	}
	const acc = move.accuracy === true ? 1 : move.accuracy / 100;
	return {score: best * acc + (ko ? 1 : 0), ko, foe, priority: move.priority || 0, protect: false};
}

// A side's choices cut to `k` a Pokémon. Each Pokémon's part is scored and cut on its own, and
// the pairs are formed from what is left. A pair that sends two attacks at a foe one of them KOs
// alone is dropped while the other foe stands: that is overkill, and the pair that KOs and hits
// the other foe is kept instead. In a 1v1 the second part is always 'pass'.
function prune(copy, side, choices, k) {
	if (!k) return choices;
	const parts = choices.map(ch => ch.split(',').map(x => x.trim()));
	const info = [new Map(), new Map()];
	const kept = [0, 1].map(slot => {
		const mine = [...new Set(parts.map(p => p[slot]))].filter(x => x !== 'pass');
		if (mine.length <= k) return new Set([...mine, 'pass']);
		let danger = null;
		const once = () => (danger === null ? (danger = threat(copy, side, slot)) : danger);
		const scored = mine.map(part => ({part, ...score(copy, side, slot, part, once)}));
		for (const x of scored) info[slot].set(x.part, x);
		const keep = new Set(['pass']);
		const byFoe = new Map();
		for (const x of scored) {
			if (x.foe === null || x.foe === undefined) continue;
			const cur = byFoe.get(x.foe);
			if (!cur || x.score > cur.score) byFoe.set(x.foe, x);
		}
		for (const x of byFoe.values()) keep.add(x.part);
		let quick = null;
		for (const x of scored) {
			if (x.priority > 0 && x.score > 0 && (!quick || x.score > quick.score)) quick = x;
		}
		if (quick) keep.add(quick.part);
		for (const x of scored) if (x.protect) keep.add(x.part);
		for (const x of [...scored].sort((a, b) => b.score - a.score)) {
			if (keep.size - 1 >= k) break;
			keep.add(x.part);
		}
		return keep;
	});
	const foes = copy.sides[1 - side].active.filter(p => p && !p.fainted).length;
	const out = choices.filter((ch, j) => {
		const [a, b] = parts[j];
		if (!kept[0].has(a) || !kept[1].has(b)) return false;
		const x = info[0].get(a), y = info[1].get(b);
		if (foes > 1 && x && y && x.foe != null && x.foe === y.foe && (x.ko || y.ko)) return false;
		return true;
	});
	return out.length ? out : choices;
}

function key(b) {
	const f = b.field;
	const mon = p => [p.hp, p.status, p.item, JSON.stringify(p.boosts), Object.keys(p.volatiles).sort().join('.'),
		p.volatiles.stall ? p.volatiles.stall.counter : '', p.volatiles.choicelock ? p.volatiles.choicelock.move : '',
		p.timesAttacked, p.species.id].join(':');
	return [f.weather, f.weatherState.duration, f.terrain, f.terrainState.duration,
		Object.entries(f.pseudoWeather).map(([k, v]) => k + v.duration).join(','),
		...b.sides.map(s => s.active.filter(p => p && !p.fainted).map(mon).join('|'))].join('/');
}

function leaf(b) {
	const share = side => side.active.filter(p => p && !p.fainted).reduce((a, p) => a + p.hp / p.maxhp, 0);
	const a = share(b.sides[0]), c = share(b.sides[1]);
	return a + c > 0 ? a / (a + c) : 0.5;
}

/**
 * A 1v1 valued as a damage race, for `search.race` (at the horizon) and `search.race_1v1` (a 1v1
 * a doubles search reaches is valued so instead of searched). Each side's attacks are taken from
 * the simulator's damage at the middle roll (`ScoringPRNG`), and each pair of them, one a side,
 * is raced out turn by turn over both HP totals: accuracy, two damage rolls and a crit a hit,
 * priority then Speed (Trick Room included, a tie re-drawn each turn), full paralysis one time
 * in eight (the Champions rule), Focus Sash and Sturdy, recoil, Life Orb and drain, Sitrus Berry,
 * Leftovers, and burn and poison. The pairs make a matrix game, solved as the search's are.
 * Status moves, boosts, Protect and switching do not exist in it: it says who wins the trade of
 * blows, which is what a 1v1 most often comes down to. Returns null for anything but a 1v1.
 */
const RACE_TURNS = 12;
function race(snap) {
	const copy = scorer(snap);
	const live = s => copy.sides[s].active.filter(p => p && !p.fainted);
	if (live(0).length !== 1 || live(1).length !== 1) return null;
	const mons = [live(0)[0], live(1)[0]];
	const attacks = [0, 1].map(s => {
		const me = mons[s], foe = mons[1 - s];
		const slot = copy.sides[s].active.indexOf(me);
		const req = copy.sides[s].activeRequest;
		const listed = req && req.active && req.active[slot] ? req.active[slot].moves : me.moveSlots;
		const hidden = new Set(me.moveSlots.filter(m => m.disabled).map(m => m.id));
		const out = [];
		for (const m of listed) {
			if (m.disabled || hidden.has(m.id) || m.pp === 0) continue;
			const move = copy.dex.moves.get(m.id);
			if (move.category === 'Status') continue;
			const d = damage(copy, me, foe, m.id);
			if (!(d > 0)) continue;
			const hits = Array.isArray(move.multihit) ? 3 : (move.multihit || 1);
			const active = prepared(copy, me, foe, m.id);
			let priority = copy.singleEvent('ModifyPriority', active, null, me, null, null, active.priority);
			priority = copy.runEvent('ModifyPriority', me, null, active, priority);
			// A two-turn move hits every other turn: Solar Beam in sun and a Power Herb skip the
			// charge, and one already charging hits on the first.
			const sun = ['sunnyday', 'desolateland'].includes(copy.field.effectiveWeather());
			const charge = !!move.flags.charge && me.item !== 'powerherb' && !(sun && ['solarbeam', 'solarblade'].includes(move.id))
				? (me.volatiles.twoturnmove ? 1 : 2) : 0;
			out.push({
				d: d * hits, acc: move.accuracy === true ? 1 : move.accuracy / 100, priority: priority || 0, charge,
				recoil: (move.recoil ? move.recoil[0] / move.recoil[1] : 0) + (me.item === 'lifeorb' ? 0.1 : 0),
				drain: move.drain ? move.drain[0] / move.drain[1] : 0,
			});
		}
		return out.length ? out : [{d: 0, acc: 1, priority: 0, recoil: 0, drain: 0}];
	});
	const speed = mons.map(p => p.getActionSpeed());
	const M = attacks[0].map(a => attacks[1].map(c => duel(mons, [a, c], speed)));
	return solve(M).value;
}

// P(p1 wins) when each side uses one attack every turn: a distribution over both HP totals.
function duel(mons, atk, speed) {
	const max = mons.map(p => p.maxhp);
	const sash = mons.map(p => p.item === 'focussash' || p.ability === 'sturdy');
	const sitrus = mons.map(p => p.item === 'sitrusberry');
	const residual = mons.map(p => (p.item === 'leftovers' ? Math.floor(p.maxhp / 16) : 0)
		- (p.status === 'brn' ? Math.floor(p.maxhp / 16) : 0) - (['psn', 'tox'].includes(p.status) ? Math.floor(p.maxhp / 8) : 0));
	const para = mons.map(p => (p.status === 'par' ? 1 / 8 : 0));
	const hitOutcomes = a => (a.d ? [
		{p: 1 - a.acc, d: 0},
		{p: a.acc / 24, d: Math.floor(a.d * 1.5)},
		{p: a.acc * 23 / 48, d: Math.floor(a.d * 88.5 / 92)},
		{p: a.acc * 23 / 48, d: Math.floor(a.d * 96.5 / 92)},
	].filter(o => o.p > 0) : [{p: 1, d: 0}]);
	const outs = atk.map(hitOutcomes);
	// A two-turn move's turns of charging: 2 hits on turns 1, 3, ...; 1 (already charging) on 0, 2, ...
	const fires = (who, turn) => !atk[who].charge || (turn % 2 === (atk[who].charge === 2 ? 1 : 0));
	const orders = atk[0].priority !== atk[1].priority ? [[atk[0].priority > atk[1].priority ? 0 : 1, 1]]
		: speed[0] !== speed[1] ? [[speed[0] > speed[1] ? 0 : 1, 1]] : [[0, 0.5], [1, 0.5]];
	let states = new Map([[`${mons[0].hp}|${mons[1].hp}|${+sitrus[0]}|${+sitrus[1]}`, 1]]);
	let won = 0, live = 0;
	for (let turn = 0; turn < RACE_TURNS && states.size; turn++) {
		const next = new Map();
		const add = (hp, berry, p) => {
			const k = `${hp[0]}|${hp[1]}|${berry[0]}|${berry[1]}`;
			next.set(k, (next.get(k) || 0) + p);
		};
		for (const [k, p0] of states) {
			const [h0, h1, b0, b1] = k.split('|').map(Number);
			for (const [first, po] of orders) {
				// Each branch: [hp, berry, p, ended (null, or p1's result)]
				let branches = [[[h0, h1], [b0, b1], p0 * po, null]];
				for (const who of [first, 1 - first]) {
					const foe = 1 - who;
					const after = [];
					for (const [hp, berry, p, end] of branches) {
						if (end !== null) { after.push([hp, berry, p, end]); continue; }
						const acts = !fires(who, turn) ? [{p: 1, d: 0}]
							: para[who] ? [{p: para[who], d: null}, ...outs[who].map(o => ({p: o.p * (1 - para[who]), d: o.d}))] : outs[who];
						for (const o of acts) {
							const h = hp.slice(), b = berry.slice();
							if (o.d) {
								let dmg = o.d;
								if (sash[foe] && h[foe] === max[foe] && dmg >= h[foe]) dmg = h[foe] - 1;
								const dealt = Math.min(dmg, h[foe]);
								h[foe] -= dealt;
								if (atk[who].recoil) h[who] -= Math.max(1, Math.round(dealt * atk[who].recoil));
								if (atk[who].drain) h[who] = Math.min(max[who], h[who] + Math.round(dealt * atk[who].drain));
								for (const x of [foe, who]) {
									if (b[x] && h[x] > 0 && h[x] <= max[x] / 2) { h[x] += Math.floor(max[x] / 4); b[x] = 0; }
								}
							}
							const out0 = h[0] <= 0, out1 = h[1] <= 0;
							const end = out0 && out1 ? 0.5 : out1 ? 1 : out0 ? 0 : null;
							after.push([h, b, p * o.p, end]);
						}
					}
					branches = after;
				}
				for (const [hp, berry, p, end] of branches) {
					if (end !== null) { won += p * end; continue; }
					const h = hp.map((x, i) => Math.min(max[i], x + residual[i]));
					const out0 = h[0] <= 0, out1 = h[1] <= 0;
					if (out0 || out1) { won += p * (out0 && out1 ? 0.5 : out1 ? 1 : 0); continue; }
					add(h, berry, p);
				}
			}
		}
		states = next;
	}
	// What is still standing after RACE_TURNS turns is scored by HP share.
	for (const [k, p] of states) {
		const [h0, h1] = k.split('|').map(Number);
		const a = h0 / max[0], c = h1 / max[1];
		won += p * (a + c > 0 ? a / (a + c) : 0.5); live += p;
	}
	return won;
}

/**
 * A position with more than one Pokémon a side valued as a damage race, for `search.race_doubles`
 * at the horizon: what HP share gets wrong is a count lead (two healthy Pokémon against one is
 * 0.67 by HP share, and the side ahead wins 88% of human 2v1s). Each Pokémon's attacks come from
 * the simulator's damage on the move as it is used (a spread move at three quarters into two
 * foes), and the race is played out `MELEE_RUNS` times with seeded dice: every turn each Pokémon
 * fires the attack that does the most towards a KO (a KO counts double), in priority then Speed
 * order; a single-target move whose target has fallen goes to the other foe, as in doubles.
 * Accuracy, roll, crit, full paralysis, Sash and Sturdy, recoil and Life Orb, drain, Sitrus,
 * Leftovers and burn or poison are drawn or applied; Protect, status moves and switching are not
 * in it. P(p1's side outlasts p2's), HP share for what is unresolved after `RACE_TURNS` turns.
 */
const MELEE_RUNS = 64;
/**
 * `search.fast_race`: the race's damage and priorities remembered across the leaves of one search,
 * keyed on what they depend on rather than recomputed on a fresh copy of each leaf. Damage at the
 * fixed roll depends on the two Pokémon (species, ability, item, status, stages, volatiles, how
 * hurt — full, a half, a third, a quarter — times attacked, the side's fallen), the field, and who
 * else is out; HP itself only for the moves in `HP_MOVES`. A leaf whose every entry is known is
 * raced without copying the battle at all. A consumable that a damage calculation spends (a resist
 * berry) is spent once per key rather than once per leaf: the only way this differs from the race
 * without it.
 */
const DAMAGE = new Map(), PRIORITY = new Map();
const HP_MOVES = new Set(['eruption', 'waterspout', 'dragonenergy', 'flail', 'reversal', 'crushgrip', 'wringout', 'hardpress']);
function monKey(p) {
	const hurt = p.hp === p.maxhp ? 'F' : p.hp * 4 <= p.maxhp ? 'q' : p.hp * 3 <= p.maxhp ? 't' : p.hp * 2 <= p.maxhp ? 'h' : 'm';
	return [p.species.id, p.ability, p.item, p.status, JSON.stringify(p.boosts), hurt, Object.keys(p.volatiles).sort().join('.'),
		p.timesAttacked, p.side.totalFainted, p.position].join(':');
}
function fieldKey(b) {
	const f = b.field;
	return [f.weather, f.terrain, Object.keys(f.pseudoWeather).sort().join('.'),
		...b.sides.map(sd => Object.keys(sd.sideConditions).sort().join('.') + '|' +
			sd.active.map(p => (p && !p.fainted ? p.species.id + '/' + p.ability : '-')).join(','))].join('/');
}
// `race_doubles: 'calibrated'`: the race's value through P = sigmoid(a * logit(race) + b), fitted on
// 1,500 training-split open-sheet games at their first turn with two or fewer a side (never the
// held-out ones the check scores). Raw, the race calls 70% of games at 95% or more and is right 87%
// of the time; calibrated, a sure race is 0.88, which is how often the side ahead wins a 2v1. A
// change here is a change of answers for 'calibrated': give it a new name rather than edit it.
const MELEE_CALIBRATION = {a: 0.403, b: -0.0244, eps: 1 / 128};
function calibrated(v) {
	const {a, b, eps} = MELEE_CALIBRATION;
	const q = Math.min(1 - eps, Math.max(eps, v));
	return 1 / (1 + Math.exp(-(a * Math.log(q / (1 - q)) + b)));
}
function melee(snap, seedKey, live = null) {
	// `live`: the leaf's own battle, read but not changed, with damage from the tables (fast_race).
	let copy = live ? null : scorer(snap);
	const scoring = () => copy || (copy = scorer(snap));
	const b = live || copy;
	const twin = p => scoring().sides[p.side.n].active[p.position];
	const field = live ? fieldKey(live) : null;
	const dmgOf = (me, f, id) => {
		if (!live) return damage(copy, me, f, id);
		const k = monKey(me) + '>' + monKey(f) + '#' + id + (HP_MOVES.has(id) ? `@${me.hp}/${f.hp}` : '') + '~' + field;
		if (!DAMAGE.has(k)) DAMAGE.set(k, damage(scoring(), twin(me), twin(f), id));
		return DAMAGE.get(k);
	};
	const priorityOf = (me, f, id) => {
		const k = live ? monKey(me) + '#' + id + '~' + field : null;
		if (k && PRIORITY.has(k)) return PRIORITY.get(k);
		const c = live ? scoring() : copy, m = live ? twin(me) : me, t = live ? twin(f) : f;
		const active = prepared(c, m, t, id);
		let priority = c.singleEvent('ModifyPriority', active, null, m, null, null, active.priority);
		priority = c.runEvent('ModifyPriority', m, null, active, priority);
		if (k) PRIORITY.set(k, priority);
		return priority;
	};
	const sides = [0, 1].map(s => b.sides[s].active.filter(p => p && !p.fainted));
	if (!sides[0].length || !sides[1].length || sides[0].length + sides[1].length <= 2) return null;
	const sun = ['sunnyday', 'desolateland'].includes(b.field.effectiveWeather());
	// mons[k]: {side, mon, moves: [{dmg: [per foe index], spread, acc, priority, recoil, drain}]}
	const mons = [];
	for (const s of [0, 1]) for (const me of sides[s]) {
		const foes = sides[1 - s];
		const slot = b.sides[s].active.indexOf(me);
		const req = b.sides[s].activeRequest;
		const listed = req && req.active && req.active[slot] ? req.active[slot].moves : me.moveSlots;
		const hidden = new Set(me.moveSlots.filter(m => m.disabled).map(m => m.id));
		const moves = [];
		for (const m of listed) {
			if (m.disabled || hidden.has(m.id) || m.pp === 0) continue;
			const move = b.dex.moves.get(m.id);
			if (move.category === 'Status') continue;
			const spread = ['allAdjacentFoes', 'allAdjacent'].includes(move.target) && foes.length > 1;
			const hits = Array.isArray(move.multihit) ? 3 : (move.multihit || 1);
			const dmg = foes.map(f => Math.floor(dmgOf(me, f, m.id) * hits * (spread ? 0.75 : 1)));
			if (!dmg.some(d => d > 0)) continue;
			const priority = priorityOf(me, foes[0], m.id);
			if (move.flags.charge && me.item !== 'powerherb' && !(sun && ['solarbeam', 'solarblade'].includes(move.id))) {
				for (let i = 0; i < dmg.length; i++) dmg[i] = Math.floor(dmg[i] / 2);
			}
			moves.push({dmg, spread, acc: move.accuracy === true ? 1 : move.accuracy / 100, priority: priority || 0,
				recoil: (move.recoil ? move.recoil[0] / move.recoil[1] : 0) + (me.item === 'lifeorb' ? 0.1 : 0),
				drain: move.drain ? move.drain[0] / move.drain[1] : 0});
		}
		mons.push({side: s, mon: me, foes: foes.map(f => sides[1 - s].indexOf(f)), moves, speed: me.getActionSpeed(),
			max: me.maxhp, sash: me.item === 'focussash' || me.ability === 'sturdy', sitrus: me.item === 'sitrusberry',
			para: me.status === 'par' ? 1 / 8 : 0,
			residual: (me.item === 'leftovers' ? Math.floor(me.maxhp / 16) : 0) - (me.status === 'brn' ? Math.floor(me.maxhp / 16) : 0)
				- (['psn', 'tox'].includes(me.status) ? Math.floor(me.maxhp / 8) : 0)});
	}
	const of = (s, j) => mons.filter(x => x.side === s)[j];
	const rng = new PRNG(seedFor(`melee|${seedKey}`));
	let won = 0;
	for (let run = 0; run < MELEE_RUNS; run++) {
		const hp = mons.map(x => x.mon.hp), berry = mons.map(x => x.sitrus), sash = mons.map(x => x.sash);
		const alive = s => mons.some((x, k) => x.side === s && hp[k] > 0);
		let result = null;
		for (let turn = 0; turn < RACE_TURNS && result === null; turn++) {
			// Each Pokémon's attack and target for the turn: the most progress towards a KO.
			const plan = mons.map((x, k) => {
				if (hp[k] <= 0) return null;
				let best = null;
				for (const mv of x.moves) {
					const targets = x.foes.map((fi, j) => ({j, k: mons.indexOf(of(1 - x.side, fi))})).filter(t => hp[t.k] > 0);
					if (!targets.length) continue;
					const hitsOn = mv.spread ? [targets] : targets.map(t => [t]);
					for (const ts of hitsOn) {
						let v = 0;
						for (const t of ts) v += Math.min(1, mv.dmg[t.j] / hp[t.k]) + (mv.dmg[t.j] >= hp[t.k] ? 1 : 0);
						v *= mv.acc;
						if (!best || v > best.v) best = {v, mv, ts};
					}
				}
				return best;
			});
			const order = mons.map((x, k) => k).filter(k => plan[k])
				.map(k => ({k, pr: plan[k].mv.priority, sp: mons[k].speed, tie: rng.random()}))
				.sort((a, b) => b.pr - a.pr || b.sp - a.sp || a.tie - b.tie).map(o => o.k);
			for (const k of order) {
				if (hp[k] <= 0) continue;
				const x = mons[k], {mv} = plan[k];
				if (x.para && rng.random() < x.para) continue;
				let ts = plan[k].ts.filter(t => hp[t.k] > 0);
				if (!ts.length && !mv.spread) {
					// A single-target move whose target fell goes to the other foe.
					ts = x.foes.map((fi, j) => ({j, k: mons.indexOf(of(1 - x.side, fi))})).filter(t => hp[t.k] > 0).slice(0, 1);
				}
				let dealt = 0;
				for (const t of ts) {
					if (rng.random() >= mv.acc) continue;
					const roll = (85 + Math.floor(rng.random() * 16)) / 92;
					let d = Math.floor(mv.dmg[t.j] * roll * (rng.random() < 1 / 24 ? 1.5 : 1));
					if (sash[t.k] && hp[t.k] === mons[t.k].max && d >= hp[t.k]) { d = hp[t.k] - 1; sash[t.k] = false; }
					d = Math.min(d, hp[t.k]);
					hp[t.k] -= d; dealt += d;
					if (berry[t.k] && hp[t.k] > 0 && hp[t.k] <= mons[t.k].max / 2) { hp[t.k] += Math.floor(mons[t.k].max / 4); berry[t.k] = false; }
				}
				if (dealt && mv.recoil) hp[k] -= Math.max(1, Math.round(dealt * mv.recoil));
				if (dealt && mv.drain) hp[k] = Math.min(x.max, hp[k] + Math.round(dealt * mv.drain));
				if (berry[k] && hp[k] > 0 && hp[k] <= x.max / 2) { hp[k] += Math.floor(x.max / 4); berry[k] = false; }
				const a0 = alive(0), a1 = alive(1);
				if (!a0 || !a1) { result = !a0 && !a1 ? 0.5 : a1 ? 0 : 1; break; }
			}
			if (result !== null) break;
			mons.forEach((x, k) => { if (hp[k] > 0) hp[k] = Math.min(x.max, hp[k] + x.residual); });
			const a0 = alive(0), a1 = alive(1);
			if (!a0 || !a1) result = !a0 && !a1 ? 0.5 : a1 ? 0 : 1;
		}
		if (result === null) {
			const share = s => mons.reduce((a, x, k) => a + (x.side === s ? Math.max(0, hp[k]) / x.max : 0), 0);
			const a = share(0), c = share(1);
			result = a + c > 0 ? a / (a + c) : 0.5;
		}
		won += result;
	}
	return won / MELEE_RUNS;
}

// Minimax value of a zero-sum matrix game for the row player: a saddle point when there is one,
// otherwise regret matching, which converges to the value at 1/sqrt(iterations).
function solve(M) {
	const rows = M.length, cols = M[0].length;
	const rowMin = M.map(r => Math.min(...r));
	const colMax = M[0].map((_, j) => Math.max(...M.map(r => r[j])));
	const lower = Math.max(...rowMin), upper = Math.min(...colMax);
	if (upper - lower < 1e-9) {
		const r = rowMin.indexOf(lower), c = colMax.indexOf(upper);
		return {value: lower, p1: M.map((_, i) => +(i === r)), p2: M[0].map((_, j) => +(j === c))};
	}
	const R1 = new Array(rows).fill(0), R2 = new Array(cols).fill(0);
	const S1 = new Array(rows).fill(0), S2 = new Array(cols).fill(0);
	const strat = R => { const pos = R.map(x => Math.max(x, 0)); const t = pos.reduce((a, x) => a + x, 0);
		return t > 0 ? pos.map(x => x / t) : R.map(() => 1 / R.length); };
	for (let it = 0; it < 4000; it++) {
		const x = strat(R1), y = strat(R2);
		const u1 = M.map(r => r.reduce((a, v, j) => a + v * y[j], 0));
		const u2 = M[0].map((_, j) => M.reduce((a, r, i) => a + r[j] * x[i], 0));
		const v = u1.reduce((a, u, i) => a + u * x[i], 0);
		for (let i = 0; i < rows; i++) { R1[i] += u1[i] - v; S1[i] += x[i]; }
		for (let j = 0; j < cols; j++) { R2[j] += v - u2[j]; S2[j] += y[j]; }
	}
	const x = S1.map(s => s / 4000), y = S2.map(s => s / 4000);
	const value = M.reduce((a, r, i) => a + r.reduce((b, v, j) => b + x[i] * v * y[j], 0), 0);
	return {value, p1: x, p2: y};
}

// Thrown by the scripted PRNG at a random call the script does not cover: its options, each with
// its probability, for the caller to try in turn.
class Branch extends Error {
	constructor(options) { super('branch'); this.options = options; }
}

/**
 * Chance, enumerated instead of sampled. Showdown draws every random event through four PRNG
 * methods — accuracy, Protect, crits, secondary effects, damage rolls, Speed ties — so replacing
 * the PRNG lets a turn be replayed down each branch with its true probability.
 *
 * Sampling was tried first and was not good enough: with a few seeds a node, a 90% Rock Slide
 * came out at 67% on one draw, and with fewer seeds deeper down each player effectively saw the
 * dice before choosing, so two runs of the same Trick Room stall gave 1.0 and 0.89.
 *
 * Damage rolls (`random(16)`) are the one place this approximates: `rolls` representative rolls
 * of equal weight instead of all sixteen. Rolls decide these positions only at a KO threshold,
 * and four keeps the branching of two attacks a turn in hand.
 */
class ScriptedPRNG {
	constructor(script, rolls, rare) { this.script = script; this.pos = 0; this.rolls = rolls || 16; this.rare = rare || 0; }
	getSeed() { return 'sodium,' + '0'.repeat(64); }
	clone() { return this; }
	pick(options) {
		if (options.length === 1) return options[0].v;
		if (this.pos < this.script.length) return options[this.script[this.pos++].k].v;
		throw new Branch(options);
	}
	random(from, to) {
		if (from === undefined) return this.pick([{v: 0.25, p: 0.5}, {v: 0.75, p: 0.5}]);
		const lo = to ? Math.floor(from) : 0, hi = to ? Math.floor(to) : Math.floor(from);
		const n = hi - lo;
		if (n === 16 && lo === 0 && this.rolls < 16) {
			const step = 16 / this.rolls;
			return this.pick(Array.from({length: this.rolls}, (_, i) => ({v: Math.floor(step * (i + 0.5)), p: 1 / this.rolls})));
		}
		if (n > 16) {
			// A percentage roll compared against a chance (secondary effects, and self drops that
			// roll even when certain). Ten representatives are exact for any chance that is a
			// multiple of a tenth, which is nearly all of them; a hundred branches were not usable.
			return this.pick(Array.from({length: 10}, (_, i) => ({v: lo + Math.floor(n * (i + 0.5) / 10), p: 0.1})));
		}
		return this.pick(Array.from({length: n}, (_, i) => ({v: lo + i, p: 1 / n})));
	}
	randomChance(numerator, denominator) {
		const p = numerator / denominator;
		if (p <= 0) return false;
		if (p >= 1) return true;
		// Events below `rare` do not happen. Off by default: a position that is lost unless a
		// 1-in-24 crit lands is worth 1 in 24, not 0, and that is the thin out a WP number most
		// needs to get right. What keeps crits affordable is `cutoff`, not this.
		if (p < this.rare) return false;
		return this.pick([{v: true, p}, {v: false, p: 1 - p}]);
	}
	sample(items) {
		// Equal items are one outcome: a 2–5-hit move samples twenty (7× 2, 7× 3, 3× 4, 3× 5), and
		// as a percentage roll its ten representatives gave 0.40 / 0.30 / 0.10 / 0.20 hits for the
		// true 0.35 / 0.35 / 0.15 / 0.15. Grouped, it is exact and four branches.
		const counts = new Map();
		for (const x of items) counts.set(x, (counts.get(x) || 0) + 1);
		if (counts.size > 16) return items[this.random(items.length)];
		return this.pick([...counts].map(([v, n]) => ({v, p: n / items.length})));
	}
	shuffle(items, start = 0, end = items.length) {
		// Only a tie between actions — a Speed tie between the two moves — decides anything. Ties
		// between event handlers are resolved in the order given rather than branched on.
		if (!(items[start] && items[start].choice)) return;
		while (start < end - 1) {
			const next = this.random(start, end);
			if (start !== next) [items[start], items[next]] = [items[next], items[start]];
			start++;
		}
	}
}

/**
 * `ScriptedPRNG`'s chance, drawn instead of branched on: the same representatives (two damage
 * rolls, ten for a percentage, equal items grouped), each picked with its probability by a seeded
 * PRNG. Sampling with the simulator's own PRNG drew all sixteen rolls, so two sampled turns almost
 * never ended in the same state, nothing merged, and each turn's outcomes multiplied sixteen-fold.
 */
class SamplingPRNG extends ScriptedPRNG {
	/**
	 * Draw `i` of the `n` drawn for a turn. The draws are stratified: the j-th random event of the
	 * turn gives each draw its own n-th of [0, 1), in an order `strata(j)` shuffles, so a Speed tie
	 * over sixteen draws splits eight and eight rather than binomially, and a 90% move misses in
	 * one or two of them rather than anywhere from none to five.
	 */
	constructor(seed, rolls, rare, i, n, strata) {
		super([], rolls, rare);
		this.draw = new PRNG(seed); this.i = i; this.n = n; this.strata = strata; this.j = 0;
	}
	pick(options) {
		if (options.length === 1) return options[0].v;
		let u = (this.strata(this.j++)[this.i] + this.draw.random()) / this.n;
		for (const o of options) { if ((u -= o.p) < 0) return o.v; }
		return options[options.length - 1].v;
	}
}

/** Shuffled strata for each random event of a turn's draws, the same for all of them. */
function strataFor(base, n) {
	const made = [];
	return j => {
		if (!made[j]) {
			const rng = new PRNG(seedFor(`${base}|strata|${j}`));
			const perm = [...Array(n).keys()];
			for (let k = n - 1; k > 0; k--) {
				const r = Math.floor(rng.random() * (k + 1));
				[perm[k], perm[r]] = [perm[r], perm[k]];
			}
			made[j] = perm;
		}
		return made[j];
	};
}

function seedFor(pathKey) {
	return 'sodium,' + crypto.createHash('sha256').update(pathKey).digest('hex');
}

function solvePosition(pos) {
	// The race's tables are for one search: another position's Pokémon share a key, not a spread.
	DAMAGE.clear(); PRIORITY.clear();
	const search = {depth: 4, rolls: 2, rare: 0, cutoff: 1e-3, min_prob: 1e-7, ...(pos.search || {})};
	const t0 = Date.now();
	const root = setUp(pos);
	if (pos.debug) {
		// One turn from the position, with the log: {"debug": {"p1": choice, "p2": choice, "seed": n}}.
		// For checking that a position is what it claims to be before trusting its value.
		const d = pos.debug;
		root.log = []; root.sentLogPos = 0;
		root.prng = new PRNG(seedFor(`debug|${d.seed || 0}`));
		root.choose('p1', d.p1 || options(root, root.sides[0])[0]);
		root.choose('p2', d.p2 || options(root, root.sides[1])[0]);
		return ({
			log: root.log, ended: root.ended, winner: root.winner, trickroom: root.field.pseudoWeather.trickroom || null,
			weather: [root.field.weather, root.field.weatherState.duration],
			sides: root.sides.map(s => Object.fromEntries(Object.entries(s.sideConditions).map(([k, v]) => [k, v.duration]))),
			active: root.sides.map(s => s.active.filter(p => p && !p.fainted).map(p => ({
				species: p.species.name, hp: p.hp, maxhp: p.maxhp, spe: p.getStat('spe'), item: p.item,
				status: p.status}))),
		});
	}
	const cache = new Map();
	let nodes = 0, dropped = 0, pruned = 0;
	// With `search.stats`: turns replayed and distinct outcomes, and the matrix sizes met, by how
	// many Pokémon each side had — what the doubles cost estimates are measured with.
	const stats = {replays: 0, outcomes: 0, matrices: {}};
	const alive = bt => bt.sides.map(sd => sd.active.filter(p => p && !p.fainted).length);

	// Every way one turn can go for a pair of choices, with its probability: the turn is replayed
	// under a scripted PRNG, and each random call past the end of the script throws its options
	// back here to be tried one by one. Outcomes that end in the same state are merged.
	/**
	 * Chance sampled rather than enumerated, for `search.sample`: the turn is played that many
	 * times, each random event drawn from the representatives the exact search would branch on
	 * (`SamplingPRNG`), and the outcomes are merged by state with their frequencies. Enumerating is exponential in
	 * the hits a turn: a doubles turn with spread moves has up to eight, and no single outcome of
	 * it is as likely as 1e-3 (PLAN-endgame-doubles, stage 1). Each turn's dice are drawn after
	 * both sides choose, as on the cartridge, so this is not the determinization that failed in
	 * the 1v1, where the dice were fixed before the choices. The seeds come from the position and
	 * the choices, so a position gives the same answer every time and can be cached.
	 */
	function sampled(snap, a, c, nodeKey) {
		const found = new Map();
		const n = search.sample;
		const strata = strataFor(`${nodeKey}|${a}|${c}`, n);
		for (let i = 0; i < n; i++) {
			const child = State.deserializeBattle(snap);
			child.restart(() => {});
			child.log = []; child.sentLogPos = 0;
			child.prng = new SamplingPRNG(seedFor(`${nodeKey}|${a}|${c}|${i}`), search.rolls, search.rare, i, n, strata);
			nodes++;
			if (!child.choose('p1', a) || !child.choose('p2', c)) {
				throw new Error(`the simulator rejected ${JSON.stringify([a, c])}`);
			}
			const k = child.ended ? `end:${child.winner}` : key(child);
			const hit = found.get(k);
			if (hit) hit.p += 1 / n; else found.set(k, {p: 1 / n, battle: child});
		}
		stats.replays = nodes;
		stats.outcomes += found.size;
		const out = [...found.values()];
		out.sampled = true;
		return out;
	}

	// With `search.sample`, a turn is still enumerated while that takes no more replays than the
	// sample would: a 1v1 turn has a handful of outcomes, and sixteen draws of it cost more than
	// all of them and are less exact. Only a turn past that budget is sampled instead.
	function outcomes(snap, a, c, nodeKey, many = false) {
		// `sample_only`: a position with more than two Pokémon (`many`) samples at once. Its turns nearly
		// always pass the budget, and the replays spent finding that out were half a cell's cost; the
		// exact question that matters, a win forced next turn, is `win_check`'s.
		if (search.sample && search.sample_only && many) {
			return sampled(snap, a, c, nodeKey);
		}
		const budget = search.sample || Infinity;
		const start = nodes, droppedBefore = dropped;
		const found = new Map();
		const stack = [[]];
		while (stack.length) {
			if (nodes - start >= budget) {
				dropped = droppedBefore;
				return sampled(snap, a, c, nodeKey);
			}
			const script = stack.pop();
			const p = script.reduce((acc, s) => acc * s.p, 1);
			const child = State.deserializeBattle(snap);
			child.restart(() => {});
			child.log = []; child.sentLogPos = 0;
			child.prng = new ScriptedPRNG(script, search.rolls, search.rare);
			nodes++;
			try {
				// A rejected choice leaves the turn unplayed, and the search would score it as if
				// it had been: Helping Hand with no target once read as a certain win.
				if (!child.choose('p1', a) || !child.choose('p2', c)) {
					throw new Error(`the simulator rejected ${JSON.stringify([a, c])}`);
				}
			} catch (e) {
				if (!(e instanceof Branch)) throw e;
				let kept = 0, likeliest = 0;
				e.options.forEach((o, k) => {
					if (o.p > e.options[likeliest].p) likeliest = k;
					if (p * o.p >= search.min_prob) { stack.push(script.concat([{k, p: o.p}])); kept++; }
					else dropped += p * o.p;
				});
				// The likeliest branch always goes on: dropping every branch of a turn left a cell with
				// no weight, and its value came back empty.
				if (!kept) {
					stack.push(script.concat([{k: likeliest, p: e.options[likeliest].p}]));
					dropped -= p * e.options[likeliest].p;
				}
				continue;
			}
			const k = child.ended ? `end:${child.winner}` : key(child);
			const hit = found.get(k);
			if (hit) hit.p += p; else found.set(k, {p, battle: child});
		}
		stats.replays = nodes;
		stats.outcomes += found.size;
		return [...found.values()];
	}

	// `reach` is the probability of the chance events on the way here. A line below `cutoff` is
	// scored by HP share rather than searched on: a crit that wins on the spot ends the battle and
	// is counted exactly, while one that only chips opens a subtree worth little — its error is at
	// most its probability, and it is reported in `leaf_mass` with the depth cut-off.
	function value(snap, battle, depth, reach) {
		if (battle.ended) return {v: battle.winner === 'p1' ? 1 : battle.winner === 'p2' ? 0 : 0.5, leaf: 0};
		if (depth >= search.depth || reach < search.cutoff) {
			let r = search.race ? race(snap) : null;
			if (r === null && search.race_doubles) {
				r = melee(snap, key(battle), search.fast_race ? battle : null);
				if (r !== null && search.race_doubles === 'calibrated') r = calibrated(r);
			}
			return {v: r === null ? leaf(battle) : r, leaf: 1};
		}
		// A 1v1 below a doubles root, valued by its damage race rather than searched.
		if (search.race_1v1 && rootAlive > 2) {
			const here = alive(battle);
			if (here[0] === 1 && here[1] === 1) {
				const r = race(snap);
				if (r !== null) { stats.raced = (stats.raced || 0) + 1; return {v: r, leaf: 1}; }
			}
		}
		const k = key(battle) + '#' + depth;
		if (cache.has(k)) return cache.get(k);
		const nodeKey = k;
		let o1 = options(battle, battle.sides[0]), o2 = options(battle, battle.sides[1]);
		if (search.prune) {
			const copy = scorer(snap);
			const n = o1.length + o2.length;
			o1 = prune(copy, 0, o1, search.prune);
			o2 = prune(copy, 1, o2, search.prune);
			pruned += n - o1.length - o2.length;
		}
		const here = alive(battle);
		const kind = here.join('v');
		stats.matrices[kind] = stats.matrices[kind] || {};
		const size = `${o1.length}x${o2.length}`;
		stats.matrices[kind][size] = (stats.matrices[kind][size] || 0) + 1;
		const M = [], L = [], V = [];
		for (const a of o1) {
			const row = [], lrow = [], vrow = [];
			for (const c of o2) {
				let sum = 0, leafSum = 0, mass = 0, sq = 0;
				const outs = outcomes(snap, a, c, nodeKey, here[0] + here[1] > 2);
				for (const o of outs) {
					// The KO extension: a turn that costs a Pokémon does not use up a turn of the
					// depth, so a trade is searched on into the smaller position instead of being
					// scored by HP share. In a 1v1 a KO ends the battle, so nothing changes there.
					const after = alive(o.battle);
					const next = search.ko_extend !== false && after[0] + after[1] < here[0] + here[1] ? depth : depth + 1;
					const r = value(State.serializeBattle(o.battle), o.battle, next, reach * o.p);
					sum += o.p * r.v; leafSum += o.p * r.leaf; mass += o.p; sq += o.p * r.v * r.v;
				}
				row.push(sum / mass); lrow.push(leafSum / mass);
				// The spread of the sampled turns' values in this cell (0 when its chance was enumerated).
				vrow.push(outs.sampled ? Math.max(0, sq / mass - (sum / mass) ** 2) : 0);
			}
			M.push(row); L.push(lrow); V.push(vrow);
		}
		const g = solve(M);
		const leafMass = L.reduce((acc, r, i) => acc + r.reduce((b, l, j) => b + g.p1[i] * l * g.p2[j], 0), 0);
		const out = {v: g.value, leaf: leafMass, M, g, o1, o2, V};
		cache.set(k, out);
		return out;
	}

	const round = x => Math.round(x * 1e4) / 1e4;

	/**
	 * `search.win_check = tol`: before anything else, does either side have a choice that wins this
	 * very turn against every reply, in all but `tol` of the turn's chance? A reply that Protects (or
	 * Endures) while that still works more than `tol` of the time only has to cost the side nothing:
	 * Protect's odds fall to a third each turn running, so the same sweep waits for it and the win
	 * is inevitable. The answer is then the worst reply's chance of the sweep (a crit or a flinch the
	 * other side needs shows there), and what is left over is valued by the race at the root. Each
	 * pair of choices is
	 * played out likeliest chance first and dropped the moment more than `tol` of it is not a win,
	 * so a choice that does not force the win usually costs a turn or two to rule out. Bounded by
	 * `search.win_budget` turns simulated (default 500, about a second); past it, no win is claimed. A forced win
	 * is the answer: the rest of the search, and the race at its horizon, cannot improve on it.
	 */
	function settles(snap, a, c, ok, tol, budget) {
		const frontier = [{script: [], p: 1}];
		let won = 0, lost = 0;
		while (frontier.length) {
			// Out of turns: what is known so far stands only if it already clears the bar.
			if (budget.left-- <= 0) return won >= 1 - tol ? won : null;
			let bi = 0;
			for (let i = 1; i < frontier.length; i++) if (frontier[i].p > frontier[bi].p) bi = i;
			const {script, p} = frontier[bi];
			frontier[bi] = frontier[frontier.length - 1]; frontier.pop();
			const child = State.deserializeBattle(snap);
			child.restart(() => {});
			child.log = []; child.sentLogPos = 0;
			child.prng = new ScriptedPRNG(script, search.rolls, search.rare);
			nodes++;
			try {
				if (!child.choose('p1', a) || !child.choose('p2', c)) return null;
			} catch (e) {
				if (!(e instanceof Branch)) throw e;
				e.options.forEach((o, k) => frontier.push({script: script.concat([{k, p: o.p}]), p: p * o.p}));
				continue;
			}
			if (ok(child)) won += p; else lost += p;
			if (lost > tol) return null;
			// Played on until all but `WIN_EXACT` of the turn is known, so a crit or a flinch that lets
			// the other side through shows in the number rather than only passing the bar.
			if (won + lost >= 1 - WIN_EXACT) return won;
		}
		return won >= 1 - tol ? won : null;
	}
	// Whether `choice` could KO every foe this turn at all: each foe's HP within the damage aimed at
	// it at the lowest roll (the lowest roll alone is one turn in sixteen, far past any tolerance),
	// without crits (a win that needs one is not forced), and two hits for a
	// foe at full HP behind a Focus Sash or Sturdy. Ruling a choice out here costs a damage
	// calculation; ruling it out by playing it costs a turn simulated per chance event.
	function couldSweep(copy, side, choice) {
		const me = copy.sides[side], foes = copy.sides[1 - side].active;
		const live = foes.map((f, i) => (f && !f.fainted ? i : -1)).filter(i => i >= 0);
		const dealt = new Map(live.map(i => [i, {d: 0, hits: 0}]));
		choice.split(',').forEach((part, slot) => {
			const c = parse(part);
			const mon = me.active[slot];
			if (!c || !mon || mon.fainted) return;
			const req = me.activeRequest;
			const id = req && req.active && req.active[slot] ? req.active[slot].moves[c.move - 1].id : mon.moveSlots[c.move - 1].id;
			const move = copy.dex.moves.get(id);
			if (move.category === 'Status') return;
			const spread = ['allAdjacentFoes', 'allAdjacent'].includes(move.target) && live.length > 1;
			const hits = Array.isArray(move.multihit) ? move.multihit[1] : (move.multihit || 1);
			const aimed = spread ? live : c.target ? [c.target - 1] : live.slice(0, 1);
			for (const i of aimed) {
				if (!dealt.has(i)) continue;
				const x = dealt.get(i);
				x.d += damage(copy, mon, foes[i], id) * hits * (spread ? 0.75 : 1) * 85 / 92;
				x.hits += hits;
			}
		});
		return live.every(i => {
			const f = foes[i], x = dealt.get(i);
			const guarded = f.hp === f.maxhp && (f.item === 'focussash' || f.ability === 'sturdy');
			return x.d >= f.hp && (!guarded || x.hits >= 2);
		});
	}
	// Whether `reply` (a choice of `side`'s) Protects, Endures or the like with more than `tol`
	// chance of it working (one time in 3^n after n in a row), and `against` has no way through it
	// (Unseen Fist, Feint and its kin).
	function stalls(b, side, reply, tol) {
		const me = b.sides[side], them = b.sides[1 - side];
		if (them.active.some(p => p && !p.fainted && (p.hasAbility('unseenfist')
			|| p.moveSlots.some(m => b.dex.moves.get(m.id).breaksProtect)))) return false;
		return reply.split(',').some((part, slot) => {
			const c = parse(part), f = me.active[slot];
			if (!c || !f || f.fainted) return false;
			const req = me.activeRequest;
			const id = req && req.active && req.active[slot] ? req.active[slot].moves[c.move - 1].id : f.moveSlots[c.move - 1].id;
			const mv = b.dex.moves.get(id);
			const odds = f.volatiles.stall ? 1 / f.volatiles.stall.counter : 1;
			return mv.stallingMove && !['wideguard', 'quickguard'].includes(mv.id) && odds > tol;
		});
	}
	const WIN_EXACT = 0.005;
	function forcedWin(tol) {
		const snap = State.serializeBattle(root);
		const o = [options(root, root.sides[0]), options(root, root.sides[1])];
		const budget = {left: search.win_budget || 500};
		const copy = scorer(snap);
		const here = alive(root);
		for (const side of [0, 1]) {
			const who = side === 0 ? 'p1' : 'p2';
			const wins = b => b.ended && b.winner === who;
			// Through a Protect: nothing of this side's falls, and the sweep is still there next turn.
			const holds = b => (b.ended ? b.winner === who : alive(b)[side] === here[side]);
			for (const mine of o[side]) {
				if (!couldSweep(copy, side, mine)) continue;
				let worst = 1, protect = false;
				// Replies that do not Protect first: those are the ones a sweep usually fails against.
				const replies = [...o[1 - side]].sort((x, y) => stalls(root, 1 - side, x, tol) - stalls(root, 1 - side, y, tol));
				for (const theirs of replies) {
					const stalled = stalls(root, 1 - side, theirs, tol);
					const ok = stalled ? holds : wins;
					const w = side === 0 ? settles(snap, mine, theirs, ok, tol, budget)
						: settles(snap, theirs, mine, ok, tol, budget);
					if (w === null) { worst = null; break; }
					if (stalled) protect = true; else worst = Math.min(worst, w);
				}
				if (worst !== null) return {side: who, choice: mine, won: worst, protect};
				if (budget.left <= 0) return null;
			}
		}
		return null;
	}
	if (search.win_check) {
		const f = forcedWin(search.win_check);
		if (f) {
			// Where the sweep fails, the race at the root stands for the rest of the game.
			const snap = State.serializeBattle(root);
			let rest = race(snap);
			if (rest === null) rest = search.race_doubles === 'calibrated' ? calibrated(melee(snap, key(root)) ?? 0.5) : leaf(root);
			const mine = f.side === 'p1' ? rest : 1 - rest;
			const v = f.won + (1 - f.won) * mine;
			return ({
				value: round(f.side === 'p1' ? v : 1 - v), leaf_mass: round(1 - f.won), nodes, dropped_mass: 0,
				ms: Date.now() - t0, forced: {side: f.side, choice: f.choice, sweep: round(f.won), through_protect: f.protect},
				moves: {[f.side]: [f.choice]},
			});
		}
	}

	const rootAlive = alive(root).reduce((a, b) => a + b, 0);
	const r = value(State.serializeBattle(root), root, 0, 1);
	return ({
		value: round(r.v), leaf_mass: round(r.leaf), nodes, dropped_mass: round(dropped), ms: Date.now() - t0,
		...(search.prune ? {pruned} : {}),
		...(search.stats ? {stats} : {}),
		// The root's sampling error: the cells' spread at the equilibrium, over the turns sampled.
		// It covers the first turn's dice; deeper turns' are in it only through their values.
		...(search.sample && r.V ? {sampling_se: round(Math.sqrt(r.V.reduce((acc, row, i) =>
			acc + row.reduce((b2, v, j) => b2 + r.g.p1[i] * v * r.g.p2[j], 0), 0) / search.sample))} : {}),
		moves: {p1: r.o1, p2: r.o2}, matrix: r.M && r.M.map(row => row.map(round)),
		strategy: r.g && {p1: r.g.p1.map(round), p2: r.g.p2.map(round)},
	});
}

/**
 * `--serve`: one process for many positions, so Showdown is loaded once rather than per position
 * (about 0.4 s each, which a live answer cannot spare). A line `{id, position}` in, a line `{id,
 * result}` or `{id, error}` out, one at a time, in order. Nothing is carried from one position to
 * the next: the race's tables are cleared, and every other state is the search's own.
 */
function serve() {
	const rl = require('readline').createInterface({input: process.stdin});
	rl.on('line', line => {
		if (!line.trim()) return;
		let req;
		try { req = JSON.parse(line); } catch (e) { return; }
		let out;
		try { out = {id: req.id, result: solvePosition(req.position)}; } catch (e) { out = {id: req.id, error: String(e && e.stack || e)}; }
		process.stdout.write(JSON.stringify(out) + '\n');
	});
}

if (process.argv.includes('--serve')) serve();
else process.stdout.write(JSON.stringify(solvePosition(JSON.parse(require('fs').readFileSync(0, 'utf8')))) + '\n');
