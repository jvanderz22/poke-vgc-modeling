import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, modeLabel, sideNames, type BattleRow, type EngineAnswer, type Entry, type LiveView, type Mode, type SavedTeam, type Species, type TrajectoryRow } from "../api";
import { EntryBar } from "./EntryBar";
import { Field } from "./Field";
import { BattleGateBanner } from "./GateBanner";
import { BattleState } from "./BattleState";
import { Info } from "./Popover";
import { SpeciesPicker } from "./SpeciesPicker";
import { TurnStepper } from "./TurnStepper";
import type { Pos } from "../screen";
import { linkProps, tabRoute, type Navigate, type Route } from "../router";

type Side = "p1" | "p2";

/** The in-battle companion.
 *
 *  Everything on this page is one shape: **the journal is the battle.** Each tap appends one
 *  entry, the backend replays the list, and what comes back is the truth — so this component
 *  keeps no battle state of its own beyond the action half-entered in front of you. Undo is popping the
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
  // The free-form entry bar, behind "something else…": the escape hatch for whatever the stepper
  // does not ask about. Clicking the field goes to whichever of the two is in front.
  const [freeForm, setFreeForm] = useState(false);
  const fieldPick = useRef<((p: Pos) => void) | null>(null);

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

  // Which four you brought, until you have said: the page cannot see your back.
  const yourFour = view.perspective !== "spectator" &&
    !view.sides[view.perspective].mons.some((m) => m.state === "not_brought");

  return (
    <div className="battle-page">
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

      <div className="battle-bar">
        <div>
          <b>{view.name}</b>{" "}
          <span className="dim small">
            {modeLabel(view.sheets, view.perspective)} · {view.started ? `turn ${view.turn}` : "team preview"}
          </span>
        </div>
        <a className="ghost tab" {...linkProps(tabRoute("battle"), navigate)}>All battles</a>
      </div>

      {/* The row where you work: the stepper where the eye lands first, and beside it the number
          and the four on the field, which is everything its questions need. */}
      <div className="battle-top">
        <div className="battle-work">
          {step === null
            ? <TurnStepper id={id} reg={reg} view={view} busy={busy} onLog={log} onUndo={undo}
                           onWalk={(index) => navigate({ tab: "battle", battle: id, step: index }, { replace: true })}
                           onSomethingElse={() => setFreeForm((f) => !f)} fieldPick={fieldPick}
                           preview={<>
                             {yourFour && <YourFour view={view} onLog={log} busy={busy} />}
                             <Leads view={view} onLog={log} busy={busy} />
                           </>} />
            : <div className="panel row" style={{ justifyContent: "space-between" }}>
                <span className="small dim">Looking at tap {step} of {view.entries_total}. Log anything to come back to now.</span>
                <button className="link" onClick={() => navigate({ tab: "battle", battle: id, step: null }, { replace: true })}>
                  back to now
                </button>
              </div>}

          {step === null && freeForm && (
            <>
              <div className="row free-form-head">
                <span className="tiny dim">Something else: the free-form entry bar</span>
                <button className="link" onClick={() => { setFreeForm(false); setSelected(null); }}>close (Esc)</button>
              </div>
              <EntryBar view={view} selected={selected} onSelect={setSelected} onLog={log} busy={busy} />
            </>
          )}
        </div>

        <div className="battle-side">
          <div className="panel win-panel">
            <h2>Win chance</h2>
            {/* In a 1v1 the engine leads: on held-out human 1v1s it predicted the winner better than the
                model (PLAN-v3 step 3.6), which stays underneath as the second opinion. */}
            {step === null && view.endgame?.eligible && <EngineRow id={id} reg={reg} view={view} />}
            <WPBar view={view} labelled={step === null && !!view.endgame?.eligible} />
            <TurnCurve id={id} reg={reg} view={view} route={route} navigate={navigate} />
          </div>
          <Field view={view} selected={freeForm ? selected : null}
                 onPick={(side, slot) => freeForm ? setSelected({ side, slot }) : fieldPick.current?.({ side, slot })}
                 onHp={(side, slot) => freeForm ? setSelected({ side, slot }) : fieldPick.current?.({ side, slot })} />
        </div>
      </div>

      <BattleState view={view} extra={{
        p1: view.started && yourFour && step === null ? <YourFour view={view} onLog={log} busy={busy} /> : undefined,
        p2: view.derived.length > 0 ? (
          <details className="worked-out">
            <summary className="tiny dim">Worked out for you ({view.derived.length})</summary>
            <ul className="tiny dim derived">
              {view.derived.slice(-8).map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          </details>
        ) : undefined,
      }} />
    </div>
  );
}

/** The number, and how much of it is still guesswork.
 *
 *  The number is the position as shown: what their sheet or this battle has not revealed is left
 *  unknown, which is how the closed-sheet rows the models were trained on look. On 1,025 held-out
 *  closed-sheet games that beat averaging over opponents drawn from the belief
 *  (docs/phase8-findings.md, "which number leads on a closed sheet").
 *
 *  The band is still drawn: the 10th-to-90th spread of `k` complete opponents, and it is the honest
 *  part. A wide band means their hidden sets decide this position and the single number is not
 *  worth much; a narrow one means it barely matters what they are holding. That is a different
 *  thing from the model being uncertain, and it is the thing you can do something about: every
 *  reveal narrows it. The draws' mean sits beside the number for comparison.
 */
function WPBar({ view, labelled = false }: { view: LiveView; labelled?: boolean }) {
  // `labelled` is a 1v1, where this is the second number under the engine's.
  const wp = view.wp;
  if (!wp || wp.error) return <p className="tiny dim" style={{ margin: "8px 0 0" }}>{wp?.error ?? ""}</p>;
  const pct = (x: number | undefined) => Math.round((x ?? 0) * 100);
  const open = view.wp?.belief?.filter((b) => b.sets > 1).length ?? 0;
  return (
    <div className={`wp${labelled ? " second" : ""}`}>
      <div className="row wp-line">
        <span>
          {labelled && <>Model: </>}<b className="wp-num big">{pct(wp.wp)}%</b>{" "}
          <span className="small">{view.perspective === "spectator" ? "P1 wins" : "you win"}</span>
        </span>
        <GateTag view={view}>
          {wp.drawn != null && Math.abs(wp.drawn - (wp.wp ?? 0)) >= 0.005 &&
            <p>{pct(wp.drawn)}% averaged over {wp.k} drawn sets.</p>}
          <p>{open > 0 ? `${open} of their six still open.` : `Band from ${wp.k} draws.`} {wp.regime}</p>
        </GateTag>
      </div>
      <div className="wp-bar">
        <span className="wp-range" style={{ left: `${pct(wp.lo)}%`, width: `${pct(wp.hi) - pct(wp.lo)}%` }} />
        <span className="wp-mark" style={{ left: `${pct(wp.wp)}%` }} />
      </div>
      {wp.hi !== wp.lo && <div className="tiny dim" style={{ marginTop: 4 }}>{pct(wp.lo)}–{pct(wp.hi)}%: depends on their sets</div>}
    </div>
  );
}

/** The model's verdict as a word beside its number, and the sentence behind it one click away.
 *  PLAN.md's rule does not bend for layout: a model that misses a gate is never shown without the
 *  verdict beside it. Only the reasoning moves. */
function GateTag({ view, children }: { view: LiveView; children?: ReactNode }) {
  const v = view.verdict;
  const tag = !view.gates?.known ? ["no model", "bad"]
    : v?.pass ? ["verified", "ok"]
    : v && v.failed.length > 0 ? ["rough guide", "warn"]
    : ["not verified", "warn"];
  return (
    <span className="row gate-tag-row">
      <span className={`gate-tag ${tag[1]}`}>{tag[0]}</span>
      <Info label="How this number was made">
        <BattleGateBanner gates={view.gates} verdict={view.verdict} />
        {children}
      </Info>
    </span>
  );
}

/** The engine's answer to a 1v1, above the model's, and labelled as a different claim.
 *
 *  The model says how positions like this have gone in human games; the engine says what best
 *  play from both sides is worth, searched a few turns deep on the pinned simulator. In decided
 *  1v1s the model barely moves with the position (docs/phase8-findings.md). The engine assumes best
 *   play, which a ~1100-rated game does not have, but on 192 held-out human 1v1s it was still the
 *   better predictor of who won (PLAN-v3 steps 3.6 and 8), so it leads and the model sits underneath.
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
      // A doubles answer has seconds, not minutes: ask again often enough to show it as it lands.
      // The guesses at their bulk land after the answer, so keep asking until they have.
      if (a.eligible && (a.searching != null || a.guesses?.pending)) timer = setTimeout(tick, a.kind ? 500 : 2000);
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
  // Above two a side, after the first faint: the model and the policy's value combined.
  const policy = ans.mode === "policy";
  // Two or fewer a side: answered within seconds, one turn deep, a damage race past it.
  const doubles = !!ans.kind && !policy;
  const forced = (ans.positions ?? []).find((p) => p.forced)?.forced;
  const horizon = doubles ? "the damage race" : "HP share";
  // The answer under three guesses at a hidden spread's other points: shown as a range on the bar
  // and spelled out, because the guess moves the answer and none of the three is the better one.
  const guesses = ans.guesses?.values ?? [];
  const lo = guesses.length > 1 ? Math.min(...guesses.map((g) => g.value)) : null;
  const hi = guesses.length > 1 ? Math.max(...guesses.map((g) => g.value)) : null;
  return (
    <div className="engine lead tiny dim">
      {ans.value != null && (
        <div className="wp-bar">
          {lo != null && hi != null &&
            <span className="wp-range" style={{ left: `${pct(lo)}%`, width: `${Math.max(1, pct(hi) - pct(lo))}%` }} />}
          <span className="wp-mark" style={{ left: `${pct(ans.value)}%` }} />
        </div>
      )}
      <div className="row" style={{ justifyContent: "space-between" }}>
        <span>
          {policy ? "Model + policy:" : "Engine:"}{" "}
          {ans.value != null
            ? <><b className="wp-num">{pct(ans.value)}%</b>{" "}
                {view.perspective === "spectator" ? "P1 wins" : "you win"}
                {policy ? <> (the policy's one-turn value {pct(ans.policy ?? 0)}%, the model {pct(ans.model ?? 0)}%)</>
                        : <> with best play from both sides</>}</>
            : <>searching…</>}
        </span>
        <span>
          {!doubles && !policy && ans.depth != null && <>searched {ans.depth} of {ans.max_depth} turns</>}
          {policy && ans.depth != null && <>one turn, {ans.kind}</>}
          {doubles && ans.depth === 0 && <>quick read</>}
          {doubles && ans.depth === 1 && <>searched one turn
            {ans.searched != null && ans.searched < (ans.positions?.length ?? 0) &&
              <> ({ans.searched} of {ans.positions?.length} move orders in time)</>}</>}
          {!policy && ans.depth != null && leaf >= 0.005 && <> · {pct(leaf)}% of it still decided by {horizon}</>}
          {ans.searching != null && (doubles || policy
            ? <> · searching ({Math.round(ans.elapsed ?? 0)}s)</>
            : <> · looking {ans.searching} deep ({Math.round(ans.elapsed ?? 0)}s)</>)}
        </span>
      </div>
      {forced && (
        <div>
          {names[forced.side] === "Them" ? "They" : names[forced.side]} can force the win this turn
          {forced.through_protect ? ", once Protect runs out" : ""}
          {forced.sweep < 0.995 && <> ({pct(forced.sweep)}%: a crit or a flinch is the way out)</>}.
        </div>
      )}
      {doubles && ans.guesses?.pending && guesses.length < 2 && ans.value != null && (
        <div>Trying two other guesses at {hidden.length > 1 ? "the hidden" : "their"} bulk…</div>
      )}
      {guesses.length > 1 && (
        <div>
          {hidden.length > 1 ? "The hidden spreads' other points are" : "Their other Stat Points are"} a guess:{" "}
          {guesses.map((g, i) => (
            <span key={g.key}>{i > 0 && " · "}{g.label} <b>{pct(g.value)}%</b>{g.key === "assumed" && " (shown)"}</span>
          ))}.
        </div>
      )}
      {guessed && (
        <div>
          Over their {ans.sets?.[guessed]?.length} likeliest sets
          {(ans.unsolved ?? 0) > 0 && <>; {pct(ans.unsolved ?? 0)}% of what they might hold is not solved</>}.
        </div>
      )}
      {apart && (
        <div className="engine-apart">
          {policy
            ? <>The combined number and the model's are {Math.abs(pct(ans.value!) - pct(model!))} points
                apart: the policy reads this position differently from how games like it have gone. On
                held-out human games after the first faint, the two combined beat the model alone.</>
            : <>The model and the engine are {Math.abs(pct(ans.value!) - pct(model!))} points apart. They
                answer different questions (how games like this have gone, and best play from here). On
                held-out human {doubles ? "endgames with two or fewer a side" : "1v1s"}, the engine was the
                closer of the two.</>}
        </div>
      )}
      {ans.error && <div className="engine-apart">The search failed: {ans.error}</div>}
      <details>
        <summary>What the engine assumes</summary>
        <ul className="derived">
          {(ans.assumptions ?? []).map((a, i) => <li key={i}>{a}</li>)}
          {!doubles && ans.depth != null && <li>Past {ans.depth} turns, whoever has more HP left is counted as winning.</li>}
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
/** Which four you brought. The page cannot see your back, and the number above two a side needs
 *  it: once a Pokémon has fainted it is the model and the policy's value of the position combined,
 *  and that value counts what you have in reserve. It also puts the model's input where it was
 *  trained, with the two you left out marked as not brought. */
function YourFour({ view, onLog, busy }: { view: LiveView; onLog: (e: Entry[]) => void; busy: boolean }) {
  const me = view.perspective as Side;
  const mons = view.sides[me].mons;
  // Whoever has already been on the field was brought.
  const [pick, setPick] = useState<string[]>(() => mons.filter((m) => m.state !== "unrevealed").map((m) => m.species));
  const toggle = (sp: string) => setPick(pick.includes(sp) ? pick.filter((x) => x !== sp) : [...pick, sp]);
  return (
    <div className="panel">
      <h2>Your four</h2>
      <div className="pad">
        {mons.map((m) => (
          <button key={m.species} className="ghost" aria-pressed={pick.includes(m.species)} disabled={busy}
                  onClick={() => toggle(m.species)}>
            {m.species}
          </button>
        ))}
        <button disabled={busy || pick.length !== 4}
                onClick={() => onLog([{ kind: "bring", side: me, species: pick }])}>
          These four
        </button>
      </div>
      <p className="tiny dim" style={{ margin: "8px 2px 0" }}>
        Once a Pokémon has fainted, with more than two a side, the number combines the model with the
        policy's one-turn value of the position, which counts the Pokémon in your back.
      </p>
    </div>
  );
}

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
            .filter((m) => m.state !== "active" && m.state !== "fainted" && m.state !== "not_brought")
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
        Any order: the four leads arrive together, so the order you log them in says nothing.
        What does is the order their abilities announce in, which is Speed order, so answer those
        in the order you saw them.
      </p>
    </div>
  );
}

/** The number at every turn so far, as a curve you can click. Clicking a turn replays the journal
 *  to that point: the same operation as undo, without throwing anything away. Asked for on demand,
 *  because it replays every prefix through the model. */
function TurnCurve({ id, reg, view, route, navigate }: {
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
    return <button className="link small curve-open" onClick={() => setOpen(true)}>Each turn so far →</button>;
  }
  return (
    <div className="curve">
      <div className="row tiny dim" style={{ justifyContent: "space-between" }}>
        <span>Each turn so far: click one to walk back to it</span>
        <span className="row">
          {route.step !== null && (
            <button className="link tiny" onClick={() => navigate({ tab: "battle", battle: id, step: null }, { replace: true })}>
              back to now
            </button>
          )}
          <button className="link tiny" onClick={() => setOpen(false)}>hide</button>
        </span>
      </div>
      {rows === null && <p className="tiny dim">Replaying…</p>}
      {rows?.length === 0 && <p className="tiny dim">Nothing to walk back yet.</p>}
      {!!rows?.length && (
        <div className="curve-bars">
          {rows.map((row) => (
            <button key={row.index} className="curve-bar" aria-current={route.step === row.index}
                    title={`turn ${row.turn}: ${Math.round(row.wp * 100)}% (${row.left.p1}v${row.left.p2})`}
                    onClick={() => navigate({ tab: "battle", battle: id, step: row.index }, { replace: true })}>
              <i style={{ height: `${Math.max(2, Math.round(row.wp * 100))}%` }} />
              <span className="curve-turn">{row.turn}</span>
            </button>
          ))}
        </div>
      )}
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
