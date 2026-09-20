import { useCallback, useEffect, useState } from "react";
import {
  api, ApiError,
  type BoardMon, type BoardSide, type Endgame, type EndgameDetail, type EndgameIndex,
  type EndgameStep,
  type Slot as SlotRow,
} from "../api";

type Filter = "all" | "played_out" | "misses";

const FILTERS: { id: Filter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "played_out", label: "Played out" },
  { id: "misses", label: "Model was wrong" },
];

/** Decided endgames: real human games the model called at 90%+ before they finished.
 *
 *  This page exists to be argued with. A 90% number is worth showing only if roughly 90 of every
 *  100 such positions are actually won, so every game here is one the model never trained on
 *  (held out by the frozen split), between two people playing teams they built, with both open
 *  team sheets on the table — and the header says how often the call was right. Step through one
 *  and the same model is re-run at every decision point of the real log. */
export function EndgamesPanel({ reg }: { reg: string }) {
  const [filter, setFilter] = useState<Filter>("all");
  const [index, setIndex] = useState<EndgameIndex | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    api.endgames(reg, filter)
      .then((r) => { if (live) { setIndex(r); setError(""); } })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e.message : String(e)); });
    return () => { live = false; };
  }, [reg, filter]);

  // A game that the current filter hides is no longer the selection.
  useEffect(() => {
    if (index && selected && !index.games.some((g) => g.replay === selected)) setSelected(null);
  }, [index, selected]);

  if (error) return <div className="banner bad">{error}</div>;
  if (!index) return <div className="panel"><p className="empty">Loading…</p></div>;

  if (!index.built) {
    return (
      <div className="panel">
        <h2>No endgame set yet</h2>
        <pre className="hint-block">vgc wp endgames</pre>
      </div>
    );
  }

  return (
    <>
      <SetHeader index={index} filter={filter} onFilter={setFilter} />
      <div className="split">
        <GameList games={index.games} selected={selected} onSelect={setSelected} />
        {selected
          ? <GameStepper key={selected} replay={selected} reg={reg}
                         minWp={index.criteria?.min_wp ?? 0.9} />
          : <div className="panel"><p className="empty">Pick a game.</p></div>}
      </div>
    </>
  );
}

function SetHeader({ index, filter, onFilter }: {
  index: EndgameIndex; filter: Filter; onFilter: (f: Filter) => void;
}) {
  const c = index.criteria, counts = index.counts;
  const correct = index.correct ?? 0, total = index.total ?? 0;
  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
        <h2 style={{ margin: 0 }}>
          {total} decided endgames{c && ` at ${(c.min_wp * 100).toFixed(0)}%+`}
        </h2>
        <span className={`small ${correct === total ? "ok" : ""}`}>
          the favoured side won {correct}/{total}
          {total > 0 && <span className="dim"> ({((correct / total) * 100).toFixed(1)}%)</span>}
        </span>
      </div>

      <p className="small dim" style={{ margin: "8px 0 0" }}>
        {c && <>held {(c.min_wp * 100).toFixed(0)}%+ for the last {c.hold} decision points · </>}
        human vs human · open sheets · held out
        {counts && <> · from {counts.eligible.toLocaleString()} eligible games</>}
        {" · "}<strong>{index.version}</strong>
      </p>
      {index.gates?.in_battle_pass === false && (
        <p className="small err" style={{ margin: "6px 0 0" }}>
          {index.version} fails its in-battle gates.
        </p>
      )}

      <div className="row" style={{ marginTop: 10 }}>
        <div className="toggle">
          {FILTERS.map((f) => (
            <button key={f.id} className="seg"
                    aria-pressed={filter === f.id} onClick={() => onFilter(f.id)}>
              {f.label}
            </button>
          ))}
        </div>
        <span className="tiny dim">
          {index.games.length < index.matched
            ? `showing ${index.games.length} of ${index.matched}`
            : `${index.matched} shown`}
        </span>
      </div>
    </div>
  );
}

function GameList({ games, selected, onSelect }: {
  games: Endgame[]; selected: string | null; onSelect: (id: string) => void;
}) {
  if (!games.length) return <div className="panel"><p className="empty">Nothing matches this filter.</p></div>;
  return (
    <div className="panel game-list">
      {games.map((g) => (
        <button key={g.replay} className="game-row" aria-current={g.replay === selected}
                onClick={() => onSelect(g.replay)}>
          <span className={`dot ${g.correct ? "ok" : "bad"}`}
                title={g.correct ? "the favoured side won" : "the favoured side lost"} />
          <span className="game-who">
            {g.players.p1} <span className="dim">vs</span> {g.players.p2}
          </span>
          <span className="game-wp">{(g.wp * 100).toFixed(0)}%</span>
          <span className="tiny dim game-meta">
            {g.side === "p1" ? "◀" : "▶"} · {g.turns}t
            {g.ended_by === "forfeit" && " · ff"}
            {g.rating != null && ` · ${g.rating}`}
          </span>
        </button>
      ))}
    </div>
  );
}

function GameStepper({ replay, reg, minWp }: { replay: string; reg: string; minWp: number }) {
  const [game, setGame] = useState<EndgameDetail | null>(null);
  const [error, setError] = useState("");
  const [i, setI] = useState(0);

  useEffect(() => {
    let live = true;
    setGame(null); setError(""); setI(0);
    api.endgame(replay, reg)
      .then((d) => {
        if (!live) return;
        setGame(d);
        // Open on the moment the game stopped being in doubt, not on turn 1: that is the position
        // the set is making a claim about, and scrolling back to it every time is friction.
        setI(Math.max(0, d.steps.findIndex((s) => confident(s, minWp))));
      })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e.message : String(e)); });
    return () => { live = false; };
  }, [replay, reg, minWp]);

  const last = (game?.steps.length ?? 1) - 1;
  const step = useCallback((d: number) => setI((n) => Math.min(last, Math.max(0, n + d))), [last]);

  // ← / → walk the game. Only this panel is on screen when it is mounted, but a text field
  // anywhere still owns its own arrow keys, so a focused input opts out.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = document.activeElement;
      if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowLeft") { step(-1); e.preventDefault(); }
      if (e.key === "ArrowRight") { step(1); e.preventDefault(); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [step]);

  if (error) return <div className="panel"><p className="err small">{error}</p></div>;
  if (!game) return <div className="panel"><p className="empty">Loading…</p></div>;

  const cur = game.steps[i];
  const wonBy = game.winner ? game.players[game.winner] : "nobody";

  return (
    // One element, not a fragment: the parent is a two-column grid and a fragment's children
    // would each become a cell of it, dealing the game's panels alternately down both columns.
    <div>
      <div className="panel">
        <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
          <h2 style={{ margin: 0 }}>
            <span className="side-tag p1">{game.players.p1}</span>
            <span className="dim"> vs </span>
            <span className="side-tag p2">{game.players.p2}</span>
          </h2>
          <span className="tiny dim">
            {wonBy} won{game.ended_by === "forfeit" && " by forfeit"} on turn {game.turns}
            {game.rating != null && ` · ${game.rating}`} ·{" "}
            <a href={game.url} target="_blank" rel="noreferrer">replay ↗</a>
          </span>
        </div>

        <Scrubber steps={game.steps} at={i} onPick={setI} p1={game.players.p1} />

        <div className="row" style={{ marginTop: 10 }}>
          <button className="ghost" onClick={() => step(-1)} disabled={i === 0}>← Back</button>
          <button className="ghost" onClick={() => step(1)} disabled={i === last}>Next →</button>
          <span className="tiny dim">{label(cur)} · step {i + 1} of {last + 1}</span>
          {game.wp_error && <span className="err tiny">WP unavailable: {game.wp_error}</span>}
        </div>
      </div>

      {/* The position the model was asked about, and its answer. Everything below this panel is
          what happened afterwards, so the order on screen is the order it happened in. */}
      <div className="panel">
        {cur.wp_p1 != null && <WpBar wp={cur.wp_p1} players={game.players} />}
        <h2 style={{ margin: "14px 0 10px" }}>{start(cur)}</h2>
        <div className="grid2">
          <Board side="p1" name={game.players.p1} board={cur.before.p1} />
          <Board side="p2" name={game.players.p2} board={cur.before.p2} />
        </div>
        {(cur.weather || cur.terrain) && (
          <p className="tiny dim" style={{ margin: "10px 0 0" }}>
            {[cur.weather, cur.terrain].filter(Boolean).join(" · ")}
          </p>
        )}
      </div>

      <div className="panel turn">
        <h2>Turn summary</h2>
        {cur.events.length === 0
          ? <p className="empty">—</p>
          : (
            <ul className="events">
              {cur.events.map((e, n) => (
                <li key={n} className={e.side ? `ev ev-${e.side}` : "ev"}>
                  {e.side && <SideBadge side={e.side} players={game.players} />}
                  <span>{e.text}</span>
                </li>
              ))}
            </ul>
          )}
      </div>

      <div className="panel">
        <h2>{cur.final ? "Final position" : finish(cur)}</h2>
        {cur.wp_after != null && (
          <div style={{ marginBottom: 12 }}>
            <WpBar wp={cur.wp_after} players={game.players} outcome={cur.outcome} />
          </div>
        )}
        <div className="grid2">
          <Slots side="p1" name={game.players.p1} rows={cur.slots.p1} left={cur.after.p1.left} />
          <Slots side="p2" name={game.players.p2} rows={cur.slots.p2} left={cur.after.p2.left} />
        </div>
      </div>
    </div>
  );
}

/** How a step is named. Team preview and a forced switch are decisions too, but they are not
 *  turns, so they are not called one. */
function label(s: EndgameStep): string {
  if (s.kind === "preview") return "team preview";
  return s.kind === "switch" ? `turn ${s.turn}, forced switch` : `turn ${s.turn}`;
}

function start(s: EndgameStep): string {
  if (s.kind === "preview") return "Team preview";
  return s.kind === "switch" ? `Turn ${s.turn} · replacement` : `Start of turn ${s.turn}`;
}

function finish(s: EndgameStep): string {
  if (s.kind === "preview") return "Leads";
  return s.kind === "switch" ? "After the replacement" : `End of turn ${s.turn}`;
}

/** Which side an event belongs to, in the colour the win-probability bar uses for it. The bar,
 *  the player names and these badges are the same two colours throughout, so the side is never
 *  something to work out. */
function SideBadge({ side, players }: { side: "p1" | "p2"; players: { p1: string; p2: string } }) {
  return (
    <span className={`ev-side ${side}`} title={players[side]}>
      {side === "p1" ? "1" : "2"}
    </span>
  );
}

/** The two active slots at the end of a step: who was standing there, and who is now.
 *
 *  A switch — chosen mid-turn, or forced after a faint — puts both on one line, because that is
 *  one event to a reader. A Pokémon that fainted with nothing yet sent in its place shows alone:
 *  the replacement is the *next* step's decision, not this one's outcome.
 *
 *  No HP bars here. Four names have to fit across the panel on a switch line, and the bars are
 *  already on the full board above; the number alone carries the same thing in a third the room.
 */
function Slots({ side, name, rows, left }: {
  side: "p1" | "p2"; name: string; rows: SlotRow[]; left: number;
}) {
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className={`side-tag ${side}`}>{name}</span>
        <span className="tiny dim">{left} left</span>
      </div>
      <div className="slots-after">
        {rows.length === 0 && <p className="empty" style={{ padding: 0 }}>—</p>}
        {rows.map((r) => (
          <div key={r.slot} className="slot-row">
            {r.changed && r.started && r.ended ? (
              <>
                <SlotMon mon={r.started} muted />
                <span className="slot-arrow" aria-label="replaced by">→</span>
                <SlotMon mon={r.ended} />
              </>
            ) : (
              (r.ended ?? r.started) && <SlotMon mon={(r.ended ?? r.started)!} muted={!r.ended} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function SlotMon({ mon, muted = false }: { mon: BoardMon; muted?: boolean }) {
  const ko = mon.state === "fainted";
  return (
    <span className={`slot-mon ${ko ? "fainted" : muted ? "bench" : "active"}`}>
      <span className="slot-name">{mon.forme}</span>
      {mon.status && !ko && <span className="board-status">{mon.status.toUpperCase()}</span>}
      <span className="slot-pct">{ko ? "KO" : `${Math.round(mon.hp * 100)}%`}</span>
    </span>
  );
}

/** Whether the model had already made up its mind here — used only to choose where to open. */
function confident(s: EndgameStep, minWp: number): boolean {
  return s.wp_p1 != null && Math.max(s.wp_p1, 1 - s.wp_p1) >= minWp;
}

/** Every decision point as a tick, tall for p1 and short for p2, so the shape of the game reads
 *  at a glance: where it turned, and how long the call held before the end.
 *
 *  The last tick is the result, which is why the trajectory is allowed to finish at the top or
 *  the bottom of the track. It is drawn hollow because it is the one tick that is not a model
 *  output; clicking it goes to the step the game ended on. */
function Scrubber({ steps, at, onPick, p1 }: {
  steps: EndgameStep[]; at: number; onPick: (i: number) => void; p1: string;
}) {
  const last = steps[steps.length - 1];
  const settled = last?.outcome && last.wp_after != null ? last.wp_after : null;
  return (
    <div className="scrub" role="group" aria-label="decision points">
      {steps.map((s, i) => {
        const wp = s.wp_p1;
        if (wp == null) return <span key={i} className="scrub-tick" aria-hidden="true" />;
        const label = `${s.kind === "preview" ? "preview" : `turn ${s.turn}`}: ${p1} ${(wp * 100).toFixed(0)}%`;
        return (
          <button key={i} className="scrub-tick" aria-current={i === at} title={label}
                  aria-label={label} onClick={() => onPick(i)}>
            <i style={{ height: `${Math.max(6, wp * 100)}%` }}
               className={wp >= 0.5 ? "tick-p1" : "tick-p2"} />
          </button>
        );
      })}
      {settled != null && (
        <button className="scrub-tick result" title={`result: ${p1} ${(settled * 100).toFixed(0)}%`}
                aria-label={`result: ${p1} ${(settled * 100).toFixed(0)}%`}
                onClick={() => onPick(steps.length - 1)}>
          <i style={{ height: `${Math.max(10, settled * 100)}%` }}
             className={settled >= 0.5 ? "tick-p1" : "tick-p2"} />
        </button>
      )}
    </div>
  );
}

/** The win-probability split. `outcome` marks the one case where the number is not a prediction
 *  at all: the game has finished, and 100/0 is the result. Saying so is the difference between
 *  showing the model being right and showing what happened. */
function WpBar({ wp, players, outcome = false }: {
  wp: number; players: { p1: string; p2: string }; outcome?: boolean;
}) {
  const pct = wp * 100;
  return (
    <div>
      <div className={`wp-split${outcome ? " settled" : ""}`} title={`${players.p1} ${pct.toFixed(1)}%`}>
        <i className="wp-split-p1" style={{ width: `${pct}%` }} />
      </div>
      <div className="row" style={{ justifyContent: "space-between", marginTop: 6 }}>
        <span className="small"><span className="side-tag p1">{players.p1}</span> {pct.toFixed(0)}%</span>
        {outcome && <span className="tiny dim">result</span>}
        <span className="small">{(100 - pct).toFixed(0)}% <span className="side-tag p2">{players.p2}</span></span>
      </div>
    </div>
  );
}

/** One side's sheet, as much of it as is still worth showing.
 *
 *  All six at team preview, because that is all anyone knows then, and they thin out as the game
 *  reveals which four are in it. A Pokémon nobody has seen is `unknown` — it may still come in —
 *  until the fourth of the four appears, at which point the two left over are known to be sitting
 *  this one out and there is no reason to keep them on screen. */
function Board({ side, name, board }: { side: "p1" | "p2"; name: string; board: BoardSide }) {
  const shown = board.mons.filter((m) => m.state !== "unselected");
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className={`side-tag ${side}`}>{name}</span>
        <span className="tiny dim">{board.left} left</span>
      </div>
      <div className="board">
        {shown.map((m) => {
          const ko = m.state === "fainted";
          const unknown = m.state === "unknown";
          return (
            <div key={m.species} className={`board-mon ${m.state}`}>
              <span className="board-name">
                {m.forme}
                {m.status && !ko && <span className="board-status">{m.status.toUpperCase()}</span>}
              </span>
              <span className={`board-hp${unknown ? " unknown" : ""}`}>
                {!unknown && (
                  <i style={{ width: `${Math.round(m.hp * 100)}%` }}
                     className={m.hp > 0.5 ? "hp-ok" : m.hp > 0.2 ? "hp-low" : "hp-crit"} />
                )}
              </span>
              <span className="tiny dim board-pct">
                {ko ? "KO" : unknown ? "?" : `${Math.round(m.hp * 100)}%`}
              </span>
            </div>
          );
        })}
      </div>
      {board.conditions.length > 0 && (
        <p className="tiny dim" style={{ margin: "6px 0 0" }}>{board.conditions.join(", ")}</p>
      )}
    </div>
  );
}
