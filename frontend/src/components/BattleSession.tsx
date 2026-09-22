import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type BattleRow, type Entry, type LiveView, type SavedTeam, type Species, type TrajectoryRow } from "../api";
import { EntryBar } from "./EntryBar";
import { Field } from "./Field";
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
              {view.started ? `turn ${view.turn}` : "team preview"} · {view.entries} taps
            </span>
          </div>
          <div className="row">
            <button className="ghost" onClick={undo} disabled={busy || view.entries === 0}>Undo</button>
            <a className="ghost tab" {...linkProps(tabRoute("battle"), navigate)}>All battles</a>
          </div>
        </div>
        <WPBar view={view} />
      </div>

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

/** Two numbers, and the gap between them said out loud.
 *
 *  Every WP model was trained with the opponent's sets visible, so a Team Preview Only position is
 *  not one any of them has been shown. The headline fills their unknowns from usage, which keeps
 *  the input in the distribution the model knows; the second leaves them unknown, which is the
 *  true position. When they disagree, the difference is the size of the guess — and a number
 *  shown without that is a number that will be believed more than it has earned.
 */
function WPBar({ view }: { view: LiveView }) {
  const wp = view.wp;
  if (!wp || wp.error) return <p className="tiny dim" style={{ margin: "8px 0 0" }}>{wp?.error ?? ""}</p>;
  const pct = Math.round((wp.wp ?? 0) * 100);
  const open = Math.round((wp.wp_open ?? 0) * 100);
  return (
    <div className="wp">
      <div className="wp-bar"><span className="wp-fill" style={{ width: `${pct}%` }} /></div>
      <div className="row tiny dim" style={{ justifyContent: "space-between", marginTop: 4 }}>
        <span><b className="wp-num">{pct}%</b> you win, against their most likely sets</span>
        <span title={wp.regime}>
          {open}% with their sets left unknown — the gap is the size of the guess
        </span>
      </div>
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
            <span className="dim small" style={{ width: 54 }}>{side === "p1" ? "You" : "Them"}</span>
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
            <span className="traj-bar"><span className="wp-fill" style={{ width: `${Math.round(row.wp * 100)}%` }} /></span>
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

/** What has been worked out about their spreads. This is a *bound*, not an estimate: `narrowed`
 *  is the share of the 66-point space ruled out, and a Pokémon nothing has been seen from reads
 *  0% because nothing has been seen from it — which is the honest number, not a missing one. */
function Belief({ view }: { view: LiveView }) {
  const seen = view.beliefs.filter((b) => b.narrowed > 0);
  if (!seen.length) return null;
  return (
    <div className="panel">
      <h2>What their spreads can still be</h2>
      <table className="belief">
        <tbody>
          {seen.map((b) => (
            <tr key={b.species}>
              <td>{b.species}</td>
              <td className="tiny dim">
                {Object.entries(b.bounds)
                  .filter(([s, [lo, hi]]) => s !== "_unspent" && (lo > 0 || hi < 32))
                  .map(([s, [lo, hi]]) => `${s} ${lo}–${hi}`)
                  .join(" · ") || "—"}
              </td>
              <td className="tiny dim">{Math.round(b.narrowed * 100)}% ruled out</td>
              <td className="tiny dim">{Object.values(b.sources).join(", ")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Your battles, and the form that starts one. */
function BattleList({ reg, navigate, teams }: { reg: string; navigate: Navigate; teams: SavedTeam[] }) {
  const [rows, setRows] = useState<BattleRow[]>([]);
  const [pool, setPool] = useState<Species[]>([]);
  const [teamId, setTeamId] = useState(teams[0]?.id ?? "");
  const [theirs, setTheirs] = useState<(string | null)[]>(Array(6).fill(null));
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api.battles(reg).then((r) => setRows(r.battles)).catch(() => {});
  }, [reg]);

  useEffect(() => { refresh(); api.pool(reg).then((p) => setPool(p.species)).catch(() => {}); }, [reg, refresh]);
  useEffect(() => { if (!teamId && teams[0]) setTeamId(teams[0].id); }, [teams, teamId]);

  const chosen = theirs.filter(Boolean) as string[];
  const taken = new Set(chosen);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const v = await api.newBattle({ name, team_id: teamId, their_species: chosen, regulation: reg });
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
        {teams.length === 0
          ? <p className="small dim">Save a team on the <b>Teams</b> page first.</p>
          : (
            <>
              <div className="row" style={{ marginBottom: 10 }}>
                <select value={teamId} onChange={(e) => setTeamId(e.target.value)}>
                  {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
                <input type="text" value={name} placeholder="what to call it (optional)"
                       style={{ flex: 1 }} onChange={(e) => setName(e.target.value)} />
              </div>
              <label>Their six, from team preview</label>
              <div className="slots">
                {theirs.map((s, i) => (
                  <SpeciesPicker key={i} value={s} placeholder={`Pokémon ${i + 1}`}
                                 pool={s ? pool : pool.filter((x) => !taken.has(x.name))}
                                 onPick={(n) => setTheirs(theirs.map((x, j) => (j === i ? n : x)))} />
                ))}
              </div>
              <div className="row" style={{ marginTop: 10 }}>
                <button disabled={busy || chosen.length !== 6 || !teamId} onClick={start}>Start</button>
                <span className="tiny dim">{chosen.length}/6 chosen</span>
              </div>
              {error && <p className="small err" style={{ marginBottom: 0 }}>{error}</p>}
              <p className="tiny dim" style={{ margin: "10px 2px 0" }}>
                Six species is all team preview gives you, and it is all this needs. Everything
                else — their items, their abilities, how they built each one — is what the battle
                is going to tell you, one tap at a time.
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
                <span className="dim tiny">{b.turn ? `turn ${b.turn}` : "preview"} · {b.entries} taps</span>
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
