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
	req.active[slot].moves.forEach((m, i) => {
		if (m.disabled) return;
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

// The largest share of this Pokémon's HP any live foe's move takes, by the same calculation.
function threat(copy, side, slot) {
	const me = copy.sides[side].active[slot];
	let worst = 0;
	for (const foe of copy.sides[1 - side].active) {
		if (!foe || foe.fainted) continue;
		for (const ms of foe.moveSlots) {
			const move = copy.dex.moves.get(ms.id);
			if (move.category === 'Status') continue;
			let d;
			try { d = copy.actions.getDamage(foe, me, copy.dex.getActiveMove(ms.id), true); } catch (e) { d = 0; }
			if (typeof d === 'number' && d > 0) worst = Math.max(worst, Math.min(1, d / me.hp));
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
		let d;
		try { d = copy.actions.getDamage(me, p, copy.dex.getActiveMove(id), true); } catch (e) { d = 0; }
		if (typeof d !== 'number' || !(d > 0)) continue;
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
	constructor(seed, rolls, rare) { super([], rolls, rare); this.draw = new PRNG(seed); }
	pick(options) {
		if (options.length === 1) return options[0].v;
		let u = this.draw.random();
		for (const o of options) { if ((u -= o.p) < 0) return o.v; }
		return options[options.length - 1].v;
	}
}

function seedFor(pathKey) {
	return 'sodium,' + crypto.createHash('sha256').update(pathKey).digest('hex');
}

function main() {
	const pos = JSON.parse(require('fs').readFileSync(0, 'utf8'));
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
		process.stdout.write(JSON.stringify({
			log: root.log, ended: root.ended, winner: root.winner, trickroom: root.field.pseudoWeather.trickroom || null,
			weather: [root.field.weather, root.field.weatherState.duration],
			sides: root.sides.map(s => Object.fromEntries(Object.entries(s.sideConditions).map(([k, v]) => [k, v.duration]))),
			active: root.sides.map(s => s.active.filter(p => p && !p.fainted).map(p => ({
				species: p.species.name, hp: p.hp, maxhp: p.maxhp, spe: p.getStat('spe'), item: p.item,
				status: p.status}))),
		}) + '\n');
		return;
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
		for (let i = 0; i < n; i++) {
			const child = State.deserializeBattle(snap);
			child.restart(() => {});
			child.log = []; child.sentLogPos = 0;
			child.prng = new SamplingPRNG(seedFor(`${nodeKey}|${a}|${c}|${i}`), search.rolls, search.rare);
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
	function outcomes(snap, a, c, nodeKey) {
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
		if (depth >= search.depth || reach < search.cutoff) return {v: leaf(battle), leaf: 1};
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
				const outs = outcomes(snap, a, c, nodeKey);
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

	const r = value(State.serializeBattle(root), root, 0, 1);
	const round = x => Math.round(x * 1e4) / 1e4;
	process.stdout.write(JSON.stringify({
		value: round(r.v), leaf_mass: round(r.leaf), nodes, dropped_mass: round(dropped), ms: Date.now() - t0,
		...(search.prune ? {pruned} : {}),
		...(search.stats ? {stats} : {}),
		// The root's sampling error: the cells' spread at the equilibrium, over the turns sampled.
		// It covers the first turn's dice; deeper turns' are in it only through their values.
		...(search.sample && r.V ? {sampling_se: round(Math.sqrt(r.V.reduce((acc, row, i) =>
			acc + row.reduce((b2, v, j) => b2 + r.g.p1[i] * v * r.g.p2[j], 0), 0) / search.sample))} : {}),
		moves: {p1: r.o1, p2: r.o2}, matrix: r.M && r.M.map(row => row.map(round)),
		strategy: r.g && {p1: r.g.p1.map(round), p2: r.g.p2.map(round)},
	}) + '\n');
}

main();
