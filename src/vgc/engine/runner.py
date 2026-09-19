"""L1 — seeded, server-free battles.

`BattleRunner` drives Showdown's `Battle` in a Node sidecar (`sidecar/showdown/battle-runner.js`)
with an explicit PRNG seed. Each side's protocol stream is fed into a poke-env `DoubleBattle`, so
policies see exactly the state a poke-env `Player` would, but a battle's outcome is a pure
function of (seed, teams, policies). That is what makes self-play reproducible, and the
recorded `inputLog` replays a battle exactly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import orjson
from poke_env.battle.double_battle import DoubleBattle
from poke_env.player.battle_order import BattleOrder, DefaultBattleOrder, DoubleBattleOrder
from poke_env.teambuilder import Teambuilder

from vgc import paths

LOG = logging.getLogger("vgc.runner")
_IGNORE = {"t:", "expire", "uhtml", "uhtmlchange", "tempnotify", "tempnotifyoff", "raw", "html"}


class RunnerError(RuntimeError):
    pass


class Policy(Protocol):
    """A battle policy. `rng` is seeded per battle and side; policies must not use global
    randomness, or battles stop being reproducible."""

    name: str

    def teampreview(self, battle: DoubleBattle, rng: random.Random) -> str:
        """Return a Showdown team choice, e.g. "team 1234" (first two lead)."""
        ...

    def choose_move(self, battle: DoubleBattle, rng: random.Random) -> BattleOrder: ...


def battle_seed(run_seed: int | str, index: int) -> list[int]:
    """Four 16-bit words for Showdown's PRNG, derived from (run seed, battle index)."""
    h = hashlib.sha256(f"{run_seed}:{index}".encode()).digest()
    return [int.from_bytes(h[i : i + 2], "big") for i in range(0, 8, 2)]


class BattleRunner:
    """One Node process; runs battles one at a time. Use one per worker process."""

    def __init__(self) -> None:
        if not (paths.SHOWDOWN / "dist" / "sim").exists():
            raise RunnerError("Showdown isn't built; see README")
        self._proc = subprocess.Popen(
            ["node", str(paths.SIDECAR / "showdown" / "battle-runner.js"), str(paths.SHOWDOWN)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )

    def request(self, req: dict[str, Any]) -> dict[str, Any]:
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps(req) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RunnerError(f"battle runner died: {self._proc.stderr.read() if self._proc.stderr else ''}")
        res = json.loads(line)
        if res.get("fatal"):
            raise RunnerError(res["error"])
        return res

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.stdin.close()  # type: ignore[union-attr]
            self._proc.wait(timeout=5)

    def __enter__(self) -> "BattleRunner":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class SideView:
    """One player's perspective: a poke-env DoubleBattle fed from that side's stream."""

    def __init__(self, battle_id: str, side: str, name: str, gen: int = 9):
        self.side = side
        self.battle = DoubleBattle(battle_id, name, LOG, gen=gen)
        self.pending = False
        self.errors: list[str] = []

    def feed(self, chunk: str) -> None:
        for line in chunk.split("\n"):
            if not line.startswith("|"):
                continue
            split = line.split("|")
            kind = split[1] if len(split) > 1 else ""
            if kind == "" or kind in _IGNORE:
                continue
            if kind == "request":
                body = line[len("|request|"):]
                if body:
                    self.battle.parse_request(orjson.loads(body))
                    self.pending = not self.battle._wait
            elif kind == "showteam":
                self._showteam(split)
            elif kind == "win":
                self.battle.won_by(split[2])
            elif kind == "tie":
                self.battle.tied()
            elif kind == "error":
                self.errors.append(line)
                self.pending = True  # Showdown re-asks after an invalid choice
            elif kind in ("bigerror", "debug"):
                continue
            else:
                self.battle.parse_message(split)

    def _showteam(self, split: list[str]) -> None:
        # Open Team Sheets: fill in items/abilities/moves (not stats) for the named side.
        role = split[2]
        sheet = Teambuilder.parse_packed_team("|".join(split[3:]))
        b = self.battle
        preview = b.teampreview_team if role == b.player_role else b.teampreview_opponent_team
        for preview_mon in preview:
            match = [m for m in sheet if m.nickname is not None and preview_mon.identifies_as(m.nickname)]
            if match:
                mon = b.get_pokemon(f"{role}: {match[0].nickname}", details=preview_mon._last_details)
                mon._update_from_teambuilder(match[0])


def _choice_text(order: BattleOrder | str) -> str:
    msg = order if isinstance(order, str) else order.message
    for prefix in ("/choose ", "/team "):
        if msg.startswith(prefix):
            return ("team " if prefix == "/team " else "") + msg[len(prefix):]
    return msg


@dataclass
class BattleRecord:
    battle_id: str
    seed: list[int]
    format: str
    p1: dict[str, str]
    p2: dict[str, str]
    winner: str | None  # "p1" | "p2" | None (tie)
    turns: int
    score: list[int]
    invalid_choices: int  # rejected choices that indicate a policy/driver bug
    trapped_retries: int  # switches rejected by a hidden trapping ability: legitimate, re-chosen
    seconds: float
    ots: bool  # Open Team Sheets shown; needed with input_log to replay exactly
    input_log: list[str] = field(repr=False)
    log: list[str] = field(repr=False)

    def summary(self) -> dict[str, Any]:
        d = self.__dict__.copy()
        d.pop("input_log")
        d.pop("log")
        return d


def play_battle(
    runner: BattleRunner,
    battle_id: str,
    seed: list[int],
    fmt: str,
    teams: tuple[str, str],
    policies: tuple[Policy, Policy],
    team_ids: tuple[str, str] = ("", ""),
    ots: bool = True,
    max_decisions: int = 2000,
) -> BattleRecord:
    t0 = time.perf_counter()
    names = ("p1", "p2")
    res = runner.request({
        "op": "start", "id": battle_id, "format": fmt, "seed": seed, "ots": ots,
        "p1": {"name": names[0], "team": teams[0]}, "p2": {"name": names[1], "team": teams[1]},
    })
    views = {s: SideView(battle_id, s, s) for s in names}
    rngs = {s: random.Random(f"{seed}:{s}") for s in names}
    pol = dict(zip(names, policies))
    invalid = trapped = 0
    for _ in range(max_decisions):
        for s in names:
            for chunk in res.get(s, []):
                views[s].feed(chunk)
        if res.get("ended"):
            end = res["end"]
            return BattleRecord(
                battle_id=battle_id, seed=seed, format=fmt,
                p1={"policy": pol["p1"].name, "team": team_ids[0]},
                p2={"policy": pol["p2"].name, "team": team_ids[1]},
                winner={"p1": "p1", "p2": "p2"}.get(end["winner"]), turns=end["turns"], score=end["score"],
                invalid_choices=invalid, trapped_retries=trapped, seconds=round(time.perf_counter() - t0, 3), ots=ots,
                input_log=end["inputLog"], log=end["log"],
            )
        side = next((s for s in names if views[s].pending), None)
        if side is None:
            raise RunnerError(f"{battle_id}: no side has a decision to make")
        view = views[side]
        view.pending = False
        if view.errors and invalid and invalid % 3 == 0:
            choice = "default"  # repeated invalid choices: let Showdown pick
        elif view.battle.teampreview:
            choice = _choice_text(pol[side].teampreview(view.battle, rngs[side]))
        else:
            choice = _choice_text(pol[side].choose_move(view.battle, rngs[side]))
        res = runner.request({"op": "choose", "id": battle_id, "side": side, "choice": choice})
        if not res["ok"]:
            # Hidden trapping (Shadow Tag, Arena Trap…) is only revealed by a rejected switch.
            if "is trapped" in (res.get("error") or ""):
                trapped += 1
                continue
            invalid += 1
            LOG.debug("%s %s invalid choice %r: %s", battle_id, side, choice, res.get("error"))
    runner.request({"op": "close", "id": battle_id})
    raise RunnerError(f"{battle_id}: exceeded {max_decisions} decisions")


def request_target(battle: DoubleBattle, slot: int, move_id: str) -> str | None:
    """A move's target type as Showdown's request states it. poke-env's move data is
    mainline Gen 9 and differs from Champions (e.g. Milk Drink targets adjacentAllyOrSelf)."""
    try:
        moves = battle.last_request["active"][slot]["moves"]
    except (KeyError, IndexError, TypeError):
        return None
    return next((m.get("target") for m in moves if m.get("id") == move_id), None)


def legal_orders(battle: DoubleBattle) -> list[list[BattleOrder]]:
    """`battle.valid_orders`, with targeting taken from Showdown's request rather than
    poke-env's Gen 9 data: moves that Champions makes `adjacentAllyOrSelf` (Milk Drink…) get
    no target from poke-env, but Showdown requires one in doubles — emit self and ally."""
    from poke_env.battle.move import Move
    from poke_env.player.battle_order import SingleBattleOrder

    out: list[list[BattleOrder]] = []
    for slot, orders in enumerate(battle.valid_orders):
        fixed: list[BattleOrder] = []
        seen: set[tuple] = set()
        for o in orders:
            mv = getattr(o, "order", None)
            if isinstance(mv, Move) and request_target(battle, slot, mv.id) == "adjacentAllyOrSelf":
                ally = battle.active_pokemon[1 - slot]
                targets = [-(slot + 1)] + ([-(2 - slot)] if ally is not None and not ally.fainted else [])
                for t in targets:
                    if (mv.id, t, o.mega) not in seen:
                        seen.add((mv.id, t, o.mega))
                        fixed.append(SingleBattleOrder(mv, move_target=t, mega=o.mega))
            else:
                fixed.append(o)
        out.append(fixed)
    return out


class RandomPolicy:
    """Uniform over legal joint orders; random 4 of 6 at team preview."""

    name = "random"

    def teampreview(self, battle: DoubleBattle, rng: random.Random) -> str:
        order = list(range(1, len(battle.team) + 1))
        rng.shuffle(order)
        return "team " + "".join(map(str, order[:4]))

    def choose_move(self, battle: DoubleBattle, rng: random.Random) -> BattleOrder:
        orders = DoubleBattleOrder.join_orders(*legal_orders(battle))
        if not orders:
            return DoubleBattleOrder(DefaultBattleOrder(), DefaultBattleOrder())
        return orders[rng.randrange(len(orders))]
