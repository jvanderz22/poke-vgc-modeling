"""Does realistic-play pruning keep what people choose? And a model of how they choose (PLAN-policy,
stage 3).

Each open-sheet human game, at every turn with more than two Pokémon left on a side, for each side:
the choice the player made, read from the log, against the solver's choice list for that position
(`list_only`: every legal choice, the pairs left after the per-Pokémon cut at `prune`, and each
pair's score). What a choice was:

  a switch      a `|switch|` before any move of the turn (U-turn's and replacements come after)
  Mega          a `|-mega|` for that slot this turn
  a move        the slot's `|move|`, unless called by another (`[from]`); its target from the log
                where the move takes one. A slot that did not act (flinch, sleep, full paralysis, a
                KO first) is unknown, not a miss

The position: the deciding side's own back as brought (the player knows it; games whose four are
not all known by the end are skipped while any is unseen), the other side's back as the heaviest
guess, every spread at its commonest Speed investment (pruning reads damage, not move order).

Reported by kind of choice: how often the person's choice per Pokémon survives the cut at
`prune` 2 and 3, and how often the pair survives the side cut at K = 4, 6, 8. Then a model of
people choosing among the kept pairs, P ∝ exp(score / T), T fitted on training games, scored on
held-out ones against a uniform choice.

    .venv/bin/python scripts/analysis/prune_vs_people.py --train-games 4000    # ~15 min on 8 workers
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
from collections import defaultdict
from multiprocessing import Pool
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "prune_vs_people.json"
ROWS = paths.DATA / "analysis" / "reg_mc" / "prune_vs_people_rows.jsonl"
KS = (4, 6, 8)
_W: dict[str, Any] = {}


def _init() -> None:
    from vgc.data.splits import load_rules
    from vgc.regulation import load_regulation

    _W["reg"] = load_regulation("reg_mc")
    _W["rules"] = load_rules(_W["reg"])
    _W["solver"] = subprocess.Popen(["node", str(paths.SIDECAR / "showdown" / "endgame-solver.js"), str(paths.SHOWDOWN),
                                     "--serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def _solve(pos: dict[str, Any]) -> dict[str, Any] | None:
    p = _W["solver"]
    p.stdin.write(json.dumps({"id": 0, "position": pos}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline()).get("result")


def choices_in(section: list[str]) -> dict[str, dict[str, Any]]:
    """What each slot ('p1a', ...) chose in one turn's lines, as read from the log."""
    out: dict[str, dict[str, Any]] = {}
    acted = False
    for line in section:
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        if kind == "upkeep":
            break
        if kind in ("move", "cant"):
            acted = True
        if kind == "switch" and not acted and len(parts) > 3:
            slot = parts[2][:3]
            out[slot] = {"switch": parts[3].split(",")[0]}
        elif kind == "-mega" and len(parts) > 2:
            out.setdefault(parts[2][:3], {})["mega"] = True
        elif kind == "move" and len(parts) > 3:
            slot = parts[2][:3]
            if any(p.startswith("[from]") for p in parts[4:]) or "move" in out.get(slot, {}) or "switch" in out.get(slot, {}):
                continue
            target = parts[4][:3] if len(parts) > 4 and parts[4][:2] in ("p1", "p2") else None
            out.setdefault(slot, {})["move"] = parts[3]
            out[slot]["target"] = target
            out[slot]["spread"] = "[spread]" in "|".join(parts[4:])
        elif kind == "cant" and len(parts) > 2 and not any(p.startswith("[of]") for p in parts[3:]):
            # A `[of]` names whose ability stopped someone else's move: not this slot's choice.
            slot = parts[2][:3]
            if not {"move", "switch"} & set(out.get(slot, {})):
                out.setdefault(slot, {})["cant"] = True
    return out


def _part(reg, slot: int, sid: str, c: dict[str, Any], sets_: list[dict[str, Any]]) -> str | None:
    """A slot's choice in the solver's words, or None if the log does not say."""
    from vgc.regulation import to_id

    if not c or c.get("cant"):
        return None
    if "switch" in c:
        idx = next((i for i, s in enumerate(sets_) if i >= 2 and to_id(s["species"]) == to_id(c["switch"])), None)
        return None if idx is None else f"switch {idx + 1}"
    if "move" not in c:
        return None
    moves = [to_id(m) for m in sets_[slot]["moves"]]
    mid = to_id(c["move"])
    if mid not in moves:
        return None
    i = moves.index(mid) + 1
    target = (reg.dex.get_move(mid) or {}).get("target")
    mega = " mega" if c.get("mega") else ""
    if target in ("normal", "any", "adjacentFoe") and not c.get("target") and not c.get("spread"):
        return None                     # blocked or failed before a target was logged
    if target in ("normal", "any", "adjacentFoe") and c.get("target") and not c.get("spread"):
        t = c["target"]
        if len(t) < 3 or t[2] not in "ab":
            return None
        if t[:2] != sid:
            return f"move {i} {'ab'.index(t[2]) + 1}{mega}"
        return f"move {i} -{'ab'.index(t[2]) + 1}{mega}"
    if target == "adjacentAlly":
        return f"move {i} -{2 - slot}{mega}"
    if target == "adjacentAllyOrSelf":
        return f"move {i} -{slot + 1}{mega}"
    return f"move {i}{mega}"


def _kind(reg, part: str, sets_: list[dict[str, Any]], slot: int) -> str:
    from vgc.regulation import to_id

    if part.startswith("switch"):
        return "switch"
    mid = to_id(sets_[slot]["moves"][int(part.split()[1]) - 1])
    mv = reg.dex.get_move(mid) or {}
    if part.endswith(" mega"):
        return "mega"
    if mid in ("protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap", "burningbulwark", "wideguard", "quickguard"):
        return "protect"
    if mid == "fakeout":
        return "fake out"
    return "attack" if mv.get("category") != "Status" else "status"


def _game(replay: dict[str, Any]) -> list[dict[str, Any]]:
    from vgc.data.snapshots import human_snapshots, replay_group
    from vgc.policy import view as V
    from vgc.regulation import to_id
    from vgc.web.endgames import is_bot
    from vgc.wp import doubles, endgame, solver

    reg = _W["reg"]
    group = replay_group(replay)
    split = _W["rules"].split_of("human", replay["id"], [], group)
    if split not in ("train", "heldout_human") or any(is_bot(p) for p in replay.get("players") or []):
        return []
    recs = [r for r in human_snapshots(replay, reg) if r["obs"]["perspective"] == "spectator" and not r["meta"]["approx"]]
    if not recs or not recs[0]["meta"]["ots"]:
        return []
    label = recs[0]["label"]
    lines = replay["log"].split("\n")
    turn_at = [i for i, x in enumerate(lines) if x.startswith("|turn|")]
    view = V.PlayerView(reg, "spectator", None)
    rows = []
    fed = 0
    for n, at in enumerate(turn_at):
        view.feed(lines[fed: at + 1])
        fed = at + 1
        st = view.o
        left = {sid: reg.bring - sum(m.state == "fainted" for m in st.sides[sid].mons) for sid in ("p1", "p2")}
        if max(left.values()) <= 2 or min(left.values()) == 0:
            continue
        end = turn_at[n + 1] if n + 1 < len(turn_at) else len(lines)
        made = choices_in(lines[at + 1: end])
        bad = any(m.volatiles for sid in ("p1", "p2") for m in doubles.actives(st, sid)) or any(
            m.status and m.status not in endgame.STATUSES for sid in ("p1", "p2") for m in V._left(st, sid))
        if bad or any(len(doubles.actives(st, sid)) < min(2, left[sid]) for sid in ("p1", "p2")):
            continue
        for sid in ("p1", "p2"):
            them = "p2" if sid == "p1" else "p1"
            unseen, n_back = V._unseen(reg, st, sid)
            if n_back and not (label.get("brought_complete") or {}).get(sid):
                continue
            mine_back = [m for m in unseen if m.species in (label.get("brought") or {}).get(sid, [])][:n_back]
            backs = V.backs(reg, st, them)
            their_back = list(backs[0][0]) if backs else []
            mons, sets_ = {}, {}
            for s2, extra in ((sid, mine_back), (them, their_back)):
                on = doubles.actives(st, s2)
                seen_back = [m for m in st.sides[s2].mons if m.state == "bench"]
                mons[s2] = on + seen_back + list(extra)
                ss = []
                for m in mons[s2]:
                    x = V._sheet_set(reg, m) | {"mega": m.forme if m.mega else None}
                    x["sp"] = solver.spread(reg, x, V._commonest_speed(reg, x))
                    ss.append(x)
                sets_[s2] = ss
            if any(endgame.UNSOLVABLE_MOVES & {to_id(x) for x in s.get("moves") or []} for ss in sets_.values() for s in ss):
                continue
            f = doubles.facts(reg, view, sets_, mons=mons)
            on_n = {s2: len(doubles.actives(st, s2)) for s2 in ("p1", "p2")}
            f["bench"] = {s2: len(mons[s2]) - on_n[s2] for s2 in ("p1", "p2")}
            f["megaUsed"] = {s2: True for s2 in ("p1", "p2") if any(m.mega for m in st.sides[s2].mons)}
            parts = []
            for slot in range(2):
                if slot >= on_n[sid]:
                    parts.append("pass")
                    continue
                parts.append(_part(reg, slot, sid, made.get(f"{sid}{'ab'[slot]}", {}), sets_[sid]))
            row = {"id": replay["id"], "group": group, "split": split, "turn": st.turn, "side": sid,
                   "kind": f"{left[sid]}v{left[them]}", "parts": parts,
                   "part_kinds": [None if p in (None, "pass") else _kind(reg, p, sets_[sid], i) for i, p in enumerate(parts)]}
            for k in (2, 3):
                pos = V.compose(reg, sets_, f, {"list_only": True, "prune": k, "side_k": 0, "switches": True, "mega": True})
                res = _solve(pos)
                if res is None:
                    break
                vals = res["values"][sid]
                row[f"k{k}"] = {"legal": res["all"][sid], "values": vals}
            else:
                # Every legal choice of each Pokémon, Mega both ways, as the model of people reads it.
                res = _solve(V.compose(reg, sets_, f, {"list_only": True, "prune": 0, "switches": True, "mega": False,
                                                       "features": True}))
                if res is None:
                    continue
                row["features"] = res["features"][sid]
                rows.append(row)
    return rows


def collect(workers: int, train_games: int, seed: int) -> list[dict[str, Any]]:
    from vgc.data.snapshots import replay_group
    from vgc.data.splits import load_rules
    from vgc.meta import pool, replays
    from vgc.regulation import load_regulation

    reg = load_regulation("reg_mc")
    rules = load_rules(reg)
    reps = [rp for fmt in pool.formats_for(reg) for rp in replays.cached(fmt)]
    train = [r for r in reps if rules.split_of("human", r["id"], [], replay_group(r)) == "train"]
    held = [r for r in reps if rules.split_of("human", r["id"], [], replay_group(r)) == "heldout_human"]
    random.Random(seed).shuffle(train)
    with Pool(workers, initializer=_init) as p:
        return [row for rows in p.imap_unordered(_game, held + train[:train_games], chunksize=4) for row in rows]


def report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from scipy.optimize import minimize_scalar

    out: dict[str, Any] = {"decisions": len(rows)}
    # Per Pokémon: is the person's choice legal as the solver lists it (the reading's check), and kept?
    per = defaultdict(lambda: defaultdict(int))
    for r in rows:
        for slot, (p, kind) in enumerate(zip(r["parts"], r["part_kinds"])):
            if p in (None, "pass"):
                if p is None:
                    per["unknown"]["n"] += 1
                continue
            legal_parts = {c.split(", ")[slot] for c in r["k2"]["legal"]}
            per[kind]["n"] += 1
            per[kind]["legal"] += p in legal_parts
            # Not offered because the list Mega Evolves wherever it can, and this player held it back.
            per[kind]["held_mega"] += p not in legal_parts and f"{p} mega" in legal_parts
            if p in legal_parts:
                for k in (2, 3):
                    kept = {c.split(", ")[slot] for c in r[f"k{k}"]["values"]}
                    per[kind][f"kept_k{k}"] += p in kept
    out["per_pokemon"] = {kind: {**v, **{f"share_k{k}": round(v[f"kept_k{k}"] / v["legal"], 3) for k in (2, 3) if v.get("legal")}}
                          for kind, v in sorted(per.items(), key=lambda kv: -kv[1]["n"])}
    # Pairs: kept at the per-Pokémon cut, and in the top K by score.
    joint = [r for r in rows if None not in r["parts"]]
    pairs = {"decisions_both_known": len(joint)}
    for k in (2, 3):
        legal = [r for r in joint if ", ".join(r["parts"]) in r[f"k{k}"]["legal"]]
        pairs[f"legal_k{k}"] = len(legal)
        ranked = []
        for r in legal:
            vals = sorted(r[f"k{k}"]["values"].items(), key=lambda kv: -kv[1])
            names = [c for c, _ in vals]
            ranked.append(names.index(", ".join(r["parts"])) if ", ".join(r["parts"]) in names else None)
        pairs[f"kept_k{k}"] = round(sum(x is not None for x in ranked) / len(ranked), 3) if ranked else None
        for K in KS:
            pairs[f"kept_k{k}_K{K}"] = round(sum(x is not None and x < K for x in ranked) / len(ranked), 3) if ranked else None
    out["pairs"] = pairs
    out["people"] = people(rows)
    # People among the kept pairs (k 2, top K 6): P ∝ exp(score / T).
    K = 6

    def cases(split: str) -> list[tuple[np.ndarray, int]]:
        cs = []
        for r in joint:
            if r["split"] != split:
                continue
            vals = sorted(r["k2"]["values"].items(), key=lambda kv: -kv[1])[:K]
            names = [c for c, _ in vals]
            h = ", ".join(r["parts"])
            if h in names and len(names) > 1:
                cs.append((np.array([v for _, v in vals]), names.index(h)))
        return cs

    def nll(T: float, cs: list[tuple[np.ndarray, int]]) -> float:
        tot = 0.0
        for v, i in cs:
            z = v / T
            z = z - z.max()
            tot -= z[i] - math.log(np.exp(z).sum())
        return tot / len(cs)
    tr, he = cases("train"), cases("heldout_human")
    fit = minimize_scalar(lambda lt: nll(math.exp(lt), tr), bounds=(-4, 4), method="bounded")
    T = math.exp(fit.x)
    out["people_model"] = {"K": K, "T": round(T, 4), "train_cases": len(tr), "heldout_cases": len(he),
                           "heldout_nll": round(nll(T, he), 4),
                           "heldout_nll_uniform": round(float(np.mean([math.log(len(v)) for v, _ in he])), 4),
                           "heldout_top1": round(float(np.mean([int(np.argmax(v) == i) for v, i in he])), 3)}
    return out


def people(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The model of people choosing (`vgc.policy.people`), fitted on training turns and scored on
    held-out ones: per Pokémon, its log-likelihood against a uniform choice and how often the choice
    made is among its top k; per side, how often the pair made is in the top K by summed score."""
    from vgc.policy import people as PP

    def slot_cases(split: str) -> list[tuple[np.ndarray, int]]:
        cs = []
        for r in rows:
            if r["split"] != split or "features" not in r:
                continue
            for slot, p in enumerate(r["parts"]):
                if p in (None, "pass"):
                    continue
                c = PP.as_cases(r["features"][slot], p)
                if c:
                    cs.append(c)
        return cs
    tr, he = slot_cases("train"), slot_cases("heldout_human")
    w = PP.fit(tr)
    lp = [PP.log_probs(X, w)[i] for X, i in he]
    ranks = [int((PP.log_probs(X, w) > PP.log_probs(X, w)[i]).sum()) for X, i in he]
    out = {"train_slots": len(tr), "heldout_slots": len(he),
           "heldout_nll": round(-float(np.mean(lp)), 4),
           "heldout_nll_uniform": round(float(np.mean([math.log(len(X)) for X, _ in he])), 4),
           **{f"slot_top{k}": round(float(np.mean([r < k for r in ranks])), 3) for k in (1, 2, 3, 4)},
           "weights": PP.weights_table(w)}
    # Pairs: the two Pokémon's log-probabilities summed, over the pairs the cartridge allows.
    pair_ranks = []
    for r in rows:
        if r["split"] != "heldout_human" or "features" not in r or None in r["parts"]:
            continue
        scored = []
        opts = [list(r["features"][s].items()) if r["parts"][s] != "pass" else [("pass", {})] for s in range(2)]
        lps = []
        for s in range(2):
            if r["parts"][s] == "pass":
                lps.append({"pass": 0.0})
                continue
            names = [n for n, _ in opts[s]]
            X = np.stack([PP.vector(f) for _, f in opts[s]])
            lps.append(dict(zip(names, PP.log_probs(X, w))))
        for a, la in lps[0].items():
            for b, lb in lps[1].items():
                if a != "pass" and a == b and a.startswith("switch"):
                    continue
                if a.endswith(" mega") and b.endswith(" mega"):
                    continue
                scored.append((la + lb, f"{a}, {b}"))
        h = ", ".join(r["parts"])
        names = [n for _, n in sorted(scored, reverse=True)]
        if h in names:
            pair_ranks.append(names.index(h))
        else:
            pair_ranks.append(None)
    known = [x for x in pair_ranks if x is not None]
    out["pairs"] = {"heldout": len(pair_ranks), "listed": len(known),
                    **{f"top{K}": round(sum(x < K for x in known) / len(pair_ranks), 3) for K in (4, 6, 8, 12, 16)}}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--train-games", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--fit-only", action="store_true")
    a = ap.parse_args()
    if a.fit_only:
        rows = [json.loads(x) for x in ROWS.read_text().splitlines()]
    else:
        rows = collect(a.workers, a.train_games, a.seed)
        ROWS.write_text("".join(json.dumps(r) + "\n" for r in rows))
    res = report(rows)
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
