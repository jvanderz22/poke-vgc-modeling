import { useCallback, useEffect, useState } from "react";
import {
  api, ApiError,
  type BoardMon, type BoardSide, type Endgame, type EndgameDetail, type EndgameIndex,
  type EndgameStep,
  type Slot as SlotRow,
} from "../api";
import { linkProps, type EndgameFilter as Filter, type Navigate, type Route } from "../router";

/** The endgames page's slice of the route: which game, which step, which filter. */
type EndgameRoute = Extract<Route, { tab: "endgames" }>;

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
export function EndgamesPanel({ reg, route, navigate }: {
  reg: string; route: EndgameRoute; navigate: Navigate;
}) {
  const [index, setIndex] = useState<EndgameIndex | null>(null);
  const [error, setError] = useState("");
  const { filter, replay: selected } = route;

  useEffect(() => {
    let live = true;
    api.endgames(reg, filter)
      .then((r) => { if (live) { setIndex(r); setError(""); } })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e.message : String(e)); });
    return () => { live = false; };
  }, [reg, filter]);

  // A game the current filter hides is no longer the selection. Replacing rather than pushing:
  // the selection was dropped by a filter change that is already in history.
  useEffect(() => {
    if (index?.built && selected && !index.games.some((g) => g.replay === selected)) {
      navigate({ ...route, replay: null, step: null }, { replace: true });
    }
  }, [index, selected, route, navigate]);

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
      <SetHeader index={index} route={route} navigate={navigate} />
      <div className="split">
        <GameList games={index.games} route={route} navigate={navigate} />
        {selected
          ? <GameStepper key={selected} replay={selected} reg={reg} route={route}
                         navigate={navigate} minWp={index.criteria?.min_wp ?? 0.9} />
          : <div className="panel"><p className="empty">Pick a game.</p></div>}
      </div>
    </>
  );
}

function SetHeader({ index, route, navigate }: {
  index: EndgameIndex; route: EndgameRoute; navigate: Navigate;
}) {
  const filter = route.filter;
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
            <a key={f.id} className="seg" aria-pressed={filter === f.id}
               {...linkProps({ ...route, filter: f.id }, navigate)}>
              {f.label}
            </a>
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

function GameList({ games, route, navigate }: {
  games: Endgame[]; route: EndgameRoute; navigate: Navigate;
}) {
  if (!games.length) return <div className="panel"><p className="empty">Nothing matches this filter.</p></div>;
  return (
    <div className="panel game-list">
      {games.map((g) => (
        // A game is the thing worth linking to, so each row is a real link to its own URL.
        <a key={g.replay} className="game-row" aria-current={g.replay === route.replay}
           {...linkProps({ ...route, replay: g.replay, step: null }, navigate)}>
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
        </a>
      ))}
    </div>
  );
}

function GameStepper({ replay, reg, route, navigate, minWp }: {
  replay: string; reg: string; route: EndgameRoute; navigate: Navigate; minWp: number;
}) {
  const [game, setGame] = useState<EndgameDetail | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let live = true;
    setGame(null); setError("");
    api.endgame(replay, reg)
      .then((d) => { if (live) setGame(d); })
      .catch((e) => { if (live) setError(e instanceof ApiError ? e.message : String(e)); });
    return () => { live = false; };
  }, [replay, reg]);

  // The result is a view of its own, one past the last turn: the whole point of clicking it is to
  // see where the game ended up, without the position and the turn that got there above it.
  const settled = game && game.steps.length > 0 && game.steps[game.steps.length - 1].outcome;
  const views = (game?.steps.length ?? 0) + (settled ? 1 : 0);

  // Which view is on screen is the URL's business. Without a step in it, open on the moment the
  // game stopped being in doubt — that is the position the set is making a claim about, and
  // winding forward to it every time is friction.
  const fallback = game ? Math.max(0, game.steps.findIndex((s) => confident(s, minWp))) : 0;
  const i = route.step != null ? Math.min(route.step - 1, views - 1) : fallback;

  const go = useCallback((n: number, replace = true) => {
    // Stepping refines the game you are already looking at, so it replaces rather than pushes:
    // the turn stays linkable without the back button becoming a rewind key.
    navigate({ ...route, step: Math.max(1, n + 1) }, { replace });
  }, [navigate, route]);
  const step = useCallback((d: number) => {
    if (i + d >= 0 && i + d < views) go(i + d);
  }, [go, i, views]);

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

  const last = views - 1;
  const isResult = i >= game.steps.length;
  const cur = game.steps[Math.min(i, game.steps.length - 1)];
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

        <Scrubber steps={game.steps} at={i} onPick={go} p1={game.players.p1} settled={!!settled} />

        <div className="row" style={{ marginTop: 10 }}>
          <button className="ghost" onClick={() => step(-1)} disabled={i === 0}>← Back</button>
          <button className="ghost" onClick={() => step(1)} disabled={i === last}>Next →</button>
          <span className="tiny dim">
            {isResult ? "result" : label(cur)} · step {i + 1} of {last + 1}
          </span>
          {game.wp_error && <span className="err tiny">WP unavailable: {game.wp_error}</span>}
          {/* What the step did to the number, beside the controls so it is on screen whichever
              panel you have scrolled to. The result view is the exception: it is the end state,
              not a step, so it shows where the game landed and not how it got there. */}
          {cur.wp_p1 != null && (
            <span className="step-wp">
              {isResult && cur.wp_after != null ? (
                <Pair wp={cur.wp_after} title="the result" />
              ) : (
                <>
                  <Pair wp={cur.wp_p1} title="at this position" />
                  {cur.wp_after != null && (
                    <>
                      <span className="dim" aria-label="becomes">→</span>
                      <Pair wp={cur.wp_after} title="after this step" />
                    </>
                  )}
                </>
              )}
            </span>
          )}
        </div>
      </div>

      {isResult ? (
        <div className="panel">
          {cur.wp_after != null && (
            <WpBar wp={cur.wp_after} players={game.players} outcome={cur.outcome} />
          )}
          <h2 style={{ margin: "14px 0 10px" }}>Final position</h2>
          <div className="grid2">
            <Board side="p1" name={game.players.p1} board={cur.after.p1} />
            <Board side="p2" name={game.players.p2} board={cur.after.p2} />
          </div>
        </div>
      ) : (
      <>
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
      </>
      )}
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

/** Every decision point as a tick split between the two sides: p1 grows up from the bottom, p2
 *  down from the top, and together they always fill it.
 *
 *  Both colours are always drawn, which is the point. A single bar whose height was p1's win
 *  probability but whose colour flipped at 50% meant a short orange tick read as "orange is
 *  losing" when it said the opposite; splitting it removes the ambiguity, because each side's
 *  share of the tick is its own number and nothing else.
 *
 *  The last tick is the result, which is why the trajectory is allowed to finish flush against
 *  one end. It is outlined because it is the one tick that is not a model output; clicking it
 *  opens the final position on its own. */
function Scrubber({ steps, at, onPick, p1, settled }: {
  steps: EndgameStep[]; at: number; onPick: (i: number) => void; p1: string; settled: boolean;
}) {
  const last = steps[steps.length - 1];
  const result = settled && last?.wp_after != null ? last.wp_after : null;
  return (
    <div className="scrub" role="group" aria-label="decision points">
      {steps.map((s, i) => {
        const wp = s.wp_p1;
        if (wp == null) return <span key={i} className="scrub-tick" aria-hidden="true" />;
        const label = `${s.kind === "preview" ? "preview" : `turn ${s.turn}`}: ${p1} ${(wp * 100).toFixed(0)}%`;
        return (
          <button key={i} className="scrub-tick" aria-current={i === at} title={label}
                  aria-label={label} onClick={() => onPick(i)}>
            <Split wp={wp} />
          </button>
        );
      })}
      {result != null && (
        <button className="scrub-tick result" title={`result: ${p1} ${(result * 100).toFixed(0)}%`}
                aria-label={`result: ${p1} ${(result * 100).toFixed(0)}%`}
                aria-current={at >= steps.length} onClick={() => onPick(steps.length)}>
          <Split wp={result} />
        </button>
      )}
    </div>
  );
}

/** A win probability as both sides' shares, in the colours they carry everywhere else. */
function Pair({ wp, title }: { wp: number; title: string }) {
  return (
    <span className="wp-pair" title={title}>
      <span className="side-tag p1">{(wp * 100).toFixed(0)}%</span>
      <span className="dim">/</span>
      <span className="side-tag p2">{((1 - wp) * 100).toFixed(0)}%</span>
    </span>
  );
}

/** One tick's two segments: p2 from the top, p1 from the bottom, meeting at the split. */
function Split({ wp }: { wp: number }) {
  return (
    <>
      <i className="tick-p2" style={{ height: `${(1 - wp) * 100}%` }} />
      <i className="tick-p1" style={{ height: `${wp * 100}%` }} />
    </>
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

const MARKS: Record<BoardMon["state"], string> = {
  active: "on the field",
  bench: "brought, off the field",
  fainted: "brought, knocked out",
  unknown: "not seen yet — may or may not have been brought",
  unselected: "not brought",
};

/** One side's whole sheet, all six, with a marker for what is known about each.
 *
 *  Nothing is dropped: the two that were left behind are part of reading the game, because what
 *  someone chose not to bring against this opponent is a decision as much as what they did. The
 *  marker carries the distinction the sheet cannot — `?` for a Pokémon nobody has seen, which may
 *  still come in, and `O` once the fourth of the four has appeared and the rest are known to be
 *  sitting this one out. */
function Board({ side, name, board }: { side: "p1" | "p2"; name: string; board: BoardSide }) {
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className={`side-tag ${side}`}>{name}</span>
        <span className="tiny dim">{board.left} left</span>
      </div>
      <div className="board">
        {board.mons.map((m) => {
          const ko = m.state === "fainted";
          // Neither of these has an HP bar to draw: one has not been seen, the other is not in
          // the game. An empty track keeps the rows aligned without inventing a number.
          const noBar = m.state === "unknown" || m.state === "unselected";
          return (
            <div key={m.species} className={`board-mon ${m.state}`}>
              <span className="board-name">
                {m.forme}
                {m.status && !ko && <span className="board-status">{m.status.toUpperCase()}</span>}
              </span>
              <span className={`board-hp${noBar ? " empty" : ""}`}>
                {!noBar && (
                  <i style={{ width: `${Math.round(m.hp * 100)}%` }}
                     className={m.hp > 0.5 ? "hp-ok" : m.hp > 0.2 ? "hp-low" : "hp-crit"} />
                )}
              </span>
              <span className="tiny dim board-pct" title={MARKS[m.state]}>
                {ko ? "KO" : m.state === "unknown" ? "?" : m.state === "unselected" ? "O"
                  : `${Math.round(m.hp * 100)}%`}
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
