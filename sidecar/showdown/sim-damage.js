#!/usr/bin/env node
/**
 * Ground-truth damage sampler: plays one scripted turn in the real simulator across many
 * seeds and reports the damage each target actually took (non-crit hits only).
 *
 *   node sidecar/showdown/sim-damage.js <showdown_dir> < scenario.json
 *
 * scenario: {format, p1: "<export text>", p2: "<export text>", p1choice, p2choice,
 *            seeds: n, targets: ["p2a", ...]}
 * Each side brings its first 4 Pokémon; slot a/b are the first two. Validation is skipped
 * (this is a mechanics probe, not a legality check).
 * Output: {"p2a": [dmg, ...], ..., "crits": n, "misses": n, "kos": n}
 * KO'd hits are excluded (their damage is clamped to remaining HP).
 */
'use strict';

const path = require('path');
const {Battle, Teams} = require(path.resolve(process.argv[2], 'dist/sim'));

const sc = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = {crits: 0, misses: 0};
for (const t of sc.targets) out[t] = [];

// Level adjustment is the validator's job, not the engine's; apply it here.
const team = text => Teams.pack(Teams.import(text).map(set => ({...set, level: sc.level || 50})));
const p1 = team(sc.p1), p2 = team(sc.p2);

// Showdown's gen5 LCG needs all four 16-bit words varied, or early rolls stay correlated.
function seedFor(i) {
	let x = (i + 1) * 2654435761 >>> 0;
	const next = () => { x ^= x << 13; x >>>= 0; x ^= x >>> 17; x ^= x << 5; x >>>= 0; return x & 0xffff; };
	return [next(), next(), next(), next()];
}

for (let s = 0; s < sc.seeds; s++) {
	const battle = new Battle({formatid: sc.format, seed: seedFor(s)});
	battle.setPlayer('p1', {name: 'A', team: p1});
	battle.setPlayer('p2', {name: 'B', team: p2});
	battle.choose('p1', 'team 1234');
	battle.choose('p2', 'team 1234');
	const start = battle.log.length;
	if (!battle.choose('p1', sc.p1choice) || !battle.choose('p2', sc.p2choice)) {
		throw new Error(`choice rejected: ${battle.sides.map(x => x.choice.error).filter(Boolean).join('; ')}`);
	}

	const hp = {};
	const critOn = new Set();
	// After `|split|pN` comes the exact-HP line, then a public percentage line: keep the first.
	const exact = lines => lines.filter((_, i) => !(i >= 2 && lines[i - 2].startsWith('|split|')));
	for (const line of exact(battle.log.slice(0, start))) {
		const m = line.match(/^\|switch\|(p\d[ab]): [^|]*\|[^|]*\|(\d+)\/(\d+)/);
		if (m) hp[m[1]] = +m[2];
	}
	for (const line of exact(battle.log.slice(start))) {
		const parts = line.split('|');
		const who = (parts[2] || '').slice(0, 3);
		if (parts[1] === '-crit') { critOn.add(who); out.crits++; }
		if (parts[1] === '-miss') out.misses++;
		if (parts[1] === '-damage' && !line.includes('[from]') && sc.targets.includes(who)) {
			const now = parseInt(parts[3]); // "140/175" or "0 fnt"
			if (now === 0) out.kos = (out.kos || 0) + 1; // damage clamped to remaining HP
			if (!critOn.has(who) && now > 0) out[who].push(hp[who] - now);
			hp[who] = now;
		}
		if (parts[1] === 'turn') break;
	}
}
process.stdout.write(JSON.stringify(out));
