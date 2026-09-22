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


def cmd_team_weakness(args: argparse.Namespace) -> int:
    from vgc.building import weakness
    from vgc.engine.calc import DamageCalc

    reg = _reg(args)
    team = parse_team(Path(args.team).read_text())
    with DamageCalc() as dc:
        r = weakness.build(reg, team, n=args.threats, dc=dc)
    if args.json:
        print(json.dumps(r, indent=1))
        return 0

    print(f"{' / '.join(r['team'])}")
    print(f"vs the top {r['threats']} by per-game usage · {r['usage_sheets']} sheets, built {r['usage_built']}")
    print(f"field: {r['field']}")
    print(f"their spreads: {r['spreads']}")

    print("\nWHAT TO READ FIRST")
    for line in r["headlines"]:
        print(f"  · {line}")

    print("\nINCOMING — the Stat Points they need for the KO ('0' = they need none)")
    print(f"  {'threat':18} {'games':>6} {'set':>5}  {'OHKOs':>6}  worst case")
    for t in sorted(r["incoming"], key=lambda x: (-x["ohkos"], -x["share"]))[:args.rows]:
        kills = [row for row in t["rows"] if row["ohko_at"] is not None]
        worst = min(kills, key=lambda row: (row["ohko_at"], row["sure_at"] is None)) if kills else None
        tail = (f"{worst['move']} KOes {worst['target']} at {worst['ohko_at']} SP"
                + (f", always at {worst['sure_at']}" if worst["sure_at"] is not None else ", never guaranteed")
                ) if worst else "nothing OHKOes"
        print(f"  {t['species']:18} {t['share']:>6.0%} {t['set_share']:>5.0%}  {t['ohkos']:>4}/6  {tail}")

    print("\nSPEED — the Speed SP they need to outrun you (— = not even 32 does it)")
    yours = r["speed"]["yours"]
    print("  yours: " + ", ".join(f"{m['species']} {m['speed']}" for m in sorted(yours, key=lambda m: -m["speed"])))
    print(f"  {'threat':18} {'0 SP':>5} {'32 SP':>6}  {'free':>5}  {'max':>4}  slowest of yours it needs points for")
    for row in sorted(r["speed"]["threats"], key=lambda x: (-x["beats_uninvested"], -x["share"]))[:args.rows]:
        needs = [m for m in row["per_mon"] if m["outspeeds_at"]]
        tail = ", ".join(f"{m['species']} {m['outspeeds_at']}" for m in sorted(
            needs, key=lambda m: m["outspeeds_at"])[:3]) or "—"
        print(f"  {row['species']:18} {row['speed_0']:>5} {row['speed_max']:>6}  "
              f"{row['beats_uninvested']:>3}/6  {row['beats_at_max']:>2}/6  {tail}")
    print(f"  {r['speed']['note']}")

    print("\nOUTGOING — threats you cannot guarantee a KO on, even against zero investment")
    holes = [row for row in weakness._by_species(r["outgoing"])
             if not any(m["ohko_frail"] for m in row["attempts"])]
    for row in sorted(holes, key=lambda x: -x["share"])[:args.rows]:
        best = max(row["attempts"], key=lambda m: m["pct_frail"], default=None)
        if best is None:
            continue
        print(f"  {row['species']:18} {row['share']:>6.0%}  best {best['by']}'s {best['move']}: "
              f"{best['pct_frail']:.0f}% uninvested → {best['pct_bulky']:.0f}% invested"
              + ("  (rolls the KO)" if best["maybe_frail"] else ""))
    if not holes:
        print("  none — something on your team guarantees a KO on every threat at zero investment")

    print("\nTYPES — weighted by how much of the threat pool carries one")
    for row in r["types"][:8]:
        who = ", ".join(f"{h['species']}{'' if h['multiplier'] < 4 else ' (4×)'}" for h in row["weak"])
        print(f"  {row['type']:10} {row['count']}/6  {row['carriers']:>4.2f} attackers per enemy team  {who}")

    print("\nSTRUCTURE — yours, against how often the meta brings one")
    for s in r["structure"]:
        print(f"  {s['trait']:18} you {s['yours']}/6   meta {s['meta_share']:>5.1%} of teams, "
              f"{s['meta_per_team']:.2f} per team")
    return 0


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

    if args.players:
        _scrape_by_player(reg, fmts, args)
    return 0


def _scrape_by_player(reg, fmts: list[str], args: argparse.Namespace) -> None:
    """Fetch every cached replay of the players who have reached `--players`, then repeat.

    Skill is a property of the player, not of the battle: 58% of battles carry no rating at all,
    so a per-battle filter discards most of what a strong player did. Going by identity is worth
    an order of magnitude — at a 1300 floor the cache holds 211 rated-at-1300 battles but 2,039
    battles played *by* someone who has been there.

    Each round re-reads the cache, so opponents met in a newly fetched high-rated game become
    seeds for the next one. That snowball is the only way the set grows: nothing here can ask the
    ladder who the good players are.
    """
    from vgc.meta import replays

    seen_players: set[str] = set()
    for rnd in range(1, args.rounds + 1):
        skill = replays.player_skill(fmts)
        floor = replays.skill_floor(skill, args.players)
        targets = sorted(replays.qualified(skill, args.players) - seen_players)
        if not targets:
            print(f"round {rnd}: no new players at the {args.players:g}th percentile")
            return
        print(f"round {rnd}: {len(targets)} new players at the {args.players:g}th "
              f"percentile (rating {floor:.0f}+)")
        new = cached = 0
        for pid in targets:
            seen_players.add(pid)
            for fmt in fmts:
                for meta in replays.search_user(pid, fmt, pages=args.player_pages):
                    if replays.cache_path(meta["id"], fmt).exists():
                        cached += 1
                        continue
                    try:
                        replays.fetch(meta["id"], fmt)
                        new += 1
                    except Exception as exc:          # a deleted or private replay
                        print(f"  {meta['id']}: {exc}", file=sys.stderr)
        print(f"  {new} new, {cached} already cached")


def cmd_meta_players(args: argparse.Namespace) -> int:
    from vgc.meta import pool, replays

    reg = _reg(args)
    skill = replays.player_skill(pool.formats_for(reg))
    placeable = [e for e in skill.values() if e["rating"] is not None]
    if args.json:
        print(json.dumps(sorted(skill.values(), key=lambda e: -(e["rating"] or 0)), indent=1))
        return 0
    games = sum(e["games"] for e in skill.values()) // 2
    print(f"{len(skill)} players over {games} replays; {len(placeable)} have a rated game and can "
          f"be placed at all")
    print(f"\n{'percentile':>11} {'rating':>7} {'players':>8} {'their sheets':>13}")
    for q in args.percentiles:
        floor = replays.skill_floor(skill, q)
        who = replays.qualified(skill, q, args.min_rated)
        theirs = sum(e["games"] for pid, e in skill.items() if pid in who)
        print(f"{q:>10.0f}% {floor or 0:>7.0f} {len(who):>8} {theirs:>13}")
    print("\nskill is the *median* of the ratings a player's battles carried, not the maximum.")
    print("The maximum rises with how much of someone we happen to have cached — mean best goes")
    print("1125 → 1282 from one rated game to ten or more, while mean median goes 1125 → 1166 —")
    print("so a filter built on it selects heavy uploaders and calls them strong.")
    print("A percentile, not a rating, because 1100 means something else on the next ladder.")
    return 0


def cmd_meta_pool(args: argparse.Namespace) -> int:
    from vgc.meta import pool

    reg = _reg(args)
    teams = pool.build_pool(reg, args.skill_percentile, args.min_rated)
    path = pool.save_pool(reg, teams, tag=args.tag)
    print(f"{len(teams)} distinct legal teams ({sum(t.count for t in teams)} sheets) → {path}")
    return 0


def _bar(share: float, width: int = 12) -> str:
    return "█" * round(share * width) + "·" * (width - round(share * width))


def cmd_meta_usage(args: argparse.Namespace) -> int:
    from vgc.meta import usage

    reg = _reg(args)
    if args.reuse:
        report = usage.load(reg)
        path = None
    else:
        report = usage.build(reg, min_rating=args.min_rating,
                             skill_percentile=args.skill_percentile, min_rated_games=args.min_rated)
        path = usage.save(reg, report)

    if args.json:
        print(json.dumps(report if not args.species else _species_entry(report, args.species), indent=1))
        return 0

    rated = report["rated_sheets"]
    print(f"{report['sheets']} sheets from {report['replays']} replays · {report['players']} players · "
          f"{report['distinct_teams']} distinct teams · {rated} rated ({rated / max(report['sheets'], 1):.0%})"
          + (f" · min rating {report['min_rating']}" if report["min_rating"] else ""))
    print(f"built {report['built']}" + (f" → {path}" if path else " (cached)"))

    if args.species:
        _print_species(_species_entry(report, args.species), args.top)
        return 0

    print(f"\n{'species':24} {'per game':>9} {'per player':>11}  {'':12}")
    for s in usage.top_species(report, args.top):
        print(f"{s['species']:24} {s['share']:>9.1%} {s['player_share']:>11.1%}  {_bar(s['share'])}")
    print(f"\n{'trait':20} {'of teams':>9} {'per team':>9}")
    for name, t in report["traits"].items():
        print(f"{name:20} {t['share']:>9.1%} {t['per_team']:>9.2f}  {_bar(t['share'])}")

    print("\nper game weights a player by how much they played; per player counts each name once.")
    print("a trait's team rate is not the sum of its species shares: 0.45 Trick Room setters per")
    print("team is 34.2% of teams, because some teams bring two.")
    print("no spread column: sheets do not carry Stat Points. `--species NAME` for the detail.")
    return 0


def _species_entry(report: dict, name: str) -> dict:
    from vgc.regulation import to_id

    entry = report["species"].get(to_id(name))
    if entry is None:
        raise SystemExit(f"{name!r} does not appear in {report['sheets']} sheets")
    return entry


def _print_species(s: dict, top: int) -> None:
    print(f"\n{s['species']}: {s['sheets']} sheets ({s['share']:.1%} per game), "
          f"{s['players']} players ({s['player_share']:.1%} per player)")
    for label, key in (("item", "items"), ("ability", "abilities"), ("nature", "natures"), ("move", "moves")):
        print(f"\n  {label}")
        for row in s[key][:top]:
            print(f"    {row['name']:26} {row['share']:>6.1%} {_bar(row['share'])}")
    print("\n  partner (lift = how much more often than that partner's overall rate)")
    for row in s["partners"][:top]:
        lift = f"{row['lift']:.2f}×" if row["lift"] else "—"
        print(f"    {row['species']:26} {row['share']:>6.1%} {lift:>7}")


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
    teams = [(t.id, t.text) for t in load_pool(reg)]
    ms = gauntlet_matchups(teams, args.n, args.policy_a, args.policy_b, seed=args.seed)
    _print_run(run(ms, reg_id=reg.id, seed=args.seed, workers=args.workers, run_id=args.run_id))
    return 0


def cmd_sim_validate(args: argparse.Namespace) -> int:
    from vgc.sim.validity import save, validate_simulator

    reg = _reg(args)
    r = validate_simulator(reg, pairs=args.pairs, n=args.n, seed=args.seed, workers=args.workers,
                           order=args.order, boots=args.bootstrap, run_id=args.run_id)
    c, run = r["corpus"], r["run"]
    print(f"corpus: {c['human_games']} human games, {c['distinct_pairings']} pairings, {c['groups']} groups "
          f"({c['pairings_in_2plus_groups']} pairings recur across series)")
    print(f"sim:    {run['battles']} battles, {run['errors']} errors, {run['battles_per_second']}/s, "
          f"{run['wall_seconds']}s, digest {run['outcome_digest']}")
    sp = r["simulated_wp_spread"]
    print(f"spread: sd {sp['sd']}, median {sp['quantiles']['p50']}, {sp['share_beyond_85_15']:.0%} of pairings beyond 85/15")
    head = f"{'subset':34} {'games':>6} {'groups':>6} {'logloss':>8} {'const':>8} {'delta':>8} {'95% ci':>18} {'auc':>6}"
    print(head)
    for s in r["subsets"] + [r["recalibrated"]]:
        ci = s.get("logloss_delta_95ci") or [float("nan")] * 2
        auc = f"{s['auc']:.4f}" if s.get("auc") is not None else "n/a"
        interval = f"[{ci[0]:+.4f}, {ci[1]:+.4f}]"
        print(f"{s['subset']:34} {s['games']:6} {s['groups']:6} {s['logloss']:8.5f} {s['logloss_constant']:8.5f} "
              f"{s['logloss_delta']:+8.5f} {interval:>18} {auc:>6}")
    v = r["verdict"]
    print(f"\nverdict on {v['scored_on']} (n={v['games']} games / {v['groups']} groups): "
          f"PASS={v['pass']}  orders_correctly={v['orders_correctly']}  "
          f"recalibrated_beats_constant={v['recalibrated_beats_constant']}  usable_as_is={v['usable_as_is']}")
    print(f"  → {v['reading']}")
    print(f"→ {save(reg, r, tag=args.tag)}")
    return 0


# --- data -----------------------------------------------------------------------------

def cmd_data_freeze(args: argparse.Namespace) -> int:
    from vgc.data import splits
    from vgc.data.snapshots import replay_group
    from vgc.meta import pool, replays

    reg = _reg(args)
    found = sorted((pool.TEAMS / reg.id).glob("ots_pool_*.json"))
    teams = pool.load_pool(reg, found[-1])
    groups = [replay_group(r) for fmt in pool.formats_for(reg) for r in replays.cached(fmt)]
    path = splits.freeze(reg, [t.id for t in teams], groups, found[-1].name)
    d = json.loads(path.read_text())
    print(f"froze {path}: {len(d['heldout_teams'])}/{d['teams_at_freeze']} teams, "
          f"{len(d['heldout_human_groups'])}/{d['human_groups_at_freeze']} replay groups held out; rates {d['rates']}")
    return 0


def _print_extract(r: dict) -> None:
    print(f"{r['source']}: {sum(r['battles'].values())} battles → {r['out_dir']} ({r['seconds']}s)")
    for split, n in sorted(r["records"].items()):
        print(f"  {split:15} {r['battles'][split]:6} battles {n:8} snapshots")
    if r["errors"]:
        print(f"  {r['errors']} errors, e.g. {r['error_examples'][:2]}")


def cmd_data_extract(args: argparse.Namespace) -> int:
    from vgc.data.pipeline import extract_selfplay

    r = extract_selfplay(Path(args.run), _reg(args), workers=args.workers)
    _print_extract(r)
    return 1 if r["errors"] else 0


def cmd_data_human(args: argparse.Namespace) -> int:
    from vgc.data.pipeline import extract_human
    from vgc.meta import pool

    reg = _reg(args)
    fmts = pool.formats_for(reg) if args.format == "both" else [reg.showdown_format + ("bo3" if args.format == "bo3" else "")]
    bad = 0
    for fmt in fmts:
        r = extract_human(fmt, reg)
        _print_extract(r)
        bad += r["errors"]
    return 1 if bad else 0


def cmd_data_generate(args: argparse.Namespace) -> int:
    from vgc.data.pipeline import extract_selfplay
    from vgc.meta.pool import load_pool
    from vgc.sim.selfplay import SELFPLAY, gauntlet_matchups, paired_matchups, run

    reg = _reg(args)
    pool = load_pool(reg)
    teams = [(t.id, t.text) for t in pool]
    if args.spreads == "sampled":
        # A corpus for gating the belief layer, not for training. `impute_sp` puts the cap in the
        # offensive stat of all 20,082 pool Pokémon, so offensive investment is a *constant* there
        # and nothing that infers it can be measured — the same degeneracy finding 8 caught in the
        # training mix. Resampled teams get their own ids so they can never be mistaken for pool
        # teams by the split or a manifest, and the run id says what they are.
        import numpy as np

        from vgc.belief import prior as belief_prior
        from vgc.meta import replays as meta_replays

        rng = np.random.default_rng(args.spread_seed)
        resampled = []
        for _, text in teams:
            new_text = belief_prior.resample_team(reg, text, rng)
            resampled.append((meta_replays.team_id(new_text), new_text))
        teams = resampled
        print(f"spreads resampled for {len(teams)} teams (seed {args.spread_seed}); "
              f"this corpus is for belief gating and must not be manifested")
    weights = None
    if args.usage_alpha > 0 or args.min_rating:
        from vgc.meta.pool import sampling_weights

        weights = sampling_weights(pool, alpha=args.usage_alpha, min_rating=args.min_rating)
        live = sum(w > 0 for w in weights)
        print(f"usage weighting: alpha {args.usage_alpha}, {live}/{len(pool)} teams, "
              f"top team {max(weights):.3%} of battles vs uniform {1 / len(pool):.3%}")
    if args.per_pair > 1:
        pairs = args.n // args.per_pair
        ms = paired_matchups(teams, pairs, args.per_pair, args.policy_a, args.policy_b, seed=args.seed, weights=weights)
        suffix = f"-p{pairs}x{args.per_pair}"
    else:
        ms = gauntlet_matchups(teams, args.n, args.policy_a, args.policy_b, seed=args.seed, weights=weights)
        suffix = f"-n{args.n}"
    tag = "-spreads" if args.spreads == "sampled" else ""
    run_id = args.run_id or f"gen-{args.policy_a}-{args.policy_b}{tag}-s{args.seed}{suffix}"
    _print_run(run(ms, reg_id=reg.id, seed=args.seed, workers=args.workers, run_id=run_id))
    r = extract_selfplay(SELFPLAY / run_id, reg, workers=args.workers)
    _print_extract(r)
    return 1 if r["errors"] else 0


def cmd_data_manifest(args: argparse.Namespace) -> int:
    from vgc.data import pipeline, splits

    reg = _reg(args)
    files = [Path(f) for f in args.files] or pipeline.snapshot_files(reg, args.split)
    m = splits.build_manifest(files, reg, purpose="train" if args.split == "train" else args.split)
    problems = splits.check_manifest(m, reg)
    out = pipeline.SNAPSHOTS / reg.id / "manifests" / f"{args.name}.json"
    if problems:
        print(f"refusing to write {out}: {len(problems)} problems")
        for p in problems[:20]:
            print(f"  {p}")
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(m, indent=1) + "\n")
    print(f"{len(m['files'])} files, {len(m['battles'])} battles, {sum(f['records'] for f in m['files'])} snapshots → {out}")
    return 0


def cmd_data_check(args: argparse.Namespace) -> int:
    from vgc.data import splits

    reg = _reg(args)
    problems = splits.check_manifest(json.loads(Path(args.manifest).read_text()), reg)
    for p in problems[:50]:
        print(p)
    print("clean" if not problems else f"{len(problems)} problems")
    return 1 if problems else 0


def cmd_data_parity(args: argparse.Namespace) -> int:
    from vgc.data.parity import run_parity

    r = run_parity(_reg(args), args.n, seed=args.seed)
    print(f"{r['battles']} battles, {r['decisions_checked']} player decisions checked, {r['missing']} unmatched")
    for k, v in r["mismatches"].items():
        print(f"  {v:5} {k}  e.g. {r['examples'][k]}")
    if r["illusion_divergences"]:
        n = sum(r["illusion_divergences"].values())
        print(f"  ({n} differences in battles with a broken Illusion: poke-env tracks Pokémon by name; not counted)")
    print("parity: exact" if not r["mismatches"] and not r["missing"] else "parity: MISMATCHES")
    return 1 if r["mismatches"] or r["missing"] else 0


def cmd_data_stats(args: argparse.Namespace) -> int:
    from vgc.data import pipeline, splits

    reg = _reg(args)
    root = pipeline.SNAPSHOTS / reg.id
    for d in sorted(p for p in root.glob("*/*") if p.is_dir() and p.parent.name != "manifests"):
        parts = []
        for f in sorted(d.glob("*.jsonl.gz")):
            battles, n = set(), 0
            for rec in splits.read_shard(f):
                battles.add(rec["battle"])
                n += 1
            parts.append(f"{f.name.split('.')[0]} {len(battles)}/{n}")
        print(f"{d.relative_to(root)}: " + ", ".join(parts) + "  (battles/snapshots)")
    return 0


# --- wp -------------------------------------------------------------------------------

def cmd_wp_featurize(args: argparse.Namespace) -> int:
    from vgc.data.pipeline import SNAPSHOTS
    from vgc.wp.dataset import build

    reg = _reg(args)
    info = build(reg, SNAPSHOTS / reg.id / "manifests" / f"{args.manifest}.json", args.name, workers=args.workers)
    print(json.dumps(info, indent=1))
    return 0


def cmd_wp_train(args: argparse.Namespace) -> int:
    import subprocess

    from vgc import paths
    from vgc.wp import models
    from vgc.wp.dataset import FEATURES

    reg = _reg(args)
    version = args.version or f"wp-v1-{args.kind}"
    if args.kind in ("logistic", "gbt"):
        out = models.train_baseline(reg, args.kind, args.dataset, version)
        print(f"{version} → {out}")
        return 0
    data = FEATURES / reg.id / args.dataset
    out = models.model_dir(reg.id, version)
    py = paths.ROOT / ".venv-train" / "bin" / "python"
    cmd = [str(py), "-m", "vgc.wp.set_torch", "--data", str(data), "--out", str(out), "--epochs", str(args.epochs),
           "--threads", str(args.threads)] + (args.extra or [])
    r = subprocess.run(cmd, env={"PYTHONPATH": str(paths.ROOT / "src"), "PATH": "/usr/bin:/bin"})
    if r.returncode:
        return r.returncode
    finish_set_model(reg, args.dataset, version)
    print(f"{version} → {out}")
    return 0


def finish_set_model(reg, dataset: str, version: str) -> None:
    """Card + vocab for a set model that `set_torch` has written."""
    from vgc.wp import models
    from vgc.wp.dataset import FEATURES

    out = models.model_dir(reg.id, version)
    info = json.loads((FEATURES / reg.id / dataset / "info.json").read_text())
    (out / "vocab.json").write_text((FEATURES / reg.id / dataset / "vocab.json").read_text())
    trained = json.loads((out / "train.json").read_text())
    models.write_card(reg.id, version, "set", info, {"training": {k: v for k, v in trained.items() if k != "history"}})


def cmd_wp_card(args: argparse.Namespace) -> int:
    """Write the card for a set model trained outside `vgc wp train` (e.g. on rented hardware)."""
    reg = _reg(args)
    finish_set_model(reg, args.dataset, args.version)
    print(f"card written for {args.version}")
    return 0


def cmd_wp_calibrate(args: argparse.Namespace) -> int:
    from vgc.wp.dataset import load
    from vgc.wp.models import calibrate

    reg = _reg(args)
    temps = calibrate(reg.id, args.version, load(reg, args.dataset, "train"), load(reg, args.dataset, "val"))
    print(f"{args.version} temperatures: {json.dumps(temps)}")
    return 0


def cmd_wp_eval(args: argparse.Namespace) -> int:
    from vgc.wp import evaluate, models
    from vgc.wp.dataset import FEATURES, load, merge

    reg = _reg(args)
    base = FEATURES / reg.id / args.dataset
    data = {p.stem[len("eval_"):]: load(reg, args.dataset, p.stem) for p in sorted(base.glob("eval_*.npz"))}
    # Every held-out human OTS game (held out by replay group or by team): the headline set.
    human = [data[k] for k in ("human_ots", "human_ots_team") if k in data]
    if human:
        data = {"human_ots_all": merge(human)} | data
    usage = evaluate.usage_rates(load(reg, args.dataset, "train"))
    all_results: dict[str, dict] = {}
    for version in [args.version] + (args.baseline or []):
        model = models.load_model(reg.id, version)
        all_results[version] = {name: evaluate.evaluate_set(model, d, usage) for name, d in data.items()}
    main_r = all_results.pop(args.version)
    print(evaluate.format_report(main_r, all_results))
    if args.version != "constant":
        out = models.model_dir(reg.id, args.version)
        (out / "eval.json").write_text(json.dumps({"dataset": args.dataset, "results": main_r,
                                                   "baselines": all_results}, indent=1) + "\n")
        g = evaluate.gates(main_r, all_results)
        fp = models.eval_fingerprint(sorted(base.glob("eval_*.npz"))) | {
            "dataset": args.dataset, "at": __import__("datetime").datetime.now().isoformat(timespec="seconds")}
        models.update_card(reg.id, args.version, headline=evaluate.headline(main_r), gates=g,
                           eval_dataset=fp)
        failed = [k for k, v in g.items() if isinstance(v, dict) and v.get("pass") is False]
        print(f"gates: {'all pass' if g['all_pass'] else 'FAILED ' + ', '.join(failed)}")
        # Say the in-battle verdict out loud: a model can fail the pooled set purely on preview
        # and still be the right thing to draw a WP number with turn by turn.
        if g.get("in_battle_pass") is not None and not g["all_pass"]:
            print(f"       in-battle only: {'PASS' if g['in_battle_pass'] else 'FAIL'}")
    # Baselines were just scored on these same rows; record it, or the registry shows the model
    # we are actually beaten by as "(not evaluated)".
    for bname, bres in all_results.items():
        if bname != "constant":
            models.update_card(reg.id, bname, headline=evaluate.headline(bres))
    return 0


def cmd_wp_preview(args: argparse.Namespace) -> int:
    from vgc.wp.tools import preview

    reg = _reg(args)
    r = preview(reg, Path(args.team).read_text(), Path(args.opponent).read_text(), args.version, context=args.context)
    if args.json:
        print(json.dumps(r, indent=1))
        return 0
    print(f"{r['version']} ({r['context']} play) — you: {', '.join(r['mine'])}")
    print(f"  vs {', '.join(r['theirs'])}")
    print(f"  WP before choosing: {r['preview_wp_player']:.1%} (spectator view {r['preview_wp_spectator']:.1%})")
    if "their_bring" in r:
        guess = sorted(r["their_bring"].items(), key=lambda kv: -kv[1])
        print("  their likely bring: " + ", ".join(f"{s} {q:.0%}" for s, q in guess))
    print(f"\n  best {args.top} of {len(r['options'])} bring + lead choices:")
    for o in r["options"][: args.top]:
        print(f"    {o['wp']:6.1%}  lead {' + '.join(o['leads']):32} back {' + '.join(o['back'])}")
    print("\n  best lead for each bring:")
    for o in r["best_by_bring"][: args.top]:
        print(f"    {o['wp']:6.1%}  {', '.join(sorted(o['bring'])):56} lead {' + '.join(o['leads'])}")
    return 0


def cmd_wp_replay(args: argparse.Namespace) -> int:
    from vgc.meta import replays
    from vgc.wp.tools import replay_trajectory

    reg = _reg(args)
    if Path(args.replay).exists():
        rep = json.loads(Path(args.replay).read_text())
    else:
        fmt = args.replay.rsplit("-", 1)[0]
        rep = replays.fetch(args.replay, fmt)
    traj = replay_trajectory(reg, rep, args.version)
    players = rep.get("players", ["p1", "p2"])
    print(f"{rep['id']}: {players[0]} (p1) vs {players[1]} (p2) — WP for p1 ({args.version})")
    for t in traj:
        bar = "█" * round(20 * t["wp_p1"])
        print(f"  {t['kind']:7} t{t['turn']:<2} {t['wp_p1']:6.1%} {bar:20}  {t['left']['p1']}v{t['left']['p2']}  "
              f"{' / '.join(t['active']['p1'])}  vs  {' / '.join(t['active']['p2'])}")
    return 0


def cmd_belief_speed(args: argparse.Namespace) -> int:
    from vgc.belief import speed
    from vgc.data.observe import Observer
    from vgc.meta import replays

    reg = _reg(args)
    if Path(args.replay).exists():
        rep = json.loads(Path(args.replay).read_text())
    else:
        rep = replays.fetch(args.replay, args.replay.rsplit("-", 1)[0])
    obs = Observer("spectator", reg.dex)
    obs.feed_many(rep["log"] if isinstance(rep["log"], list) else rep["log"].split("\n"))

    # `--known` names the side whose spreads you wrote, which is the side you can actually pin the
    # other against. Without it every pair has two unknowns and the answer is honestly "nothing".
    known: dict[tuple[str, str], int] = {}
    if args.known:
        for mon in obs.sides[args.known].mons:
            known[(args.known, mon.species)] = args.assume
    beliefs = speed.infer(reg, obs, known)

    if args.json:
        print(json.dumps({"summary": speed.summary(beliefs),
                          "beliefs": [b.to_json() for b in beliefs.values()]}, indent=1))
        return 0

    players = rep.get("players", ["p1", "p2"])
    print(f"{rep.get('id', args.replay)}: {players[0]} (p1) vs {players[1]} (p2)")
    print(f"{len(obs.moves_log)} moves, {len(speed.pairs(reg, obs.moves_log))} of their pairs raced"
          + (f"; assuming {args.known} ran {args.assume} Speed SP throughout" if args.known else
             "; no side's spread given, so every pair has two unknowns (--known p1)"))
    print(f"\n{'':4}{'pokemon':20} {'nature':9} {'Speed SP':>12} {'ruled out':>10}  from")
    for b in sorted(beliefs.values(), key=lambda x: (-x.narrowed, x.species)):
        lo_hi = f"{b.bounds[0]}-{b.bounds[1]}" if b.bounds else "—"
        note = f"{b.used} pairs" + (f", {b.deferred} deferred" if b.deferred else "")
        if b.contradicted:
            note += "  ⚠ contradicted, widened back"
        print(f"{b.side:4}{b.species:20} {str(b.nature):9} {lo_hi:>12} {b.narrowed:>10.0%}  {note}")
    s = speed.summary(beliefs)
    print(f"\n{s['constraints_used']} constraints used, {s['constraints_deferred']} deferred "
          f"(both sides unknown), {s['any_narrowed']}/{s['pokemon']} narrowed at all")
    print("a bound is never tightened past a speed tie: Showdown breaks ties at random.")
    return 0


def cmd_belief_sets(args: argparse.Namespace) -> int:
    """The ranking half of the belief: what an opponent is *likely* to be holding.

    Unlike every other `vgc belief` command this reads no battle. It counts the open-team-sheet
    corpus once into `data/teams/<reg>/setprior.json`, and after that answers questions about a
    species. The distinction matters and the output says so: a bound can only be wrong because of
    a bug, and a ranking can be wrong because somebody brought something unusual.
    """
    from vgc.belief import sets as set_belief

    reg = _reg(args)
    if args.build:
        report = set_belief.build(reg)
        path = set_belief.save(reg, report)
        set_belief.corpus.cache_clear()
        n = sum(len(s["sets"]) for s in report["species"].values())
        print(f"{path}  ·  {len(report['species'])} species, {n} distinct sets, "
              f"{report['sheets']} sheets")
        if not args.species:
            return 0

    if not set_belief.prior_path(reg).exists():
        print(f"no set prior for {reg.id}. Build it with `vgc belief sets --build` "
              f"(reads every cached open-team-sheet replay; about half a minute).")
        return 1
    if not args.species:
        print("give a species, or --build to count the corpus")
        return 2

    b = set_belief.for_species(reg, args.species)
    if args.json:
        print(json.dumps(b.to_json(), indent=1))
        return 0
    if not b.sheets:
        print(f"{args.species}: nobody in the corpus has brought one. "
              f"Every legal option is equally likely as far as this knows.")
        return 0
    print(f"{b.species}: {b.seen} sheets, {len(b.sheets)} distinct sets")
    print(f"the single most common one is {b.concentration:.1%} of them — which is why a point "
          f"estimate over it is a guess and not an answer")
    for label, dist in (("ability", b.ability()), ("item", b.item()), ("nature", b.nature())):
        top = ", ".join(f"{k or '(none)'} {v:.1%}" for k, v in list(dist.items())[:4])
        print(f"  {label:8} {top}")
    print("  moves    " + ", ".join(f"{m} {q:.0%}" for m, q in b.moves(6)))
    best = b.top()
    if best:
        print(f"\nmost common set ({best.count / max(b.seen, 1):.1%}): "
              f"{best.item or 'no item'} · {best.ability} · {best.nature} · "
              + "/".join(best.moves))
    print("\nno spreads: sheets do not carry Stat Points. That is what `vgc belief sp` is for, "
          "and it reads this battle rather than other people's.")
    return 0


def cmd_belief_sp(args: argparse.Namespace) -> int:
    """Both channels at once, over the whole 66-point allocation rather than one stat at a time."""
    from vgc.belief import sp as sp_belief
    from vgc.data.observe import Observer
    from vgc.engine.calc import DamageCalc
    from vgc.meta import replays
    from vgc.teams import parse_team

    reg = _reg(args)
    if Path(args.replay).exists():
        rep = json.loads(Path(args.replay).read_text())
    else:
        rep = replays.fetch(args.replay, args.replay.rsplit("-", 1)[0])
    obs = Observer("spectator", reg.dex)
    obs.feed_many(rep["log"] if isinstance(rep["log"], list) else rep["log"].split("\n"))

    # The damage channel measures their attacker against a defender whose spread you actually
    # wrote, so it needs your team file — not a guess at it. Without one, only turn order is read.
    known, speed_known = {}, {}
    if args.team:
        for mon in parse_team(Path(args.team).read_text()):
            known[(args.known, mon.species)] = mon
    else:
        # Enough for turn order, and honestly not enough for damage: a Speed you assumed cannot
        # measure how hard something hit you.
        speed_known = {(args.known, m.species): args.assume for m in obs.sides[args.known].mons}
        print(f"no --team given, so only the turn-order channel runs, assuming {args.known} ran "
              f"{args.assume} Speed SP throughout (the damage channel needs their real spreads)")

    def run(dc):
        return sp_belief.infer(reg, obs, known, dc, speed_known=speed_known,
                               dead_zero=not args.no_dead_zero, spend_all=not args.allow_unspent)

    if known:
        with DamageCalc() as dc:
            beliefs = run(dc)
    else:
        beliefs = run(None)

    if args.json:
        print(json.dumps({"summary": sp_belief.summary(beliefs),
                          "beliefs": [b.to_json() for b in beliefs.values()]}, indent=1))
        return 0

    players = rep.get("players", ["p1", "p2"])
    print(f"{rep.get('id', args.replay)}: {players[0]} (p1) vs {players[1]} (p2)")
    print(f"\n{'':4}{'pokemon':20} {'Speed SP':>9} {'offence':>14} {'bulk total':>11} {'ruled out':>10}  from")
    for b in sorted(beliefs.values(), key=lambda x: (-x.narrowed, x.species)):
        bounds = b.bounds()
        off = next((s for s in ("atk", "spa") if s in b.sources), None)
        span = lambda t: f"{t[0]}-{t[1]}" if t else "—"  # noqa: E731
        note = ", ".join(filter(None, [
            f"{b.speed_used} pairs" if b.speed_used else "",
            f"{b.damage_used} of their hits" if b.damage_used else "",
            f"{b.bulk_used} of yours" if b.bulk_used else ""])) or "nothing read"
        if b.contradicted:
            note += f"  ⚠ {b.contradicted} contradiction, widened back"
        print(f"{b.side:4}{b.species:20} {span(bounds.get('spe')):>9} "
              f"{(off + ' ' + span(bounds.get(off))) if off else '—':>14} "
              f"{span(b.spent_on(('hp', 'def', 'spd'))):>11} {b.narrowed:>10.1%}  {note}")
    s = sp_belief.summary(beliefs)
    print(f"\n{s['any_narrowed']}/{s['pokemon']} narrowed at all; {s['bulk_bounded']} had their bulk "
          f"bounded — by your own damage where it landed, and by the 66-point budget elsewhere.")
    if args.allow_unspent:
        print("reading the budget as the format enforces it — ≤66, points may be left unspent.")
    return 0


def cmd_wp_endgames(args: argparse.Namespace) -> int:
    """Build the browsable set of decided endgames, and report how often the call was right."""
    from vgc import paths
    from vgc.web import endgames
    from vgc.wp.models import in_battle_version

    reg = _reg(args)
    version = args.version or in_battle_version(reg.id)
    if not version:
        print("no WP model is registered for this regulation", file=sys.stderr)
        return 1

    def progress(seen: int, kept: int) -> None:
        if seen % 250 == 0:
            print(f"\r  {seen} replays read, {kept} selected", end="", file=sys.stderr, flush=True)

    result = endgames.scan(reg, version, min_wp=args.min_wp, hold=args.hold,
                           min_turns=args.min_turns, progress=None if args.quiet else progress)
    print("\r" + " " * 48 + "\r", end="", file=sys.stderr)
    path = endgames.write_index(reg, result)
    c, games = result["counts"], result["games"]
    print(f"{version}: {c['selected']} endgames at {args.min_wp:.0%}+ held for the last {args.hold} "
          f"decision points, out of {c['eligible']} held-out human OTS games "
          f"({c['cached']} replays cached)")
    if games:
        correct, n = result["correct"], len(games)
        print(f"  the favoured side went on to win {correct}/{n} ({correct / n:.1%})")
        played_out = [g for g in games if g["ended_by"] == "normal"]
        print(f"  {len(played_out)} played to a KO, {n - len(played_out)} ended in a forfeit")
        for g in games[:5]:
            mark = "ok " if g["correct"] else "MISS"
            print(f"  {mark} {g['wp']:6.1%} {g['side']} · {g['players']['p1']} vs {g['players']['p2']} · "
                  f"{g['turns']} turns · {g['replay']}")
    print(f"  wrote {path.relative_to(paths.ROOT)}")
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    try:
        from vgc.web.app import serve
    except ImportError as e:
        print(f"the web app needs its extras: pip install -e '.[web]'  ({e})", file=sys.stderr)
        return 1
    print(f"http://{args.host}:{args.port}  (ctrl-c to stop)")
    serve(host=args.host, port=args.port, reload=args.reload)
    return 0


def cmd_wp_registry(args: argparse.Namespace) -> int:
    from vgc.wp.models import REGISTRY

    reg = json.loads(REGISTRY.read_text()) if REGISTRY.exists() else {"wp": []}
    # Log losses from different manifests are not comparable; mark everything not on the newest one.
    # Comparability is a question about the rows a model was *scored* on, not the manifest it was
    # trained from: two models trained months apart and re-evaluated today are directly
    # comparable, and the training manifest changes whenever it is merely rebuilt. Anchor on the
    # newest evaluation fingerprint, and say "not evaluated since" when a model has none — an old
    # card that predates the fingerprint cannot be claimed to match.
    evaluated = [e for e in reg["wp"] if e.get("eval_sha256") and e.get("eval_at")]
    current = max(evaluated, key=lambda e: e["eval_at"])["eval_sha256"] if evaluated else None
    for e in reg["wp"]:
        h = e.get("headline", {}).get("human_spectator", {})
        tail = f"human spectator logloss {h['logloss']:.4f} ece {h['ece']:.4f}" if h else "(not evaluated)"
        # Gate status, so nobody reads a good-looking log loss and assumes the model is usable.
        g = e.get("gates")
        if g:
            failed = [k for k, v in g.items() if isinstance(v, dict) and v.get("pass") is False]
            tail += "  gates: all pass" if g.get("all_pass") else f"  gates: FAIL ({', '.join(failed)})"
            # The split verdict: usable during a battle even when the pooled set fails on preview.
            if not g.get("all_pass") and g.get("in_battle_pass"):
                tail += "  [in-battle: PASS]"
        if h and current:
            if not e.get("eval_sha256"):
                tail += "  [scored before eval fingerprints — comparability unknown]"
            elif e["eval_sha256"] != current:
                tail += "  [scored on different eval rows — not comparable]"
        print(f"{e['regulation']:7} {e['version']:24} {e['kind']:9} {e['created']}  {tail}")
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
    p = with_reg(team.add_parser("weakness", help="what the meta does to this team: KO breakpoints, speed, holes"))
    p.add_argument("team", help="Showdown export file")
    p.add_argument("--threats", type=int, default=30, help="how many of the most-used species to check")
    p.add_argument("--rows", type=int, default=12, help="rows per section")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_team_weakness)
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
    p.add_argument("--players", type=float, metavar="PERCENTILE",
                   help="after the sweep, fetch every replay of players at or above this "
                        "percentile of the observed population — skill belongs to the player, not "
                        "to one battle, and 58%% of battles carry no rating at all")
    p.add_argument("--player-pages", type=int, default=4, help="pages per player (50 replays each)")
    p.add_argument("--rounds", type=int, default=2,
                   help="repeat, so opponents found in new high-rated games seed the next round")
    p.set_defaults(func=cmd_meta_scrape)
    p = with_reg(meta.add_parser("players", help="who is in the cache, and how strong they got"))
    p.add_argument("--percentiles", type=float, nargs="+", default=[0, 25, 50, 75, 90])
    p.add_argument("--min-rated", type=int, default=1, help="rated games needed to be placed")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_meta_players)
    p = with_reg(meta.add_parser("pool", help="build a dated team pool from cached replays' team sheets"))
    p.add_argument("--tag", default="", help="suffix, to keep an earlier pool of the same date")
    p.add_argument("--skill-percentile", type=float, metavar="P",
                   help="only sheets brought by a player at or above the Pth percentile")
    p.add_argument("--min-rated", type=int, default=1)
    p.set_defaults(func=cmd_meta_pool)
    p = with_reg(meta.add_parser("usage", help="what the corpus brings: species, items, abilities, natures, moves, partners"))
    p.add_argument("--species", help="the full detail for one species instead of the table")
    p.add_argument("--top", type=int, default=30, help="rows per table")
    p.add_argument("--min-rating", type=int, help="only sheets from replays rated at least this (ratings are sparse)")
    p.add_argument("--skill-percentile", type=float, metavar="P",
                   help="only sheets brought by a player at or above the Pth percentile of the "
                        "observed population — the filter for 'not the bottom half'")
    p.add_argument("--min-rated", type=int, default=1)
    p.add_argument("--reuse", action="store_true", help="print the last saved report instead of recounting")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_meta_usage)

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
    p = with_reg(simp.add_parser("validate", help="does heuristic self-play predict real human results?"))
    p.add_argument("--pairs", type=int, default=0, help="real-meta pairings to simulate (0 = all)")
    p.add_argument("--n", type=int, default=15, help="battles per pairing")
    p.add_argument("--order", choices=["random", "most_played"], default="random",
                   help="random is unbiased; most_played selects close Bo3 series (see the module docstring)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--workers", type=int, default=6)
    p.add_argument("--bootstrap", type=int, default=2000, help="cluster-bootstrap reps over Bo3 groups")
    p.add_argument("--tag", default="", help="keep this result alongside the untagged one")
    p.add_argument("--run-id")
    p.set_defaults(func=cmd_sim_validate)

    data = sub.add_parser("data", help="battle snapshots, held-out splits, manifests").add_subparsers(dest="data_cmd", required=True)
    p = with_reg(data.add_parser("freeze", help="freeze the held-out split (once per regulation)"))
    p.set_defaults(func=cmd_data_freeze)
    p = with_reg(data.add_parser("extract", help="snapshots from a self-play run's inputLogs"))
    p.add_argument("--run", required=True, help="data/selfplay/<run_id>")
    p.add_argument("--workers", type=int, default=4)
    p.set_defaults(func=cmd_data_extract)
    p = with_reg(data.add_parser("human", help="snapshots from cached human replays"))
    p.add_argument("--format", choices=["bo3", "bo1", "both"], default="both")
    p.set_defaults(func=cmd_data_human)
    p = with_run(data.add_parser("generate", help="self-play across the team pool, then extract snapshots"))
    p.add_argument("--spreads", choices=["imputed", "sampled"], default="imputed",
                   help="'sampled' redraws every spread from vgc.belief.prior — for gating the "
                        "belief layer, never for training (see the flag's note when it runs)")
    p.add_argument("--spread-seed", type=int, default=11)
    p.add_argument("--per-pair", type=int, default=1,
                   help="battles per team pairing (>1 repeats pairings, which is what team-preview WP needs)")
    p.add_argument("--usage-alpha", type=float, default=0.0,
                   help="weight pairings by how often each team appeared in replays (0 uniform, 1 proportional)")
    p.add_argument("--min-rating", type=int, help="only teams seen at this replay rating or above")
    p.set_defaults(func=cmd_data_generate)
    p = with_reg(data.add_parser("manifest", help="write a checked training manifest"))
    p.add_argument("--name", required=True)
    p.add_argument("--split", choices=["train", "heldout_battle", "heldout_team", "heldout_human"], default="train")
    p.add_argument("files", nargs="*", help="snapshot shards (default: every <split> shard)")
    p.set_defaults(func=cmd_data_manifest)
    p = with_reg(data.add_parser("check", help="re-check a manifest against the frozen split"))
    p.add_argument("manifest")
    p.set_defaults(func=cmd_data_check)
    p = with_reg(data.add_parser("parity", help="live poke-env view vs re-derived snapshots"))
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--seed", type=int, default=0)
    p.set_defaults(func=cmd_data_parity)
    p = with_reg(data.add_parser("stats", help="snapshot datasets on disk"))
    p.set_defaults(func=cmd_data_stats)

    wp = sub.add_parser("wp", help="win probability models").add_subparsers(dest="wp_cmd", required=True)
    p = with_reg(wp.add_parser("featurize", help="feature arrays from a checked manifest + the held-out sets"))
    p.add_argument("--manifest", default="wp-v1-train")
    p.add_argument("--name", default="wp-v1")
    p.add_argument("--workers", type=int, default=6)
    p.set_defaults(func=cmd_wp_featurize)
    p = with_reg(wp.add_parser("train", help="train a WP model version"))
    p.add_argument("--kind", choices=["logistic", "gbt", "set"], required=True)
    p.add_argument("--dataset", default="wp-v1")
    p.add_argument("--version")
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--threads", type=int, default=6)
    p.add_argument("extra", nargs="*", help="extra set_torch flags after --")
    p.set_defaults(func=cmd_wp_train)
    p = with_reg(wp.add_parser("card", help="write a model card for a set model trained outside `wp train`"))
    p.add_argument("--version", required=True)
    p.add_argument("--dataset", default="wp-v1")
    p.set_defaults(func=cmd_wp_card)
    p = with_reg(wp.add_parser("calibrate", help="fit per-context temperatures (train/val rows only)"))
    p.add_argument("--version", required=True)
    p.add_argument("--dataset", default="wp-v1")
    p.set_defaults(func=cmd_wp_calibrate)
    p = with_reg(wp.add_parser("eval", help="calibration and accuracy on the frozen held-out sets"))
    p.add_argument("--version", required=True)
    p.add_argument("--baseline", action="append", help="versions to compare against (repeatable; 'constant' = 50%%)")
    p.add_argument("--dataset", default="wp-v1")
    p.set_defaults(func=cmd_wp_eval)
    p = with_reg(wp.add_parser("preview", help="team preview (open sheets): WP for every bring + lead choice"))
    p.add_argument("--team", required=True, help="your team (Showdown text)")
    p.add_argument("--opponent", required=True, help="their open team sheet (Showdown text)")
    p.add_argument("--version", default="wp-v1-set")
    p.add_argument("--context", choices=["human", "heuristic"], default="human")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_wp_preview)
    p = with_reg(wp.add_parser("replay", help="WP trajectory of a public replay (id or JSON file)"))
    p.add_argument("replay")
    p.add_argument("--version", default="wp-v1-set")
    p.set_defaults(func=cmd_wp_replay)
    p = with_reg(wp.add_parser("endgames", help="held-out human games the model called at 90%%+ before the end"))
    p.add_argument("--version", default="", help="default: the model whose in-battle gates pass")
    p.add_argument("--min-wp", type=float, default=0.90, help="the confidence the call has to reach")
    p.add_argument("--hold", type=int, default=3, help="decision points it must hold that confidence for")
    p.add_argument("--min-turns", type=int, default=4, help="skip games too short to have an endgame")
    p.add_argument("--quiet", action="store_true")
    p.set_defaults(func=cmd_wp_endgames)
    belief = sub.add_parser("belief", help="what the battle reveals about their hidden spread")
    bsub = belief.add_subparsers(dest="belief_cmd", required=True)
    p = with_reg(bsub.add_parser("speed", help="turn order → a bound on their Speed Stat Points"))
    p.add_argument("replay", help="replay id or a JSON file")
    p.add_argument("--known", choices=["p1", "p2"], help="the side whose spread you know")
    p.add_argument("--assume", type=int, default=32, help="Speed SP to assume for --known")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_belief_speed)
    p = with_reg(bsub.add_parser("sp", help="both channels, over the whole 66-point allocation"))
    p.add_argument("replay", help="replay id or a JSON file")
    p.add_argument("--known", choices=["p1", "p2"], default="p1", help="the side whose spreads you know")
    p.add_argument("--team", help="that side's team file — the damage channel needs real spreads")
    p.add_argument("--assume", type=int, default=32,
                   help="Speed SP to assume for --known when no --team is given")
    p.add_argument("--allow-unspent", action="store_true",
                   help="do not assume they spent all 66 points (the format only enforces ≤66)")
    p.add_argument("--no-dead-zero", action="store_true",
                   help="do not assume 0 in a stat no move of theirs scales off")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_belief_sp)
    p = with_reg(bsub.add_parser("sets", help="P(item, ability, nature, moves | species) from usage"))
    p.add_argument("species", nargs="?", help="whose sets to show")
    p.add_argument("--build", action="store_true", help="recount the corpus first")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_belief_sets)

    p = wp.add_parser("registry", help="all WP model versions")
    p.set_defaults(func=cmd_wp_registry)
    p = sub.add_parser("web", help="battle-companion web app on localhost")
    p.add_argument("--host", default="127.0.0.1", help="127.0.0.1 keeps it on this machine")
    p.add_argument("--port", type=int, default=8001, help="8001, not 8000: `vgc server start` uses 8000")
    p.add_argument("--reload", action="store_true", help="reload on source changes (development)")
    p.set_defaults(func=cmd_web)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
