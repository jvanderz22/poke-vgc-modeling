import { useCallback, useEffect, useState } from "react";
import { api, ApiError, modeLabel, sideNames, type BattleRow, type EngineAnswer, type Entry, type LiveView, type Mode, type SavedTeam, type Species, type TrajectoryRow } from "../api";
import { EntryBar } from "./EntryBar";
import { Field } from "./Field";
import { BattleGateBanner } from "./GateBanner";
import { Questions } from "./Questions";
import { SpeciesPicker } from "./SpeciesPicker";
import { linkProps, tabRoute, type Navigate, type Route } from "../router";

type Side = "p1" | "p2";

/** The in-battle companion.
 *
 *  Everything on this page is one shape: **the journal is the battle.** Each tap appends one
 *  entry, the backend replays the list, and what comes back is the truth — so this component
 *  keeps no battle state of its own beyond which Pokémon you have selected. Undo is popping the
 *  tail; walking back to turn 6 is replaying a prefix; the WP curve is replaying every prefix.
 *  One mechanism, and nothing here can drift out of step with it.
 */
export function BattleSession({ reg, route, navigate, teams }: {
  reg: string;
  route: Extract<Route, { tab: "battle" }>;
  navigate: Navigate;
  teams: SavedTeam[];
}) {
  const [view, setView] = useState<LiveView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<{ side: Side; slot: number } | null>(null);

  const id = route.battle;
  const step = route.step;

  useEffect(() => {
    if (!id) { setView(null); return; }
    setBusy(true);
    const load = step === null ? api.battle(id, reg) : api.battleAt(id, step, reg);
    load.then(setView).catch((e) => setError(String(e.message ?? e))).finally(() => setBusy(false));
  }, [id, step, reg]);

  const log = useCallback(async (entries: Entry[]) => {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      // Logging while looking at an earlier turn would append to the end, not to what is on
      // screen — so the first tap brings you back to the present, where the taps belong.
      if (step !== null) navigate({ tab: "battle", battle: id, step: null }, { replace: true });
      setView(await api.log(id, entries, reg));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [id, reg, step, navigate]);

  const undo = useCallback(async () => {
    if (!id) return;
    setBusy(true);
    setSelected(null);
    try { setView(await api.undo(id, reg)); } finally { setBusy(false); }
  }, [id, reg]);

  if (!id) return <BattleList reg={reg} navigate={navigate} teams={teams} />;
  if (!view) return <div className="panel"><p className="small dim">{error ?? "Loading…"}</p></div>;

  return (
    <>
      {error && <div className="banner bad">{error}</div>}
      {view.contradictions.map((c, i) => (
        <div key={i} className="banner warn">
          {c.note} <button className="link" onClick={undo}>undo the last tap</button>
        </div>
      ))}
      {view.errors.length > 0 && (
        <div className="banner bad">
          {view.errors[view.errors.length - 1]} — <button className="link" onClick={undo}>undo that</button>
        </div>
      )}

      <div className="panel battle-head">
        <div className="row" style={{ justifyContent: "space-between" }}>
          <div>
            <b>{view.name}</b>{" "}
            <span className="dim small">
              {modeLabel(view.sheets, view.perspective)} ·{" "}
              {view.started ? `turn ${view.turn}` : "team preview"} · {view.entries} taps
            </span>
          </div>
          <div className="row">
            <button className="ghost" onClick={undo} disabled={busy || view.entries === 0}>Undo</button>
            <a className="ghost tab" {...linkProps(tabRoute("battle"), navigate)}>All battles</a>
          </div>
        </div>
        {/* In a 1v1 the engine leads: on held-out human 1v1s it predicted the winner better than the
            model (PLAN-v3 step 3.6), which stays underneath as the second opinion. */}
        {step === null && view.endgame?.eligible && <EngineRow id={id} reg={reg} view={view} />}
        <WPBar view={view} labelled={step === null && !!view.endgame?.eligible} />
      </div>
      <BattleGateBanner gates={view.gates} verdict={view.verdict} />

      <Questions questions={view.questions} onAnswer={(e) => log([e])} busy={busy} />

      <Field view={view} selected={selected}
             onPick={(side, slot) => setSelected({ side, slot })}
             onHp={(side, slot) => setSelected({ side, slot })} />

      {step === null
        ? <EntryBar view={view} selected={selected} onSelect={setSelected} onLog={log} busy={busy} />
        : <div className="panel"><p className="small dim">
            Looking at tap {step} of {view.entries_total}. Log anything to come back to now.
          </p></div>}

      {!view.started && <Leads view={view} onLog={log} busy={busy} />}

      <Timeline id={id} reg={reg} view={view} route={route} navigate={navigate} />
      <Belief view={view} />
      {view.derived.length > 0 && (
        <div className="panel">
          <h2>Worked out for you</h2>
          <ul className="small dim derived">
            {view.derived.slice(-8).map((d, i) => <li key={i}>{d}</li>)}
          </ul>
        </div>
      )}
    </>
  );
}

/** The number, and how much of it is still guesswork.
 *
 *  Every WP model was trained with the opponent's sets visible — the known-flags are 1.000 across
 *  all 433,052 rows — so a Team Preview Only position is not one any of them has been shown. The
 *  bar is therefore an average over `k` complete opponents drawn from the belief: each draw is a
 *  fully-known position, so each is a row the model knows, and the mean is an estimate of the
 *  expectation over the ones it does not.
 *
 *  The band is the 10th-to-90th spread of those draws, and it is the honest part. A wide band
 *  means their hidden sets decide this position and the single number is not worth much; a narrow
 *  one means it barely matters what they are holding. That is a different thing from the model
 *  being uncertain, and it is the thing you can do something about — every reveal narrows it.
 *
 *  `wp_open` sits beside it rather than being hidden, because the gate would not settle between
 *  them: over 400 held-out games the averaged number beats filling in one guessed set on every
 *  model tried, but whether it beats leaving the opponent unknown depends on which model is
 *  serving (docs/phase8-findings.md). Two numbers that disagree by a point are worth showing as
 *  two numbers.
 */
function WPBar({ view, labelled = false }: { view: LiveView; labelled?: boolean }) {
  // `labelled` is a 1v1, where this is the second number under the engine's.
  const wp = view.wp;
  if (!wp || wp.error) return <p className="tiny dim" style={{ margin: "8px 0 0" }}>{wp?.error ?? ""}</p>;
  const pct = (x: number | undefined) => Math.round((x ?? 0) * 100);
  const open = view.wp?.belief?.filter((b) => b.sets > 1).length ?? 0;
  return (
    <div className={`wp${labelled ? " second" : ""}`}>
      <div className="wp-bar">
        <span className="wp-range" style={{ left: `${pct(wp.lo)}%`, width: `${pct(wp.hi) - pct(wp.lo)}%` }} />
        <span className="wp-mark" style={{ left: `${pct(wp.wp)}%` }} />
      </div>
      <div className="row tiny dim" style={{ justifyContent: "space-between", marginTop: 5 }}>
        <span>
          {labelled && <>Model: </>}<b className="wp-num">{pct(wp.wp)}%</b>{" "}
          {view.perspective === "spectator" ? "P1 wins" : "you win"}
          {wp.hi !== wp.lo && <> · {pct(wp.lo)}–{pct(wp.hi)}% depending on what they are holding</>}
        </span>
        <span title={wp.regime}>
          {wp.wp_open != null && <>{pct(wp.wp_open)}% with their sets left unknown · </>}
          averaged over {wp.k} draws{open > 0 ? ` · ${open} of their six still open` : ""}
        </span>
      </div>
    </div>
  );
}

/** The engine's answer to a 1v1, above the model's, and labelled as a different claim.
 *
 *  The model says how positions like this have gone in human games; the engine says what best
 *  play from both sides is worth, searched a few turns deep on the pinned simulator. In decided
 *  1v1s the model barely moves with the position (docs/phase8-findings.md). The engine assumes best
 *  play, which a ~1100-rated game does not have, but on 174 held-out human 1v1s it was still the
 *  better predictor of who won (PLAN-v3 step 3.6), so it leads and the model sits underneath.
 *  When the two disagree the page says so rather than averaging them.
 *
 *  The search deepens in the background, one turn at a time, and this asks again until it is done.
 *  A shallow answer rests partly on HP share, and the page says how much, but it is not faded: in
 *  the same check the answers resting ~half on HP share still beat the model. */
function EngineRow({ id, reg, view }: { id: string; reg: string; view: LiveView }) {
  const [ans, setAns] = useState<EngineAnswer | null>(null);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = () => api.solve(id, reg).then((a) => {
      if (!live) return;
      setAns(a);
      if (a.eligible && a.searching != null) timer = setTimeout(tick, 2000);
    }).catch(() => { if (live) timer = setTimeout(tick, 5000); });
    setAns(null);
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [id, reg, view]);

  if (!ans) return <div className="engine lead tiny dim">Engine: starting a search…</div>;
  if (!ans.eligible) return ans.reason ? <div className="engine lead tiny dim">Engine: {ans.reason}.</div> : null;
  const pct = (x: number) => Math.round(x * 100);
  const leaf = ans.leaf_mass ?? 1;
  const model = view.wp?.wp;
  const apart = ans.value != null && model != null && Math.abs(ans.value - model) > 0.2;
  const hidden = view.perspective === "spectator" ? (["p1", "p2"] as const) : (["p2"] as const);
  const names = sideNames(view.perspective);
  const sets = hidden.flatMap((sid) => (ans.sets?.[sid] ?? []).map((s) => ({ ...s, side: sid })));
  const guessed = hidden.find((sid) => (ans.sets?.[sid]?.length ?? 0) > 1);
  return (
    <div className="engine lead tiny dim">
      {ans.value != null && (
        <div className="wp-bar">
          <span className="wp-mark" style={{ left: `${pct(ans.value)}%` }} />
        </div>
      )}
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span>
          Engine:{" "}
          {ans.value != null
            ? <><b className="wp-num">{pct(ans.value)}%</b>{" "}
                {view.perspective === "spectator" ? "P1 wins" : "you win"} with best play from both sides</>
            : <>searching…</>}
        </span>
        <span>
          {ans.depth != null && <>searched {ans.depth} of {ans.max_depth} turns</>}
          {ans.depth != null && leaf >= 0.005 && <> · {pct(leaf)}% of it still decided by HP share</>}
          {ans.searching != null && <> · looking {ans.searching} deep ({Math.round(ans.elapsed ?? 0)}s)</>}
        </span>
      </div>
      {guessed && (
        <div>
          Over their {ans.sets?.[guessed]?.length} likeliest sets
          {(ans.unsolved ?? 0) > 0 && <>; {pct(ans.unsolved ?? 0)}% of what they might hold is not solved</>}.
        </div>
      )}
      {apart && (
        <div className="engine-apart">
          The model and the engine are {Math.abs(pct(ans.value!) - pct(model!))} points apart. They answer
          different questions (how games like this have gone, and best play from here). On held-out
          human 1v1s, the engine was the closer of the two.
        </div>
      )}
      {ans.error && <div className="engine-apart">The search failed: {ans.error}</div>}
      <details>
        <summary>What the engine assumes</summary>
        <ul className="derived">
          {(ans.assumptions ?? []).map((a, i) => <li key={i}>{a}</li>)}
          {ans.depth != null && <li>Past {ans.depth} turns, whoever has more HP left is counted as winning.</li>}
          {sets.map((s, i) => (
            <li key={`s${i}`}>
              {hidden.length > 1 && <>{names[s.side]}: </>}{s.weight < 1 && <>{pct(s.weight)}%: </>}
              {s.set.species}{s.set.item ? ` @ ${s.set.item}` : ""}, {s.set.ability}, {s.set.nature ?? "nature unknown"}:{" "}
              {s.set.moves.join(", ")}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

/** Who starts. Four taps and the game is under way — and the arrivals ask their questions in the
 *  order you tap them, which is what turns a lead into Speed evidence before a move is used. */
function Leads({ view, onLog, busy }: { view: LiveView; onLog: (e: Entry[]) => void; busy: boolean }) {
  const [pick, setPick] = useState<{ side: Side; slot: number } | null>(null);
  const filled = (side: Side, slot: number) =>
    view.sides[side].mons.find((m) => m.state === "active" && m.slot === slot);
  return (
    <div className="panel">
      <h2>Leads</h2>
      <div className="lead-grid">
        {(["p2", "p1"] as Side[]).map((side) => (
          <div key={side} className="row">
            <span className="dim small" style={{ width: 54 }}>{sideNames(view.perspective)[side]}</span>
            {[0, 1].map((slot) => {
              const m = filled(side, slot);
              return (
                <button key={slot} className="ghost" disabled={busy}
                        onClick={() => setPick(pick?.side === side && pick.slot === slot ? null : { side, slot })}>
                  {m ? m.species : `slot ${slot + 1}`}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {pick && (
        <div className="pad" style={{ marginTop: 8 }}>
          {view.sides[pick.side].mons
            .filter((m) => m.state !== "active" && m.state !== "fainted")
            .map((m) => (
              <button key={m.species} className="ghost" disabled={busy}
                      onClick={() => {
                        onLog([{ kind: "lead", side: pick.side, slot: pick.slot, species: m.species }]);
                        setPick(null);
                      }}>
                {m.species}
              </button>
            ))}
        </div>
      )}
      <p className="tiny dim" style={{ margin: "8px 2px 0" }}>
        Log them in the order they arrived on screen. Abilities announce in Speed order, so that
        order is a Speed comparison you have before anyone has moved.
      </p>
    </div>
  );
}

/** Every turn of the game so far, and the call at each one. Clicking a row replays the journal to
 *  that point — the same operation as undo, without throwing anything away. */
function Timeline({ id, reg, view, route, navigate }: {
  id: string; reg: string; view: LiveView;
  route: Extract<Route, { tab: "battle" }>; navigate: Navigate;
}) {
  const [rows, setRows] = useState<TrajectoryRow[] | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    api.trajectory(id, reg).then((t) => setRows(t.turns)).catch(() => setRows([]));
  }, [id, reg, open, view.entries]);

  if (!open) {
    return (
      <div className="panel">
        <button className="link" onClick={() => setOpen(true)}>Walk the game back, turn by turn →</button>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>Turn by turn</h2>
        <button className="link" onClick={() => setOpen(false)}>hide</button>
      </div>
      {rows === null && <p className="small dim">Replaying…</p>}
      {rows?.length === 0 && <p className="small dim">Nothing to walk back yet.</p>}
      <div className="trajectory">
        {rows?.map((row, i) => (
          <button key={i} className={`traj-row${route.step === row.index ? " on" : ""}`}
                  onClick={() => navigate({ tab: "battle", battle: id, step: row.index }, { replace: true })}>
            <span className="traj-turn">turn {row.turn}</span>
            <span className="traj-bar">
              <span className="wp-range"
                    style={{ left: `${Math.round(row.lo * 100)}%`,
                             width: `${Math.round((row.hi - row.lo) * 100)}%` }} />
              <span className="wp-mark" style={{ left: `${Math.round(row.wp * 100)}%` }} />
            </span>
            <span className="traj-wp">{Math.round(row.wp * 100)}%</span>
            <span className="traj-left dim tiny">{row.left.p1}v{row.left.p2}</span>
          </button>
        ))}
      </div>
      {route.step !== null && (
        <button className="link" style={{ marginTop: 8 }}
                onClick={() => navigate({ tab: "battle", battle: id, step: null }, { replace: true })}>
          back to now
        </button>
      )}
    </div>
  );
}

/** What is still open about their six — the two halves of the belief, side by side, because they
 *  are different kinds of claim and the screen should not blur them.
 *
 *  **The spread** is a bound from this battle's own turn orders: `ruled out` is the share of the
 *  66-point space that is gone, and 0% means nothing has been seen yet rather than something
 *  missing. It can only be wrong because of a bug.
 *
 *  **The set** is a ranking from 15,000 other people's sheets: `sets left` is how many of them are
 *  still consistent with what you have seen, and it falls as items and moves reveal themselves.
 *  It can be wrong because somebody brought something unusual — and when nobody's sheet matches
 *  at all, it says so rather than pretending.
 */
function Belief({ view }: { view: LiveView }) {
  const spreads = new Map(view.beliefs.map((b) => [b.species, b]));
  const rows = view.wp?.belief ?? [];
  if (!rows.length && !view.beliefs.some((b) => b.narrowed > 0)) return null;
  return (
    <div className="panel">
      <h2>What is still open about their six</h2>
      <table className="belief">
        <thead>
          <tr className="tiny dim">
            <th>Pokémon</th><th>spread (a bound)</th><th>ruled out</th>
            <th>set (a ranking)</th><th>from</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((b) => {
            const sp = spreads.get(b.species);
            const bounds = sp
              ? Object.entries(sp.bounds)
                  .filter(([st, [lo, hi]]) => st !== "_unspent" && (lo > 0 || hi < 32))
                  .map(([st, [lo, hi]]) => `${st} ${lo}–${hi}`).join(" · ")
              : "";
            return (
              <tr key={b.species}>
                <td>{b.species}</td>
                <td className="tiny dim">{bounds || "nothing seen yet"}</td>
                <td className="tiny dim">{sp ? `${Math.round(sp.narrowed * 100)}%` : "—"}</td>
                <td className="tiny dim">
                  {b.off_meta
                    ? <span className="warn-text">nobody&apos;s sheet matches</span>
                    : <>{b.sets} set{b.sets === 1 ? "" : "s"} left
                        {b.sets > 1 && <> · top {Math.round(b.concentration * 100)}%</>}</>}
                </td>
                <td className="tiny dim">
                  {[...(sp ? Object.values(sp.sources) : []), ...b.evidence].join(", ") || "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="tiny dim" style={{ margin: "8px 2px 0" }}>
        The spread is a bound — the truth is inside it. The set is a ranking — it can be wrong,
        and being wrong costs a tap.
      </p>
    </div>
  );
}

/** The new-battle form's mode buttons, by `Mode.id`. */
const MODE_BUTTONS: Record<string, string> = {
  closed: "Hidden (team preview only)", open: "Open", watching: "Watching (both open)",
};

/** Your battles, and the form that starts one. */
function BattleList({ reg, navigate, teams }: { reg: string; navigate: Navigate; teams: SavedTeam[] }) {
  const [rows, setRows] = useState<BattleRow[]>([]);
  const [pool, setPool] = useState<Species[]>([]);
  const [teamId, setTeamId] = useState(teams[0]?.id ?? "");
  const [modes, setModes] = useState<Mode[]>([]);
  const [modeId, setModeId] = useState<string | null>(null);
  const [theirs, setTheirs] = useState<(string | null)[]>(Array(6).fill(null));
  const [theirSheet, setTheirSheet] = useState("");
  const [p1Sheet, setP1Sheet] = useState("");
  const mode = modes.find((m) => m.id === modeId);
  const sheets = mode?.sheets ?? null;
  const watching = mode?.perspective === "spectator";
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.battles(reg).then((r) => setRows(r.battles)).catch(() => {});
  }, [reg]);

  useEffect(() => { refresh(); api.pool(reg).then((p) => setPool(p.species)).catch(() => {}); }, [reg, refresh]);
  // The modes on offer are the backend's, so the form cannot offer one it has no model for.
  useEffect(() => {
    api.models(reg).then((m) => {
      setModes(m.modes);
      setModeId((cur) => cur ?? m.modes.find((x) => x.version)?.id ?? null);
    }).catch(() => {});
  }, [reg]);
  useEffect(() => { if (!teamId && teams[0]) setTeamId(teams[0].id); }, [teams, teamId]);

  const chosen = theirs.filter(Boolean) as string[];
  const taken = new Set(chosen);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const opponent = sheets === "open" ? { their_team: theirSheet } : { their_species: chosen };
      const v = watching
        ? await api.newBattle({ name, p1_team: p1Sheet, their_team: theirSheet, regulation: reg })
        : await api.newBattle({ name, team_id: teamId, regulation: reg, ...opponent });
      navigate({ tab: "battle", battle: v.id, step: null });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="panel">
        <h2>Start a battle</h2>
        <div className="row" style={{ marginBottom: 10 }}>
          <span className="dim small">Team sheets</span>
          {modes.map((m) => (
            <button key={m.id} className={modeId === m.id ? "" : "ghost"}
                    aria-pressed={modeId === m.id} disabled={!m.version}
                    title={m.version ? `win probability from ${m.version}` : "no model for this mode"}
                    onClick={() => setModeId(m.id)}>
              {MODE_BUTTONS[m.id] ?? m.id}
            </button>
          ))}
        </div>
        {!watching && teams.length === 0
          ? <p className="small dim">Save a team on the <b>Teams</b> page first, or watch someone else's game.</p>
          : (
            <>
              <div className="row" style={{ marginBottom: 10 }}>
                {!watching && (
                  <select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
                    {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                )}
                <input type="text" value={name} placeholder="what to call it (optional)"
                       style={{ flex: 1 }} onChange={(e) => setName(e.target.value)} />
              </div>
              {sheets === "closed" ? (
                <>
                  <label>Their six, from team preview</label>
                  <div className="slots">
                    {theirs.map((s, i) => (
                      <SpeciesPicker key={i} value={s} placeholder={`Pokémon ${i + 1}`}
                                     pool={s ? pool : pool.filter((x) => !taken.has(x.name))}
                                     onPick={(n) => setTheirs(theirs.map((x, j) => (j === i ? n : x)))} />
                    ))}
                  </div>
                </>
              ) : (
                <>
                  {watching && (
                    <>
                      <label>Player 1's open team sheet, as Showdown text</label>
                      <textarea value={p1Sheet} rows={8} style={{ width: "100%" }}
                                placeholder={"Rillaboom @ Miracle Seed\nAbility: Grassy Surge\nAdamant Nature\n- Fake Out\n..."}
                                onChange={(e) => setP1Sheet(e.target.value)} />
                    </>
                  )}
                  <label>{watching ? "Player 2's" : "Their"} open team sheet, as Showdown text</label>
                  <textarea value={theirSheet} rows={watching ? 8 : 10} style={{ width: "100%" }}
                            placeholder={"Incineroar @ Sitrus Berry\nAbility: Intimidate\nCareful Nature\n- Fake Out\n..."}
                            onChange={(e) => setTheirSheet(e.target.value)} />
                </>
              )}
              <div className="row" style={{ marginTop: 10 }}>
                <button disabled={busy || !sheets || (watching ? !(p1Sheet.trim() && theirSheet.trim())
                                  : !teamId || (sheets === "closed" ? chosen.length !== 6 : !theirSheet.trim()))}
                        onClick={start}>Start</button>
                {sheets === "closed" && <span className="tiny dim">{chosen.length}/6 chosen</span>}
              </div>
              {error && <p className="small err" style={{ marginBottom: 0 }}>{error}</p>}
              <p className="tiny dim" style={{ margin: "10px 2px 0" }}>
                {watching
                  ? <>Someone else's game: both sheets are shown and neither side's Stat Points are, so
                      both Speeds are worked out from the battle, and the number is player 1's chance.</>
                  : sheets === "closed"
                  ? <>Six species is all team preview gives you, and it is all this needs. Everything
                      else — their items, their abilities, how they built each one — is what the
                      battle is going to tell you, one tap at a time.</>
                  : <>An open sheet shows their items, abilities, moves and natures — but not their
                      Stat Points, so their Speed and damage are still worked out from the battle.</>}
                {" "}The win probability comes from the model chosen for this kind of battle.
              </p>
            </>
          )}
      </div>

      {rows.length > 0 && (
        <div className="panel">
          <h2>Your battles</h2>
          <div className="battle-rows">
            {rows.map((b) => (
              <a key={b.id} className="battle-row"
                 {...linkProps({ tab: "battle", battle: b.id, step: null }, navigate)}>
                <span><b>{b.name}</b> <span className="dim tiny">{b.updated.replace("T", " ")}</span></span>
                <span className="dim tiny">{b.theirs.join(" · ")}</span>
                <span className="dim tiny">
                  {modeLabel(b.sheets, b.perspective)} · {b.turn ? `turn ${b.turn}` : "preview"} · {b.entries} taps
                </span>
                <button className="danger" onClick={async (e) => {
                  e.preventDefault();
                  await api.deleteBattle(b.id, reg);
                  refresh();
                }}>delete</button>
              </a>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
