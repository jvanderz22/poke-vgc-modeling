"""L1 — damage calculator. A long-lived Node sidecar running @smogon/calc (Champions mode)."""

from __future__ import annotations

import itertools
import json
import subprocess
from dataclasses import dataclass
from typing import Any

from vgc import paths
from vgc.teams.sets import PokemonSet

SIDECAR_DIR = paths.SIDECAR / "calc"


class CalcError(RuntimeError):
    pass


@dataclass
class CalcResult:
    damage: list[int]  # the 16 damage rolls
    defender_hp: int
    desc: str
    ko_text: str | None
    move_type: str
    move_category: str
    attacker_stats: dict[str, int]
    defender_stats: dict[str, int]

    @property
    def min(self) -> int:
        return min(self.damage)

    @property
    def max(self) -> int:
        return max(self.damage)

    @property
    def percent(self) -> tuple[float, float]:
        return (100 * self.min / self.defender_hp, 100 * self.max / self.defender_hp)


def _mon(p: PokemonSet, boosts: dict[str, int] | None = None, status: str = "", cur_hp: int | None = None) -> dict:
    return {
        "species": p.species,
        "item": p.item,
        "ability": p.ability,
        "nature": p.nature,
        "sp": p.sp.as_dict(),
        "boosts": boosts or {},
        "status": status,
        "curHP": cur_hp,
    }


class DamageCalc:
    """Use as a context manager, or call `close()`; one sidecar process serves many calcs."""

    def __init__(self) -> None:
        if not (SIDECAR_DIR / "node_modules" / "@smogon" / "calc").exists():
            raise CalcError(f"calc sidecar not installed; run: (cd {SIDECAR_DIR} && npm ci)")
        self._proc = subprocess.Popen(
            ["node", str(SIDECAR_DIR / "index.js")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )
        self._ids = itertools.count(1)

    def raw(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = next(self._ids)
        assert self._proc.stdin and self._proc.stdout
        self._proc.stdin.write(json.dumps({**request, "id": req_id}) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            err = self._proc.stderr.read() if self._proc.stderr else ""
            raise CalcError(f"calc sidecar died: {err.strip()}")
        resp = json.loads(line)
        if resp.get("id") != req_id:
            raise CalcError(f"out-of-order response {resp.get('id')} for {req_id}")
        if not resp["ok"]:
            raise CalcError(resp["error"])
        return resp

    def batch(self, requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Many raw calcs in one round trip. Each result is {"ok": True, "damage": [...],
        "defenderHP", "defenderCurHP", ...} or {"ok": False, "error"}; no desc/KO text."""
        if not requests:
            return []
        return self.raw({"batch": requests})["results"]

    def calc(
        self,
        attacker: PokemonSet,
        defender: PokemonSet,
        move: str,
        *,
        field: dict[str, Any] | None = None,
        crit: bool = False,
        attacker_boosts: dict[str, int] | None = None,
        defender_boosts: dict[str, int] | None = None,
    ) -> CalcResult:
        r = self.raw({
            "attacker": _mon(attacker, attacker_boosts),
            "defender": _mon(defender, defender_boosts),
            "move": {"name": move, "isCrit": crit},
            "field": field or {},
        })
        return CalcResult(
            damage=r["damage"], defender_hp=r["defenderHP"], desc=r["desc"],
            ko_text=(r["ko"] or {}).get("text"), move_type=r["moveType"], move_category=r["moveCategory"],
            attacker_stats=r["attackerStats"], defender_stats=r["defenderStats"],
        )

    def close(self) -> None:
        if self._proc.poll() is None:
            self._proc.stdin.close()  # type: ignore[union-attr]
            self._proc.wait(timeout=5)

    def __enter__(self) -> "DamageCalc":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
