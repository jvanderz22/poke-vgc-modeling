// Watching a game on YouTube (docs/PLAN-video.md step 4): which video, the frames the page reads
// off its player, and the team sheets those frames fill in.

import type { SheetRead } from "./api";
import { emptySheet, filled, type SheetEntry } from "./sheet";
import { entriesFromRead } from "./components/SheetFromImage";
import type { FrameDone, FrameJob, FrameMs } from "./frameWorker";
import type { OcrConfig, RegionLines } from "./ocr/screen.ts";

// --- the video ------------------------------------------------------------------------------

/** "1h2m3s", "90s" or "90" as seconds; 0 for anything else. */
function seconds(t: string | null): number {
  if (!t) return 0;
  if (/^\d+s?$/.test(t)) return parseInt(t, 10);
  const m = t.match(/^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/);
  return m ? (+(m[1] ?? 0)) * 3600 + (+(m[2] ?? 0)) * 60 + (+(m[3] ?? 0)) : 0;
}

/** A YouTube link as the video's id and the second it should open at, or null if it isn't one.
 *  Takes watch, youtu.be, live, shorts and embed links, with `t=` or `start=`. */
export function parseYouTube(link: string): { video: string; start: number } | null {
  let url: URL;
  try { url = new URL(link.trim()); } catch { return null; }
  const host = url.hostname.replace(/^(www|m|music)\./, "");
  let video: string | null = null;
  if (host === "youtu.be") video = url.pathname.slice(1).split("/")[0];
  else if (host === "youtube.com" || host === "youtube-nocookie.com") {
    video = url.searchParams.get("v")
      ?? url.pathname.match(/^\/(?:live|shorts|embed|v)\/([\w-]+)/)?.[1] ?? null;
  }
  if (!video || !/^[\w-]{6,20}$/.test(video)) return null;
  return { video, start: seconds(url.searchParams.get("t") ?? url.searchParams.get("start")) };
}

// --- the sheets -----------------------------------------------------------------------------

/** Whether a read off a frame is a team sheet, and not a frame that only names a few Pokémon (a
 *  battle's nameplates, team preview): most of six cards, each with moves under it. */
export function isSheet(read: SheetRead): boolean {
  return read.entries.filter((e) => e.species && e.moves.length >= 2).length >= 4;
}

export type WatchSheets = { sheets: [SheetEntry[], SheetEntry[]]; names: [string, string] };

export type Merge = WatchSheets & {
  /** Which player the read went to, or null for a third team the page has no place for. */
  slot: 0 | 1 | null;
  /** Where a later read disagreed with what the form already holds. The form keeps its value. */
  conflicts: string[];
};

const speciesOf = (s: SheetEntry[]) => new Set(s.map((e) => e.species).filter(Boolean) as string[]);

/** A sheet read off a frame, merged into the two being filled in.
 *
 *  The same sheet is on screen for many frames, so a read goes to the player whose sheet shares
 *  most of its Pokémon, or to the first empty one. It only fills what is empty: nothing already
 *  in a form is changed, whether a reader put it there or you did. Where a read disagrees with a
 *  field that is already set, that is reported, because one of the two is wrong. */
export function mergeRead(current: WatchSheets, read: SheetRead): Merge {
  const sheets: [SheetEntry[], SheetEntry[]] = [current.sheets[0].map(copy), current.sheets[1].map(copy)];
  const names: [string, string] = [...current.names];
  const incoming = entriesFromRead(read).filter((e) => e.species);
  const theirs = speciesOf(incoming);
  const overlap = (s: SheetEntry[]) => [...speciesOf(s)].filter((x) => theirs.has(x)).length;

  let slot: 0 | 1 | null = null;
  const o = [overlap(sheets[0]), overlap(sheets[1])];
  if (Math.max(...o) >= 3) slot = o[0] >= o[1] ? 0 : 1;
  else if (filled(sheets[0]) === 0) slot = 0;
  else if (filled(sheets[1]) === 0) slot = 1;
  if (slot === null) return { sheets: current.sheets, names: current.names, slot, conflicts: [] };

  const sheet = sheets[slot];
  const conflicts: string[] = [];
  for (const e of incoming) {
    let card = sheet.find((c) => c.species === e.species);
    if (!card) {
      card = sheet.find((c) => !c.species);
      if (!card) continue;
      card.species = e.species;
    }
    for (const k of ["ability", "nature", "item"] as const) {
      if (!e[k]) continue;
      if (!card[k]) card[k] = e[k];
      else if (card[k] !== e[k]) conflicts.push(`${e.species}: read ${e[k]} where the form has ${card[k]}`);
    }
    for (const m of e.moves) {
      if (!m || card.moves.includes(m)) continue;
      const free = card.moves.indexOf(null);
      if (free >= 0) card.moves[free] = m;
      else conflicts.push(`${e.species}: read ${m}, but four moves are already in`);
    }
  }
  if (read.title && !names[slot]) names[slot] = titleCase(read.title);
  return { sheets, names, slot, conflicts };
}

function copy(e: SheetEntry): SheetEntry {
  return { ...e, moves: [...e.moves] };
}

/** "CARSON CONFER" as "Carson Confer": broadcasts set names in capitals. */
function titleCase(s: string): string {
  return s === s.toUpperCase() ? s.toLowerCase().replace(/\b\p{L}/gu, (c) => c.toUpperCase()) : s;
}

export function emptyWatch(): WatchSheets {
  return { sheets: [emptySheet(), emptySheet()], names: ["", ""] };
}

// --- the frames -----------------------------------------------------------------------------

export class CaptureError extends Error {}

/** The player's pixels, captured. One capture serves sheet reading, clip recording and saved
 *  frames, so the browser asks to share the tab once. */
export type Capture = {
  stream: MediaStream;
  /** The picture now, at the capture's full size or scaled down to `maxWidth`. */
  frame: (type?: string, quality?: number, maxWidth?: number) => Promise<Blob | null>;
  /** A 64×36 grey thumbnail, for telling one picture from the next. */
  thumb: () => Promise<Float32Array | null>;
  /** A frame for the battle reader, at most `maxWidth` across, with the lines the page read off
   *  it itself once `startOcr` has worked (null before, or without WebGPU). */
  battleFrame: (quality: number, maxWidth: number) => Promise<BattleShot | null>;
  /** Load the server's OCR models onto the GPU, so battle frames are read here. Throws when the
   *  browser can't (no WebGPU); the server reads them then. */
  startOcr: (config: OcrConfig) => Promise<boolean>;
  /** Read every region on the next battle frame: the server lost what it had been sent. */
  resetOcr: () => void;
  size: () => { width: number; height: number };
  stop: () => void;
};

/** What the capture asks of the browser, when it starts: limits applied later only drop and shrink
 *  frames after the tab has been captured, which saves little (measured 2026-10-15, Chrome 141).
 *
 *  - 8 frames a second: the readers sample at most every quarter second, so a faster capture only
 *    costs, and at 30 the video itself stuttered. A frame a sample takes is at most 125 ms older
 *    than the video time stamped on it. A recorded clip is 8 a second too.
 *  - At most 1600 across, the most any reader takes (SHEET_WIDTH). With no limit, Chrome rendered
 *    the whole tab at 3.5× to capture the player sharply (3150 wide for a 900 px player), where the
 *    screen is 2×. The limit applies to the cropped player, not to the tab, and keeps its shape.
 *
 *  Together they cut the capture's CPU from about 71 points of a core to 17. */
const CAPTURE = { frameRate: { ideal: 8, max: 8 }, width: { max: 1600 }, height: { max: 1600 } };

/** Capture the pixels of `target`, the element around the player.
 *
 *  Only `target` is captured, never the rest of the page: the page's own sheet forms are text a
 *  reader would happily read back. That needs Region Capture (Chrome and Edge), so without it this
 *  refuses instead of capturing the whole tab. */
export async function startCapture(target: HTMLElement, onEnded: () => void): Promise<Capture> {
  const CropTarget = (window as unknown as { CropTarget?: { fromElement(e: Element): Promise<unknown> } }).CropTarget;
  if (!navigator.mediaDevices?.getDisplayMedia) throw new CaptureError("This browser can't capture a tab.");
  if (!CropTarget) throw new CaptureError("Capturing the video needs Chrome or Edge, which can capture just the player.");

  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: CAPTURE, audio: false, preferCurrentTab: true, selfBrowserSurface: "include",
  } as DisplayMediaStreamOptions);
  const [track] = stream.getVideoTracks();
  const stopTracks = () => stream.getTracks().forEach((t) => t.stop());
  try {
    await (track as unknown as { cropTo(t: unknown): Promise<void> }).cropTo(await CropTarget.fromElement(target));
  } catch {
    stopTracks();
    throw new CaptureError("Share this tab when asked: the page can only capture the player in its own tab.");
  }

  const video = document.createElement("video");
  video.muted = true;
  video.srcObject = stream;
  await video.play();

  // The page only takes the picture (createImageBitmap, which scales it without holding the page);
  // the worker draws, reads and encodes it.
  const worker = new Worker(new URL("./frameWorker.ts", import.meta.url), { type: "module" });
  const pending = new Map<number, (done: FrameDone | null) => void>();
  let nextJob = 0;
  worker.onmessage = ({ data }: MessageEvent<FrameDone>) => {
    pending.get(data.id)?.(data);
    pending.delete(data.id);
  };
  let stopped = false;

  type Without<T> = T extends unknown ? Omit<T, "id"> : never;
  /** A message to the worker and its answer; null once stopped. */
  const ask = (msg: Without<FrameJob>, transfer: Transferable[] = []) => new Promise<FrameDone | null>((ok) => {
    const id = nextJob++;
    pending.set(id, ok);
    worker.postMessage({ id, ...msg } as FrameJob, transfer);
  });

  /** The picture now at `width` × `height`, handed to the worker; null once stopped. */
  const job = async (width: number, height: number,
                     rest: { kind: "encode"; type: string; quality: number } | { kind: "thumb" } | { kind: "battle"; quality: number }) => {
    if (stopped || !video.videoWidth) return null;
    const scale = width === video.videoWidth && height === video.videoHeight
      ? {} : { resizeWidth: width, resizeHeight: height, resizeQuality: "high" as const };
    const bitmap = await createImageBitmap(video, scale);
    if (stopped) { bitmap.close(); return null; }
    const done = await ask({ bitmap, ...rest }, [bitmap]);
    if (done && "error" in done) throw new Error(`Couldn't take a frame: ${done.error}`);
    return done;
  };

  const stop = () => {
    if (stopped) return;
    stopped = true;
    stopTracks();
    video.srcObject = null;
    worker.terminate();
    pending.forEach((ok) => ok(null));
    pending.clear();
  };
  // "Stop sharing" in the browser's own bar ends the track without asking the page.
  track.addEventListener("ended", () => { stop(); onEnded(); });

  return {
    stream,
    stop,
    size: () => ({ width: video.videoWidth, height: video.videoHeight }),
    thumb: async () => {
      const done = await job(64, 36, { kind: "thumb" });
      return done && "thumb" in done ? done.thumb : null;
    },
    frame: async (type = "image/jpeg", quality = 0.9, maxWidth = Infinity) => {
      const k = Math.min(1, maxWidth / (video.videoWidth || 1));
      const done = await job(Math.round(video.videoWidth * k), Math.round(video.videoHeight * k),
                             { kind: "encode", type, quality });
      return done && "blob" in done ? done.blob : null;
    },
    battleFrame: async (quality, maxWidth) => {
      const began = performance.now();
      const k = Math.min(1, maxWidth / (video.videoWidth || 1));
      const done = await job(Math.round(video.videoWidth * k), Math.round(video.videoHeight * k), { kind: "battle", quality });
      if (!done || !("read" in done)) return null;
      const shot = done as Extract<FrameDone, { read: unknown }>;
      const { draw, mask, ocr } = shot.ms;
      // Taking the picture and handing it to the worker and back: the rest of the time.
      const take = performance.now() - began - draw - mask - ocr;
      return { jpeg: shot.blob, read: shot.read, sig: shot.sig, ms: { take, ...shot.ms } };
    },
    startOcr: async (config) => {
      const done = await ask({ kind: "ocr", config });
      if (done && "error" in done) throw new Error(done.error);
      return !!done;
    },
    resetOcr: () => { void ask({ kind: "ocr-reset" }); },
  };
}

/** How far apart two thumbnails must be (mean absolute grey level, 0–255) to count as a different
 *  picture. Compression noise on a still frame stays well under it. */
const CHANGED = 1.5;
/** A frame is sent no sooner than this after the last, and only if the picture changed; a still
 *  picture is sent again after REPEAT_MS, in case the read before it was cut short. */
const INTERVAL_MS = 1500;
/** A sheet reads the same from 1600 wide as from a full Retina capture; the server reads at 1280.
 *  The capture is no wider than this (CAPTURE). */
const SHEET_WIDTH = 1600;
const REPEAT_MS = 15000;

function distance(a: Float32Array, b: Float32Array): number {
  let d = 0;
  for (let i = 0; i < a.length; i++) d += Math.abs(a[i] - b[i]);
  return d / a.length;
}

/** Hand each new picture to `onFrame` while `wanted()`. One frame is in flight at a time, so a
 *  slow read slows the rate rather than queueing frames. Returns the function that stops it. */
export function readFrames(cap: Capture, onFrame: (jpeg: Blob) => Promise<void>, wanted: () => boolean): () => void {
  let lastSent: Float32Array | null = null;
  let lastAt = 0;
  let inFlight = false;
  let stopped = false;
  const tick = async () => {
    if (stopped || inFlight || !wanted()) return;
    const now = Date.now();
    if (now - lastAt < INTERVAL_MS) return;
    // Claimed before anything is awaited: taking and encoding a frame can outlast the 250 ms
    // between ticks, and every tick that got past this check while it ran sent a frame of its own.
    inFlight = true;
    try {
      const g = await cap.thumb();
      if (!g || stopped) return;
      if (lastSent && distance(g, lastSent) < CHANGED && now - lastAt < REPEAT_MS) return;
      const jpeg = await cap.frame("image/jpeg", 0.9, SHEET_WIDTH);
      if (!jpeg || stopped) return;
      lastSent = g;
      lastAt = now;
      await onFrame(jpeg);
    } catch (e) {
      console.error(e);
    } finally {
      inFlight = false;
    }
  };
  const timer = window.setInterval(tick, 250);
  return () => { stopped = true; window.clearInterval(timer); };
}

// --- clips ----------------------------------------------------------------------------------

/** What the player was doing at a moment of a recording: `ms` from the recording's start, and
 *  the video's own time then (null when the player wasn't ready). */
export type ClipTime = { ms: number; video: number | null; playing: boolean };

export type Clip = { webm: Blob; meta: ClipMeta };

/** The JSON saved beside a clip, which `vgc.video.clip` reads. */
export type ClipMeta = {
  video: string; mime: string; width: number; height: number; recorded: string; times: ClipTime[];
};

/** Record the capture as a clip, sampling the video's time every 200 ms alongside it, so any
 *  frame of the clip can be placed in the video: the fixtures PLAN-video step 3 is built from. */
/** A battle's frames go every quarter second: a message is up for 1.5–2 s, and the reader counts
 *  it once it has read the same thing twice running. */
const BATTLE_INTERVAL_MS = 250;
/** The reader works at 1200 wide (`screen.WORK_WIDTH`); a larger frame only costs upload. */
const BATTLE_WIDTH = 1200;

/** A battle frame, what the page read off it, a hash of its pixels, and where the time went
 *  (`take` is the picture being taken and passed to the worker). */
export type BattleShot = { jpeg: Blob; read: RegionLines | null; sig: number; ms: FrameMs & { take: number } };

/** A picture the same to the pixel for this long while the video plays is a capture that has
 *  stopped: a hidden tab or a covered window isn't painted, though the video plays on. The
 *  broadcast itself never holds that still (over 300 s of a game, one frame step at most). */
const STALLED_S = 1.5;

/** Stream frames for the battle reader, each with the video time it shows. A frame goes only once
 *  the last has been answered, so a slow host reads fewer frames instead of falling behind, and
 *  none goes while the video is paused (`time` gives null).
 *
 *  A picture that stops changing while the video plays (STALLED_S) is not sent as the moments it
 *  isn't. Once it moves again, `onStalled` is told when it stopped, to take the video back there:
 *  the reader reads a rewind past what it has written again, so nothing in between is lost.
 *  Returns the stopper. */
export function streamFrames(cap: Capture, time: () => number | null,
                             onFrame: (shot: BattleShot, videoS: number) => Promise<void>,
                             onStalled: (from: number, to: number) => void): () => void {
  let stopped = false;
  let last: { sig: number; since: number } | null = null;  // the picture, and when it was first seen
  let stalled: number | null = null;  // when the picture stopped changing, while frames are held
  const loop = async () => {
    while (!stopped) {
      const began = Date.now();
      const at = time();
      if (at !== null) {
        let shot: BattleShot | null = null;
        try { shot = await cap.battleFrame(0.85, BATTLE_WIDTH); } catch (e) { console.error(e); }
        if (stopped) break;
        if (shot) {
          const same = last !== null && shot.sig === last.sig;
          if (last && same && at - last.since > STALLED_S) {
            stalled ??= last.since;
          } else if (!same && stalled !== null) {
            // Moving again: this frame isn't sent; the video goes back to where it stopped.
            const from = stalled;
            stalled = last = null;
            onStalled(from, at);
            shot = null;
          } else if (!last || !same || at < last.since) {
            last = { sig: shot.sig, since: at };
          }
        }
        if (shot && stalled === null) try { await onFrame(shot, at); } catch { /* reported by onFrame; keep reading */ }
      }
      await new Promise((ok) => window.setTimeout(ok, Math.max(0, BATTLE_INTERVAL_MS - (Date.now() - began))));
    }
  };
  void loop();
  return () => { stopped = true; };
}

export function startRecording(cap: Capture, videoId: string,
                               clock: () => { video: number | null; playing: boolean }): { stop: () => Promise<Clip> } {
  const mime = ["video/webm;codecs=vp9", "video/webm;codecs=vp8", "video/webm"]
    .find((m) => MediaRecorder.isTypeSupported(m)) ?? "";
  // Sharp enough that the text box and the HP bars survive: these clips are read, not watched.
  const rec = new MediaRecorder(cap.stream, { mimeType: mime || undefined, videoBitsPerSecond: 6_000_000 });
  const chunks: Blob[] = [];
  const times: ClipTime[] = [];
  const recorded = new Date().toISOString();
  const { width, height } = cap.size();
  let t0 = 0;
  const sample = () => times.push({ ms: Math.round(performance.now() - t0), ...clock() });
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  rec.onstart = () => { t0 = performance.now(); sample(); };
  rec.start(1000);
  const timer = window.setInterval(sample, 200);
  return {
    stop: () => new Promise((ok) => {
      window.clearInterval(timer);
      sample();
      rec.onstop = () => ok({
        webm: new Blob(chunks, { type: mime || "video/webm" }),
        meta: { video: videoId, mime: mime || "video/webm", width, height, recorded, times },
      });
      rec.stop();
    }),
  };
}

/** Save a blob to the browser's downloads under `name`. */
export function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

/** 754.3 seconds as "12m34s", for file names that sort and read. */
export function stamp(seconds: number | null): string {
  if (seconds === null) return "unknown";
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s`;
}
