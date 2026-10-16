import { useEffect, useState } from "react";
import { api, sideNames, type LiveMon, type LiveView, type SpeedRead, type TrajectoryRow } from "../api";
import { useEngine } from "../engine";
import { across, type Side } from "../screen";
import { BRING, PRETTY, toId, turnLines, turnStart } from "../turn";
import type { Navigate, Route } from "../router";
import { hpClass, LABEL, ORDER, standing } from "./BattleState";
import { BattleGateBanner } from "./GateBanner";
import { Info } from "./Popover";

// The battle beside a watched video, read at a glance: the number, the four on the field with
// what they can do, who moves first, the turn as it happened, and both sixes under the video.
// Everything here reads the same `LiveView` the battle page does; only the arrangement differs.

type Name = (id: string) => string;

const pct = (x: number) => Math.round(x * 100);
const BOOSTS: Record<string, string> = { atk: "Atk", def: "Def", spa: "SpA", spd: "SpD", spe: "Spe", accuracy: "Acc", evasion: "Eva" };
const other = (s: Side): Side => (s === "p1" ? "p2" : "p1");

/** The model's verdict as a word, kept beside its number wherever that number is (PLAN.md: a model
 *  that misses a gate is never shown without it). */
function gateTag(view: LiveView): [string, string] {
  const v = view.verdict;
  return !view.gates?.known ? ["no model", "bad"]
    : v?.pass ? ["verified", "ok"]
    : v && v.failed.length > 0 ? ["rough guide", "warn"]
    : ["not verified", "warn"];
}

/** The win chance: one number, said as the question it answers.
 *
 *  Where the engine leads (two or fewer a side, where it called held-out human games better than
 *  the model, PLAN-v3 steps 3.6 and 8) its answer is the number, and the model's stays on screen
 *  beside it as "games like this", with its gate verdict. Elsewhere the model's is the number. The
 *  difference between the two, and how each was made, is one click away. */
export function WinPanel({ id, reg, view, route, navigate }: {
  id: string; reg: string; view: LiveView;
  route: Extract<Route, { tab: "battle" }>; navigate: Navigate;
}) {
  const eligible = route.step === null && !!view.endgame?.eligible;
  const ans = useEngine(id, reg, view, eligible);
  const names = sideNames(view);
  const wp = view.wp && !view.wp.error ? view.wp : null;
  const model = wp?.wp ?? null;
  const engine = eligible && ans?.eligible && ans.value != null ? ans.value : null;
  const policy = ans?.mode === "policy";
  const lead = engine ?? model;
  const point = engine != null ? ans?.as_of : wp?.as_of;
  const stale = !!point && !point.current;
  const [tag, tone] = gateTag(view);
  const forced = engine != null ? (ans?.positions ?? []).find((p) => p.forced)?.forced : undefined;
  const band = engine == null && wp?.lo != null && wp.hi != null && wp.hi - wp.lo >= 0.02;

  if (lead == null) {
    return <section className="panel wb-win"><p className="tiny dim" style={{ margin: 0 }}>{view.wp?.error ?? "No number yet."}</p></section>;
  }
  return (
    <section className="panel wb-win">
      <div className="wb-win-head" style={stale ? { opacity: 0.6 } : undefined}>
        <b className="wb-big p1">{pct(lead)}%</b>
        <span className="small dim wb-who" title={names.p1}>{names.p1}</span>
        <span style={{ flex: 1 }} />
        <span className="small dim wb-who" title={names.p2}>{names.p2}</span>
        <b className="wb-small p2">{100 - pct(lead)}%</b>
      </div>
      <div className="wb-split" style={stale ? { opacity: 0.6 } : undefined}>
        <i className="p1" style={{ width: `${pct(lead)}%` }} /><i className="p2" />
      </div>
      <div className="row wb-win-foot">
        <span className="small">
          {engine != null
            ? policy ? "Games like this, with the policy's read of the position" : "With best play from both sides"
            : "How games like this went"}
        </span>
        {engine == null && <span className={`gate-tag ${tone}`}>{tag}</span>}
        <span style={{ flex: 1 }} />
        {engine != null && model != null && (
          <span className="wb-aside">games like this <b>{pct(model)}%</b> <span className={`gate-tag ${tone}`}>{tag}</span></span>
        )}
        {eligible && engine == null && <span className="tiny dim">best play: {ans?.eligible === false ? "not available" : "searching…"}</span>}
        <Info label="How these numbers were made">
          <WinInfo view={view} ans={ans} engine={engine} model={model} policy={policy} />
        </Info>
      </div>
      {stale && point && (
        <div className="tiny dim">
          {point.kind === "switch" ? <>As of the replacements at the end of turn {point.turn}</>
            : <>As of the start of turn {point.turn}</>}; it updates when the turn is done.
        </div>
      )}
      {band && <div className="tiny dim">With their unseen sets filled in from what people run: {pct(wp!.lo!)}–{pct(wp!.hi!)}%.</div>}
      {forced && (
        <div className="small">
          {names[forced.side]} can force the win this turn{forced.through_protect ? ", once Protect runs out" : ""}
          {forced.sweep < 0.995 && <> ({pct(forced.sweep)}%: a crit or a flinch is the way out)</>}.
        </div>
      )}
      <Curve id={id} reg={reg} view={view} route={route} navigate={navigate} engine={engine} />
    </section>
  );
}

function WinInfo({ view, ans, engine, model, policy }: {
  view: LiveView; ans: ReturnType<typeof useEngine>; engine: number | null; model: number | null; policy: boolean;
}) {
  const guesses = ans?.guesses?.values ?? [];
  return (
    <>
      {engine != null && (
        <>
          <p><b>{pct(engine)}%</b> is {policy
            ? <>the model and the policy's one-turn value of the position combined (the policy {pct(ans?.policy ?? 0)}%, the model {pct(ans?.model ?? 0)}%). On held-out human games after the first faint, the two combined beat the model alone.</>
            : <>the engine: it plays the position out, assuming both players choose well. With two or fewer a side it called held-out human games better than the model, so it leads here.</>}</p>
          {model != null && <p><b>{pct(model)}%</b> is the model: how human games that looked like this one went.</p>}
          {guesses.length > 1 && (
            <p>Their other Stat Points are a guess: {guesses.map((g) => `${g.label} ${pct(g.value)}%`).join(" · ")}.</p>
          )}
          {(ans?.unsolved ?? 0) > 0 && <p>{pct(ans!.unsolved!)}% of what they might hold is not solved.</p>}
          {ans?.assumptions && ans.assumptions.length > 0 && (
            <details><summary>What the engine assumes</summary>
              <ul className="derived">{ans.assumptions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </details>
          )}
        </>
      )}
      <BattleGateBanner gates={view.gates} verdict={view.verdict} />
      {view.wp?.regime && <p>{view.wp.regime}</p>}
    </>
  );
}

/** The number at each turn so far, as a line: the model's, since the trajectory replays it, with
 *  the engine's answer as a dot at the end where it leads. A turn's point walks back to it.
 *
 *  The trajectory replays every turn mark through the model, so it is asked for once a turn, not
 *  on every tap the reader makes; the live number stands in for its last point. */
function Curve({ id, reg, view, route, navigate, engine }: {
  id: string; reg: string; view: LiveView;
  route: Extract<Route, { tab: "battle" }>; navigate: Navigate; engine: number | null;
}) {
  const [rows, setRows] = useState<TrajectoryRow[] | null>(null);
  useEffect(() => {
    api.trajectory(id, reg).then((t) => setRows(t.turns)).catch(() => setRows([]));
  }, [id, reg, view.turn, view.ended]);
  if (!rows?.length) return null;
  const live = route.step === null && view.wp?.wp != null;
  const points = rows.map((r, i) => (live && i === rows.length - 1 ? { ...r, wp: view.wp!.wp! } : r));
  const x = (i: number) => ((i + 0.5) / points.length) * 100;
  const y = (v: number) => (1 - v) * 100;
  return (
    <div className="wb-curve">
      <div className="wb-curve-plot">
        <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
          <line x1="0" y1="50" x2="100" y2="50" className="mid" vectorEffect="non-scaling-stroke" />
          <polyline points={points.map((p, i) => `${x(i)},${y(p.wp)}`).join(" ")} vectorEffect="non-scaling-stroke" />
        </svg>
        {points.map((p, i) => (
          <button key={p.index} className="wb-dot" aria-current={route.step === p.index}
                  style={{ left: `${x(i)}%`, top: `${y(p.wp)}%` }}
                  title={`turn ${p.turn}: games like this ${pct(p.wp)}% (${p.left.p1} v ${p.left.p2}). Walk back to it.`}
                  aria-label={`Turn ${p.turn}, ${pct(p.wp)}%: walk back to it`}
                  onClick={() => navigate({ tab: "battle", battle: id, step: p.index }, { replace: true })} />
        ))}
        {engine != null && (
          <span className="wb-dot engine" style={{ left: `${x(points.length - 1)}%`, top: `${y(engine)}%` }}
                title={`best play: ${pct(engine)}%`} />
        )}
      </div>
      <div className="wb-curve-turns">
        {points.map((p, i) => (
          <span key={p.index} style={{ left: `${x(i)}%` }}>{live && i === points.length - 1 ? "now" : `T${p.turn}`}</span>
        ))}
      </div>
      {route.step !== null && (
        <button className="link tiny" onClick={() => navigate({ tab: "battle", battle: id, step: null }, { replace: true })}>back to now</button>
      )}
    </div>
  );
}

/** How many turns a condition has left, this one included, from the turn it went up. Screens last
 *  eight with Light Clay, and while a side's items are not all known that is still possible. */
const LASTS: Record<string, number> = {
  reflect: 5, lightscreen: 5, auroraveil: 5, tailwind: 4, safeguard: 5, mist: 5,
  trickroom: 5, gravity: 5, magicroom: 5, wonderroom: 5,
};
const CLAY = new Set(["reflect", "lightscreen", "auroraveil"]);

/** A field or side condition by its own name (Rain, not the move Rain Dance that set it). */
const fieldName = (id: string, name: Name) => PRETTY[toId(id)] ?? name(id);

function turnsLeft(id: string, since: number, turn: number, mons: LiveMon[]): string | null {
  const lasts = LASTS[id];
  if (lasts == null) return null;
  const left = (n: number) => since + n - turn;
  if (CLAY.has(id)) {
    if (mons.some((m) => m.item === "lightclay")) return left(8) > 0 ? `${left(8)}` : null;
    const maybe = mons.some((m) => m.item === null && m.state !== "not_brought");
    if (maybe) return `${Math.max(1, left(5))}–${left(8)}`;
  }
  return left(lasts) > 0 ? `${left(lasts)}` : null;
}

function Condition({ id, since, view, side, name }: { id: string; since: number; view: LiveView; side?: Side; name: Name }) {
  const mons = side ? view.sides[side].mons : [];
  const left = typeof since === "number" ? turnsLeft(id, since, view.turn, mons) : null;
  return <span className={`chip field${side ? ` ${side}` : ""}`}>{fieldName(id, name)}{left && <> · {left} left</>}</span>;
}

/** The field: the four Pokémon on it with all they are known to carry, the field and side
 *  conditions with the turns they have left, and who moves first. Each player's two are a column,
 *  the one on the left of the screen first. */
export function FieldPanel({ view, name, left, onPick, selected }: {
  view: LiveView; name: Name; left: Side;
  onPick?: (side: Side, slot: number) => void;
  selected?: { side: Side; slot: number } | null;
}) {
  const names = sideNames(view);
  const sides: Side[] = [left, other(left)];
  const at = (side: Side, slot: number) => view.sides[side].mons.find((m) => m.state === "active" && m.slot === slot) ?? null;
  return (
    <section className="panel wb-field">
      <div className="wb-field-head">
        <span className="h2-inline">On the field</span>
        <span style={{ flex: 1 }} />
        {view.field.weather && <span className="chip field">{fieldName(view.field.weather, name)}</span>}
        {view.field.terrain && <span className="chip field">{fieldName(view.field.terrain, name)}</span>}
        {Object.entries(view.field.pseudo).map(([id, since]) => <Condition key={id} id={id} since={since} view={view} name={name} />)}
        {sides.flatMap((s) => Object.entries(view.sides[s].conditions).map(([id, since]) => (
          <Condition key={`${s}${id}`} id={id} since={since} view={view} side={s} name={name} />
        )))}
      </div>
      <div className="wb-cards">
        {[0, 1].flatMap((slot) => sides.map((side) => {
          const m = at(side, slot);
          return m
            ? <MonCard key={`${side}${slot}`} view={view} mon={m} side={side} slot={slot} who={names[side]} name={name}
                       selected={selected?.side === side && selected.slot === slot}
                       onPick={onPick && (() => onPick(side, slot))} />
            : <div key={`${side}${slot}`} className={`wb-card empty ${side}`}>{names[side]} · {across(side, slot)}: empty</div>;
        }))}
      </div>
      <SpeedChart view={view} />
    </section>
  );
}

/** One Pokémon on the field. Its moves are the four the sheet shows; where the sheet is hidden,
 *  the ones it has used and "Unknown" for the rest, each with the likeliest moves a hover away. */
function MonCard({ view, mon, side, slot, who, name, selected, onPick }: {
  view: LiveView; mon: LiveMon; side: Side; slot: number; who: string; name: Name;
  selected: boolean; onPick?: () => void;
}) {
  const boosts = Object.entries(mon.boosts).filter(([, n]) => n).map(([k, n]) => `${BOOSTS[k] ?? k} ${n > 0 ? "+" : "−"}${Math.abs(n)}`);
  const known = mon.moves.length ? mon.moves : mon.moves_used;
  const likely = view.menu[side].actives.find((a) => a?.slot === slot)?.likely ?? [];
  const unknown = Math.max(0, 4 - known.length);
  const item = mon.item === "" ? (mon.lost_item ? `${name(mon.lost_item)} (used)` : "no item") : mon.item ? name(mon.item) : null;
  const ability = mon.ability ? name(mon.ability)
    : mon.ability_unknown.length ? `not ${mon.ability_unknown.map(name).join(", ")}` : null;
  const title = (
    <>
      <b>{mon.forme}</b>
      {mon.status && <span className={`chip status-${mon.status}`}>{mon.status}</span>}
      <span className="tiny dim wb-card-where" title={who}>{across(side, slot)}</span>
      <span className="wb-card-hp mono">{pct(mon.hp)}%</span>
    </>
  );
  return (
    <div className={`wb-card ${side}${selected ? " selected" : ""}`}>
      {onPick
        ? <button type="button" className="wb-card-name" onClick={onPick}>{title}</button>
        : <div className="wb-card-name">{title}</div>}
      <span className="board-hp"><i className={hpClass(mon.hp)} style={{ width: `${pct(mon.hp)}%` }} /></span>
      {(boosts.length > 0 || mon.volatiles.length > 0) && (
        <div className="chips">
          {boosts.map((b) => <span key={b} className="chip boost">{b}</span>)}
          {mon.volatiles.map((v) => <span key={v} className="chip">{name(v)}</span>)}
        </div>
      )}
      <div className="wb-card-set">
        {item ? <span>{item}</span> : <span className="chip unknown">item ?</span>}
        {ability ? <span>{ability}</span> : <span className="chip unknown">ability ?</span>}
        {mon.nature && <span className="dim">{mon.nature}</span>}
      </div>
      <ul className="wb-moves">
        {known.map((mv) => <li key={mv}>{name(mv)}</li>)}
        {Array.from({ length: unknown }, (_, i) => (
          <li key={`u${i}`}>
            <button className="wb-unknown" type="button" aria-describedby={likely.length ? `likely-${side}${slot}` : undefined}>Unknown</button>
            {likely.length > 0 && i === 0 && (
              <span className="wb-likely" role="tooltip" id={`likely-${side}${slot}`}>
                <span className="tiny dim">Likeliest, as the share of sets still possible that run it</span>
                {likely.slice(0, 6).map((l) => (
                  <span key={l.id} className="wb-likely-row">
                    <span>{l.name}</span>
                    <span className="board-hp"><i className="likely" style={{ width: `${pct(l.share)}%` }} /></span>
                    <span className="mono tiny dim">{pct(l.share)}%</span>
                  </span>
                ))}
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

type Racer = { side: Side; slot: number; name: string; range: [number, number] | null; solid: boolean };

/** Speed as ranges on one axis, then each pair: decided (no spread either could have changes it)
 *  or open (the ranges overlap). A hatched bar is the whole range its nature and Stat Points allow;
 *  a solid one has been narrowed by turn order this battle, or is exact. */
function SpeedChart({ view }: { view: LiveView }) {
  if (view.speed.length === 0) return <div className="wb-speed tiny dim">Speed reads once both sides are on the field.</div>;
  const racers = new Map<string, Racer>();
  const narrowed = (side: Side, species: string, range: [number, number] | null) => {
    if (!range) return false;
    if (range[0] === range[1]) return true;
    const spe = view.beliefs.find((b) => b.side === side && b.species === species)?.bounds.spe;
    return !!spe && (spe[0] > 0 || spe[1] < 32);
  };
  const add = (side: Side, slot: number, name: string, range: [number, number] | null) => {
    if (!racers.has(`${side}${slot}`)) racers.set(`${side}${slot}`, { side, slot, name, range, solid: narrowed(side, name, range) });
  };
  for (const r of view.speed) {
    add("p1", r.my_slot, r.mine, r.my_speed);
    add("p2", r.their_slot, r.theirs, r.their_speed);
  }
  const rows = [...racers.values()].sort((a, b) => (b.range?.[1] ?? 0) - (a.range?.[1] ?? 0));
  const ranged = rows.filter((r) => r.range);
  const lo = Math.floor(Math.min(...ranged.map((r) => r.range![0])) / 10) * 10 - 5;
  const hi = Math.ceil(Math.max(...ranged.map((r) => r.range![1])) / 10) * 10 + 5;
  const at = (v: number) => ((v - lo) / (hi - lo)) * 100;
  const trickRoom = view.speed.some((r) => r.trick_room);
  return (
    <div className="wb-speed">
      <div className="wb-speed-head">
        <span className="h2-inline">Speed</span>
        {trickRoom && <span className="chip field">Trick Room: slowest first</span>}
      </div>
      {rows.map((r) => (
        <div key={`${r.side}${r.slot}`} className="wb-speed-row">
          <span className={`wb-speed-name ${r.side}`}>{r.name}</span>
          <span className="wb-speed-track">
            {r.range && <i className={`${r.side}${r.solid ? " solid" : ""}`} style={{ left: `${at(r.range[0])}%`, width: `${Math.max(1, at(r.range[1]) - at(r.range[0]))}%` }} />}
          </span>
          <span className="mono tiny dim wb-speed-range">{r.range ? (r.range[0] === r.range[1] ? r.range[0] : `${r.range[0]}–${r.range[1]}`) : "?"}</span>
        </div>
      ))}
      <div className="wb-pairs">
        {view.speed.map((r, i) => <Pair key={i} read={r} />)}
      </div>
    </div>
  );
}

function Pair({ read }: { read: SpeedRead }) {
  if (read.verdict === "faster" || read.verdict === "slower") {
    const [a, b] = read.verdict === "faster" ? [read.mine, read.theirs] : [read.theirs, read.mine];
    return <span className="wb-pair decided" title="No spread either could have changes it">{a} › {b}</span>;
  }
  const overlap = read.my_speed && read.their_speed
    ? [Math.max(read.my_speed[0], read.their_speed[0]), Math.min(read.my_speed[1], read.their_speed[1])] : null;
  return (
    <span className="wb-pair open" title={read.verdict === "unknown" ? "No Speed to compare yet" : "The ranges overlap: either could move first"}>
      {read.mine} ? {read.theirs}{overlap && overlap[0] <= overlap[1] && <span className="mono"> {overlap[0]}–{overlap[1]}</span>}
    </span>
  );
}

/** The turn so far and the one before it, read-only, each line with the video time it was read at.
 *  A line walks the battle back to before it; a time puts the video there. */
export function WatchLog({ view, onWalk, onManual, onSeek }: {
  view: LiveView; onWalk: (index: number) => void; onManual?: () => void; onSeek?: (seconds: number) => void;
}) {
  const names = sideNames(view);
  const start = turnStart(view.journal);
  const lines = turnLines(view, names);
  const prev = start > 0 ? turnLines({ ...view, journal: view.journal.slice(0, start) }, names) : [];
  const prevStart = start > 0 ? turnStart(view.journal.slice(0, start)) : -1;
  const time = (i: number) => {
    const s = view.journal[i]?.video_s;
    return typeof s === "number" ? s : null;
  };
  const Clock = ({ index }: { index: number }) => {
    const s = time(index);
    if (s === null) return null;
    const label = `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
    return onSeek
      ? <button className="link mono wb-time" onClick={() => onSeek(s)} title="Put the video here">{label}</button>
      : <span className="mono wb-time dim">{label}</span>;
  };
  return (
    <section className="panel wb-log">
      <div className="row wb-log-head">
        <span className="h2-inline bright">{view.started ? `Turn ${view.turn}` : "Team preview"}</span>
        {start >= 0 && <Clock index={start} />}
        <span style={{ flex: 1 }} />
        {start >= 0 && (
          <button className="ghost small-btn" onClick={() => onWalk(prevStart >= 0 ? prevStart : 0)}>
            ← {prevStart >= 0 ? "last turn" : "leads"}
          </button>
        )}
        {onManual && <button className="link tiny manual-link" onClick={onManual}>Enter taps by hand…</button>}
      </div>
      {lines.length === 0 && <p className="tiny dim" style={{ margin: "2px 0" }}>Nothing yet this turn.</p>}
      <ol className="wb-lines">
        {lines.map((l) => (
          <li key={l.index} className={l.error ? "bad" : undefined}>
            <button className="wb-line" onClick={() => onWalk(l.index)} title="Walk the battle back to just before this">
              <span className="turn-check">{l.error ? "!" : "✓"}</span> {l.text}
              {l.error && <span className="turn-err"> {l.error}</span>}
            </button>
            <Clock index={l.index} />
          </li>
        ))}
      </ol>
      {prev.length > 0 && (
        <>
          <div className="row wb-log-head prev">
            <span className="h2-inline">{prevStart >= 0 ? `Turn ${view.turn - 1}` : "Leads"}</span>
            {prevStart >= 0 && <Clock index={prevStart} />}
          </div>
          <ol className="wb-lines prev">
            {prev.map((l) => (
              <li key={l.index}>
                <button className="wb-line" onClick={() => onWalk(l.index)} title="Walk the battle back to just before this">{l.text}</button>
                <Clock index={l.index} />
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}

/** Both sixes, a row each, under the video: who is on the field, in the back, fainted, not
 *  brought, or not yet known to be. The whole set is in each card's tooltip. */
export function TeamStrips({ view, name, left }: { view: LiveView; name: Name; left: Side }) {
  const names = sideNames(view);
  return (
    <div className="wb-strips">
      {[left, other(left)].map((side) => {
        const mons = view.sides[side].mons;
        const seen = mons.filter((m) => m.state !== "unrevealed" && m.state !== "not_brought").length;
        const rows = mons.map((m) => ({ m, st: standing(m, seen) }))
          .sort((a, b) => ORDER.indexOf(a.st) - ORDER.indexOf(b.st) || (a.m.slot ?? 0) - (b.m.slot ?? 0));
        const standingLeft = rows.filter((r) => r.st === "field" || r.st === "back").length;
        const unknownBrought = seen < BRING;
        return (
          <div key={side} className="wb-strip">
            <div className="wb-strip-head">
              <b className={`side-name ${side}`} title={names[side]}>{names[side]}</b>
              <span className="tiny dim">{standingLeft} left{unknownBrought ? ` · ${seen} of ${BRING} brought seen` : ""}</span>
            </div>
            <div className="wb-strip-row">
              {rows.map(({ m, st }) => {
                const shown = st !== "out" && st !== "unknown";
                const set = [m.item === "" ? (m.lost_item ? `${name(m.lost_item)} (used)` : "no item") : m.item ? name(m.item) : "item ?",
                             m.ability ? name(m.ability) : "ability ?"].join(" · ");
                const moves = (m.moves.length ? m.moves : m.moves_used).map(name).join(", ");
                return (
                  <div key={m.species} className={`wb-mini ${side} st-${st}`} title={`${m.forme}: ${set}${moves ? `\n${moves}` : ""}`}>
                    <div className="wb-mini-top">
                      <span className="wb-mini-name">{m.forme}</span>
                      <span className="wb-mini-state">{st === "field" && m.slot != null ? across(side, m.slot) : LABEL[st]}</span>
                    </div>
                    <div className="wb-mini-hp">
                      <span className={`board-hp${shown ? "" : " empty"}`}>{shown && <i className={hpClass(m.hp)} style={{ width: `${pct(m.hp)}%` }} />}</span>
                      <span className="mono tiny dim">{shown ? `${pct(m.hp)}%` : ""}</span>
                    </div>
                    <div className="wb-mini-set">{set}</div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
    </div>
  );
}
