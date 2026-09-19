"""`vgc` — the real API. The MCP server (Phase 6) wraps these same functions."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vgc.regulation import STAT_IDS, available_regulations, load_regulation, to_id
from vgc.teams import calc_stats, export_team, is_legal, parse_team, validate_team
from vgc.teams.sets import STAT_LABELS, PokemonSet
from vgc.teams.showdown_text import parse_set
from vgc.teams.validate import validate_set


def _reg(args: argparse.Namespace):
    return load_regulation(args.regulation)


# --- server ---------------------------------------------------------------------------

def cmd_server(args: argparse.Namespace) -> int:
    from vgc.engine import showdown

    if args.action == "start":
        pid = showdown.start_server(port=args.port, simulators=args.simulators)
        print(f"Showdown running on localhost:{args.port} (pid {pid}, {args.simulators} simulator subprocesses)")
    elif args.action == "stop":
        print("stopped" if showdown.stop_server() else "not running")
    else:
        pid = showdown.server_pid()
        print(f"running (pid {pid})" if pid else "not running")
        return 0 if pid else 1
    return 0


# --- regulation -----------------------------------------------------------------------

def cmd_regulation(args: argparse.Namespace) -> int:
    reg = _reg(args)
    if args.action == "export":
        from vgc.engine import showdown

        print(f"wrote {showdown.export_dex(reg)}")
        return 0
    dex = reg.dex
    selectable = [s for s in dex.species.values() if not s["battleOnly"]]
    print(f"{reg.name} ({reg.id}) — {reg.showdown_format}, {reg.window[0]} → {reg.window[1]}")
    print(f"  {reg.style}, bring {reg.bring} of {reg.team_size}, level {reg.level}")
    print(f"  SP: {reg.sp_budget} total, {reg.sp_per_stat_cap} per stat; IVs fixed at {reg.fixed_iv}")
    print(f"  mechanics: mega={reg.mega} tera={reg.tera} dynamax={reg.dynamax} z={reg.z_moves}")
    print(f"  pool: {len(selectable)} species, {sum(s['isMega'] for s in dex.species.values())} Megas, "
          f"{len(dex.items)} items, {len(dex.moves)} moves")
    print(f"  Showdown {reg.showdown_sha[:10]} · snapshot exported {dex.meta['exported_at']}")
    print(f"  available: {', '.join(available_regulations())}")
    return 0


# --- team -----------------------------------------------------------------------------

def cmd_team_validate(args: argparse.Namespace) -> int:
    reg = _reg(args)
    text = Path(args.team).read_text()
    problems = validate_team(parse_team(text), reg)
    for p in problems:
        if p.severity == "error" or not args.errors_only:
            print(p)
    legal = is_legal(problems)
    print(f"{'LEGAL' if legal else 'ILLEGAL'} for {reg.name}")
    if args.showdown:
        from vgc.engine.showdown import validate_with_showdown

        sd = validate_with_showdown(text, reg)
        print(f"Showdown validator: {'accepted' if not sd else 'rejected'}")
        for line in sd:
            print(f"  {line}")
        if (not sd) != legal:
            print("  ⚠ disagreement between vgc and Showdown")
    return 0 if legal else 1


def cmd_team_stats(args: argparse.Namespace) -> int:
    reg = _reg(args)
    team = parse_team(Path(args.team).read_text())
    header = f"{'':22}" + "".join(f"{STAT_LABELS[s]:>5}" for s in STAT_IDS)
    print(header)
    for mon in team:
        if reg.dex.get_species(mon.species) is None:
            print(f"{mon.species:22} (not in {reg.name})")
            continue
        rows = [(mon.species, None)]
        mega = reg.dex.mega_forme(mon.species_id, to_id(mon.item or ""))
        if mega:
            rows.append((f"  → {mega['name']}", to_id(mega["name"])))
        for label, forme in rows:
            st = calc_stats(mon, reg.dex, reg, species_id=forme)
            print(f"{label:22}" + "".join(f"{st[s]:>5}" for s in STAT_IDS))
    return 0


# --- calc -----------------------------------------------------------------------------

def _set_from_spec(spec: str) -> PokemonSet:
    """`"Rillaboom @ Miracle Seed | Ability: Grassy Surge | Adamant Nature | EVs: 32 Atk"`."""
    return parse_set("\n".join(part.strip() for part in spec.split("|")))


def cmd_calc(args: argparse.Namespace) -> int:
    from vgc.engine.calc import DamageCalc

    reg = _reg(args)
    attacker, defender = _set_from_spec(args.attacker), _set_from_spec(args.defender)
    for mon, want_mega in ((attacker, args.attacker_mega), (defender, args.defender_mega)):
        if want_mega:
            mega = reg.dex.mega_forme(mon.species_id, to_id(mon.item or ""))
            if mega is None:
                print(f"error: {mon.species} @ {mon.item} can't Mega Evolve", file=sys.stderr)
                return 2
            mon.species, mon.ability = mega["name"], mega["abilities"]["0"]
    # Surface legality problems: @smogon/calc silently ignores items absent from the format.
    for mon in (attacker, defender):
        battle_only = bool((reg.dex.get_species(mon.species) or {}).get("battleOnly"))
        for p in validate_set(mon, reg):
            if p.code == "ability_illegal" and battle_only:
                continue  # a Mega's ability is checked against its base forme; fine for calcs
            if p.code in {"species_illegal", "item_illegal", "ability_illegal", "sp_budget", "sp_stat_cap", "tera_disabled"}:
                print(f"warning: {p}", file=sys.stderr)
    if args.move and to_id(args.move) not in reg.dex.moves:
        print(f"warning: {args.move} is not legal in {reg.name}", file=sys.stderr)

    field: dict = {"gameType": "Singles" if args.singles else "Doubles"}
    if args.weather:
        field["weather"] = args.weather
    if args.terrain:
        field["terrain"] = args.terrain
    boosts = lambda s: {k: int(v) for k, v in (kv.split("=") for kv in s.split(","))} if s else None  # noqa: E731
    with DamageCalc() as dc:
        r = dc.calc(attacker, defender, args.move, field=field, crit=args.crit,
                    attacker_boosts=boosts(args.attacker_boosts), defender_boosts=boosts(args.defender_boosts))
    if args.json:
        print(json.dumps(r.__dict__, indent=1))
        return 0
    print(r.desc or f"{args.move} does no damage")
    print(f"rolls: {r.damage}")
    return 0


# --- meta -----------------------------------------------------------------------------

def cmd_meta_scrape(args: argparse.Namespace) -> int:
    from vgc.meta import pool, replays

    reg = _reg(args)
    fmts = pool.formats_for(reg) if args.format == "both" else [reg.showdown_format + ("bo3" if args.format == "bo3" else "")]
    for fmt in fmts:
        new = cached = 0
        for meta in replays.search(fmt, pages=args.pages):
            if replays.cache_path(meta["id"], fmt).exists():
                cached += 1
                continue
            replays.fetch(meta["id"], fmt)
            new += 1
        print(f"{fmt}: {new} new, {cached} already cached")
    return 0


def cmd_meta_pool(args: argparse.Namespace) -> int:
    from vgc.meta import pool

    reg = _reg(args)
    teams = pool.build_pool(reg)
    path = pool.save_pool(reg, teams)
    print(f"{len(teams)} distinct legal teams ({sum(t.count for t in teams)} sheets) → {path}")
    return 0


# --- sim ------------------------------------------------------------------------------

def _print_run(s: dict) -> None:
    lo, hi = s["a_win_rate_95ci"]
    print(f"{s['battles']} battles, {s['errors']} errors, {s['ties']} ties, {s['invalid_choices']} invalid choices")
    print(f"A win rate {s['a_win_rate']:.1%} (95% CI {lo:.1%}–{hi:.1%}), mean {s['mean_turns']} turns")
    print(f"{s['wall_seconds']}s on {s['workers']} workers = {s['battles_per_second']} battles/s · digest {s['outcome_digest']}")
    print(f"logs: {s['out_dir']}")


def cmd_sim_battle(args: argparse.Namespace) -> int:
    from vgc.sim.selfplay import Matchup, run

    a, b = Path(args.team_a).read_text(), Path(args.team_b).read_text()
    ms = [Matchup(a, b, args.policy_a, args.policy_b, Path(args.team_a).stem, Path(args.team_b).stem, swap_sides=bool(i % 2))
          for i in range(args.n)]
    _print_run(run(ms, reg_id=args.regulation, seed=args.seed, workers=args.workers, run_id=args.run_id))
    return 0


def cmd_sim_selfplay(args: argparse.Namespace) -> int:
    from vgc.meta.pool import load_pool
    from vgc.sim.selfplay import gauntlet_matchups, run

    reg = _reg(args)
    teams = [(f"pool{i}", t.text) for i, t in enumerate(load_pool(reg))]
    ms = gauntlet_matchups(teams, args.n, args.policy_a, args.policy_b, seed=args.seed)
    _print_run(run(ms, reg_id=reg.id, seed=args.seed, workers=args.workers, run_id=args.run_id))
    return 0


# --- parser ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vgc", description="Pokémon Champions VGC advisor")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_reg(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        p.add_argument("--regulation", "-r", default="reg_mc", help="regulation id (default: reg_mc)")
        return p

    p = sub.add_parser("server", help="manage the local Showdown server")
    p.add_argument("action", choices=["start", "stop", "status"])
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--simulators", type=int, default=4, help="Showdown simulator subprocesses")
    p.set_defaults(func=cmd_server)

    p = with_reg(sub.add_parser("regulation", help="show or re-export a regulation"))
    p.add_argument("action", choices=["show", "export"], nargs="?", default="show")
    p.set_defaults(func=cmd_regulation)

    team = sub.add_parser("team", help="team tools").add_subparsers(dest="team_cmd", required=True)
    p = with_reg(team.add_parser("validate", help="check legality (SP, clauses, pool, no Tera)"))
    p.add_argument("team", help="Showdown team text file")
    p.add_argument("--showdown", action="store_true", help="also run Showdown's validator and compare")
    p.add_argument("--errors-only", action="store_true")
    p.set_defaults(func=cmd_team_validate)
    p = with_reg(team.add_parser("stats", help="actual level-50 stats, including Mega formes"))
    p.add_argument("team")
    p.set_defaults(func=cmd_team_stats)

    p = with_reg(sub.add_parser("calc", help="damage calc (Champions rules, doubles by default)"))
    p.add_argument("--attacker", required=True, help='set spec, lines joined by "|"')
    p.add_argument("--defender", required=True)
    p.add_argument("--move", required=True)
    p.add_argument("--attacker-mega", action="store_true", help="calc as the held stone's Mega forme")
    p.add_argument("--defender-mega", action="store_true")
    p.add_argument("--weather", choices=["Sun", "Rain", "Sand", "Snow"])
    p.add_argument("--terrain", choices=["Electric", "Grassy", "Psychic", "Misty"])
    p.add_argument("--singles", action="store_true")
    p.add_argument("--crit", action="store_true")
    p.add_argument("--attacker-boosts", help="e.g. atk=-1,spe=1")
    p.add_argument("--defender-boosts", help="e.g. def=1")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_calc)

    meta = sub.add_parser("meta", help="replays and team pools").add_subparsers(dest="meta_cmd", required=True)
    p = with_reg(meta.add_parser("scrape", help="cache recent public replays (gzipped, data/replays/)"))
    p.add_argument("--pages", type=int, default=4, help="50 replays per page, newest first")
    p.add_argument("--format", choices=["bo3", "bo1", "both"], default="bo3", help="Bo3 games always carry team sheets")
    p.set_defaults(func=cmd_meta_scrape)
    p = with_reg(meta.add_parser("pool", help="build a dated team pool from cached replays' team sheets"))
    p.set_defaults(func=cmd_meta_pool)

    simp = sub.add_parser("sim", help="seeded, parallel battles").add_subparsers(dest="sim_cmd", required=True)
    policies = ["heuristic", "random"]

    def with_run(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        with_reg(p)
        p.add_argument("--n", type=int, default=100)
        p.add_argument("--policy-a", choices=policies, default="heuristic")
        p.add_argument("--policy-b", choices=policies, default="heuristic")
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--workers", type=int, default=4)
        p.add_argument("--run-id")
        return p

    p = with_run(simp.add_parser("battle", help="team A vs team B, sides alternating"))
    p.add_argument("--team-a", required=True)
    p.add_argument("--team-b", required=True)
    p.set_defaults(func=cmd_sim_battle)
    p = with_run(simp.add_parser("selfplay", help="random pairs from the latest team pool"))
    p.set_defaults(func=cmd_sim_selfplay)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
