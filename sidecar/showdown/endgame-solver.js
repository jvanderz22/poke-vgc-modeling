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

// How many Pokémon a side has on the field: one or two, the first ones in its team.
function actives(pos, side) {
	return Math.max(1, Math.min(2, (pos.active || {})[side.id] || 1));
}

// How many it has in the back (`bench`, PLAN-policy stage 1): the ones after those on the field,
// before the fainted ones. None unless the position says so, and only behind two on the field:
// with a Pokémon in the back, an empty slot is filled before anyone chooses again.
function benched(pos, side) {
	const m = Math.max(0, Math.min(2, (pos.bench || {})[side.id] || 0));
	if (m && actives(pos, side) !== 2) throw new Error(`${side.id} has a Pokémon in the back but not two on the field`);
	return m;
}

// The Pokémon a side has left, on the field or in the back.
function standing(side) {
	return side.pokemon.filter(p => p && !p.fainted);
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
		const n = actives(pos, side), m = benched(pos, side);
		for (const p of side.pokemon.slice(n + m)) {
			p.hp = 0; p.fainted = true; p.status = 'fnt'; p.switchFlag = false;
		}
		side.pokemonLeft = n + m;
		side.totalFainted = (s.fainted || {})[side.id] ?? 4 - n - m;
		// A field about a Pokémon lists those on the field, then those in the back.
		side.pokemon.slice(0, n + m).forEach((me, i) => {
			me.clearBoosts();
			// On the field for a while unless the position says it just arrived: Fake Out and First
			// Impression fail after a Pokémon's first turn out, and a hand reading of the benchmark
			// assumed they would. One in the back starts its count again when it comes in.
			if (i < n && !per(s.fresh, side, i)) { me.activeTurns = 3; me.activeMoveActions = 3; }
			const pct = per(s.hp, side, i) ?? 100;
			me.sethp(Math.max(1, Math.round(me.maxhp * pct / 100)));
			if (per(s.mega, side, i)) b.actions.runMegaEvo(me);
		});
	}
	if (s.weather) { f.setWeather(s.weather[0], 'debug'); f.weatherState.duration = s.weather[1]; }
	if (s.terrain) { f.setTerrain(s.terrain[0], 'debug'); f.terrainState.duration = s.terrain[1]; }
	if (s.trickroom) { f.addPseudoWeather('trickroom', 'debug'); f.pseudoWeather.trickroom.duration = s.trickroom; }
	for (const side of b.sides) side.pokemon.slice(0, actives(pos, side) + benched(pos, side)).forEach((me, i) => {
		// What a switch clears (stages, a lock, the Protect counter) belongs to those on the field.
		const out = i < actives(pos, side);
		for (const [stat, n] of Object.entries((out && per(s.boosts, side, i)) || {})) me.boosts[stat] = n;
		if (per(s.consumed, side, i)) {
			me.setItem('');
			// Unburden's speed is a volatile, and a switch clears it.
			if (out && me.hasAbility('unburden')) me.addVolatile('unburden');
		}
		const lock = out && per(s.choicelock, side, i);
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
		const stall = out && per(s.stall, side, i);
		if (stall) { me.addVolatile('stall'); me.volatiles.stall.counter = 3 ** stall; }
	});
	// `megaUsed`: the side has Mega Evolved already, with a Pokémon no longer in the position, so a
	// second stone holder cannot (a position with a bench may have one).
	for (const side of b.sides) if ((s.megaUsed || {})[side.id]) for (const p of side.pokemon) p.canMegaEvo = null;
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
//
// `flags` (PLAN-policy stage 1), all off unless the search turns them on:
//   switches  a switch to each Pokémon in the back, unless this one is trapped (Shadow Tag,
//             Arena Trap: the simulator knows, though the request may hide it)
//   mega      where a Pokémon can Mega Evolve, its moves are offered with the Mega and not
//             without: it is what players do, and it keeps the list from doubling
function slotOptions(b, side, slot, flags = {}) {
	const req = side.activeRequest;
	const p = side.active[slot];
	if (!p || p.fainted || !req.active[slot]) return ['pass'];
	const foes = b.sides[1 - side.n].active.map((q, i) => (q && !q.fainted ? i : -1)).filter(i => i >= 0);
	const out = [];
	// A move Imprison blocks is disabled 'hidden': the last Pokémon a side has is shown it as
	// usable, and the simulator refuses it when chosen. A player would be told and choose again,
	// so it is no choice at all. With every move blocked, any choice is Struggle.
	const hidden = new Set(p.moveSlots.filter(s => s.disabled === 'hidden').map(s => s.id));
	if (req.active[slot].moves.every(m => m.disabled || hidden.has(m.id))) return ['move 1', ...switches(side, slot, p, flags)];
	const can = flags.mega && req.active[slot].canMegaEvo;
	// `mega: 'both'` offers each move with and without it, for a model that knows when people wait.
	const suffixes = can ? (flags.mega === 'both' ? ['', ' mega'] : [' mega']) : [''];
	for (const mega of suffixes) req.active[slot].moves.forEach((m, i) => {
		if (m.disabled || hidden.has(m.id)) return;
		if (['normal', 'any', 'adjacentFoe'].includes(m.target) && foes.length) {
			for (const f of foes) out.push(`move ${i + 1} ${f + 1}${mega}`);
		} else if (m.target === 'adjacentAlly') {
			out.push(`move ${i + 1} -${2 - slot}${mega}`);
		} else if (m.target === 'adjacentAllyOrSelf') {
			out.push(`move ${i + 1} -${slot + 1}${mega}`);
		} else {
			out.push(`move ${i + 1}${mega}`);
		}
	});
	out.push(...switches(side, slot, p, flags));
	return out.length ? out : ['pass'];
}

// The Pokémon a side has in the back, as the choices that bring each in ('switch 3').
function bench(side) {
	return side.pokemon.map((q, i) => ({q, i})).filter(x => x.i >= side.active.length && !x.q.fainted)
		.map(x => `switch ${x.i + 1}`);
}

function switches(side, slot, p, flags) {
	if (!flags.switches || p.trapped || side.activeRequest.active[slot].trapped) return [];
	return bench(side);
}

// A side asked to fill its empty slots (after a KO, or in the middle of a turn after U-turn,
// Parting Shot or an Eject Button): every way to fill as many as its back allows.
function replacements(side) {
	const need = side.activeRequest.forceSwitch.map(Boolean);
	// Revival Blessing asks the same way, for a fainted Pokémon to bring back (the adapters do not
	// build positions with it, whose fainted Pokémon are fillers; the list is still kept legal).
	const revive = need.findIndex((n, i) => n && side.active[i] && side.slotConditions[side.active[i].position].revivalblessing);
	if (revive >= 0) {
		const fainted = side.pokemon.map((q, i) => ({q, i})).filter(x => x.i >= side.active.length && x.q.fainted)
			.map(x => `switch ${x.i + 1}`);
		return fainted.map(f => need.map((n, i) => (i === revive ? f : 'pass')).join(', '));
	}
	const back = bench(side);
	const want = Math.min(need.filter(Boolean).length, back.length);
	const per = need.map(n => (n ? [...back, 'pass'] : ['pass']));
	const out = [];
	for (const x of per[0]) for (const y of per.length > 1 ? per[1] : ['pass']) {
		if (x !== 'pass' && x === y) continue;
		if ([x, y].filter(c => c !== 'pass').length !== want) continue;
		out.push(per.length > 1 ? `${x}, ${y}` : x);
	}
	return out.length ? out : ['pass'];
}

function options(b, side, flags = {}) {
	const req = side.activeRequest;
	if (!req || req.wait) return ['pass'];
	if (req.forceSwitch) return replacements(side);
	if (!req.active) return ['pass'];
	let [a, c] = [0, 1].map(slot => slotOptions(b, side, slot, flags));
	// Both able to Mega Evolve: only one can, so each also keeps its moves without it.
	const megas = list => list.some(x => x.endsWith(' mega'));
	if (megas(a) && megas(c)) {
		const both = list => [...list, ...list.filter(x => x.endsWith(' mega')).map(x => x.slice(0, -5))];
		[a, c] = [both(a), both(c)];
	}
	const out = [];
	for (const x of a) for (const y of c) {
		if (x === 'pass' && y === 'pass') continue;
		// Two slots cannot bring in the same Pokémon, and a side Mega Evolves once.
		if (x.startsWith('switch') && x === y) continue;
		if (x.endsWith(' mega') && y.endsWith(' mega')) continue;
		out.push(`${x}, ${y}`);
	}
	return out.length ? out : ['pass'];
}

// Whether a battle is waiting on replacements rather than on a turn's moves.
function replacing(b) {
	return b.sides.some(s => s.activeRequest && s.activeRequest.forceSwitch);
}

// Both sides' choices for one step, skipping a side that has nothing to choose (the other side's
// replacement in the middle of a turn). False if the simulator rejects one.
function play(b, a, c) {
	// Who is waiting is read before anyone chooses: the one side owing a replacement finishes the
	// turn when it chooses, and the other side then has the next turn's request, not a wait.
	const waiting = b.sides.map(side => !!(side.activeRequest && side.activeRequest.wait));
	for (const [i, ch] of [[0, a], [1, c]]) {
		if (waiting[i]) continue;
		if (!b.choose(b.sides[i].id, ch)) return false;
	}
	return true;
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

// One choice's parts: the move slot (1-based) and target, from 'move 2 1', 'move 3 -2' or
// 'move 1 2 mega'. A switch is not a move: null.
function parse(part) {
	const m = /^move (\d+)(?: (-?\d+))?(?: mega)?$/.exec(part.trim());
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
	return threatTo(copy, side, copy.sides[side].active[slot]);
}

// The same for any Pokémon of `side`'s, one in the back included (were it on the field now).
function threatTo(copy, side, me) {
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

/**
 * What one Pokémon's choice is, as numbers, for a model of how people choose (PLAN-policy, stage 3):
 * `list_only` with `search.features` reports them for every legal choice. Damage and KO as `score`
 * has them; the kind of move; for a switch, how much less the one coming in stands to lose. The
 * names are the model's columns (`vgc.policy.people`).
 */
const SPEED_CONTROL = new Set(['tailwind', 'trickroom', 'icywind', 'electroweb', 'thunderwave', 'scaryface', 'bulldoze', 'rocktomb']);
const REDIRECT = new Set(['followme', 'ragepowder']);
const SCREENS = new Set(['reflect', 'lightscreen', 'auroraveil']);
const DISRUPT = new Set(['taunt', 'encore', 'disable', 'imprison', 'quash', 'snarl', 'faketears', 'partingshot']);
const INFLICT = new Set(['spore', 'sleeppowder', 'hypnosis', 'yawn', 'willowisp', 'thunderwave', 'toxic', 'glare', 'nuzzle']);
function features(copy, side, slot, part, danger) {
	const f = {};
	const me = copy.sides[side].active[slot];
	if (part.startsWith('switch')) {
		const t = threatTo(copy, side, copy.sides[side].pokemon[+part.split(' ')[1] - 1]);
		f.switch = 1; f.switch_saved = danger() - t; f.switch_danger = danger();
		return f;
	}
	const c = parse(part);
	if (!c) return f;
	if (part.endsWith(' mega')) f.mega = 1;
	const req = copy.sides[side].activeRequest;
	const id = req && req.active && req.active[slot] ? req.active[slot].moves[c.move - 1].id : me.moveSlots[c.move - 1].id;
	const move = copy.dex.moves.get(id);
	const sc = score(copy, side, slot, part.replace(/ mega$/, ''), danger);
	if (move.stallingMove) {
		f[['wideguard', 'quickguard'].includes(id) ? 'guard' : 'protect'] = 1;
		f.protect_danger = danger();
		if (me.volatiles.stall) f.protect_again = 1;
		return f;
	}
	if (id === 'fakeout') { f.fakeout = 1; f.fakeout_works = me.activeTurns <= 1 ? 1 : 0; return f; }
	if (SPEED_CONTROL.has(id)) {
		f.speed_control = 1;
		if (id === 'tailwind' && copy.sides[side].sideConditions.tailwind) f.already_up = 1;
		if (id === 'trickroom' && copy.field.pseudoWeather.trickroom) f.trickroom_up = 1;
	}
	if (move.category === 'Status') {
		f.status = 1;
		if (REDIRECT.has(id)) f.redirect = 1;
		else if (id === 'helpinghand') f.helpinghand = 1;
		else if (SCREENS.has(id)) { f.screen = 1; if (copy.sides[side].sideConditions[id]) f.already_up = 1; }
		else if (DISRUPT.has(id)) f.disrupt = 1;
		else if (INFLICT.has(id)) f.inflict = 1;
		else if (move.boosts && move.target === 'self') { f.setup = 1; f.setup_danger = danger(); }
		else if (move.heal || move.flags.heal) { f.heal = 1; f.missing_hp = 1 - me.hp / me.maxhp; }
		else if (!SPEED_CONTROL.has(id)) f.other_status = 1;
		return f;
	}
	f.attack = 1; f.damage = sc.score - (sc.ko ? 1 : 0); f.ko = sc.ko ? 1 : 0;
	if ((sc.priority || 0) > 0) f.priority = 1;
	if (['allAdjacentFoes', 'allAdjacent'].includes(move.target)) f.spread = 1;
	if (c.target !== null && c.target < 0) f.at_ally = 1;
	return f;
}

/**
 * `search.prune_by: 'people'` (PLAN-policy, stage 3): realistic play as people play. Each Pokémon keeps
 * what `prune` keeps and the `people_top` (3) choices a model of people ranks highest, and the side's
 * pairs are ranked by the two Pokémon's summed scores, the top `side_k` kept. The model is a
 * conditional logit over `features`, fitted on 33,468 choices of training-split human players
 * (`scripts/analysis/prune_vs_people.py`, `vgc.policy.people`). Held out, a person's pair is in the top
 * 6 so ranked 35% of the time (43% in the top 8), against 28% (31%) for `prune`'s scores; a person's
 * status move is in the model's top 3 79% of the time, against 19% for `prune` at 3. A change of
 * weights takes a new `prune_by` name.
 */
const PEOPLE = {attack: -1.0887, damage: 2.1375, ko: 0.1294, priority: -0.129, spread: 0.8316, protect: -0.1302, protect_danger: 1.3673, protect_again: -1.3165, fakeout: 0.7634, fakeout_works: 0.7634, speed_control: 0.1999, already_up: -1.3157, trickroom_up: -0.826, status: 0.8115, redirect: 0.5909, helpinghand: 0.0719, screen: 0.4203, disrupt: -0.4573, inflict: -0.3016, setup: 0.7165, setup_danger: -0.5593, heal: -0.327, missing_hp: 0.1571, other_status: -0.2025, switch: -0.356, switch_saved: 0.9159, switch_danger: 0.7254, mega: 0.8283};
function peopleLogit(f) {
	let z = 0;
	for (const [k, v] of Object.entries(f)) z += (PEOPLE[k] || 0) * v;
	return z;
}
function prunePeople(copy, side, choices, k, K, top = 3, values = null) {
	const kept = prune(copy, side, choices, k);
	const parts = choices.map(ch => ch.split(',').map(x => x.trim()));
	const logit = [new Map(), new Map()];
	const keep = [0, 1].map(slot => {
		const mine = [...new Set(parts.map(p => p[slot]))].filter(x => x !== 'pass');
		let danger = null;
		const once = () => (danger === null ? (danger = threat(copy, side, slot)) : danger);
		for (const part of mine) logit[slot].set(part, peopleLogit(features(copy, side, slot, part, once)));
		const out = new Set(['pass', ...kept.map(ch => ch.split(',').map(x => x.trim())[slot])]);
		for (const part of [...mine].sort((a, b) => logit[slot].get(b) - logit[slot].get(a)).slice(0, top)) out.add(part);
		return out;
	});
	const ranked = choices.map((ch, j) => ({ch, j, p: parts[j]}))
		.filter(x => keep[0].has(x.p[0]) && keep[1].has(x.p[1] || 'pass'))
		.map(x => ({...x, v: (logit[0].get(x.p[0]) || 0) + (logit[1].get(x.p[1]) || 0)}));
	ranked.sort((x, y) => y.v - x.v || x.j - y.j);
	if (values) for (const x of ranked) values.set(x.ch, x.v);
	const out = (K ? ranked.slice(0, K) : ranked).map(x => x.ch);
	return out.length ? out : choices;
}

// A side's choices cut to `k` a Pokémon. Each Pokémon's part is scored and cut on its own, and
// the pairs are formed from what is left. A pair that sends two attacks at a foe one of them KOs
// alone is dropped while the other foe stands: that is overkill, and the pair that KOs and hits
// the other foe is kept instead. In a 1v1 the second part is always 'pass'.
//
// Switches (`search.switches`) are not cut with the moves. At most one is kept a Pokémon: to the
// one in the back that takes least from the foes' attacks, and only while the one on the field
// stands to lose at least `SWITCH_THREAT` of its HP to a single attack and the one coming in
// would lose less. `K` (`search.side_k`) then cuts the side's pairs to the K with the highest
// summed scores, a switch scoring the share of HP it saves.
const SWITCH_THREAT = 0.5;
function prune(copy, side, choices, k, K = 0, values = null) {
	if (!k) return choices;
	const parts = choices.map(ch => ch.split(',').map(x => x.trim()));
	const info = [new Map(), new Map()];
	const saved = [new Map(), new Map()];
	const kept = [0, 1].map(slot => {
		const all = [...new Set(parts.map(p => p[slot]))].filter(x => x !== 'pass');
		const mine = all.filter(x => !x.startsWith('switch'));
		let danger = null;
		const once = () => (danger === null ? (danger = threat(copy, side, slot)) : danger);
		const swap = bestSwitch(copy, side, all.filter(x => x.startsWith('switch')), once, saved[slot]);
		if (mine.length <= k) return new Set([...mine, ...(swap ? [swap] : []), 'pass']);
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
		if (swap) keep.add(swap);
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
	if (!out.length) return choices;
	if ((!K || out.length <= K) && !values) return out;
	const value = (slot, part) => {
		if (part === 'pass') return 0;
		if (saved[slot].has(part)) return saved[slot].get(part);
		if (!info[slot].has(part)) {
			let danger = null;
			const once = () => (danger === null ? (danger = threat(copy, side, slot)) : danger);
			info[slot].set(part, {part, ...score(copy, side, slot, part, once)});
		}
		return info[slot].get(part).score;
	};
	const ranked = out.map((ch, j) => {
		const [a, b] = ch.split(',').map(x => x.trim());
		return {ch, j, v: value(0, a) + value(1, b || 'pass')};
	});
	ranked.sort((x, y) => y.v - x.v || x.j - y.j);
	// `values` (list_only): what each pair that survived the per-Pokémon cut scored, for a model of
	// how people choose among them.
	if (values) for (const x of ranked) values.set(x.ch, x.v);
	return K ? ranked.slice(0, K).map(x => x.ch) : ranked.map(x => x.ch);
}

// The one switch worth keeping for a slot (see `prune`), or null; what it saves goes in `saved`.
function bestSwitch(copy, side, swaps, danger, saved) {
	if (!swaps.length || danger() < SWITCH_THREAT) return null;
	let best = null;
	for (const part of swaps) {
		const t = threatTo(copy, side, copy.sides[side].pokemon[+part.split(' ')[1] - 1]);
		if (t < danger() && (!best || t < best.t)) best = {part, t};
	}
	if (best) saved.set(best.part, danger() - best.t);
	return best ? best.part : null;
}

function key(b) {
	const f = b.field;
	const mon = p => [p.hp, p.status, p.item, JSON.stringify(p.boosts), Object.keys(p.volatiles).sort().join('.'),
		p.volatiles.stall ? p.volatiles.stall.counter : '', p.volatiles.choicelock ? p.volatiles.choicelock.move : '',
		p.timesAttacked, p.species.id].join(':');
	const out = [f.weather, f.weatherState.duration, f.terrain, f.terrainState.duration,
		Object.entries(f.pseudoWeather).map(([k, v]) => k + v.duration).join(','),
		...b.sides.map(s => s.active.filter(p => p && !p.fainted).map(mon).join('|'))].join('/');
	// The back, and a turn stopped for a replacement, only where there are any: a position without
	// a bench keeps the key it always had, and with it its sampled draws and cached answers.
	const back = b.sides.map(s => s.pokemon.slice(s.active.length).filter(p => p && !p.fainted).map(mon).join('|'));
	const stop = replacing(b) ? b.sides.map(s => JSON.stringify((s.activeRequest || {}).forceSwitch || 0)).join(',') +
		';' + b.queue.list.map(a => a.choice + (a.pokemon ? a.pokemon.side.id + a.pokemon.position : '')).join(',') : '';
	return back.some(x => x) || stop ? `${out}/back:${back.join('/')}/stop:${stop}` : out;
}

function leaf(b) {
	const share = side => standing(side).reduce((a, p) => a + p.hp / p.maxhp, 0);
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
	if (standing(copy.sides[0]).length !== 1 || standing(copy.sides[1]).length !== 1) return null;
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
// `race_doubles: 'blend'`: the race read together with what it underweights, P = sigmoid(a *
// logit(race) + b * logit(HP share) + c * (p1's Pokémon standing − p2's) + d), fitted on 3,542
// training-split open-sheet games at their first turn with two or fewer a side (never the held-out
// ones). Out of fold there it beat 'calibrated' on log loss by 0.026 in 2v2s and 0.049 in 2v1s and
// 1v2s: the race alone is too sure, and a count lead is worth more than the extra attacker it gives
// the race. The same rule: a change of answers takes a new name.
const MELEE_BLEND = {a: 0.2894, b: 0.8177, c: 0.7973, d: 0.0185, eps: 1 / 128};
function blended(v, battle) {
	const {a, b, c, d, eps} = MELEE_BLEND;
	const lg = x => { const q = Math.min(1 - eps, Math.max(eps, x)); return Math.log(q / (1 - q)); };
	const standing = s => battle.sides[s].active.filter(p => p && !p.fainted).length;
	return 1 / (1 + Math.exp(-(a * lg(v) + b * lg(leaf(battle)) + c * (standing(0) - standing(1)) + d)));
}
// `race_doubles: 'blend_boosts'`: 'blend' with the net stat stages as well (p1's summed over its
// Pokémon standing, minus p2's), which the race already sees in its damage and Speed but the blend
// shrinks with everything else: a side +3 or more ahead won about 10 points more than 'blend' gave
// it. Fitted the same way; out of fold 0.008 better than 'blend' in log loss, in every kind.
const MELEE_BLEND_BOOSTS = {a: 0.2799, b: 0.9160, c: 0.8601, e: 0.1480, d: 0.0133, eps: 1 / 128};
function blendedBoosts(v, battle) {
	const {a, b, c, e, d, eps} = MELEE_BLEND_BOOSTS;
	const lg = x => { const q = Math.min(1 - eps, Math.max(eps, x)); return Math.log(q / (1 - q)); };
	const up = s => battle.sides[s].active.filter(p => p && !p.fainted);
	const stages = s => up(s).reduce((t, p) => t + Object.values(p.boosts).reduce((u, x) => u + x, 0), 0);
	return 1 / (1 + Math.exp(-(a * lg(v) + b * lg(leaf(battle)) + c * (up(0).length - up(1).length) +
		e * (stages(0) - stages(1)) + d)));
}
// `race_doubles: 'policy'` (PLAN-policy, stage 3): with a Pokémon in the back, `meleeBench`'s race
// read with HP share over every Pokémon left, the count and the net stages of those on the field,
// P = sigmoid(a · logit(race) + b · logit(HP share) + c · count lead + e · stages + d), fitted on
// 26,635 training-split open-sheet states (the first turn of each kind above two a side, both backs
// guessed; `scripts/analysis/bench_race.py`). Held out (5,144 states): log loss 0.548 against the
// served model's 0.554 (not distinguishable) and the floor's 0.562. Without a back, 'blend_boosts'.
// A change of answers takes a new name.
const BENCH_BLEND = {a: 0.1372, b: 1.7251, c: 0.4604, e: 0.0997, d: 0.0223, eps: 1 / 128};
const lgClip = x => { const q = Math.min(1 - 1 / 128, Math.max(1 / 128, x)); return Math.log(q / (1 - q)); };
function hasBench(battle) {
	return battle.sides.some(sd => standing(sd).length > sd.active.filter(p => p && !p.fainted).length);
}
function stagesOn(battle, s) {
	return battle.sides[s].active.filter(p => p && !p.fainted)
		.reduce((t, p) => t + Object.values(p.boosts).reduce((u, x) => u + x, 0), 0);
}
function benchBlended(v, battle) {
	const {a, b, c, e, d} = BENCH_BLEND;
	return 1 / (1 + Math.exp(-(a * lgClip(v) + b * lgClip(leaf(battle)) +
		c * (standing(battle.sides[0]).length - standing(battle.sides[1]).length) + e * (stagesOn(battle, 0) - stagesOn(battle, 1)) + d)));
}
// `race_doubles: 'floor'`: no race, only HP share, the count and the stages, fitted the same way
// (held out: 0.562). For comparing what the race adds to a search, not for answers.
const FLOOR = {b: 2.1925, c: 0.5346, e: 0.1205, d: 0.0152};
function floorValue(v, battle) {
	const {b, c, e, d} = FLOOR;
	return 1 / (1 + Math.exp(-(b * lgClip(leaf(battle)) +
		c * (standing(battle.sides[0]).length - standing(battle.sides[1]).length) + e * (stagesOn(battle, 0) - stagesOn(battle, 1)) + d)));
}
// The race as the horizon's value under `search.race_doubles`: raw (true), 'calibrated', 'blend',
// 'blend_boosts', 'policy' or 'floor'.
const BLENDS = {calibrated: (v, battle) => calibrated(v), blend: blended, blend_boosts: blendedBoosts,
	policy: (v, battle) => (hasBench(battle) ? benchBlended(v, battle) : blendedBoosts(v, battle)), floor: floorValue};
function horizon(mode, v, battle) {
	return BLENDS[mode] ? BLENDS[mode](v, battle) : v;
}
function melee(snap, seedKey, live = null, search = null) {
	const runs = (search && search.melee_runs) || MELEE_RUNS;
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
	const rng = dice(search, `melee|${seedKey}`);
	let won = 0;
	for (let run = 0; run < runs; run++) {
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
	return won / runs;
}

/**
 * `search.race_bench` (PLAN-policy, stage 3): the race with Pokémon in the back. `melee` races only
 * those on the field, which is the whole game at two or fewer a side and a fraction of it above.
 * Here a fallen Pokémon's slot is filled at the end of the turn from the back (the one that does
 * most towards KOs against the foes then on the field), a spread move takes its 0.75 only while it
 * hits two, and Tailwind and Trick Room run out by their turns left instead of lasting the race.
 * Damage, priorities, accuracy, rolls, crits, Sash, Sitrus, recoil, drain, paralysis and residual
 * damage as `melee` has them, with the same tables under `fast_race`. P(p1's side outlasts p2's),
 * HP share over everything left for what is unresolved after `RACE_TURNS` turns. Its own name, so
 * `melee`'s answers, and the horizon fitted on them, do not move.
 */
function meleeBench(snap, seedKey, live = null, search = null) {
	const runs = (search && search.melee_runs) || MELEE_RUNS;
	let copy = live ? null : scorer(snap);
	const scoring = () => copy || (copy = scorer(snap));
	const b = live || copy;
	const twin = p => {
		const sd = scoring().sides[p.side.n];
		return p.isActive ? sd.active[p.position] : sd.pokemon[p.side.pokemon.indexOf(p)];
	};
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
	const left = [0, 1].map(s => standing(b.sides[s]));
	if (!left[0].length || !left[1].length) return null;
	const sun = ['sunnyday', 'desolateland'].includes(b.field.effectiveWeather());
	const tr = b.field.pseudoWeather.trickroom ? b.field.pseudoWeather.trickroom.duration || 0 : 0;
	const tw = [0, 1].map(s => (b.sides[s].sideConditions.tailwind ? b.sides[s].sideConditions.tailwind.duration || 0 : 0));
	// mons[k]: {side, mon, moves: [{dmg: [per mons index of a foe], spread, ...}], speed: Speed without Tailwind}
	const mons = [];
	for (const s of [0, 1]) for (const me of left[s]) mons.push({side: s, mon: me});
	for (const x of mons) {
		const me = x.mon;
		const req = me.isActive ? b.sides[x.side].activeRequest : null;
		const slot = me.isActive ? me.position : -1;
		const listed = req && req.active && req.active[slot] ? req.active[slot].moves : me.moveSlots;
		const hidden = new Set(me.moveSlots.filter(m => m.disabled).map(m => m.id));
		const foes = mons.filter(y => y.side !== x.side);
		x.moves = [];
		for (const m of listed) {
			if (m.disabled || hidden.has(m.id) || m.pp === 0) continue;
			const move = b.dex.moves.get(m.id);
			if (move.category === 'Status') continue;
			const spread = ['allAdjacentFoes', 'allAdjacent'].includes(move.target);
			const hits = Array.isArray(move.multihit) ? 3 : (move.multihit || 1);
			const dmg = new Map(foes.map(f => [mons.indexOf(f), Math.floor(dmgOf(me, f.mon, m.id) * hits)]));
			if (![...dmg.values()].some(d => d > 0)) continue;
			if (move.flags.charge && me.item !== 'powerherb' && !(sun && ['solarbeam', 'solarblade'].includes(move.id))) {
				for (const [k, d] of dmg) dmg.set(k, Math.floor(d / 2));
			}
			x.moves.push({dmg, spread, acc: move.accuracy === true ? 1 : move.accuracy / 100,
				priority: priorityOf(me, foes[0].mon, m.id) || 0,
				recoil: (move.recoil ? move.recoil[0] / move.recoil[1] : 0) + (me.item === 'lifeorb' ? 0.1 : 0),
				drain: move.drain ? move.drain[0] / move.drain[1] : 0});
		}
		let v = me.getActionSpeed();
		if (tr) v = 1e4 - v;
		x.speed = tw[x.side] ? v / 2 : v;
		Object.assign(x, {max: me.maxhp, sash: me.item === 'focussash' || me.ability === 'sturdy', sitrus: me.item === 'sitrusberry',
			para: me.status === 'par' ? 1 / 8 : 0,
			residual: (me.item === 'leftovers' ? Math.floor(me.maxhp / 16) : 0) - (me.status === 'brn' ? Math.floor(me.maxhp / 16) : 0)
				- (['psn', 'tox'].includes(me.status) ? Math.floor(me.maxhp / 8) : 0)});
	}
	const rng = dice(search, `meleebench|${seedKey}`);
	const startOn = mons.map(x => x.mon.isActive);
	let won = 0;
	for (let run = 0; run < runs; run++) {
		const hp = mons.map(x => x.mon.hp), berry = mons.map(x => x.sitrus), sash = mons.map(x => x.sash);
		const on = [...startOn];
		const alive = s => mons.some((x, k) => x.side === s && hp[k] > 0);
		const onField = s => mons.map((x, k) => k).filter(k => mons[k].side === s && on[k] && hp[k] > 0);
		const progress = (k, foes) => {
			let best = null;
			for (const mv of mons[k].moves) {
				const ts = foes.filter(f => (mv.dmg.get(f) || 0) > 0);
				if (!ts.length) continue;
				const groups = mv.spread ? [foes] : ts.map(f => [f]);
				for (const g of groups) {
					let v = 0;
					for (const f of g) { const d = (mv.dmg.get(f) || 0) * (mv.spread && foes.length > 1 ? 0.75 : 1); v += Math.min(1, d / hp[f]) + (d >= hp[f] ? 1 : 0); }
					v *= mv.acc;
					if (!best || v > best.v) best = {v, mv, ts: g};
				}
			}
			return best;
		};
		let result = null;
		for (let turn = 0; turn < RACE_TURNS && result === null; turn++) {
			const plan = mons.map((x, k) => (on[k] && hp[k] > 0 ? progress(k, onField(1 - x.side)) : null));
			const speedNow = k => {
				const s = mons[k].speed * (turn < tw[mons[k].side] ? 2 : 1);
				return turn < tr ? 1e4 - s : s;
			};
			const order = mons.map((x, k) => k).filter(k => plan[k])
				.map(k => ({k, pr: plan[k].mv.priority, sp: speedNow(k), tie: rng.random()}))
				.sort((a, c) => c.pr - a.pr || c.sp - a.sp || a.tie - c.tie).map(o => o.k);
			for (const k of order) {
				if (hp[k] <= 0) continue;
				const x = mons[k], {mv} = plan[k];
				if (x.para && rng.random() < x.para) continue;
				const foes = onField(1 - x.side);
				let ts = plan[k].ts.filter(t => hp[t] > 0 && on[t]);
				if (!ts.length && !mv.spread) ts = foes.slice(0, 1);
				if (mv.spread) ts = foes;
				let dealt = 0;
				for (const t of ts) {
					if (rng.random() >= mv.acc) continue;
					const roll = (85 + Math.floor(rng.random() * 16)) / 92;
					let d = Math.floor((mv.dmg.get(t) || 0) * (mv.spread && ts.length > 1 ? 0.75 : 1) * roll * (rng.random() < 1 / 24 ? 1.5 : 1));
					if (sash[t] && hp[t] === mons[t].max && d >= hp[t]) { d = hp[t] - 1; sash[t] = false; }
					d = Math.min(d, hp[t]);
					hp[t] -= d; dealt += d;
					if (berry[t] && hp[t] > 0 && hp[t] <= mons[t].max / 2) { hp[t] += Math.floor(mons[t].max / 4); berry[t] = false; }
				}
				if (dealt && mv.recoil) hp[k] -= Math.max(1, Math.round(dealt * mv.recoil));
				if (dealt && mv.drain) hp[k] = Math.min(x.max, hp[k] + Math.round(dealt * mv.drain));
				if (berry[k] && hp[k] > 0 && hp[k] <= x.max / 2) { hp[k] += Math.floor(x.max / 4); berry[k] = false; }
				const a0 = alive(0), a1 = alive(1);
				if (!a0 || !a1) { result = !a0 && !a1 ? 0.5 : a1 ? 0 : 1; break; }
			}
			if (result !== null) break;
			mons.forEach((x, k) => { if (hp[k] > 0 && on[k]) hp[k] = Math.min(x.max, hp[k] + x.residual); });
			const a0 = alive(0), a1 = alive(1);
			if (!a0 || !a1) { result = !a0 && !a1 ? 0.5 : a1 ? 0 : 1; break; }
			// The end of the turn: each empty slot filled from the back.
			for (const s of [0, 1]) {
				while (onField(s).length < 2) {
					const back = mons.map((y, k) => k).filter(k => mons[k].side === s && !on[k] && hp[k] > 0);
					if (!back.length) break;
					const foes = onField(1 - s);
					const pick = back.reduce((best, k) => {
						const p = progress(k, foes);
						return !best || (p ? p.v : 0) > best.v ? {k, v: p ? p.v : 0} : best;
					}, null);
					on[pick.k] = true;
				}
			}
		}
		if (result === null) {
			const share = s => mons.reduce((a, x, k) => a + (x.side === s ? Math.max(0, hp[k]) / x.max : 0), 0);
			const a = share(0), c = share(1);
			result = a + c > 0 ? a / (a + c) : 0.5;
		}
		won += result;
	}
	return won / runs;
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
		this.draw = typeof seed === 'string' ? new PRNG(seed) : seed; this.i = i; this.n = n; this.strata = strata; this.j = 0;
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

/**
 * `search.fast_dice` (PLAN-policy stage 1): the race's dice and the sampled turns' draws from sfc32
 * seeded by the same key, instead of Showdown's PRNG. Showdown's is ChaCha20, built to be
 * unpredictable, and it was a fifth of a search with a bench. These dice only need to be seeded
 * and even; the simulator's own random calls still go through the scripted or sampling PRNG.
 */
class FastDice {
	constructor(pathKey) {
		const h = crypto.createHash('sha256').update(pathKey).digest();
		[this.a, this.b, this.c, this.d] = [0, 4, 8, 12].map(i => h.readUInt32LE(i));
		for (let i = 0; i < 12; i++) this.random();
	}
	random() {
		const t = (((this.a + this.b) | 0) + this.d) | 0;
		this.d = (this.d + 1) | 0;
		this.a = this.b ^ (this.b >>> 9);
		this.b = (this.c + (this.c << 3)) | 0;
		this.c = (this.c << 21) | (this.c >>> 11);
		this.c = (this.c + t) | 0;
		return (t >>> 0) / 4294967296;
	}
}

function dice(search, pathKey) {
	return search && search.fast_dice ? new FastDice(pathKey) : new PRNG(seedFor(pathKey));
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
	if (pos.walk) {
		// {"walk": {"games": n, "turns": t, "seed": s}}: each game plays on from the position with real
		// dice, every step a random choice from the list the search would be given (search.switches and
		// search.mega apply). Any the simulator rejects is reported: the list must hold only what the
		// cartridge would accept (PLAN-policy, stage 1).
		const w = pos.walk, flags = {switches: !!search.switches, mega: search.mega === 'both' ? 'both' : !!search.mega};
		const snap = State.serializeBattle(root);
		const report = {games: 0, steps: 0, ended: 0, replacements: 0, switches: 0, megas: 0, rejected: []};
		for (let g = 0; g < (w.games || 1); g++) {
			const b = State.deserializeBattle(snap);
			b.restart(() => {});
			b.prng = new PRNG(seedFor(`walk|${w.seed || 0}|${g}`));
			const pick = new PRNG(seedFor(`walk-pick|${w.seed || 0}|${g}`));
			report.games++;
			for (let t = 0; t < (w.turns || 30) && !b.ended; t++) {
				// As the search does each turn: Showdown stops a battle whose unsent log passes 1,000 lines.
				b.log = []; b.sentLogPos = 0;
				const o = b.sides.map(sd => options(b, sd, flags));
				const ch = o.map(list => list[Math.floor(pick.random() * list.length)]);
				if (replacing(b)) report.replacements++;
				report.switches += ch.join(',').split('switch').length - 1;
				report.megas += ch.join(',').split(' mega').length - 1;
				report.steps++;
				const asked = b.sides.map(sd => JSON.stringify(sd.activeRequest && {wait: sd.activeRequest.wait,
					forceSwitch: sd.activeRequest.forceSwitch, active: !!sd.activeRequest.active}));
				if (!play(b, ch[0], ch[1])) {
					const side = b.sides.find(sd => sd.choice.error);
					report.rejected.push({choice: ch, error: side ? side.choice.error : 'rejected', turn: b.turn, asked,
						requestState: b.requestState});
					break;
				}
			}
			if (b.ended) report.ended++;
		}
		return report;
	}
	if (search.list_only) {
		// The root's choices, as the search would be given them: every legal one, then those realistic
		// play keeps (per Pokémon at `prune`, then `side_k` a side), with each kept pair's score.
		const flags = {switches: !!search.switches, mega: search.mega === 'both' ? 'both' : !!search.mega};
		const copy = scorer(State.serializeBattle(root));
		const out = {all: {}, kept: {}, values: {}};
		for (const [i, id] of [[0, 'p1'], [1, 'p2']]) {
			const all = options(root, root.sides[i], flags);
			const values = new Map();
			out.all[id] = all;
			out.kept[id] = !search.prune ? all : search.prune_by === 'people'
				? prunePeople(copy, i, all, search.prune, search.side_k, search.people_top || 3, values)
				: prune(copy, i, all, search.prune, search.side_k, values);
			out.values[id] = Object.fromEntries(values);
			if (search.features) {
				// Every legal choice's parts, each Pokémon's on its own.
				out.features = out.features || {};
				out.features[id] = [0, 1].map(slot => {
					let danger = null;
					const once = () => (danger === null ? (danger = threat(copy, i, slot)) : danger);
					let parts = [...new Set(all.map(ch => ch.split(',').map(x => x.trim())[slot]))].filter(x => x !== 'pass');
					// Both ways where it can Mega Evolve (with `mega` off the list has only the plain one).
					const req = root.sides[i].activeRequest;
					if (!flags.mega && req && req.active && req.active[slot] && req.active[slot].canMegaEvo) {
						parts = parts.concat(parts.filter(x => x.startsWith('move')).map(x => `${x} mega`));
					}
					return Object.fromEntries(parts.map(part => [part, features(copy, i, slot, part, once)]));
				});
			}
		}
		return out;
	}
	const cache = new Map();
	let nodes = 0, dropped = 0, pruned = 0;
	// With `search.stats`: turns replayed and distinct outcomes, and the matrix sizes met, by how
	// many Pokémon each side had — what the doubles cost estimates are measured with.
	const stats = {replays: 0, outcomes: 0, matrices: {}};
	// Pokémon left a side, the back included (without a bench, the same as those on the field).
	const alive = bt => bt.sides.map(sd => standing(sd).length);
	const flags = {switches: !!search.switches, mega: search.mega === 'both' ? 'both' : !!search.mega};

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
		// `search.crn`: every cell of the node draws the same dice, so two choices are compared under
		// one set of draws rather than two (approximately: different choices use the dice differently).
		// `search.salt` draws other dice for the same position (a reference to check sampling against).
		const cell = (search.salt ? `${search.salt}|` : '') + (search.crn ? 'crn' : `${a}|${c}`);
		const strata = strataFor(`${nodeKey}|${cell}`, n);
		for (let i = 0; i < n; i++) {
			const child = State.deserializeBattle(snap);
			child.restart(() => {});
			child.log = []; child.sentLogPos = 0;
			const draw = search.fast_dice ? new FastDice(`${nodeKey}|${cell}|${i}`) : seedFor(`${nodeKey}|${cell}|${i}`);
			child.prng = new SamplingPRNG(draw, search.rolls, search.rare, i, n, strata);
			nodes++;
			if (!play(child, a, c)) {
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
				if (!play(child, a, c)) {
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
		// Filling an empty slot is a choice of its own, made before anything is valued: it costs no
		// depth, and no position is valued with a slot still empty.
		const swap = replacing(battle);
		if (!swap && (depth >= search.depth || reach < search.cutoff)) {
			// The race is for a 1v1 only, and finding that out on a copy of the battle cost a
			// deserialization a leaf; the count says so first, with the same answer.
			const left = alive(battle);
			let r = search.race && left[0] === 1 && left[1] === 1 ? race(snap) : null;
			if (r === null && search.race_doubles) {
				const back = search.race_bench && left.some((n, i) => n > battle.sides[i].active.filter(p => p && !p.fainted).length);
				r = (back ? meleeBench : melee)(snap, key(battle), search.fast_race ? battle : null, search);
				if (r !== null) r = horizon(search.race_doubles, r, battle);
			}
			return {v: r === null ? leaf(battle) : r, leaf: 1};
		}
		// A 1v1 below a doubles root, valued by its damage race rather than searched.
		if (search.race_1v1 && rootAlive > 2 && !swap) {
			const here = alive(battle);
			if (here[0] === 1 && here[1] === 1) {
				const r = race(snap);
				if (r !== null) { stats.raced = (stats.raced || 0) + 1; return {v: r, leaf: 1}; }
			}
		}
		const k = key(battle) + '#' + depth;
		if (cache.has(k)) return cache.get(k);
		const nodeKey = k;
		let o1 = options(battle, battle.sides[0], flags), o2 = options(battle, battle.sides[1], flags);
		if (search.prune && !swap) {
			const copy = scorer(snap);
			const n = o1.length + o2.length;
			const cut = search.prune_by === 'people'
				? (i, o) => prunePeople(copy, i, o, search.prune, search.side_k, search.people_top || 3)
				: (i, o) => prune(copy, i, o, search.prune, search.side_k);
			o1 = cut(0, o1);
			o2 = cut(1, o2);
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
				// A replacement is enumerated first like a 1v1 turn: bringing a Pokémon in rarely rolls dice.
				const outs = outcomes(snap, a, c, nodeKey, !swap && here[0] + here[1] > 2);
				for (const o of outs) {
					// The KO extension: a turn that costs a Pokémon does not use up a turn of the
					// depth, so a trade is searched on into the smaller position instead of being
					// scored by HP share. In a 1v1 a KO ends the battle, so nothing changes there.
					const after = alive(o.battle);
					const next = swap || (search.ko_extend !== false && after[0] + after[1] < here[0] + here[1]) ? depth : depth + 1;
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
				if (!play(child, a, c)) return null;
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
		const o = [options(root, root.sides[0], flags), options(root, root.sides[1], flags)];
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
	// With a Pokémon in the back no turn wins outright: a KO only brings it in.
	if (search.win_check && !replacing(root) && alive(root).every((n, s) => n === root.sides[s].active.filter(p => p && !p.fainted).length)) {
		const f = forcedWin(search.win_check);
		if (f) {
			// Where the sweep fails, the race at the root stands for the rest of the game.
			const snap = State.serializeBattle(root);
			let rest = race(snap);
			if (rest === null) rest = BLENDS[search.race_doubles]
				? horizon(search.race_doubles, melee(snap, key(root), null, search) ?? 0.5, root) : leaf(root);
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
