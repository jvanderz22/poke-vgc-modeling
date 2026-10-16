import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type Entry, type Problem, type ReadStatus, type Species } from "../api";
import { filled, SHEET_SIZE, toShowdown } from "../sheet";
import type { Navigate, Route } from "../router";
import {
  CaptureError, download, emptyWatch, isSheet, mergeRead, parseYouTube, readFrames, stamp, startCapture,
  startRecording, streamFrames, type Capture, type Clip, type WatchSheets,
} from "../watch";
import { mountPlayer, type YouTubePlayer } from "../youtube";
import { BattleSession } from "./BattleSession";
import { SheetForm } from "./SheetForm";

type WatchRoute = Extract<Route, { tab: "watch" }>;

/** Watching a game on YouTube: the video on one half of the page, the match on the other.
 *
 *  Before the battle, the match half is both players' sheets, and the page can read them off the
 *  video as the broadcast shows them. Once it starts, it is the ordinary battle page beside the
 *  video, which the battle reader can fill as the game plays (PLAN-video step 4), and which can
 *  always be corrected by hand. */
export function WatchPage({ reg, route, navigate, pool }: {
  reg: string;
  route: WatchRoute;
  navigate: Navigate;
  pool: Species[];
}) {
  const [watch, setWatch] = useWatchDraft(`vgc:${reg}:watch`);
  // Bumped when the battle reader writes to the journal, so the battle half shows it.
  const [refresh, setRefresh] = useState(0);
  const onRead = useCallback(() => setRefresh((n) => n + 1), []);
  // While the reader is on it writes turn 1 itself, at the first choice screen; the battle half
  // starting it too would put the reader's turns one ahead.
  const [readerOn, setReaderOn] = useState(false);
  // The taps made by hand, hidden until asked for: the reader fills the journal, and the match
  // half is for watching it. Shown again on each visit.
  const [manual, setManual] = useState(false);
  // The player's time, set by the video half. An entry made by hand carries it, as the reader's
  // do, so a transcription lines up with the video and with a read of it (PLAN-video step 3).
  const videoTime = useRef<() => number | null>(() => null);
  const stampEntries = useCallback((entries: Entry[]) => {
    const at = videoTime.current();
    return at === null ? entries : entries.map((e) => ("video_s" in e ? e : { ...e, video_s: Math.round(at * 100) / 100 }));
  }, []);

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
                   watch={watch} onWatch={setWatch} reading={!route.battle} onRead={onRead} onReaderOn={setReaderOn}
                   onPlayer={(f) => { videoTime.current = f; }}
                   manual={manual} onManual={setManual} />
      <div className="watch-match">
        {route.battle
          ? <BattleSession reg={reg} route={{ tab: "battle", battle: route.battle, step: route.step }}
                           navigate={inPage} teams={[]} refresh={refresh} stamp={stampEntries}
                           autoStart={!readerOn} manual={manual} onManual={setManual} />
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

function VideoColumn({ reg, route, navigate, watch, onWatch, reading, onRead, onReaderOn, onPlayer, manual, onManual }: {
  reg: string;
  route: WatchRoute;
  navigate: Navigate;
  watch: WatchSheets;
  onWatch: (f: (w: WatchSheets) => WatchSheets) => void;
  reading: boolean;
  onRead: () => void;
  onReaderOn: (on: boolean) => void;
  onPlayer: (time: () => number | null) => void;
  manual: boolean;
  onManual: (on: boolean) => void;
}) {
  const [link, setLink] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const player = useRef<YouTubePlayer | null>(null);
  const [playerProblem, setPlayerProblem] = useState<string | null>(null);
  const capture = useRef<Capture | null>(null);
  const [capturing, setCapturing] = useState(false);
  const recorder = useRef<{ stop: () => Promise<Clip> } | null>(null);
  const [recordingSince, setRecordingSince] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [status, setStatus] = useState<string | null>(null);
  const [conflicts, setConflicts] = useState<string[]>([]);
  const [captureError, setCaptureError] = useState<string | null>(null);
  // A moment marked for review: the note being written, and the last one saved.
  const [flagNote, setFlagNote] = useState<string | null>(null);
  const [flagged, setFlagged] = useState<string | null>(null);
  // The battle reader: on while frames are going to it, and what it said last.
  // On by default: once there is a battle, a captured player is read. Waiting for a second button
  // read nothing in prod (2026-10-16), with the capture running and no sign anything was wrong.
  const [battleReading, setBattleReading] = useState(true);
  const [readStatus, setReadStatus] = useState<ReadStatus | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const answered = useRef<number[]>([]);
  // Where a battle's frames are OCR'd: on this page's GPU once the models have loaded, on the
  // server before then or without WebGPU (the deployed server's shared CPU can't keep up alone).
  const [ocr, setOcr] = useState<{ where: "loading" | "page" | "server"; why?: string } | null>(null);

  // The capture reads the latest sheets through a ref: it outlives any one render.
  const latest = useRef(watch);
  latest.current = watch;
  const full = (w: WatchSheets) => w.sheets.every((s) => s.every((e) => e.species && e.ability && e.nature
    && e.item && e.moves.every(Boolean)));
  const sheetsWanted = reading && !full(watch);

  const videoTime = () => {
    const p = player.current;
    return p ? { video: Math.round(p.getCurrentTime() * 1000) / 1000, playing: p.getPlayerState() === 1 }
             : { video: null, playing: false };
  };

  useEffect(() => {
    if (!route.video || !host.current) return;
    setPlayerProblem(null);
    return mountPlayer(host.current, route.video, route.start,
                       (p) => { player.current = p; onPlayer(() => p.getCurrentTime()); },
                       (message) => setPlayerProblem(message));
  }, [route.video, route.start]);

  const stopRecording = useCallback(async () => {
    const r = recorder.current;
    recorder.current = null;
    setRecordingSince(null);
    if (!r) return;
    const clip = await r.stop();
    const first = clip.meta.times.find((t) => t.video !== null)?.video ?? null;
    const name = `${clip.meta.video}-${stamp(first)}`;
    download(clip.webm, `${name}.webm`);
    download(new Blob([JSON.stringify(clip.meta)], { type: "application/json" }), `${name}.json`);
  }, []);

  const stop = useCallback(() => {
    if (recorder.current) void stopRecording();
    capture.current?.stop();
    capture.current = null;
    setCapturing(false);
  }, [stopRecording]);
  useEffect(() => stop, [stop, route.video]);

  // Sheets are read while there is no battle yet and a field is still empty.
  useEffect(() => {
    if (!capturing || !sheetsWanted || !capture.current) return;
    setStatus("Reading: put a team sheet on screen.");
    return readFrames(capture.current, async (jpeg) => {
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
        if (e instanceof ApiError && e.status === 429) return;  // the server is still on earlier frames
        setCaptureError(e instanceof ApiError ? e.message : String(e));
      }
    }, () => !full(latest.current));
  }, [capturing, sheetsWanted, reg, onWatch]);

  // The battle is read while there is one, the player is captured, and reading hasn't been stopped:
  // a frame every quarter second while the video plays, none while it is paused.
  const battle = route.battle;
  useEffect(() => {
    if (!capturing || !battleReading || !battle || !capture.current || !route.video) return;
    const video = route.video;
    answered.current = [];
    const playing = () => {
      const p = player.current;
      return p && p.getPlayerState() === 1 ? p.getCurrentTime() : null;
    };
    return streamFrames(capture.current, playing, async (jpeg, at, read) => {
      try {
        const s = await api.readFrame(battle, jpeg, Math.round(at * 1000) / 1000, video, reg, read);
        if (s.unread?.length) capture.current?.resetOcr();
        const now = Date.now();
        answered.current = [...answered.current.filter((x) => now - x < 5000), now];
        setReadStatus(s);
        setReadError(null);
        if (s.entries.length || s.resynced) onRead();
        if (s.ended) setBattleReading(false);
      } catch (e) {
        setReadError(e instanceof ApiError ? e.message : String(e));
        throw e;
      }
    });
  }, [capturing, battleReading, battle, route.video, reg, onRead]);

  // A different battle (the next game of a Bo3) starts unread.
  useEffect(() => { setBattleReading(true); setReadStatus(null); }, [battle]);
  useEffect(() => { onReaderOn(capturing && battleReading && !!battle); }, [capturing, battleReading, battle, onReaderOn]);

  async function stopReading() {
    setBattleReading(false);
    if (!battle) return;
    try {
      await api.stopReading(battle, reg);
      onRead();
    } catch (e) {
      setReadError(e instanceof ApiError ? e.message : String(e));
    }
  }

  useEffect(() => {
    if (recordingSince === null) return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [recordingSince]);

  async function start() {
    if (!box.current) return;
    setCaptureError(null);
    try {
      const cap = await startCapture(box.current, () => {
        capture.current = null;
        setCapturing(false);
        setOcr(null);
        if (recorder.current) void stopRecording();
      });
      capture.current = cap;
      setCapturing(true);
      // Frames go to the server unread until the models are on the GPU (a 31 MB download, once).
      setOcr({ where: "loading" });
      api.ocrConfig().then((config) => cap.startOcr(config)).then(
        () => setOcr({ where: "page" }),
        (e) => setOcr({ where: "server", why: e instanceof Error ? e.message : String(e) }),
      );
    } catch (e) {
      // Declining the share prompt lands here too, and needs no message.
      if (e instanceof CaptureError) setCaptureError(e.message);
      else if ((e as Error).name !== "NotAllowedError") setCaptureError(String((e as Error).message ?? e));
    }
  }

  async function saveFlag() {
    if (!route.video) return;
    const at = videoTime().video;
    try {
      await api.flagVideo(route.video, at, flagNote ?? "");
      setFlagged(`Flagged at ${stamp(at)}. \`vgc video review\` lists it with what the reader missed.`);
      setFlagNote(null);
    } catch (e) {
      setFlagged(`Couldn't save the flag: ${e instanceof ApiError ? e.message : String(e)}`);
    }
  }

  async function saveFrame() {
    const png = await capture.current?.frame("image/png");
    if (png && route.video) download(png, `${route.video}-${stamp(videoTime().video)}.png`);
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

  const elapsed = recordingSince === null ? 0 : Math.floor((now - recordingSince) / 1000);
  return (
    <div className="watch-video">
      {/* Only this box is captured, so nothing of the page's own may sit inside it. */}
      <div ref={box} className="watch-player"><div ref={host} className="watch-host" /></div>
      {playerProblem && <p className="small err" style={{ margin: "6px 2px" }}>{playerProblem}</p>}
      <div className="row watch-controls">
        {capturing ? (
          <>
            <button className="ghost" onClick={stop}>Stop capturing</button>
            {battle && (battleReading
              ? <button className="ghost recording" onClick={() => void stopReading()}>● Stop reading the battle</button>
              : <button onClick={() => { setReadError(null); setBattleReading(true); }}>Read the battle</button>)}
            {recordingSince === null
              ? <button className="ghost" onClick={() => {
                  if (!capture.current || !route.video) return;
                  recorder.current = startRecording(capture.current, route.video, videoTime);
                  setRecordingSince(Date.now());
                  setNow(Date.now());
                }}>Record clip</button>
              : <button className="ghost recording" onClick={() => void stopRecording()}>
                  ● Stop recording · {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
                </button>}
            <button className="ghost" onClick={saveFrame}>Save frame</button>
          </>
        ) : (
          <button onClick={start}>
            {reading ? "Capture the player and read the sheets" : battleReading ? "Capture the player and read the battle" : "Capture the player"}
          </button>
        )}
        <span style={{ flex: 1 }} />
        <button className="ghost" title="Something the page got wrong or didn't catch: noted at this point in the video, to look at later"
                onClick={() => { setFlagNote(flagNote === null ? "" : null); setFlagged(null); }}>
          ⚑ Flag this moment
        </button>
        <button className="link tiny" onClick={() => navigate({ ...route, video: null, start: 0 })}>change video</button>
      </div>
      {flagNote !== null && (
        <form className="row watch-flag" onSubmit={(e) => { e.preventDefault(); void saveFlag(); }}>
          <input autoFocus value={flagNote} onChange={(e) => setFlagNote(e.target.value)} maxLength={500}
                 placeholder="What was wrong or missed? (optional)" style={{ flex: 1 }} />
          <button type="submit">Save</button>
          <button type="button" className="link tiny" onClick={() => setFlagNote(null)}>cancel</button>
        </form>
      )}
      {flagged && <div className="tiny dim" style={{ margin: "4px 2px" }}>{flagged}</div>}
      <div className="tiny dim" style={{ margin: "6px 2px 0" }}>
        {!capturing && "Chrome or Edge: share this tab when asked. Only the player is captured."}
        {capturing && sheetsWanted && status}
        {capturing && reading && !sheetsWanted && "Both sheets are full, so no more are read."}
        {capturing && " A recorded clip saves as a .webm and a .json of the video's times, for building the battle reader."}
      </div>
      {captureError && <p className="small err" style={{ margin: "4px 2px" }}>{captureError}</p>}
      {battle && ((capturing && battleReading) || readStatus) && (
        <ReadingLine status={readStatus} on={capturing && battleReading} rate={answered.current.length / 5} error={readError} ocr={ocr} />
      )}
      {conflicts.length > 0 && (
        <div className="tiny warn-text" style={{ margin: "4px 2px" }}>
          A later frame disagreed with what is in the form; the form kept its value. Check:
          <ul style={{ margin: "2px 0 0 16px", padding: 0 }}>{conflicts.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
      {battle && (
        <button className="link tiny watch-manual" aria-pressed={manual} onClick={() => onManual(!manual)}>
          {manual ? "Hide the manual inputs" : "Enter taps by hand…"}
        </button>
      )}
    </div>
  );
}

/** Where the battle reader stands: what it read last, how fast, and what it couldn't read. */
function ReadingLine({ status, on, rate, error, ocr }: {
  status: ReadStatus | null; on: boolean; rate: number; error: string | null;
  ocr: { where: "loading" | "page" | "server"; why?: string } | null;
}) {
  const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
  return (
    <div className="watch-reading small">
      {error && <div className="err">The reader stopped on a frame: {error}</div>}
      {!status && on && <div className="dim">Reading: play the video. Nothing is read while it is paused.</div>}
      {status && (
        <>
          <div>
            {status.ended ? "Read to the end of the game." : on ? "Reading" : "Not reading"}
            {status.read_to !== null && <> · up to {clock(status.read_to)}</>}
            {on && rate > 0 && <> · {rate.toFixed(1)} frames/s</>}
            {on && ocr && <> · {ocr.where === "page" ? "read on this computer's GPU"
              : ocr.where === "loading" ? "loading the reader onto the GPU" : "read on the server"}</>}
            {status.missed > 0 && <> · <span className="warn-text">{status.missed} not read</span></>}
            {status.provisional > 0 && <> · {status.provisional} read on an unconfirmed phrasing</>}
          </div>
          {on && ocr?.where === "server" && ocr.why && (
            <div className="dim">Read on the server ({ocr.why}), which may not keep up for a whole game.</div>
          )}
          {status.message && <div className="dim">Last message: “{status.message}”</div>}
          {status.left && (
            <div className="dim">
              Player {status.left === "p1" ? 1 : 2} is on the left of the screen
              {status.left === "p2" && " (the sheets were read the other way round; the Pokémon say so)"}.
            </div>
          )}
          {on && rate > 0 && rate < 2 && (
            <div className="warn-text">Under 2 frames a second: a short message can be missed.</div>
          )}
          {status.behind && status.read_to !== null && (
            <div className="dim">Rewound: reading picks up again after {clock(status.read_to)}, where it had got to.</div>
          )}
          {status.resynced && <div className="dim">The journal was edited by hand; reading on from it as it stands.</div>}
          {status.gaps.map(([a, b]) => (
            <div key={a} className="warn-text">Not read: {clock(a)}–{clock(b)}, skipped over.</div>
          ))}
          {status.recent_missed.length > 0 && (
            <details>
              <summary className="dim">What it couldn't read (all of it goes to <code>vgc video review</code>)</summary>
              <ul style={{ margin: "2px 0 0 16px", padding: 0 }}>
                {status.recent_missed.map((m) => <li key={`${m.t}${m.why}`}>{clock(m.t)} {m.why}: {m.what}</li>)}
              </ul>
            </details>
          )}
        </>
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
  const errors = useSheetErrors(reg, watch.sheets);
  const legal = errors.every((e) => e !== null && e.length === 0);

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
        p1_name: n1, p2_name: n2,
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
        <span className="row">
          <h2 style={{ margin: 0 }}>The two sheets</h2>
          <div className="toggle" role="group" aria-label="Best of">
            <button className="seg" aria-pressed={bestOf === 1} onClick={() => setBestOf(1)}>Bo1</button>
            <button className="seg" aria-pressed={bestOf === 3} onClick={() => setBestOf(3)}>Bo3</button>
          </div>
        </span>
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
          {errors[i]?.length ? (
            <ul className="problems">
              {errors[i]!.map((p, k) => <li key={k} className={p.severity}>{p.pokemon ? `${p.pokemon}: ` : ""}{p.message}</li>)}
            </ul>
          ) : null}
        </div>
      ))}
      <div className="row" style={{ marginTop: 10 }}>
        <button disabled={busy || !ready || !legal} onClick={start}>Start watching{bestOf === 3 ? " game 1 of a Bo3" : ""}</button>
        {!ready && <span className="tiny dim">{filled(p1)}/{SHEET_SIZE} and {filled(p2)}/{SHEET_SIZE} on the sheets</span>}
        {ready && !legal && (
          <span className="tiny dim">
            {errors.some((e) => e?.length) ? "Not legal yet: fix what is listed under the sheets." : "Checking the sheets…"}
          </span>
        )}
      </div>
      {error && <p className="small err" style={{ marginBottom: 0 }}>{error}</p>}
      <p className="tiny dim" style={{ margin: "10px 2px 0" }}>
        Someone else's game: both sheets are shown and neither side's Stat Points are, so both
        Speeds are worked out from the battle, and the number is player 1's chance.
      </p>
    </div>
  );
}

/** Each full sheet's errors from the validator, as the sheets change: a battle won't start from an
 *  illegal sheet, so what is wrong is shown where it can be fixed. null while unchecked. */
function useSheetErrors(reg: string, sheets: WatchSheets["sheets"]): (Problem[] | null)[] {
  const texts = sheets.map((s) => (filled(s) === SHEET_SIZE ? toShowdown(s) : null));
  const [errors, setErrors] = useState<(Problem[] | null)[]>([null, null]);
  useEffect(() => {
    setErrors([null, null]);
    let live = true;
    const t = window.setTimeout(() => {
      void Promise.all(texts.map((x) => (x === null ? null : api.validate(x, reg, true).then(
        // A check that didn't come back blocks nothing: starting the battle checks again.
        (v) => v.problems.filter((p) => p.severity === "error"), () => []))))
        .then((out) => { if (live) setErrors(out); });
    }, 300);
    return () => { live = false; window.clearTimeout(t); };
  }, [reg, texts[0], texts[1]]);  // eslint-disable-line react-hooks/exhaustive-deps
  return errors;
}
