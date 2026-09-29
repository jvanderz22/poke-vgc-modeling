#!/usr/bin/env node
/**
 * Endgame solver: the value of a 1v1 doubles endgame, by search over the real simulator.
 *
 *   node sidecar/showdown/endgame-solver.js <showdown_dir> < position.json
 *
 * A position is two teams of four (the Pokémon left first, then three that have fainted) and the
 * state to put them in:
 *   {format, p1: "<team text>", p2: "<team text>",
 *    setup: {hp: {p1, p2} (percent), mega: {p1: bool, p2: bool}, fainted: {p1: n, p2: n},
 *            weather: [id, turns], terrain: [id, turns], trickroom: turns,
 *            boosts: {p1: {...}, p2: {...}}, consumed: {p1: bool, p2: bool},
 *            choicelock: {p1: moveid, p2: moveid}, timesAttacked: {p1: n, p2: n},
 *            fresh: {p1: bool, p2: bool} (just switched in; otherwise it has been out a while),
 *            status: {p1: 'brn' | 'par' | 'psn', p2: ...},
 *            sides: {p1: {tailwind | reflect | lightscreen | auroraveil: turns}, p2: {...}}},
 *    search: {depth, rolls (damage rolls per hit, default 2), rare (chance events below this
 *             probability do not happen, default 0: every crit counts), cutoff (a line whose
 *             probability falls below this is scored by HP share instead of searched on,
 *             default 1e-3), min_prob (branches within a turn below this are dropped, 1e-7)}}
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

const path = require('path');
const crypto = require('crypto');
const SD = path.resolve(process.argv[2]);
const {Battle, Teams} = require(path.join(SD, 'dist/sim'));
const {State} = require(path.join(SD, 'dist/sim/state'));
const {PRNG} = require(path.join(SD, 'dist/sim/prng'));

const pack = text => Teams.pack(Teams.import(text).map(s => ({...s, level: 50})));

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
		for (const p of side.pokemon.slice(1)) {
			p.hp = 0; p.fainted = true; p.status = 'fnt'; p.switchFlag = false;
		}
		side.pokemonLeft = 1;
		side.totalFainted = (s.fainted || {})[side.id] ?? 3;
		const me = side.pokemon[0];
		me.clearBoosts();
		// On the field for a while unless the position says it just arrived: Fake Out and First
		// Impression fail after a Pokémon's first turn out, and a hand reading of the benchmark
		// assumed they would.
		if (!(s.fresh || {})[side.id]) { me.activeTurns = 3; me.activeMoveActions = 3; }
		const pct = (s.hp || {})[side.id] ?? 100;
		me.sethp(Math.max(1, Math.round(me.maxhp * pct / 100)));
		if ((s.mega || {})[side.id]) b.actions.runMegaEvo(me);
	}
	if (s.weather) { f.setWeather(s.weather[0], 'debug'); f.weatherState.duration = s.weather[1]; }
	if (s.terrain) { f.setTerrain(s.terrain[0], 'debug'); f.terrainState.duration = s.terrain[1]; }
	if (s.trickroom) { f.addPseudoWeather('trickroom', 'debug'); f.pseudoWeather.trickroom.duration = s.trickroom; }
	for (const side of b.sides) {
		const me = side.pokemon[0];
		for (const [stat, n] of Object.entries((s.boosts || {})[side.id] || {})) me.boosts[stat] = n;
		if ((s.consumed || {})[side.id]) {
			me.setItem('');
			if (me.hasAbility('unburden')) me.addVolatile('unburden');
		}
		const lock = (s.choicelock || {})[side.id];
		if (lock) {
			b.activeMove = b.dex.getActiveMove(lock);
			me.addVolatile('choicelock');
			b.activeMove = null;
			me.lastMove = b.dex.moves.get(lock);
		}
		if ((s.timesAttacked || {})[side.id]) me.timesAttacked = s.timesAttacked[side.id];
		// Only the statuses whose effect is fixed once set. Sleep and bad poison carry a counter the
		// cartridge does not show, so a position with either is not built (vgc.wp.endgame says so).
		const status = (s.status || {})[side.id];
		if (status) me.setStatus(status, me, null, true);
		for (const [id, turns] of Object.entries((s.sides || {})[side.id] || {})) {
			side.addSideCondition(id, me);
			side.sideConditions[id].duration = turns;
		}
	}
	b.makeRequest('move');
	return b;
}

// The choices one side can make: each usable move of its one active Pokémon, aimed at the one foe.
function options(b, side) {
	const req = side.activeRequest;
	if (!req || req.wait || !req.active) return ['pass'];
	const foe = b.sides[1 - side.n].active.findIndex(p => p && !p.fainted);
	const out = [];
	side.active.forEach((p, slot) => {
		if (!p || p.fainted) return;
		req.active[slot].moves.forEach((m, i) => {
			if (m.disabled) return;
			const move = b.dex.moves.get(m.id);
			const aimed = ['normal', 'any', 'adjacentFoe'].includes(move.target) && foe >= 0;
			const one = `move ${i + 1}${aimed ? ' ' + (foe + 1) : ''}`;
			out.push(slot === 0 ? `${one}, pass` : `pass, ${one}`);
		});
	});
	return out.length ? out : ['pass'];
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
	sample(items) { return items[this.random(items.length)]; }
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
	let nodes = 0, dropped = 0;

	// Every way one turn can go for a pair of choices, with its probability: the turn is replayed
	// under a scripted PRNG, and each random call past the end of the script throws its options
	// back here to be tried one by one. Outcomes that end in the same state are merged.
	function outcomes(snap, a, c) {
		const found = new Map();
		const stack = [[]];
		while (stack.length) {
			const script = stack.pop();
			const p = script.reduce((acc, s) => acc * s.p, 1);
			const child = State.deserializeBattle(snap);
			child.restart(() => {});
			child.log = []; child.sentLogPos = 0;
			child.prng = new ScriptedPRNG(script, search.rolls, search.rare);
			nodes++;
			try {
				child.choose('p1', a);
				child.choose('p2', c);
			} catch (e) {
				if (!(e instanceof Branch)) throw e;
				e.options.forEach((o, k) => {
					if (p * o.p >= search.min_prob) stack.push(script.concat([{k, p: o.p}]));
					else dropped += p * o.p;
				});
				continue;
			}
			const k = child.ended ? `end:${child.winner}` : key(child);
			const hit = found.get(k);
			if (hit) hit.p += p; else found.set(k, {p, battle: child});
		}
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
		const o1 = options(battle, battle.sides[0]), o2 = options(battle, battle.sides[1]);
		const M = [], L = [];
		for (const a of o1) {
			const row = [], lrow = [];
			for (const c of o2) {
				let sum = 0, leafSum = 0, mass = 0;
				for (const o of outcomes(snap, a, c)) {
					const r = value(State.serializeBattle(o.battle), o.battle, depth + 1, reach * o.p);
					sum += o.p * r.v; leafSum += o.p * r.leaf; mass += o.p;
				}
				row.push(sum / mass); lrow.push(leafSum / mass);
			}
			M.push(row); L.push(lrow);
		}
		const g = solve(M);
		const leafMass = L.reduce((acc, r, i) => acc + r.reduce((b, l, j) => b + g.p1[i] * l * g.p2[j], 0), 0);
		const out = {v: g.value, leaf: leafMass, M, g, o1, o2};
		cache.set(k, out);
		return out;
	}

	const r = value(State.serializeBattle(root), root, 0, 1);
	const round = x => Math.round(x * 1e4) / 1e4;
	process.stdout.write(JSON.stringify({
		value: round(r.v), leaf_mass: round(r.leaf), nodes, dropped_mass: round(dropped), ms: Date.now() - t0,
		moves: {p1: r.o1, p2: r.o2}, matrix: r.M && r.M.map(row => row.map(round)),
		strategy: r.g && {p1: r.g.p1.map(round), p2: r.g.p2.map(round)},
	}) + '\n');
}

main();
