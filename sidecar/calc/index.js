#!/usr/bin/env node
/**
 * Damage-calc sidecar: one JSON request per stdin line, one JSON response per stdout line.
 * Keeps @smogon/calc loaded so Python pays no per-call process spawn.
 *
 * Request:  {"id": 1, "attacker": {...}, "defender": {...}, "move": {...}, "field": {...}}
 *   pokemon: {species, item?, ability?, nature?, sp?: {hp..spe}, boosts?, status?, curHP?, curHPFraction?}
 *   move:    {name, isCrit?, hits?}
 *   field:   @smogon/calc Field options (gameType defaults to "Doubles")
 * Response: {"id": 1, "ok": true, "damage": [16 rolls], "defenderHP": n, "desc": "...", ...}
 * Batch:    {"id": 2, "batch": [req, ...]} → {"id": 2, "ok": true, "results": [{ok, ...} | {ok: false, error}]}
 *           (batch items skip desc/KO text, which is slow and only needed for display)
 *
 * Champions is generation 0 in @smogon/calc, and its `evs` field holds Stat Points.
 */
'use strict';

const readline = require('readline');
const {calculate, Generations, Pokemon, Move, Field} = require('@smogon/calc');

const gen = Generations.get(0);
const LEVEL = 50;

function mon(p) {
	const m = new Pokemon(gen, p.species, {
		level: LEVEL,
		item: p.item || undefined,
		ability: p.ability || undefined,
		nature: p.nature || 'Serious',
		evs: p.sp || {},
		boosts: p.boosts || {},
		status: p.status || '',
		curHP: p.curHP,
	});
	// Opponents' HP is only known as a fraction; scale it to this spread's max HP.
	if (p.curHPFraction != null) m.originalCurHP = Math.max(1, Math.round(m.maxHP() * p.curHPFraction));
	return m;
}

/** Collapse multi-hit / multi-strike results to 16 total-damage rolls. */
function rolls(damage) {
	if (typeof damage === 'number') return Array(16).fill(damage);
	if (Array.isArray(damage[0])) return damage[0].map((_, i) => damage.reduce((s, hit) => s + hit[i], 0));
	return damage;
}

function handle(req, verbose = true) {
	const attacker = mon(req.attacker);
	const defender = mon(req.defender);
	const move = new Move(gen, req.move.name, {isCrit: !!req.move.isCrit, hits: req.move.hits});
	const field = new Field({gameType: 'Doubles', ...(req.field || {})});
	const result = calculate(gen, attacker, defender, move, field);
	const damage = rolls(result.damage);
	let desc = '', ko = null;
	if (verbose && Math.max(...damage) > 0) {
		desc = result.desc();
		ko = result.kochance();
	}
	return {
		damage,
		defenderHP: defender.maxHP(),
		defenderCurHP: defender.curHP(),
		attackerStats: attacker.stats,
		defenderStats: defender.stats,
		moveType: result.move.type,
		moveCategory: result.move.category,
		desc,
		ko: ko && {chance: ko.chance ?? null, n: ko.n, text: ko.text},
	};
}

const rl = readline.createInterface({input: process.stdin});
rl.on('line', line => {
	if (!line.trim()) return;
	let id = null;
	try {
		const req = JSON.parse(line);
		id = req.id ?? null;
		const body = req.batch
			? {results: req.batch.map(r => { try { return {ok: true, ...handle(r, false)}; } catch (e) { return {ok: false, error: String(e && e.message || e)}; } })}
			: handle(req);
		process.stdout.write(JSON.stringify({id, ok: true, ...body}) + '\n');
	} catch (e) {
		process.stdout.write(JSON.stringify({id, ok: false, error: String(e && e.message || e)}) + '\n');
	}
});
