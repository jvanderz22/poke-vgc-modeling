"""Phase 7 — the analytic weakness report. No model, no gate; every number is a calc or a lookup.

What this answers is Q1: given six Pokémon, what beats them. It answers it against the meta the
usage report measured, using the pinned `@smogon/calc` for every damage number, and it refuses to
state anything it cannot compute.

**The one thing it cannot compute is the opponent's spread**, and that shapes the whole report.
An Open Team Sheet carries species, item, ability, moves and nature — not the 66 Stat Points
(`unpack_sheet`: "no stats"), so "Kingambit OHKOs your Sinistcha" is not a fact, it is a fact about
an assumed spread. Rather than assume one, this reports the *breakpoint*: the fewest Stat Points
they must have put in Attack for the OHKO to exist. That is a statement with no free parameter in
it, it is what a player actually wants to know, and it is the same arithmetic Phase 8's damage
channel runs backwards — so the speed and damage tables here are a down payment on that phase and
not something to throw away when it lands.

The asymmetry is worth naming. Against *incoming* damage the unknown is one number — their
offensive SP — because your own spread is known, so the answer is an exact threshold. Against
*outgoing* damage the unknown is two (HP and the relevant defence), and there is no single
threshold, so that half reports a bracket between an uninvested and a fully invested defender
instead of inventing a spread to sit between them.

Three more things the report says out loud rather than burying:

- **A threat is one set, and usually a minority one.** Threats take each species' modal joint set
  from the usage report, and the report prints that set's share: Kingambit's most common set is
  22.4% of its sheets, so the other 78% of the time the numbers here are about a different
  Pokémon. Marginal modes are not used — item mode, ability mode and nature mode need not
  co-occur on any real sheet.
- **The field is empty.** No terrain, no weather, no screens, no Intimidate, no boosts. 80% of
  teams bring a terrain setter and 66.8% bring Intimidate, so real damage will often differ; the
  neutral field is the one number that does not depend on a guess about turn order.
- **Abilities that scale with the battle** — Supreme Overlord, Unburden — are evaluated at their
  base state, because there is no battle here to read them from.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from vgc.engine.calc import DamageCalc
from vgc.meta import usage
from vgc.regulation import STAT_IDS, Regulation, to_id
from vgc.teams.sets import PokemonSet, StatPoints, Team, calc_stats

# Which stat a move's damage scales with, for the breakpoint sweep. Everything else follows the
# move's category; Body Press is the one that does not. Moves that key off something other than
# the attacker's own investment — Foul Play off *your* Attack, Seismic Toss off nothing — need no
# entry, because a flat sweep is reported as flat rather than as "0 SP is enough".
OFFENSIVE_STAT = {"bodypress": "def"}


def _damaging(entry: dict) -> bool:
    """Category, not base power. 25 legal moves deal damage on a listed base power of 0 — Low
    Kick, Grass Knot, Gyro Ball, Seismic Toss and the rest compute it from weight, speed or a
    constant — and Low Kick is on Kingambit's most common set, so testing base power would drop a
    main attacking move from the most-used Pokémon's threat entry without saying anything."""
    return bool(entry) and entry.get("category") in ("Physical", "Special")


@dataclass
class Threat:
    """One meta Pokémon, as its most common complete set. The spread is deliberately absent."""

    species: str
    species_id: str
    item: str | None
    ability: str | None
    nature: str
    moves: list[str]
    share: float        # of all sheets — how often you face this species
    set_share: float    # of that species' sheets — how often it is *this* set
    sheets: int

    def at(self, sp: dict[str, int]) -> PokemonSet:
        return PokemonSet(species=self.species, item=self.item, ability=self.ability,
                          nature=self.nature, moves=list(self.moves),
                          sp=StatPoints.from_dict(sp), level=50)


def threats(reg: Regulation, report: dict[str, Any] | None = None, n: int = 30) -> list[Threat]:
    """The n most-used species, each as the single set most people bring it as."""
    report = report if report is not None else usage.load(reg)
    out = []
    for s in usage.top_species(report, n):
        if not s["sets"]:
            continue
        top = s["sets"][0]
        out.append(Threat(
            species=s["species"], species_id=to_id(s["species"]),
            item=None if top["item"] == "(none)" else top["item"],
            ability=None if top["ability"] == "(none)" else top["ability"],
            nature="Serious" if top["nature"] == "(none)" else top["nature"],
            moves=list(top["moves"]), share=s["share"], set_share=top["share"], sheets=s["sheets"],
        ))
    return out


# --- speed ----------------------------------------------------------------------------

def _speed(reg: Regulation, mon: PokemonSet, sp: int) -> int:
    probe = PokemonSet(species=mon.species, nature=mon.nature, sp=StatPoints(spe=sp), level=50)
    return calc_stats(probe, reg.dex, reg)["spe"]


def speed_table(reg: Regulation, team: Team, pool: list[Threat]) -> dict[str, Any]:
    """Your speeds, which are known, against theirs, which are a range 66 points wide.

    The useful number is not "are they faster" — unanswerable — but the Speed investment at which
    they become faster. `outspeeds_at` is the fewest Speed SP that puts a threat above each of
    yours; `None` means not even 32 does it, and 0 means they beat you without investing at all.
    Speed ties go to neither side, so the threshold is the first SP strictly above.
    """
    mine = [{"species": m.species, "speed": calc_stats(m, reg.dex, reg)["spe"]} for m in team]
    rows = []
    for t in pool:
        base = t.at({})
        speeds = [_speed(reg, base, sp) for sp in range(reg.sp_per_stat_cap + 1)]
        per_mon = []
        for mon in mine:
            need = next((sp for sp, v in enumerate(speeds) if v > mon["speed"]), None)
            per_mon.append({"species": mon["species"], "outspeeds_at": need})
        rows.append({
            "species": t.species, "share": t.share, "nature": t.nature,
            "speed_0": speeds[0], "speed_max": speeds[-1],
            "beats_uninvested": sum(1 for r in per_mon if r["outspeeds_at"] == 0),
            "beats_at_max": sum(1 for r in per_mon if r["outspeeds_at"] is not None),
            "per_mon": per_mon,
        })
    return {"yours": mine, "threats": rows,
            "note": "under Tailwind both sides double, so the thresholds are unchanged; under "
                    "Trick Room the comparison inverts and a threshold becomes a ceiling"}


# --- damage ---------------------------------------------------------------------------

def _stat_for(reg: Regulation, move: str) -> str | None:
    entry = reg.dex.get_move(move) or {}
    if not _damaging(entry):
        return None
    return OFFENSIVE_STAT.get(to_id(move)) or ("atk" if entry.get("category") == "Physical" else "spa")


def _sweep(dc: DamageCalc, attacker_at, defender: PokemonSet, move: str, cap: int) -> list[dict]:
    """Damage at every legal investment in the move's offensive stat, 0..cap, in one round trip."""
    return dc.batch([{"attacker": _packed(attacker_at(sp)), "defender": _packed(defender),
                      "move": {"name": move}, "field": {"gameType": "Doubles"}} for sp in range(cap + 1)])


def _packed(mon: PokemonSet) -> dict:
    return {"species": mon.species, "item": mon.item, "ability": mon.ability,
            "nature": mon.nature, "sp": mon.sp.as_dict(), "boosts": {}, "status": "", "curHP": None}


def _breakpoint(results: list[dict], hp: int, roll: str) -> int | None:
    """Fewest SP at which the move reaches `hp`. `roll` is "max" (it can happen) or "min" (it
    always does). Damage is monotone in the attacking stat, so the first hit is the threshold."""
    pick = max if roll == "max" else min
    return next((sp for sp, r in enumerate(results)
                 if r.get("ok") and r["damage"] and pick(r["damage"]) >= hp), None)


def incoming(reg: Regulation, team: Team, pool: list[Threat], dc: DamageCalc) -> list[dict[str, Any]]:
    """What they do to you, as the investment each KO requires.

    Your spread is known, so this is exact: one unknown, one threshold. `ohko_at` is the fewest
    offensive SP at which some roll kills; `sure_at` the fewest at which every roll does.
    """
    cap = reg.sp_per_stat_cap
    out = []
    for t in pool:
        rows = []
        for move in t.moves:
            stat = _stat_for(reg, move)
            if stat is None:
                continue
            for mon in team:
                hp = calc_stats(mon, reg.dex, reg)["hp"]
                res = _sweep(dc, lambda sp: t.at({stat: sp}), mon, move, cap)
                lo, hi = res[0], res[-1]
                # An immunity comes back as sixteen zeros, which is a truthy list — so the test is
                # whether anything lands at full investment, not whether a roll array exists.
                # Sinistcha is Grass/Ghost and takes nothing from Fake Out; a 0% row with no
                # threshold is noise at best, and reads as a threat that needs no investment.
                if not (hi.get("ok") and max(hi["damage"], default=0) > 0):
                    continue
                flat = max(lo["damage"]) == max(hi["damage"])
                rows.append({
                    "move": move, "stat": stat, "target": mon.species, "target_hp": hp,
                    "pct_at_0": 100 * max(lo["damage"]) / hp,
                    "pct_at_max": 100 * max(hi["damage"]) / hp,
                    "ohko_at": _breakpoint(res, hp, "max"),
                    "sure_at": _breakpoint(res, hp, "min"),
                    "half_at": _breakpoint(res, (hp + 1) // 2, "max"),
                    # Foul Play and the like scale with the *defender's* stat, so their own
                    # investment changes nothing. Saying so beats reporting a threshold of 0.
                    "spread_independent": flat,
                })
        kills = {r["target"] for r in rows if r["ohko_at"] is not None}
        out.append({"species": t.species, "share": t.share, "set_share": t.set_share,
                    "item": t.item, "ability": t.ability, "nature": t.nature,
                    "ohkos": len(kills), "ohkos_uninvested": len({r["target"] for r in rows if r["ohko_at"] == 0}),
                    "rows": rows})
    return out


# The two ends of the 66-point budget, for a defender whose spread nobody can see. Neither is a
# guess at what they run: they bracket it, and the bracket is the honest answer.
FRAIL = {"hp": 0, "def": 0, "spd": 0}


def _bulky(stat: str, cap: int) -> dict[str, int]:
    return {"hp": cap, stat: cap}


def outgoing(reg: Regulation, team: Team, pool: list[Threat], dc: DamageCalc) -> list[dict[str, Any]]:
    """What you do to them — as a bracket, because here the unknown is two numbers, not one.

    Their HP and their relevant defence are both hidden, and no single threshold separates the
    cases, so each matchup is reported at an uninvested defender and at a fully invested one. A
    move that fails to OHKO even the uninvested end is a hole in the team, whatever they run.
    """
    cap = reg.sp_per_stat_cap
    out = []
    for mon in team:
        rows = []
        for t in pool:
            best = None
            for move in mon.moves:
                stat = _stat_for(reg, move)
                if stat is None:
                    continue
                defence = "def" if (reg.dex.get_move(move) or {}).get("category") == "Physical" else "spd"
                reqs = [{"attacker": _packed(mon), "defender": _packed(t.at(sp)),
                         "move": {"name": move}, "field": {"gameType": "Doubles"}}
                        for sp in (FRAIL, _bulky(defence, cap))]
                frail, bulky = dc.batch(reqs)
                if not (frail.get("ok") and frail["damage"] and max(frail["damage"])):
                    continue
                cand = {
                    "move": move,
                    "pct_frail": 100 * max(frail["damage"]) / frail["defenderHP"],
                    "pct_bulky": 100 * max(bulky["damage"]) / bulky["defenderHP"],
                    "ohko_frail": min(frail["damage"]) >= frail["defenderHP"],
                    "ohko_bulky": min(bulky["damage"]) >= bulky["defenderHP"],
                    "maybe_frail": max(frail["damage"]) >= frail["defenderHP"],
                }
                if best is None or cand["pct_frail"] > best["pct_frail"]:
                    best = cand
            rows.append({"species": t.species, "share": t.share,
                         **(best or {"move": None, "pct_frail": 0.0, "pct_bulky": 0.0,
                                     "ohko_frail": False, "ohko_bulky": False, "maybe_frail": False})})
        out.append({"species": mon.species, "rows": rows})
    return out


# --- types and structure ---------------------------------------------------------------

def _multiplier(reg: Regulation, attacking: str, defending: Iterable[str]) -> float:
    chart = reg.dex.type_chart.get(attacking, {})
    mult = 1.0
    for d in defending:
        mult *= chart.get(d, 1.0)
    return mult


def type_pressure(reg: Regulation, team: Team, pool: list[Threat]) -> list[dict[str, Any]]:
    """Which attacking types hurt your six, weighted by how much of the meta actually carries one.

    A 4× weakness to a type nobody attacks with is not a weakness, so the ranking is by exposure.

    `carriers` is the *expected number* of that type's attackers on an opposing team, summed over
    the threat pool's per-game shares — not a share, and it can exceed 1: Fighting comes to 1.10,
    because more than one of the top thirty carries a Fighting move. Summing shares and calling
    the result a percentage is the mistake `usage.TRAITS` exists to avoid, so it is not made here
    either. The threat pool is the top n species, so this understates the tail.
    """
    out = []
    for atk_type in sorted(reg.dex.type_chart):
        hit = []
        for mon in team:
            types = (reg.dex.get_species(mon.species) or {}).get("types", [])
            m = _multiplier(reg, atk_type, types)
            if m > 1:
                hit.append({"species": mon.species, "multiplier": m})
        if not hit:
            continue
        carriers = sum(t.share for t in pool
                       if any((reg.dex.get_move(mv) or {}).get("type") == atk_type
                              and _damaging(reg.dex.get_move(mv) or {}) for mv in t.moves))
        out.append({"type": atk_type, "weak": hit, "count": len(hit),
                    "quad": sum(1 for h in hit if h["multiplier"] >= 4), "carriers": carriers})
    return sorted(out, key=lambda r: (-r["count"] * r["carriers"], -r["count"]))


def structure(reg: Regulation, team: Team, report: dict[str, Any]) -> list[dict[str, Any]]:
    """Your structural traits next to how often the meta brings each one."""
    table = usage.TRAITS | usage._derived_traits(reg)
    mine = usage._team_traits(team, table)
    return [{"trait": name, "yours": mine.get(name, 0),
             "meta_share": t["share"], "meta_per_team": t["per_team"]}
            for name, t in report["traits"].items()]


# --- the report -------------------------------------------------------------------------

def build(reg: Regulation, team: Team, *, report: dict[str, Any] | None = None,
          n: int = 30, dc: DamageCalc | None = None) -> dict[str, Any]:
    report = report if report is not None else usage.load(reg)
    pool = threats(reg, report, n)
    own = dc is None
    dc = dc or DamageCalc()
    try:
        body = {
            "regulation": reg.id,
            "usage_built": report["built"],
            "usage_sheets": report["sheets"],
            "threats": n,
            "field": "doubles, no terrain / weather / screens / Intimidate / boosts",
            "spreads": "theirs are unknown and not assumed; incoming damage is reported as the "
                       "Stat Point investment each KO requires, outgoing as a bracket",
            "team": [m.species for m in team],
            "speed": speed_table(reg, team, pool),
            "incoming": incoming(reg, team, pool, dc),
            "outgoing": outgoing(reg, team, pool, dc),
            "types": type_pressure(reg, team, pool),
            "structure": structure(reg, team, report),
        }
    finally:
        if own:
            dc.close()
    body["headlines"] = headlines(body, reg)
    return body


def _by_species(outgoing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transpose `outgoing` from your-Pokémon-major to threat-major, keyed by name rather than by
    position: the rows are built in pool order for every attacker, but a report that reads them by
    index would break silently the first time that stops being true."""
    per: dict[str, dict[str, Any]] = {}
    for mon in outgoing:
        for row in mon["rows"]:
            entry = per.setdefault(row["species"], {"species": row["species"],
                                                    "share": row["share"], "attempts": []})
            if row.get("move"):
                entry["attempts"].append({**row, "by": mon["species"]})
    return list(per.values())


def headlines(body: dict[str, Any], reg: Regulation, limit: int = 8) -> list[str]:
    """The handful of sentences worth reading first. Each one restates a number from the tables."""
    out: list[str] = []
    cap = reg.sp_per_stat_cap

    worst = sorted(body["incoming"], key=lambda t: (-t["ohkos"], -t["share"]))[:3]
    for t in worst:
        if t["ohkos"]:
            out.append(f"{t['species']} ({t['share']:.0%} of games) OHKOes {t['ohkos']} of your six"
                       + (f", {t['ohkos_uninvested']} of them uninvested" if t["ohkos_uninvested"] else ""))

    # A hole: nothing you have kills it even at zero defensive investment. "Can it happen" and
    # "does it always" are different claims and the headline has to say which — a 114% max roll
    # with an 88% min roll is not "nothing OHKOes it", it is a roll you will lose one time in five.
    for row in sorted(_by_species(body["outgoing"]), key=lambda r: -r["share"]):
        if not any(m["maybe_frail"] for m in row["attempts"]):
            best = max((m["pct_frail"] for m in row["attempts"]), default=0.0)
            out.append(f"nothing on your team can OHKO {row['species']} even uninvested "
                       f"(best roll {best:.0f}%)")
        elif not any(m["ohko_frail"] for m in row["attempts"]):
            best = max(row["attempts"], key=lambda m: m["pct_frail"])
            out.append(f"no guaranteed OHKO on {row['species']} even uninvested — the closest is "
                       f"{best['by']}'s {best['move']} at {best['pct_frail']:.0f}% top roll")

    for row in body["types"][:2]:
        if row["count"] >= 3:
            quad = f", {row['quad']} of them 4×" if row["quad"] else ""
            out.append(f"{row['type']} hits {row['count']} of your six for super effective{quad}; "
                       f"you can expect {row['carriers']:.1f} {row['type']} attackers per enemy team")

    fast = [r for r in body["speed"]["threats"] if r["beats_uninvested"] >= len(body["team"]) - 1]
    if fast:
        out.append(f"{len(fast)} of the top threats outspeed your whole team without investing a "
                   f"single point: " + ", ".join(r["species"] for r in fast[:4]))

    for s in body["structure"]:
        if s["trait"] == "trick_room" and s["yours"] == 0 and s["meta_share"] > 0.2:
            out.append(f"{s['meta_share']:.0%} of teams bring Trick Room and you bring neither a "
                       f"setter nor Taunt")
        if s["trait"] == "redirection" and s["yours"] == 0 and s["meta_share"] > 0.3:
            out.append(f"{s['meta_share']:.0%} of teams bring redirection and you bring none")
    return out[:limit]
