"""Is a fresh scrape the same population as the corpus? (PLAN-v5 step 0, "before use"; practice 2.)

Before new replays join the corpus or test a gate, they are compared with the cached ones on what
has moved a metric before: ratings, forfeits, bots, concentration, open sheets, the split, the
meta, and whether the pipeline reads them. Per format, three groups:

  old        `data/replays/<format>/`, the cache the models were built from
  gap-fill   staged replays uploaded no later than the cache's newest (the weeks already covered)
  fresh      staged replays uploaded after it: unseen by every model and fit

Checks:
  population   rated share, rating quantiles, forfeit share, turns of played-out games, bots,
               games per player, the share of games from the 10 most active players
  sheets       games with both `|showteam|` sheets, and both legal (Bo3 carries them; Bo1 should not)
  split        train / heldout_team / heldout_human under the frozen rules (teams for sheets only)
  players      fresh games whose players both, either or neither appear in the old cache
  meta         species shares from team preview (`|poke|`), the total variation distance between
               old and fresh, the top movers, and species never previewed in the old cache
  parse        `human_snapshots` on a sample of each group: the share that raises, and snapshots
               a game

    .venv/bin/python scripts/analysis/fresh_corpus.py [--staged data/replays-new] [--parse 400]
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import re
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

from vgc import paths

OUT = paths.DATA / "analysis" / "reg_mc" / "fresh_corpus.json"
_POKE = re.compile(r"^\|poke\|p[12]\|([^,|]+)", re.M)


def _load(d: Path) -> list[dict[str, Any]]:
    return [json.loads(gzip.decompress(f.read_bytes())) for f in sorted(d.glob("*.json.gz"))]


def _forfeit(log: str) -> bool:
    return any(line.startswith("|-message|") and "forfeited" in line for line in log.split("\n"))


def _q(xs: list[float], qs=(0.1, 0.25, 0.5, 0.75, 0.9, 0.99)) -> dict[str, float] | None:
    return {f"p{int(q * 100)}": float(np.quantile(xs, q)) for q in qs} if xs else None


def population(rs: list[dict[str, Any]]) -> dict[str, Any]:
    from vgc.web.endgames import is_bot

    rated = [r["rating"] for r in rs if r.get("rating")]
    ff = [_forfeit(r.get("log", "")) for r in rs]
    turns = [len(re.findall(r"^\|turn\|", r.get("log", ""), re.M)) for r, f in zip(rs, ff) if not f]
    per = Counter(p.lower() for r in rs for p in r.get("players") or [])
    top10 = {p for p, _ in per.most_common(10)}
    return {"games": len(rs), "rated_share": round(len(rated) / max(1, len(rs)), 4), "rating": _q(rated),
            "forfeit_share": round(float(np.mean(ff)), 4) if rs else None,
            "turns_played_out": _q(turns, (0.25, 0.5, 0.75)),
            "bot_games": sum(any(is_bot(p) for p in r.get("players") or []) for r in rs),
            "players": len(per), "one_game_players": round(sum(v == 1 for v in per.values()) / max(1, len(per)), 4),
            "games_per_player_median": float(np.median(list(per.values()))) if per else None,
            "top10_player_share": round(sum(any(p.lower() in top10 for p in r.get("players") or []) for r in rs)
                                        / max(1, len(rs)), 4)}


def sheets_and_split(rs: list[dict[str, Any]], reg, rules) -> dict[str, Any]:
    from vgc.data.snapshots import replay_group
    from vgc.meta.replays import team_id, team_key, teams_from_replay

    both = legal = 0
    split = Counter()
    for r in rs:
        n_sheets = r.get("log", "").count("|showteam|")
        both += n_sheets >= 2
        teams = teams_from_replay(r, reg) if n_sheets else []
        legal += len(teams) >= 2
        ids = [team_id(team_key(t)) for _, t in teams]
        split[rules.split_of("human", r["id"], ids, replay_group(r))] += 1
    n = max(1, len(rs))
    return {"both_sheets": round(both / n, 4), "both_legal": round(legal / n, 4),
            "split": {k: round(v / n, 4) for k, v in sorted(split.items())}}


def species(rs: list[dict[str, Any]]) -> Counter:
    c = Counter()
    for r in rs:
        c.update(s.strip() for s in _POKE.findall(r.get("log", "")))
    return c


def meta(old: Counter, fresh: Counter, top: int = 12) -> dict[str, Any]:
    no, nf = sum(old.values()), sum(fresh.values())
    keys = set(old) | set(fresh)
    share = {k: (old[k] / no if no else 0.0, fresh[k] / nf if nf else 0.0) for k in keys}
    moved = sorted(keys, key=lambda k: abs(share[k][1] - share[k][0]), reverse=True)[:top]
    return {"tv_distance": round(0.5 * sum(abs(a - b) for a, b in share.values()), 4),
            "top_movers": [{"species": k, "old": round(share[k][0], 4), "fresh": round(share[k][1], 4)} for k in moved],
            "new_species": sorted((k for k in fresh if not old[k]), key=lambda k: -fresh[k])[:20],
            "new_species_share": round(sum(fresh[k] for k in fresh if not old[k]) / max(1, nf), 4)}


def players_seen(old: list[dict[str, Any]], fresh: list[dict[str, Any]]) -> dict[str, float]:
    seen = {p.lower() for r in old for p in r.get("players") or []}
    k = Counter(sum(p.lower() in seen for p in r.get("players") or []) for r in fresh)
    n = max(1, len(fresh))
    return {"both_seen": round(k[2] / n, 4), "one_seen": round(k[1] / n, 4), "neither_seen": round(k[0] / n, 4)}


def parse(rs: list[dict[str, Any]], reg, n: int, seed: int = 0) -> dict[str, Any]:
    from vgc.data.snapshots import human_snapshots

    sample = random.Random(seed).sample(rs, min(n, len(rs)))
    errors, counts = Counter(), []
    for r in sample:
        try:
            counts.append(len(human_snapshots(r, reg)))
        except Exception as e:  # what the extraction would drop
            errors[type(e).__name__] += 1
    return {"sampled": len(sample), "raised": round(sum(errors.values()) / max(1, len(sample)), 4),
            "errors": dict(errors), "snapshots_a_game": _q(counts, (0.25, 0.5, 0.75))}


def main() -> None:
    from vgc.data.splits import load_rules
    from vgc.meta.pool import formats_for
    from vgc.meta.replays import REPLAYS
    from vgc.regulation import load_regulation

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--staged", type=Path, default=paths.DATA / "replays-new")
    ap.add_argument("--parse", type=int, default=400, help="replays a group to run the extraction on")
    a = ap.parse_args()
    reg = load_regulation("reg_mc")
    rules = load_rules(reg)
    res: dict[str, Any] = {}
    for fmt in formats_for(reg):
        old = _load(REPLAYS / fmt)
        staged = _load(a.staged / fmt)
        edge = max(r.get("uploadtime") or 0 for r in old)
        groups = {"old": old, "gap_fill": [r for r in staged if (r.get("uploadtime") or 0) <= edge],
                  "fresh": [r for r in staged if (r.get("uploadtime") or 0) > edge]}
        out: dict[str, Any] = {"edge": edge}
        for g, rs in groups.items():
            out[g] = population(rs) | sheets_and_split(rs, reg, rules) | {"parse": parse(rs, reg, a.parse)}
            print(fmt, g, json.dumps({k: out[g][k] for k in ("games", "rated_share", "forfeit_share", "bot_games",
                                                              "both_legal", "split")}), flush=True)
        out["players_seen"] = players_seen(old, groups["fresh"])
        out["meta"] = meta(species(old), species(groups["fresh"]))
        res[fmt] = out
    OUT.write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
