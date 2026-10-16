import { useEffect, useState } from "react";
import { api, sideNames, type LiveMon, type LiveView } from "../api";
import { turnLines } from "../turn";
import type { Navigate } from "../router";

type Side = "p1" | "p2";

const PRETTY: Record<string, string> = {
  sunnyday: "Sun", raindance: "Rain", sandstorm: "Sandstorm", snow: "Snow",
  grassyterrain: "Grassy Terrain", electricterrain: "Electric Terrain", psychicterrain: "Psychic Terrain",
  mistyterrain: "Misty Terrain", trickroom: "Trick Room", gravity: "Gravity",
  reflect: "Reflect", lightscreen: "Light Screen", auroraveil: "Aurora Veil", tailwind: "Tailwind",
  safeguard: "Safeguard", mist: "Mist", wideguard: "Wide Guard", quickguard: "Quick Guard",
};
const STATUS: Record<string, string> = { brn: "burned", par: "paralysed", psn: "poisoned", tox: "badly poisoned", slp: "asleep", frz: "frozen" };
const STATS = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"];

/** A turn looked back at, as a log: the field as the turn began, everything that happened in it,
 *  and the field as it ended.
 *
 *  Each end is the journal replayed to a turn mark, the same operation as walking back, so the two
 *  boards are what the page showed at those moments and cannot disagree with it. A turn runs from
 *  its `turn` tap to the next one, so the replacements sent in after a faint belong to the turn the
 *  faint happened in, as they do on the cartridge. */
export function TurnReview({ id, reg, view, step, navigate }: {
  id: string; reg: string; view: LiveView; step: number; navigate: Navigate;
}) {
  const [marks, setMarks] = useState<number[] | null>(null);
  const [total, setTotal] = useState<number>(view.entries_total ?? 0);
  const [ends, setEnds] = useState<{ start: LiveView; end: LiveView } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const names = sideNames(view);

  // Where every turn starts, from the whole journal: the view on screen is only a prefix of it.
  useEffect(() => {
    api.battleLite(id, reg).then((full) => {
      setMarks(full.journal.flatMap((e, i) => (e.kind === "turn" ? [i] : [])));
      setTotal(full.journal.length);
    }).catch((e) => setError(String(e.message ?? e)));
  }, [id, reg, view.entries_total]);

  // The turn this step is in: the one starting here, or the one under way.
  const at = marks ? (marks.includes(step) ? step : [...marks].reverse().find((m) => m < step) ?? -1) : null;
  const next = marks && at !== null ? marks.find((m) => m > at) ?? total : null;
  const prev = marks && at !== null ? [...marks].reverse().find((m) => m < at) : undefined;

  useEffect(() => {
    if (at === null || next === null) return;
    setEnds(null);
    const start = at === step ? Promise.resolve(view) : api.battleAt(id, Math.max(at, 0), reg);
    Promise.all([start, api.battleAt(id, next, reg)])
      .then(([s, e]) => setEnds({ start: s, end: e }))
      .catch((e) => setError(String(e.message ?? e)));
  }, [id, reg, at, next, step, view]);

  const go = (index: number | null) => navigate({ tab: "battle", battle: id, step: index }, { replace: true });
  const turn = ends?.end.turn ?? view.turn;
  const pct = (v: LiveView) => (v.wp?.wp != null ? `${Math.round(v.wp.wp * 100)}%` : null);
  const lines = ends ? turnLines(ends.end, names) : [];

  return (
    <div className="panel turn-review">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <h2 style={{ margin: 0 }}>{at === -1 ? "Team preview and leads" : `Turn ${turn}`}</h2>
        <span className="row">
          <button className="ghost small-btn" disabled={prev === undefined} onClick={() => prev !== undefined && go(prev)}>← turn</button>
          <button className="ghost small-btn" disabled={next === null || next >= total}
                  onClick={() => next !== null && go(next)}>turn →</button>
          <button className="link small" onClick={() => go(null)}>back to now</button>
        </span>
      </div>
      {error && <p className="small err">{error}</p>}
      {!ends && !error && <p className="tiny dim">Replaying…</p>}
      {ends && (
        <>
          <Snapshot title="At the start" view={ends.start} names={names} wp={pct(ends.start)} />
          <div className="review-block">
            <div className="review-title">What happened</div>
            {lines.length === 0
              ? <p className="tiny dim" style={{ margin: 0 }}>Nothing was logged in this turn.</p>
              : <ol className="review-lines">
                  {lines.map((l) => (
                    <li key={l.index} className={l.error ? "err" : undefined}>
                      {l.text}{l.error && <span className="tiny"> ({l.error})</span>}
                    </li>
                  ))}
                </ol>}
          </div>
          <Snapshot title={next !== null && next >= total ? "Now" : "At the end"} view={ends.end} names={names} wp={pct(ends.end)} />
          <p className="tiny dim" style={{ margin: "8px 0 0" }}>
            Looking at tap {step} of {total}. Log anything to come back to now.
          </p>
        </>
      )}
    </div>
  );
}

/** The field at one moment, as a few lines of text: what is out and how it stands, then the field. */
function Snapshot({ title, view, names, wp }: {
  title: string; view: LiveView; names: Record<Side, string>; wp: string | null;
}) {
  const field = [view.field.weather, view.field.terrain, ...Object.keys(view.field.pseudo)]
    .filter(Boolean).map((f) => PRETTY[f as string] ?? f);
  return (
    <div className="review-block">
      <div className="review-title row" style={{ justifyContent: "space-between" }}>
        <span>{title}</span>
        {wp && <span className="tiny dim">{view.perspective === "spectator" ? <span className="side-name" style={{ maxWidth: "16ch" }} title={names.p1}>{names.p1}</span> : "you"} {wp}</span>}
      </div>
      {(["p2", "p1"] as Side[]).map((sid) => {
        const side = view.sides[sid];
        const out = [0, 1].map((slot) => side.mons.find((m) => m.state === "active" && m.slot === slot) ?? null);
        const fainted = side.mons.filter((m) => m.state === "fainted").length;
        const conditions = Object.keys(side.conditions).map((c) => PRETTY[c] ?? c);
        return (
          <div key={sid} className={`review-side${view.perspective === "spectator" ? " named" : ""}`}>
            <span className="review-who side-name" title={names[sid]}>{names[sid]}</span>
            <span>
              {out.every((m) => !m) ? <span className="dim">nothing out</span>
                : out.map((m, i) => <span key={i}>{i > 0 && " · "}{m ? <MonLine mon={m} /> : <span className="dim">empty</span>}</span>)}
              {fainted > 0 && <span className="dim"> · {fainted} fainted</span>}
              {conditions.length > 0 && <span className="dim"> · {conditions.join(", ")}</span>}
            </span>
          </div>
        );
      })}
      {field.length > 0 && <div className={`review-side${view.perspective === "spectator" ? " named" : ""}`}><span className="review-who">Field</span><span>{field.join(", ")}</span></div>}
    </div>
  );
}

function MonLine({ mon }: { mon: LiveMon }) {
  const boosts = STATS.filter((s) => mon.boosts[s]).map((s) => `${s} ${mon.boosts[s] > 0 ? "+" : ""}${mon.boosts[s]}`);
  return (
    <>
      <b>{mon.forme}</b> {Math.round(mon.hp * 100)}%
      {mon.status && <> {STATUS[mon.status] ?? mon.status}</>}
      {boosts.length > 0 && <> ({boosts.join(", ")})</>}
    </>
  );
}
