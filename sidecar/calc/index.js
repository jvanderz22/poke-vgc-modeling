#!/usr/bin/env node
/**
 * Damage-calc sidecar: one JSON request per stdin line, one JSON response per stdout line.
 * Keeps @smogon/calc loaded so Python pays no per-call process spawn.
 *
 * Request:  {"id": 1, "attacker": {...}, "defender": {...}, "move": {...}, "field": {...}}
 *   pokemon: {species, item?, ability?, nature?, sp?: {hp..spe}, boosts?, status?, curHP?}
 *   move:    {name, isCrit?, hits?}
 *   field:   @smogon/calc Field options (gameType defaults to "Doubles")
 * Response: {"id": 1, "ok": true, "damage": [16 rolls], "defenderHP": n, "desc": "...", ...}
 *
 * Champions is generation 0 in @smogon/calc, and its `evs` field holds Stat Points.
 */
'use strict';

const readline = require('readline');
const {calculate, Generations, Pokemon, Move, Field} = require('@smogon/calc');

const gen = Generations.get(0);
const LEVEL = 50;

function mon(p) {
	return new Pokemon(gen, p.species, {
		level: LEVEL,
		item: p.item || undefined,
		ability: p.ability || undefined,
		nature: p.nature || 'Serious',
		evs: p.sp || {},
		boosts: p.boosts || {},
		status: p.status || '',
		curHP: p.curHP,
	});
}

/** Collapse multi-hit / multi-strike results to 16 total-damage rolls. */
function rolls(damage) {
	if (typeof damage === 'number') return Array(16).fill(damage);
	if (Array.isArray(damage[0])) return damage[0].map((_, i) => damage.reduce((s, hit) => s + hit[i], 0));
	return damage;
}

function handle(req) {
	const attacker = mon(req.attacker);
	const defender = mon(req.defender);
	const move = new Move(gen, req.move.name, {isCrit: !!req.move.isCrit, hits: req.move.hits});
	const field = new Field({gameType: 'Doubles', ...(req.field || {})});
	const result = calculate(gen, attacker, defender, move, field);
	const damage = rolls(result.damage);
	let desc = '', ko = null;
	if (Math.max(...damage) > 0) {
		desc = result.desc();
		ko = result.kochance();
	}
	return {
		damage,
		defenderHP: defender.maxHP(),
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
		process.stdout.write(JSON.stringify({id, ok: true, ...handle(req)}) + '\n');
	} catch (e) {
		process.stdout.write(JSON.stringify({id, ok: false, error: String(e && e.message || e)}) + '\n');
	}
});
