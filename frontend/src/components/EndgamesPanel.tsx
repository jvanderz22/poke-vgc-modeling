import { useCallback, useEffect, useState } from "react";
import {
  api, ApiError,
  type Endgame, type EndgameDetail, type EndgameIndex, type EndgameStep,
} from "../api";

type Filter = "all" | "played_out" | "misses";

const FILTERS: { id: Filter; label: string; hint: string }[] = [
  { id: "all", label: "All", hint: "every game in the set" },
  { id: "played_out", label: "Played out", hint: "no forfeits — someone had to finish the job" },
  { id: "misses", label: "Model was wrong", hint: "the favoured side went on to lose" },
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
        <p className="small dim" style={{ margin: "0 0 10px" }}>
          The set is built offline because it reads every cached replay — about a minute.
        </p>
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
          : <div className="panel"><p className="empty">Pick a game to step through it.</p></div>}
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
        {c && <>
          Human vs human, open team sheets, and the model held {(c.min_wp * 100).toFixed(0)}%+ on
          one side for the last {c.hold} decision points.{" "}
        </>}
        {counts && <>
          Drawn from {counts.eligible.toLocaleString()} held-out games
          ({counts.cached.toLocaleString()} replays cached) — games{" "}
          <strong>{index.version}</strong> never trained on, so this is not the model marking its
          own homework.
        </>}
      </p>
      {index.gates?.in_battle_pass === false && (
        <p className="small err" style={{ margin: "6px 0 0" }}>
          Note: {index.version} does not pass its in-battle gates. Read these numbers as a
          demonstration, not a calibrated claim.
        </p>
      )}

      <div className="row" style={{ marginTop: 10 }}>
        <div className="toggle">
          {FILTERS.map((f) => (
            <button key={f.id} className="seg" title={f.hint}
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
  if (!game) return <div className="panel"><p className="empty">Loading the replay…</p></div>;

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
          <span className="tiny dim">
            {cur.kind === "preview" ? "team preview"
              : cur.kind === "end" ? "the finish"
              : cur.kind === "switch" ? `turn ${cur.turn}, forced switch` : `turn ${cur.turn}`}
            {" · "}step {i + 1} of {last + 1} · arrow keys work
          </span>
          {game.wp_error && <span className="err tiny">WP unavailable: {game.wp_error}</span>}
        </div>
      </div>

      <div className="panel">
        <WpBar wp={cur.wp_p1} players={game.players} />
        <div className="grid2" style={{ marginTop: 12 }}>
          <Board side="p1" name={game.players.p1} board={cur.board.p1} />
          <Board side="p2" name={game.players.p2} board={cur.board.p2} />
        </div>
        {(cur.weather || cur.terrain) && (
          <p className="tiny dim" style={{ margin: "10px 0 0" }}>
            {[cur.weather, cur.terrain].filter(Boolean).join(" · ")}
          </p>
        )}
      </div>

      <div className="panel turn">
        <h2>
          {cur.kind === "preview" ? "Both sheets are on the table"
            : cur.kind === "end" ? "How it finished"
            : "What happened next"}
        </h2>
        {cur.events.length === 0
          ? <p className="empty">Nothing worth narrating.</p>
          : (
            <ul className="events">
              {cur.events.map((e, n) => (
                <li key={n} className={e.side ? `ev ev-${e.side}` : "ev"}>
                  {e.side && (
                    <span className="badge rounded-pill text-bg-dark ev-side">
                      {e.side === "p1" ? "1" : "2"}
                    </span>
                  )}
                  <span>{e.text}</span>
                </li>
              ))}
            </ul>
          )}
      </div>
    </div>
  );
}

/** Whether the model had already made up its mind here — used only to choose where to open. */
function confident(s: EndgameStep, minWp: number): boolean {
  return s.wp_p1 != null && Math.max(s.wp_p1, 1 - s.wp_p1) >= minWp;
}

/** Every decision point as a tick, tall for p1 and short for p2, so the shape of the game reads
 *  at a glance: where it turned, and how long the call held before the end. */
function Scrubber({ steps, at, onPick, p1 }: {
  steps: EndgameStep[]; at: number; onPick: (i: number) => void; p1: string;
}) {
  return (
    <div className="scrub" role="group" aria-label="decision points">
      {steps.map((s, i) => {
        const wp = s.wp_p1;
        const label = wp == null
          ? "the finish"
          : `${s.kind === "preview" ? "preview" : `turn ${s.turn}`}: ${p1} ${(wp * 100).toFixed(0)}%`;
        return (
          <button key={i} className="scrub-tick" aria-current={i === at} title={label}
                  aria-label={label} onClick={() => onPick(i)}>
            <i style={wp == null ? undefined : { height: `${Math.max(6, wp * 100)}%` }}
               className={wp == null ? "tick-end" : wp >= 0.5 ? "tick-p1" : "tick-p2"} />
          </button>
        );
      })}
    </div>
  );
}

function WpBar({ wp, players }: { wp: number | null; players: { p1: string; p2: string } }) {
  if (wp == null) {
    return <p className="small dim" style={{ margin: 0 }}>The game is over — there is nothing left to predict.</p>;
  }
  const pct = wp * 100;
  return (
    <div>
      <div className="wp-split" title={`${players.p1} ${pct.toFixed(1)}%`}>
        <i className="wp-split-p1" style={{ width: `${pct}%` }} />
      </div>
      <div className="row" style={{ justifyContent: "space-between", marginTop: 6 }}>
        <span className="small"><span className="side-tag p1">{players.p1}</span> {pct.toFixed(0)}%</span>
        <span className="small">{(100 - pct).toFixed(0)}% <span className="side-tag p2">{players.p2}</span></span>
      </div>
    </div>
  );
}

const SHOWN = new Set(["active", "bench", "fainted"]);

function Board({ side, name, board }: {
  side: "p1" | "p2"; name: string; board: { left: number; mons: { species: string; forme: string; state: string; hp: number; status: string | null; position: number | null }[]; conditions: string[] };
}) {
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
        <span className={`side-tag ${side}`}>{name}</span>
        <span className="tiny dim">{board.left} left</span>
      </div>
      <div className="board">
        {board.mons.filter((m) => SHOWN.has(m.state)).map((m) => (
          <div key={m.species} className={`board-mon ${m.state}`}>
            <span className="board-name">
              {m.forme}
              {m.status && <span className="badge rounded-pill text-bg-dark board-status">{m.status.toUpperCase()}</span>}
            </span>
            <span className="board-hp">
              <i style={{ width: `${Math.round(m.hp * 100)}%` }}
                 className={m.hp > 0.5 ? "hp-ok" : m.hp > 0.2 ? "hp-low" : "hp-crit"} />
            </span>
            <span className="tiny dim board-pct">
              {m.state === "fainted" ? "KO" : `${Math.round(m.hp * 100)}%`}
            </span>
          </div>
        ))}
      </div>
      {board.conditions.length > 0 && (
        <p className="tiny dim" style={{ margin: "6px 0 0" }}>{board.conditions.join(", ")}</p>
      )}
    </div>
  );
}
