#!/usr/bin/env node
/**
 * Export the legal pool for one Showdown format as JSON, using Showdown's own
 * dex and team validator as the source of truth.
 *
 *   node sidecar/showdown/export-dex.js <showdown_dir> <format_id> > out.json
 *
 * Legality is decided exactly as `validate-team` decides it: species by
 * `isNonstandard` + the format's rule table, moves by `TeamValidator.checkCanLearn`,
 * items by `isNonstandard` + rule table.
 */
'use strict';

const path = require('path');
const [showdownDir, formatId] = process.argv.slice(2);
if (!showdownDir || !formatId) {
	console.error('usage: export-dex.js <showdown_dir> <format_id>');
	process.exit(2);
}
const {Dex, TeamValidator} = require(path.resolve(showdownDir, 'dist/sim'));

const format = Dex.formats.get(formatId);
if (!format.exists) {
	console.error(`unknown format: ${formatId}`);
	process.exit(1);
}
const dex = Dex.mod(format.mod);
const ruleTable = Dex.formats.getRuleTable(format);
const validator = TeamValidator.get(format.id);

const legalItems = dex.items.all().filter(i =>
	i.exists && !i.isNonstandard && !ruleTable.isBanned('item:' + i.id));
const allMoves = dex.moves.all().filter(m => m.exists && !m.isNonstandard && !m.isZ && !m.isMax);

const species = {};
for (const s of dex.species.all()) {
	if (s.isNonstandard || ruleTable.isBannedSpecies(s)) continue;
	// Battle-only formes (Megas etc.) aren't selectable in a team; they inherit
	// the out-of-battle forme's moves, so skip the learnset scan for them.
	const outOfBattle = s.battleOnly ? null : s;
	let moves = [];
	if (outOfBattle) {
		const sources = validator.allSources(outOfBattle);
		moves = allMoves
			.filter(m => !validator.checkCanLearn(m, outOfBattle, sources, {}))
			.map(m => m.id)
			.sort();
	}
	species[s.id] = {
		name: s.name,
		num: s.num,
		baseSpecies: s.baseSpecies,
		forme: s.forme || null,
		types: s.types,
		baseStats: s.baseStats,
		abilities: s.abilities,
		weightkg: s.weightkg,
		battleOnly: s.battleOnly || null,
		requiredItem: s.requiredItem || null,
		requiredItems: s.requiredItems || null,
		isMega: !!s.isMega,
		changesFrom: s.changesFrom || null,
		gender: s.gender || null,
		moves,
	};
}

// Items carry no stats of their own, so the model would only see an id unless we export what
// each item *does*. Showdown keeps mechanics in code, but the effect hooks an item defines are a
// good data-driven proxy (onBasePower = damage boost, onSourceModifyDamage = resist berry,
// onModifySpe = Choice Scarf, onResidual = Leftovers), alongside the plain data fields.
const items = {};
for (const i of legalItems) {
	items[i.id] = {
		name: i.name,
		megaStone: i.megaStone || null,
		isBerry: !!i.isBerry,
		isChoice: !!i.isChoice,
		isGem: !!i.isGem,
		boosts: i.boosts || null,
		naturalGift: i.naturalGift || null,
		flingBasePower: (i.fling && i.fling.basePower) || 0,
		itemUser: i.itemUser || null,
		hooks: Object.keys(i).filter(k => k.startsWith('on') && typeof i[k] === 'function').sort(),
		desc: i.shortDesc || i.desc || '',
	};
}

const learnable = new Set(Object.values(species).flatMap(s => s.moves));
const moves = {};
for (const m of allMoves) {
	if (!learnable.has(m.id)) continue;
	moves[m.id] = {
		name: m.name, type: m.type, category: m.category, basePower: m.basePower,
		accuracy: m.accuracy, pp: m.pp, priority: m.priority, target: m.target,
		flags: Object.keys(m.flags).sort(), desc: m.shortDesc || m.desc || '',
	};
}

const abilities = {};
for (const s of Object.values(species)) {
	for (const a of Object.values(s.abilities)) {
		const ab = dex.abilities.get(a);
		if (!ab.exists || ruleTable.isBanned('ability:' + ab.id)) continue;
		abilities[ab.id] = {name: ab.name, desc: ab.shortDesc || ab.desc || ''};
	}
}

const natures = {};
for (const n of dex.natures.all()) natures[n.id] = {name: n.name, plus: n.plus || null, minus: n.minus || null};

// typeChart[attacking][defending] = multiplier
const typeNames = dex.types.all().filter(t => !t.isNonstandard).map(t => t.name);
const typeChart = {};
for (const atk of typeNames) {
	typeChart[atk] = {};
	for (const def of typeNames) {
		typeChart[atk][def] = dex.getImmunity(atk, def) ? 2 ** dex.getEffectiveness(atk, def) : 0;
	}
}

process.stdout.write(JSON.stringify({
	format: {id: format.id, name: format.name, mod: format.mod, gameType: format.gameType,
		ruleset: format.ruleset, rules: [...ruleTable.keys()]},
	species, items, moves, abilities, natures, typeChart,
}));
