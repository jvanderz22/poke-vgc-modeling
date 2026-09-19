#!/usr/bin/env node
/**
 * Seeded battle runner: drives Showdown's `Battle` directly (no server, no websockets)
 * over a JSON-lines stdin/stdout protocol, so self-play is reproducible from a seed.
 *
 *   node sidecar/showdown/battle-runner.js <showdown_dir>
 *
 * Requests (one JSON object per line; every request gets exactly one response):
 *   {"op":"start","id":"b1","format":"...","seed":[a,b,c,d],"ots":true,
 *    "p1":{"name":"A","team":"<export text>"},"p2":{...}}
 *   {"op":"choose","id":"b1","side":"p1","choice":"move 1 1, move 2"}
 *   {"op":"close","id":"b1"}
 * Response: {"id","ok","error"?,"p1":[chunk...],"p2":[chunk...],"ended","end"?}
 *   p1/p2 are that side's protocol chunks since the last response (secret info split
 *   per side exactly as the server would), requests last. `end` carries winner, turns,
 *   the omniscient log and the replayable inputLog.
 *
 * Teams go through TeamValidator first — as on the server — which both rejects illegal
 * teams and applies "Adjust Level = 50".
 */
'use strict';

const path = require('path');
const readline = require('readline');
const sim = require(path.resolve(process.argv[2], 'dist/sim'));
const {extractChannelMessages} = require(path.resolve(process.argv[2], 'dist/sim/battle'));
const {Battle, Teams, TeamValidator} = sim;

const battles = new Map();

function prepareTeam(format, text) {
	const team = Teams.import(text);
	if (!team) throw new Error('could not parse team');
	const problems = TeamValidator.get(format).validateTeam(team);
	if (problems && problems.length) throw new Error('invalid team: ' + problems.join('; '));
	return team;
}

function start(req) {
	if (battles.has(req.id)) throw new Error(`battle ${req.id} already exists`);
	const state = {out: {p1: [], p2: []}, requests: {p1: [], p2: []}, omniscient: [], end: null, battle: null};
	const send = (type, data) => {
		if (type === 'update') {
			const ch = extractChannelMessages(Array.isArray(data) ? data.join('\n') : data, [-1, 1, 2]);
			state.omniscient.push(...ch[-1]);
			if (ch[1].length) state.out.p1.push(ch[1].join('\n'));
			if (ch[2].length) state.out.p2.push(ch[2].join('\n'));
		} else if (type === 'sideupdate') {
			const nl = data.indexOf('\n');
			const side = data.slice(0, nl);
			state.requests[side].push(data.slice(nl + 1));
		} else if (type === 'end') {
			state.end = JSON.parse(data);
		}
	};
	const p1team = prepareTeam(req.format, req.p1.team);
	const p2team = prepareTeam(req.format, req.p2.team);
	const battle = new Battle({formatid: req.format, seed: req.seed, send});
	state.battle = battle;
	battles.set(req.id, state);
	battle.setPlayer('p1', {name: req.p1.name, team: p1team});
	battle.setPlayer('p2', {name: req.p2.name, team: p2team});
	if (req.ots) battle.showOpenTeamSheets();
	battle.sendUpdates();
	return state;
}

function flush(id, state) {
	const res = {id, ok: true, ended: false};
	for (const side of ['p1', 'p2']) {
		res[side] = state.out[side].concat(state.requests[side]);
		state.out[side] = [];
		state.requests[side] = [];
	}
	if (state.end) {
		res.ended = true;
		res.end = {
			winner: state.end.winner || null,
			turns: state.end.turns,
			seed: state.end.seed,
			score: state.end.score,
			inputLog: state.end.inputLog,
			log: state.omniscient,
		};
		battles.delete(id);
	}
	return res;
}

/** Re-simulate a finished battle from its inputLog (+ its `ots` flag); returns the same `end` block. */
function replay(req) {
	let end = null;
	const log = [];
	let battle = null;
	for (const line of req.inputLog) {
		const [cmd, rest] = [line.slice(1, line.indexOf(' ')), line.slice(line.indexOf(' ') + 1)];
		if (cmd === 'start') {
			battle = new Battle({...JSON.parse(rest), send: (type, data) => {
				if (type === 'update') log.push(...extractChannelMessages(Array.isArray(data) ? data.join('\n') : data, [-1])[-1]);
				if (type === 'end') end = JSON.parse(data);
			}});
		} else if (cmd === 'player') {
			const [slot, opts] = [rest.slice(0, 2), JSON.parse(rest.slice(3))];
			battle.setPlayer(slot, opts);
			// OTS is revealed by a direct call, not an input, so the inputLog can't carry it.
			if (req.ots && slot === 'p2') battle.showOpenTeamSheets();
		} else if (cmd === 'p1' || cmd === 'p2') {
			battle.choose(cmd, rest);
		} else if (!cmd.startsWith('version')) {
			throw new Error(`unsupported input line: ${line}`);
		}
		battle && battle.sendUpdates();
	}
	if (!end) throw new Error('replay did not finish');
	return {id: req.id, ok: true, ended: true, end: {winner: end.winner || null, turns: end.turns, seed: end.seed, score: end.score, inputLog: end.inputLog, log}};
}

function handle(req) {
	if (req.op === 'start') return flush(req.id, start(req));
	if (req.op === 'replay') return replay(req);
	const state = battles.get(req.id);
	if (!state) throw new Error(`no battle ${req.id}`);
	if (req.op === 'close') {
		battles.delete(req.id);
		return {id: req.id, ok: true, ended: true};
	}
	if (req.op === 'choose') {
		const ok = state.battle.choose(req.side, req.choice);
		state.battle.sendUpdates();
		const res = flush(req.id, state);
		if (!ok) {
			res.ok = false;
			res.error = state.battle.getSide(req.side).choice.error || 'invalid choice';
		}
		return res;
	}
	throw new Error(`unknown op ${req.op}`);
}

readline.createInterface({input: process.stdin}).on('line', line => {
	if (!line.trim()) return;
	let id = null;
	try {
		const req = JSON.parse(line);
		id = req.id ?? null;
		process.stdout.write(JSON.stringify(handle(req)) + '\n');
	} catch (e) {
		process.stdout.write(JSON.stringify({id, ok: false, fatal: true, error: String(e && e.message || e)}) + '\n');
	}
});
