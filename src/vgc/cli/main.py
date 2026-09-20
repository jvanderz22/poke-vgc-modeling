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
    path = pool.save_pool(reg, teams, tag=args.tag)
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
    teams = [(t.id, t.text) for t in load_pool(reg)]
    ms = gauntlet_matchups(teams, args.n, args.policy_a, args.policy_b, seed=args.seed)
    _print_run(run(ms, reg_id=reg.id, seed=args.seed, workers=args.workers, run_id=args.run_id))
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
    run_id = args.run_id or f"gen-{args.policy_a}-{args.policy_b}-s{args.seed}{suffix}"
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
        pv = out / "preview_vs_sim.json"
        g = evaluate.gates(main_r, all_results, json.loads(pv.read_text()) if pv.exists() else None)
        models.update_card(reg.id, args.version, headline=evaluate.headline(main_r), gates=g)
        failed = [k for k, v in g.items() if isinstance(v, dict) and v.get("pass") is False]
        print(f"gates: {'all pass' if g['all_pass'] else 'FAILED ' + ', '.join(failed)}")
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


def cmd_wp_check_preview(args: argparse.Namespace) -> int:
    from vgc.wp.tools import preview_vs_sim

    r = preview_vs_sim(_reg(args), args.version, pairs=args.pairs, n=args.n, seed=args.seed, workers=args.workers)
    for key in ("all", "train_teams", "heldout_team"):
        print(f"{key:13} {json.dumps(r[key])}")
    out = Path(f"models/wp/{args.regulation}/{args.version}/preview_vs_sim.json")
    out.write_text(json.dumps(r, indent=1) + "\n")
    print(f"→ {out}")
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
    current = max((e.get("manifest_sha256") for e in reg["wp"] if e.get("manifest_sha256")),
                  key=lambda s: max(e["created"] for e in reg["wp"] if e.get("manifest_sha256") == s), default=None)
    for e in reg["wp"]:
        h = e.get("headline", {}).get("human_spectator", {})
        tail = f"human spectator logloss {h['logloss']:.4f} ece {h['ece']:.4f}" if h else "(not evaluated)"
        # Gate status, so nobody reads a good-looking log loss and assumes the model is usable.
        g = e.get("gates")
        if g:
            failed = [k for k, v in g.items() if isinstance(v, dict) and v.get("pass") is False]
            tail += "  gates: all pass" if g.get("all_pass") else f"  gates: FAIL ({', '.join(failed)})"
        if h and current and e.get("manifest_sha256") != current:
            tail += "  [scored on an earlier dataset — not comparable]"
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
    p.add_argument("--tag", default="", help="suffix, to keep an earlier pool of the same date")
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
    p = with_reg(wp.add_parser("check-preview", help="preview WP vs simulated win rate for team pairings"))
    p.add_argument("--version", required=True)
    p.add_argument("--pairs", type=int, default=30)
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--workers", type=int, default=6)
    p.set_defaults(func=cmd_wp_check_preview)
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
