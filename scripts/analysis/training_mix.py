"""What information regime is actually in the training mix?

PLAN-v2 finding 8 is a measurement on the training rows rather than on the format id: the
opponent's item, ability and move known-flags were **1.000** across 433,052 in-battle rows, so
the model had never seen an unknown opponent and could not have learned what one means. That
number was produced by hand. It decided the shape of Phase 8, so it gets a script.

Reports, per shard and pooled, on the rows a model is actually fit on:

  both_sheets      the snapshot's own `meta.ots` — were both team sheets shown
  item / ability   of the opponent's six, the fraction whose item / ability is filled in
  moves            the fraction of the opponent's six with at least one move known
  revealed         the same three, counting only what the *battle* revealed (`*_source` is not
                   "sheet"), which is what a closed-sheet game has to work from

"Opponent" is only defined for a player row: a spectator row sees both sides equally and a
preview row has seen nothing yet, so both are counted separately rather than folded in.

`--by-turn` asks the same question of a single shard per turn bucket, which is how a *real*
closed-sheet game differs from a synthetically masked one: information arrives as it is shown,
so the known-rates climb. `--mask` blanks item and ability the way
[`wp_closed_sheet.py`](wp_closed_sheet.py)'s `mask()` does, so an OTS shard can be compared
against a genuinely closed-sheet one on the same axis.

    .venv/bin/python scripts/analysis/training_mix.py --manifest wp-v1c-train
    .venv/bin/python scripts/analysis/training_mix.py --by-turn \
        data/snapshots/reg_mc/human/gen9championsvgc2026regmc/heldout_human.jsonl.gz
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from vgc import paths                                        # noqa: E402
from vgc.data.splits import read_shard                       # noqa: E402


class Tally:
    __slots__ = ("rows", "battles", "in_battle", "ots", "item", "ability", "moves",
                 "item_seen", "ability_seen", "moves_seen", "mons", "preview")

    def __init__(self) -> None:
        self.rows = self.in_battle = self.ots = self.mons = self.preview = 0
        self.item = self.ability = self.moves = 0
        self.item_seen = self.ability_seen = self.moves_seen = 0
        self.battles: set[str] = set()

    def add(self, rec: dict) -> None:
        self.rows += 1
        self.battles.add(rec["battle"])
        self.ots += bool(rec["meta"].get("ots"))
        obs = rec["obs"]
        persp = obs["perspective"]
        if rec["kind"] == "preview":
            self.preview += 1
        if persp not in ("p1", "p2") or rec["kind"] == "preview":
            return
        self.in_battle += 1
        them = "p2" if persp == "p1" else "p1"
        for m in obs["sides"][them]["mons"]:
            self.mons += 1
            self.item += m["item"] is not None
            self.ability += m["ability"] is not None
            self.moves += bool(m["moves"])
            # What the battle itself showed, as opposed to what the sheet handed over. A
            # cartridge game has only this column.
            self.item_seen += m["item"] is not None and m.get("item_source") != "sheet"
            self.ability_seen += m["ability"] is not None and m.get("ability_source") != "sheet"
            self.moves_seen += bool(m["moves_used"])

    def report(self) -> dict:
        r = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
        return {
            "rows": self.rows, "battles": len(self.battles), "preview_rows": self.preview,
            "in_battle_player_rows": self.in_battle, "opposing_mons": self.mons,
            "both_sheets": r(self.ots, self.rows),
            "opponent_known": {"item": r(self.item, self.mons), "ability": r(self.ability, self.mons),
                               "moves": r(self.moves, self.mons)},
            "revealed_in_battle": {"item": r(self.item_seen, self.mons),
                                   "ability": r(self.ability_seen, self.mons),
                                   "moves": r(self.moves_seen, self.mons)},
        }


def _bucket(turn: int) -> str:
    return "t1-2" if turn <= 2 else "t3-4" if turn <= 4 else "t5-6" if turn <= 6 else "t7+"


def by_turn(path: Path, mask: bool) -> dict:
    """How much of the opponent is known, per turn bucket, on one shard.

    The featurizer reads `moves or moves_used` (`Featurizer._known_moves`), so that union is what
    is counted here rather than the sheet's move list — otherwise a closed-sheet shard reads as
    0.000 moves known at turn 20, which is an artefact of where the observer files a revealed
    move and not a fact about the game.
    """
    acc: dict[str, list[float]] = {}
    for rec in read_shard(path):
        obs = rec["obs"]
        if obs["perspective"] not in ("p1", "p2") or rec["kind"] == "preview":
            continue
        turn = obs.get("turn")
        if turn is None:
            continue
        row = acc.setdefault(_bucket(turn), [0, 0, 0, 0.0])
        them = "p2" if obs["perspective"] == "p1" else "p1"
        for m in obs["sides"][them]["mons"]:
            item, ability, moves = m["item"], m["ability"], m["moves"]
            if mask:  # what the step-3 gate does to an OTS row, for the whole battle
                item = ability = None
                moves = []
            row[0] += 1
            row[1] += item is not None
            row[2] += ability is not None
            row[3] += len((moves[:4] or m["moves_used"][:4])) / 4
    r = lambda a, b: round(a / b, 4) if b else None  # noqa: E731
    return {b: {"opposing_mons": v[0], "item": r(v[1], v[0]), "ability": r(v[2], v[0]),
                "moves_of_4": r(v[3], v[0])}
            for b, v in sorted(acc.items(), key=lambda kv: ("t1-2", "t3-4", "t5-6", "t7+").index(kv[0]))}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regulation", default="reg_mc")
    ap.add_argument("--manifest", default="wp-v1-train")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many rows per shard (0: all)")
    ap.add_argument("--by-turn", nargs="+", metavar="SHARD",
                    help="per-turn-bucket known-rates for these shards instead of a manifest")
    ap.add_argument("--mask", action="store_true",
                    help="with --by-turn: blank item and ability the way the step-3 gate does")
    args = ap.parse_args()

    if args.by_turn:
        print(json.dumps({"masked": args.mask,
                          "shards": {f: by_turn(Path(f), args.mask) for f in args.by_turn}}, indent=1))
        return

    path = paths.ROOT / "data" / "snapshots" / args.regulation / "manifests" / f"{args.manifest}.json"
    manifest = json.loads(path.read_text())
    per, pooled = {}, Tally()
    for f in manifest["files"]:
        t = Tally()
        for i, rec in enumerate(read_shard(paths.ROOT / f["path"])):
            if args.limit and i >= args.limit:
                break
            t.add(rec)
            pooled.add(rec)
        per[f["path"]] = t.report()
    print(json.dumps({"manifest": args.manifest, "limit": args.limit or None,
                      "shards": per, "pooled": pooled.report()}, indent=1))


if __name__ == "__main__":
    main()
