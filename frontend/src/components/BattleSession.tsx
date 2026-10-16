import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, modeLabel, sideNames, type BattleRow, type EngineAnswer, type Entry, type LiveView, type Mode, type SavedTeam, type Series, type Species, type TrajectoryRow } from "../api";
import { EntryBar } from "./EntryBar";
import { Field } from "./Field";
import { BattleGateBanner } from "./GateBanner";
import { BattleState } from "./BattleState";
import { Info } from "./Popover";
import { SheetForm, useSheetDraft } from "./SheetForm";
import { filled, SHEET_SIZE, toShowdown } from "../sheet";
import { SpeciesPicker } from "./SpeciesPicker";
import { TurnReview } from "./TurnReview";
import { TurnStepper } from "./TurnStepper";
import { turnLines, turnStart } from "../turn";
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
export function BattleSession({ reg, route, navigate, teams, refresh = 0, stamp, autoStart = true, manual = true, onManual }: {
  reg: string;
  route: Extract<Route, { tab: "battle" }>;
  navigate: Navigate;
  teams: SavedTeam[];
  /** Bumped when something other than this page wrote to the battle (the video reader). */
  refresh?: number;
  /** Adds to each entry before it is logged: the watch page puts the video's time on it. */
  stamp?: (entries: Entry[]) => Entry[];
  /** Start turn 1 once the leads are in; off while the video reader writes the turns itself. */
  autoStart?: boolean;
  /** Show what logs a tap by hand: the stepper, the free-form bar, forfeiting. The watch page
   *  hides them until asked, since the video reader fills the journal. */
  manual?: boolean;
  /** Where the page can switch `manual` from, beside the log and the stepper. */
  onManual?: (on: boolean) => void;
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
  }, [id, step, reg, refresh]);

  const log = useCallback(async (entries: Entry[]) => {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      // Logging while looking at an earlier turn would append to the end, not to what is on
      // screen — so the first tap brings you back to the present, where the taps belong.
      if (step !== null) navigate({ tab: "battle", battle: id, step: null }, { replace: true });
      setView(await api.log(id, stamp ? stamp(entries) : entries, reg));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [id, reg, step, navigate, stamp]);

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
            {view.series && <>game {view.series.game} of a Bo{view.series.best_of}{" "}
              ({view.series.score.p1}–{view.series.score.p2}) · </>}
            {modeLabel(view.sheets, view.perspective)} · {view.started ? `turn ${view.turn}` : "team preview"}
          </span>
        </div>
        <div className="row">
          {manual && step === null && !view.ended && <Forfeit view={view} onLog={log} busy={busy} />}
          <a className="ghost tab" {...linkProps(tabRoute("battle"), navigate)}>All battles</a>
        </div>
      </div>

      {/* The row where you work: the stepper where the eye lands first, and beside it the number
          and the four on the field, which is everything its questions need. */}
      <div className="battle-top">
        <div className="battle-work">
          {view.series && view.ended && step === null && <SeriesNext view={view} series={view.series} navigate={navigate} />}
          {step === null && !manual && (
            <TurnLog view={view} onWalk={(index) => navigate({ tab: "battle", battle: id, step: index }, { replace: true })}
                     onManual={onManual && (() => onManual(true))} />
          )}
          {step === null && manual && onManual && (
            <div className="row manual-toggle">
              <button className="link tiny" onClick={() => onManual(false)}>Hide the manual inputs</button>
            </div>
          )}
          {step === null
            ? manual && <TurnStepper id={id} reg={reg} view={view} busy={busy} autoStart={autoStart} onLog={log} onUndo={undo}
                           onWalk={(index) => navigate({ tab: "battle", battle: id, step: index }, { replace: true })}
                           onSomethingElse={() => setFreeForm((f) => !f)} fieldPick={fieldPick}
                           preview={<>
                             {yourFour && <YourFour view={view} onLog={log} busy={busy} />}
                             <Leads view={view} onLog={log} busy={busy} />
                           </>} />
            : <TurnReview id={id} reg={reg} view={view} step={step} navigate={navigate} />}

          {manual && step === null && freeForm && (
            <>
              <div className="row free-form-head">
                <span className="tiny dim">Something else: the free-form entry bar</span>
                <button className="link" onClick={() => { setFreeForm(false); setSelected(null); }}>close (Esc)</button>
              </div>
              <EntryBar view={view} selected={selected} onSelect={setSelected} onLog={log} busy={busy} />
            </>
          )}
          {view.series && <SeriesPanel view={view} series={view.series} navigate={navigate} />}
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
          <Field view={view} selected={manual && freeForm ? selected : null}
                 onPick={(side, slot) => !manual ? undefined : freeForm ? setSelected({ side, slot }) : fieldPick.current?.({ side, slot })}
                 onHp={(side, slot) => !manual ? undefined : freeForm ? setSelected({ side, slot }) : fieldPick.current?.({ side, slot })} />
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
  // Inside a turn the number stays where the turn started (or where its replacements were
  // chosen) and says so, because the model was only ever trained on those points.
  const asOf = wp.as_of && !wp.as_of.current ? wp.as_of : null;
  return (
    <div className={`wp${labelled ? " second" : ""}`}>
      <div className="row wp-line">
        <span>
          {labelled && <>Model: </>}<b className="wp-num big" style={asOf ? { opacity: 0.6 } : undefined}>{pct(wp.wp)}%</b>{" "}
          <span className="small">{view.perspective === "spectator" ? <><Who name={sideNames(view).p1} /> wins</> : "you win"}</span>
        </span>
        <GateTag view={view}>
          {wp.drawn != null && Math.abs(wp.drawn - (wp.wp ?? 0)) >= 0.005 &&
            <p>{pct(wp.drawn)}% averaged over {wp.k} drawn sets.</p>}
          <p>{open > 0 ? `${open} of their six still open.` : `Band from ${wp.k} draws.`} {wp.regime}</p>
        </GateTag>
      </div>
      {asOf && (
        <div className="tiny dim">
          {asOf.kind === "switch" ? <>As of the replacements at the end of turn {asOf.turn}</>
            : <>As of the start of turn {asOf.turn}</>}; it updates when the turn is done.
        </div>
      )}
      <div className="wp-bar">
        <span className="wp-range" style={{ left: `${pct(wp.lo)}%`, width: `${pct(wp.hi) - pct(wp.lo)}%` }} />
        <span className="wp-mark" style={{ left: `${pct(wp.wp)}%` }} />
      </div>
      {/* The band is not a margin around the number: it is the same model asked again with their
          unseen sets filled in from what people run, so the number can sit outside it. Saying what
          it is, and when the two disagree, is the point of drawing it. */}
      {wp.hi !== wp.lo && (
        <div className="tiny dim wp-band-note">
          <span className="wp-key band" /> With their unseen sets filled in from what people run:{" "}
          <b>{pct(wp.lo)}–{pct(wp.hi)}%</b> ({wp.k} draws).
          {(pct(wp.wp) < pct(wp.lo) || pct(wp.wp) > pct(wp.hi)) && (
            <> The headline leaves those sets unknown, which predicted better on held-out games with
              sheets hidden; the gap says their sets matter here.</>
          )}
        </div>
      )}
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
  const names = sideNames(view);
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
                {view.perspective === "spectator" ? <><Who name={sideNames(view).p1} /> wins</> : "you win"}
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

/** The turn so far, read-only: what the stepper lists above its questions, for when the taps are
 *  being made by the video reader rather than by hand. A line walks the battle back to before it,
 *  and the turns before this one are a click away. */
function TurnLog({ view, onWalk, onManual }: { view: LiveView; onWalk: (index: number) => void; onManual?: () => void }) {
  const lines = turnLines(view, sideNames(view));
  const start = turnStart(view.journal);
  let prev = -1;
  for (let i = start - 1; i >= 0; i--) if (view.journal[i].kind === "turn") { prev = i; break; }
  return (
    <div className="panel turn-log">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>{view.started ? `Turn ${view.turn}` : "Team preview"}</h2>
        <span className="row">
          {start >= 0 && (
            <button className="ghost small-btn" onClick={() => onWalk(prev >= 0 ? prev : 0)}>
              ← {prev >= 0 ? "last turn" : "leads"}
            </button>
          )}
          {onManual && <button className="link tiny manual-link" onClick={onManual}>Enter taps by hand…</button>}
        </span>
      </div>
      {lines.length === 0
        ? <p className="tiny dim" style={{ margin: 0 }}>Nothing logged yet this turn.</p>
        : (
          <ol className="turn-lines">
            {lines.map((l) => (
              <li key={l.index}>
                <button className={`turn-line${l.error ? " bad" : ""}`} onClick={() => onWalk(l.index)}
                        title="Walk the battle back to just before this">
                  <span className="turn-check">{l.error ? "!" : "✓"}</span> {l.text}
                  {l.error && <span className="turn-err"> {l.error}</span>}
                </button>
              </li>
            ))}
          </ol>
        )}
    </div>
  );
}

/** A player's name in a line of text, cut short past a couple of words with the whole in its title. */
function Who({ name }: { name: string }) {
  return <span className="side-name" style={{ maxWidth: "20ch" }} title={name}>{name}</span>;
}

/** Where the series goes from here, once this game is over: the next game, set up when this one
 *  ended, or the series' result. */
function SeriesNext({ view, series, navigate }: { view: LiveView; series: Series; navigate: Navigate }) {
  const names = sideNames(view);
  if (series.decided) {
    const winner = series.score.p1 > series.score.p2 ? "p1" : "p2";
    return (
      <div className="panel series-next">
        <b>{names[winner] === "You" ? "You won" : `${names[winner]} won`} the series {Math.max(series.score.p1, series.score.p2)}–{Math.min(series.score.p1, series.score.p2)}.</b>
      </div>
    );
  }
  if (!series.next) return null;
  return (
    <div className="panel series-next row" style={{ justifyContent: "space-between" }}>
      <span>
        <b>Game {series.game + 1} is set up</b>{" "}
        <span className="small dim">({names.p1} {series.score.p1}–{series.score.p2} {names.p2}): same teams, and what this game showed carries over.</span>
      </span>
      <a className="btn-link" {...linkProps({ tab: "battle", battle: series.next, step: null }, navigate)}>
        Open game {series.game + 1} →
      </a>
    </div>
  );
}

/** The series this battle is a game of: each game's line, and what this one started out knowing
 *  from the ones before it. */
function SeriesPanel({ view, series, navigate }: { view: LiveView; series: Series; navigate: Navigate }) {
  const names = sideNames(view);
  const hidden: ("p1" | "p2")[] = view.perspective === "spectator" ? ["p1", "p2"] : ["p2"];
  const { races, reveals } = series.carried;
  return (
    <div className="panel">
      <h2>Bo{series.best_of}: {names.p1} {series.score.p1}–{series.score.p2} {names.p2}</h2>
      <div className="series-games">
        {series.games.map((g) => {
          const here = g.id === view.id;
          const line = (
            <>
              <span className="series-game-n">Game {g.game}</span>
              <span className="small">
                {g.result ? `${names[g.result as "p1" | "p2"]} won` : g.ended ? "ended" : here ? "this game" : g.turn ? `turn ${g.turn}` : "not started"}
              </span>
              <span className="tiny dim">
                {hidden.map((sid) => g.led[sid].length > 0 && (
                  <span key={sid}>
                    {names[sid] === "Them" ? "They" : names[sid]} led {g.led[sid].join(" + ")}
                    {g.brought[sid].length > g.led[sid].length &&
                      <>, then {g.brought[sid].filter((x) => !g.led[sid].includes(x)).join(", ")}</>}
                    {". "}
                  </span>
                ))}
              </span>
            </>
          );
          return here
            ? <div key={g.id} className="series-game current">{line}</div>
            : <a key={g.id} className="series-game" {...linkProps({ tab: "battle", battle: g.id, step: null }, navigate)}>{line}</a>;
        })}
      </div>
      {series.game > 1 && (
        <div className="tiny dim" style={{ marginTop: 8 }}>
          <b>Carried from earlier games:</b>{" "}
          {races > 0
            ? <>{races} race{races === 1 ? "" : "s"} in turn order, which bound the same Stat Points in this game: the
                Speed read, the engine and the model's number all use them.</>
            : <>no races in turn order yet.</>}
          {reveals.length > 0 && (
            <ul className="derived">
              {reveals.map((r) => (
                <li key={`${r.side}${r.species}`}>
                  {r.species}: {[r.item, r.ability, ...r.moves].filter(Boolean).join(", ")}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
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
  // Whoever has already been on the field was brought: the leads first, left then right.
  const [pick, setPick] = useState<string[]>(() => mons.filter((m) => m.state !== "unrevealed")
    .sort((a, b) => Number(a.state !== "active") - Number(b.state !== "active") || (a.slot ?? 2) - (b.slot ?? 2))
    .map((m) => m.species));
  const toggle = (sp: string) => setPick(pick.includes(sp) ? pick.filter((x) => x !== sp) : [...pick, sp]);
  // Before turn 1 the first two selected are the leads, left then right, so the leads question
  // only has their side left to ask. Lead order is not Speed evidence, so nothing is lost by it.
  const leading = !view.started;
  const confirm = () => {
    const entries: Entry[] = [{ kind: "bring", side: me, species: pick }];
    if (leading) {
      const on = (slot: number) => mons.find((m) => m.state === "active" && m.slot === slot)?.species;
      const waiting = pick.slice(0, 2).filter((sp) => sp !== on(0) && sp !== on(1));
      for (const slot of [0, 1]) {
        if (!on(slot) && waiting.length) entries.push({ kind: "lead", side: me, slot, species: waiting.shift()! });
      }
    }
    onLog(entries);
  };
  return (
    <div className="panel">
      <h2>Your four</h2>
      <div className="pad">
        {mons.map((m) => {
          const at = pick.indexOf(m.species);
          return (
            <button key={m.species} className="choice check" role="checkbox" aria-checked={at >= 0}
                    disabled={busy || (at < 0 && pick.length >= 4)}
                    onClick={() => toggle(m.species)}>
              {at >= 0 && <span className="order-badge">{at + 1}</span>}
              {m.species}
              {leading && at >= 0 && at < 2 && <span className="opt-sub"> leads {at === 0 ? "left" : "right"}</span>}
            </button>
          );
        })}
        <button className="commit" disabled={busy || pick.length !== 4} onClick={confirm}>
          {pick.length === 4 ? (leading ? "Confirm these four and the two leads" : "Confirm these four")
            : `${pick.length} of 4 selected`}
        </button>
      </div>
      <p className="tiny dim" style={{ margin: "8px 2px 0" }}>
        {leading && <>Select in order: your first two are your leads, left then right. </>}
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
  const names = sideNames(view);
  const watching = view.perspective === "spectator";
  return (
    <div className="panel">
      <h2>Leads</h2>
      <div className="lead-grid">
        {(["p2", "p1"] as Side[]).map((side) => (
          <div key={side} className="row">
            <span className="dim small side-name" style={{ width: watching ? 110 : 54 }} title={names[side]}>{names[side]}</span>
            {[0, 1].map((slot) => {
              const m = filled(side, slot);
              return (
                <button key={slot} className="choice radio" role="radio" disabled={busy}
                        aria-checked={pick?.side === side && pick.slot === slot}
                        onClick={() => setPick(pick?.side === side && pick.slot === slot ? null : { side, slot })}>
                  {m ? m.species : slot === 0 ? "left" : "right"}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {pick && (
        <div className="pad" style={{ marginTop: 8 }}>
          <span className="tiny dim" style={{ alignSelf: "center" }}>
            Who led on {names[pick.side] === "You" ? "your" : names[pick.side] === "Them" ? "their" : `${names[pick.side]}'s`} {pick.slot === 0 ? "left" : "right"}?
          </span>
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

/** Ending the game early, for either side. The other side wins; it is one `end` entry like any
 *  other, so undo takes it back. */
function Forfeit({ view, onLog, busy }: { view: LiveView; onLog: (e: Entry[]) => void; busy: boolean }) {
  const names = sideNames(view);
  const label: Record<Side, string> = view.perspective === "spectator"
    ? { p1: `${names.p1} forfeits`, p2: `${names.p2} forfeits` } : { p1: "I forfeit", p2: "They forfeit" };
  const quit = (loser: Side) => {
    const winner: Side = loser === "p1" ? "p2" : "p1";
    if (confirm(`${label[loser]}: end the battle with ${names[winner]} as the winner?`)) {
      onLog([{ kind: "end", winner, by: "forfeit" }]);
    }
  };
  return (
    <>
      {(["p1", "p2"] as Side[]).map((s) => (
        <button key={s} className="danger small-btn" disabled={busy} onClick={() => quit(s)}>
          <span className="side-name" style={{ maxWidth: "24ch" }} title={label[s]}>{label[s]}</span>
        </button>
      ))}
    </>
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
  const [theirSheet, setTheirSheet] = useSheetDraft(`vgc:${reg}:sheet:their`);
  const [p1Sheet, setP1Sheet] = useSheetDraft(`vgc:${reg}:sheet:p1`);
  const mode = modes.find((m) => m.id === modeId);
  const sheets = mode?.sheets ?? null;
  const watching = mode?.perspective === "spectator";
  const [name, setName] = useState("");
  const [bestOf, setBestOf] = useState<1 | 3>(1);
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
      const opponent = sheets === "open" ? { their_team: toShowdown(theirSheet) } : { their_species: chosen };
      const v = watching
        ? await api.newBattle({ name, p1_team: toShowdown(p1Sheet), their_team: toShowdown(theirSheet), regulation: reg, best_of: bestOf })
        : await api.newBattle({ name, team_id: teamId, regulation: reg, best_of: bestOf, ...opponent });
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
        <div className="row" style={{ marginBottom: 10 }}>
          <span className="dim small">Format</span>
          <div className="toggle" role="group" aria-label="Best of">
            <button className="seg" aria-pressed={bestOf === 1} onClick={() => setBestOf(1)}>Bo1</button>
            <button className="seg" aria-pressed={bestOf === 3} onClick={() => setBestOf(3)}>Bo3</button>
          </div>
          {bestOf === 3 && (
            <span className="tiny dim">
              Ending a game sets up the next with the same teams, starting from what the earlier games showed.
            </span>
          )}
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
                    <SheetForm reg={reg} pool={pool} value={p1Sheet} onChange={setP1Sheet}
                               label="Player 1's open team sheet" />
                  )}
                  <SheetForm reg={reg} pool={pool} value={theirSheet} onChange={setTheirSheet}
                             label={`${watching ? "Player 2's" : "Their"} open team sheet`} />
                </>
              )}
              <div className="row" style={{ marginTop: 10 }}>
                <button disabled={busy || !sheets || (watching
                                  ? filled(p1Sheet) !== SHEET_SIZE || filled(theirSheet) !== SHEET_SIZE
                                  : !teamId || (sheets === "closed" ? chosen.length !== 6 : filled(theirSheet) !== SHEET_SIZE))}
                        onClick={start}>Start</button>
                {sheets === "closed" && <span className="tiny dim">{chosen.length}/6 chosen</span>}
                {sheets === "open" && filled(theirSheet) !== SHEET_SIZE &&
                  <span className="tiny dim">{filled(theirSheet)}/{SHEET_SIZE} on their sheet</span>}
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
                  {b.series && <>game {b.series.game} of a Bo{b.series.best_of} · </>}
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
