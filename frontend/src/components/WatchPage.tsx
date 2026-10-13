import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Species } from "../api";
import { filled, SHEET_SIZE, toShowdown } from "../sheet";
import type { Navigate, Route } from "../router";
import {
  CaptureError, emptyWatch, isSheet, mergeRead, parseYouTube, startCapture, type Capture, type WatchSheets,
} from "../watch";
import { BattleSession } from "./BattleSession";
import { SheetForm } from "./SheetForm";

type WatchRoute = Extract<Route, { tab: "watch" }>;

/** Watching a game on YouTube: the video on one half of the page, the match on the other.
 *
 *  Before the battle, the match half is both players' sheets, and the page can read them off the
 *  video as the broadcast shows them. Once it starts, it is the ordinary battle page, entered by
 *  hand beside the video. Reading the battle itself off the video is PLAN-video's next step. */
export function WatchPage({ reg, route, navigate, pool }: {
  reg: string;
  route: WatchRoute;
  navigate: Navigate;
  pool: Species[];
}) {
  const [watch, setWatch] = useWatchDraft(`vgc:${reg}:watch`);

  // The battle page navigates to itself (stepping back through turns, the next game of a Bo3).
  // Here those stay beside the video; anything else leaves the page as it would anyway.
  const inPage = useCallback<Navigate>((to, options) => {
    if (to.tab === "battle" && to.battle) {
      navigate({ tab: "watch", battle: to.battle, step: to.step, video: route.video, start: route.start }, options);
    } else {
      navigate(to, options);
    }
  }, [navigate, route.video, route.start]);

  return (
    <div className="watch-page">
      <VideoColumn reg={reg} route={route} navigate={navigate}
                   watch={watch} onWatch={setWatch} reading={!route.battle} />
      <div className="watch-match">
        {route.battle
          ? <BattleSession reg={reg} route={{ tab: "battle", battle: route.battle, step: route.step }}
                           navigate={inPage} teams={[]} />
          : <WatchSetup reg={reg} route={route} navigate={navigate} pool={pool} watch={watch} onWatch={setWatch} />}
      </div>
    </div>
  );
}

/** Both sheets and both names, kept across reloads like the other sheet forms. */
function useWatchDraft(key: string): [WatchSheets, (w: WatchSheets | ((w: WatchSheets) => WatchSheets)) => void] {
  const [watch, setState] = useState<WatchSheets>(() => {
    try {
      const raw = localStorage.getItem(key);
      const w = raw ? JSON.parse(raw) : null;
      return w?.sheets?.length === 2 && w.sheets.every((s: unknown[]) => s.length === SHEET_SIZE) ? w : emptyWatch();
    } catch { return emptyWatch(); }
  });
  const set = useCallback((next: WatchSheets | ((w: WatchSheets) => WatchSheets)) => {
    setState((cur) => {
      const w = typeof next === "function" ? next(cur) : next;
      try { localStorage.setItem(key, JSON.stringify(w)); } catch { /* private mode: kept in memory */ }
      return w;
    });
  }, [key]);
  return [watch, set];
}

function VideoColumn({ reg, route, navigate, watch, onWatch, reading }: {
  reg: string;
  route: WatchRoute;
  navigate: Navigate;
  watch: WatchSheets;
  onWatch: (f: (w: WatchSheets) => WatchSheets) => void;
  reading: boolean;
}) {
  const [link, setLink] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);
  const player = useRef<HTMLDivElement>(null);
  const capture = useRef<Capture | null>(null);
  const [capturing, setCapturing] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<string[]>([]);
  const [captureError, setCaptureError] = useState<string | null>(null);

  // The capture reads the latest sheets through a ref: it outlives any one render.
  const latest = useRef(watch);
  latest.current = watch;
  const full = (w: WatchSheets) => w.sheets.every((s) => s.every((e) => e.species && e.ability && e.nature
    && e.item && e.moves.every(Boolean)));
  const wanted = () => reading && !full(latest.current);

  const stop = useCallback(() => {
    capture.current?.stop();
    capture.current = null;
    setCapturing(false);
  }, []);
  useEffect(() => stop, [stop, route.video]);
  useEffect(() => { if (!reading) stop(); }, [reading, stop]);

  async function start() {
    if (!player.current) return;
    setCaptureError(null);
    try {
      capture.current = await startCapture(player.current, async (jpeg) => {
        try {
          const read = await api.readSheet(jpeg, reg);
          const at = new Date().toLocaleTimeString();
          if (!isSheet(read)) { setStatus(`No sheet on screen (checked ${at}).`); return; }
          const merged = mergeRead(latest.current, read);
          if (merged.slot === null) {
            setStatus(`A third team's sheet is on screen (${at}); both players already have one.`);
            return;
          }
          latest.current = merged;
          onWatch(() => ({ sheets: merged.sheets, names: merged.names }));
          setConflicts((c) => [...new Set([...c, ...merged.conflicts])]);
          setStatus(`Read ${read.title ? `${read.title}'s` : "a"} sheet into Player ${merged.slot + 1} (${at}).`);
        } catch (e) {
          setCaptureError(e instanceof ApiError ? e.message : String(e));
          stop();
        }
      }, wanted, stop);
      setCapturing(true);
      setStatus("Reading: put a team sheet on screen.");
    } catch (e) {
      // Declining the share prompt lands here too, and needs no message.
      if (e instanceof CaptureError) setCaptureError(e.message);
      else if ((e as Error).name !== "NotAllowedError") setCaptureError(String((e as Error).message ?? e));
    }
  }

  if (!route.video) {
    return (
      <div className="watch-video">
        <div className="panel">
          <h2>Watch a game</h2>
          <form onSubmit={(e) => {
            e.preventDefault();
            const v = parseYouTube(link);
            if (!v) { setLinkError("That isn't a YouTube link."); return; }
            navigate({ ...route, video: v.video, start: v.start });
          }}>
            <div className="row">
              <input value={link} onChange={(e) => { setLink(e.target.value); setLinkError(null); }}
                     placeholder="YouTube link, with the time to start at if you like" style={{ flex: 1 }} />
              <button type="submit" disabled={!link.trim()}>Open</button>
            </div>
          </form>
          {linkError && <p className="small err" style={{ margin: "6px 0 0" }}>{linkError}</p>}
          <p className="tiny dim" style={{ margin: "8px 2px 0" }}>
            The video plays on this half of the page and the match goes on the other. "Share" on
            YouTube, with "Start at" ticked, gives a link that opens where the team sheets are shown.
          </p>
        </div>
      </div>
    );
  }

  const filledSheets = watch.sheets.filter((s) => filled(s) === SHEET_SIZE).length;
  return (
    <div className="watch-video">
      {/* Only this box is captured, so nothing of the page's own may sit inside it. */}
      <div ref={player} className="watch-player">
        <iframe src={`https://www.youtube.com/embed/${route.video}?start=${route.start}&rel=0`}
                title="Video" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen />
      </div>
      <div className="row watch-controls">
        {reading && (capturing
          ? <button className="ghost" onClick={stop}>Stop reading</button>
          : <button onClick={start}>Read sheets from the video</button>)}
        {capturing && !wanted() && <span className="tiny dim">Both sheets are full, so nothing more is read.</span>}
        {capturing && wanted() && status && <span className="tiny dim">{status}</span>}
        {!capturing && reading && filledSheets < 2 && (
          <span className="tiny dim">Chrome or Edge: share this tab when asked, and only the player is read.</span>
        )}
        <span style={{ flex: 1 }} />
        <button className="link tiny" onClick={() => navigate({ ...route, video: null, start: 0 })}>change video</button>
      </div>
      {captureError && <p className="small err" style={{ margin: "4px 2px" }}>{captureError}</p>}
      {conflicts.length > 0 && (
        <div className="tiny warn-text" style={{ margin: "4px 2px" }}>
          A later frame disagreed with what is in the form; the form kept its value. Check:
          <ul style={{ margin: "2px 0 0 16px", padding: 0 }}>{conflicts.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
    </div>
  );
}

function WatchSetup({ reg, route, navigate, pool, watch, onWatch }: {
  reg: string;
  route: WatchRoute;
  navigate: Navigate;
  pool: Species[];
  watch: WatchSheets;
  onWatch: (f: (w: WatchSheets) => WatchSheets) => void;
}) {
  const [bestOf, setBestOf] = useState<1 | 3>(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [p1, p2] = watch.sheets;
  const ready = filled(p1) === SHEET_SIZE && filled(p2) === SHEET_SIZE;

  const setSheet = (i: 0 | 1) => (s: typeof p1) =>
    onWatch((w) => ({ ...w, sheets: i === 0 ? [s, w.sheets[1]] : [w.sheets[0], s] }));
  const setName = (i: 0 | 1) => (n: string) =>
    onWatch((w) => ({ ...w, names: i === 0 ? [n, w.names[1]] : [w.names[0], n] }));

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const [n1, n2] = watch.names.map((n) => n.trim());
      const v = await api.newBattle({
        name: n1 || n2 ? `${n1 || "P1"} vs ${n2 || "P2"}` : "", regulation: reg, best_of: bestOf,
        p1_team: toShowdown(p1), their_team: toShowdown(p2),
      });
      navigate({ ...route, battle: v.id, step: null });
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="panel">
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
        <h2 style={{ margin: 0 }}>The two sheets</h2>
        <span className="row">
          <button className="link tiny" onClick={() => onWatch((w) => ({
            sheets: [w.sheets[1], w.sheets[0]], names: [w.names[1], w.names[0]],
          }))}>swap players</button>
          <button className="link tiny" onClick={() => onWatch(() => emptyWatch())}>clear both</button>
        </span>
      </div>
      {([0, 1] as const).map((i) => (
        <div key={i} className="watch-sheet">
          <input value={watch.names[i]} onChange={(e) => setName(i)(e.target.value)}
                 placeholder={`Player ${i + 1}'s name`} className="watch-name" />
          <SheetForm reg={reg} pool={pool} value={watch.sheets[i]} onChange={setSheet(i)}
                     label={`Player ${i + 1}'s open team sheet`} />
        </div>
      ))}
      <div className="row" style={{ marginTop: 10 }}>
        <div className="toggle" role="group" aria-label="Best of">
          <button className="seg" aria-pressed={bestOf === 1} onClick={() => setBestOf(1)}>Bo1</button>
          <button className="seg" aria-pressed={bestOf === 3} onClick={() => setBestOf(3)}>Bo3</button>
        </div>
        <button disabled={busy || !ready} onClick={start}>Start watching</button>
        {!ready && <span className="tiny dim">{filled(p1)}/{SHEET_SIZE} and {filled(p2)}/{SHEET_SIZE} on the sheets</span>}
      </div>
      {error && <p className="small err" style={{ marginBottom: 0 }}>{error}</p>}
      <p className="tiny dim" style={{ margin: "10px 2px 0" }}>
        Someone else's game: both sheets are shown and neither side's Stat Points are, so both
        Speeds are worked out from the battle, and the number is player 1's chance.
      </p>
    </div>
  );
}
