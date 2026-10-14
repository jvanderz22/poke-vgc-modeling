// Watching a game on YouTube (docs/PLAN-video.md step 4): which video, the frames the page reads
// off its player, and the team sheets those frames fill in.

import type { SheetRead } from "./api";
import { emptySheet, filled, type SheetEntry } from "./sheet";
import { entriesFromRead } from "./components/SheetFromImage";

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
  thumb: () => Float32Array;
  size: () => { width: number; height: number };
  stop: () => void;
};

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
    video: { frameRate: 30 }, audio: false, preferCurrentTab: true, selfBrowserSurface: "include",
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

  const small = document.createElement("canvas");
  small.width = 64;
  small.height = 36;
  const sctx = small.getContext("2d", { willReadFrequently: true })!;
  const full = document.createElement("canvas");
  let stopped = false;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    stopTracks();
    video.srcObject = null;
  };
  // "Stop sharing" in the browser's own bar ends the track without asking the page.
  track.addEventListener("ended", () => { stop(); onEnded(); });

  return {
    stream,
    stop,
    size: () => ({ width: video.videoWidth, height: video.videoHeight }),
    thumb: () => {
      sctx.drawImage(video, 0, 0, 64, 36);
      const px = sctx.getImageData(0, 0, 64, 36).data;
      const out = new Float32Array(64 * 36);
      for (let i = 0; i < out.length; i++) out[i] = px[4 * i] * 0.299 + px[4 * i + 1] * 0.587 + px[4 * i + 2] * 0.114;
      return out;
    },
    frame: (type = "image/jpeg", quality = 0.9, maxWidth = Infinity) => {
      if (stopped || !video.videoWidth) return Promise.resolve(null);
      const k = Math.min(1, maxWidth / video.videoWidth);
      full.width = Math.round(video.videoWidth * k);
      full.height = Math.round(video.videoHeight * k);
      full.getContext("2d")!.drawImage(video, 0, 0, full.width, full.height);
      return new Promise((ok) => full.toBlob(ok, type, quality));
    },
  };
}

/** How far apart two thumbnails must be (mean absolute grey level, 0–255) to count as a different
 *  picture. Compression noise on a still frame stays well under it. */
const CHANGED = 1.5;
/** A frame is sent no sooner than this after the last, and only if the picture changed; a still
 *  picture is sent again after REPEAT_MS, in case the read before it was cut short. */
const INTERVAL_MS = 1500;
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
    const g = cap.thumb();
    if (lastSent && distance(g, lastSent) < CHANGED && now - lastAt < REPEAT_MS) return;
    const jpeg = await cap.frame();
    if (!jpeg || stopped) return;
    inFlight = true;
    lastSent = g;
    lastAt = now;
    try { await onFrame(jpeg); } finally { inFlight = false; }
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

/** Stream frames for the battle reader, each with the video time it shows. A frame goes only once
 *  the last has been answered, so a slow host reads fewer frames instead of falling behind, and
 *  none goes while the video is paused (`time` gives null). Returns the stopper. */
export function streamFrames(cap: Capture, time: () => number | null,
                             onFrame: (jpeg: Blob, videoS: number) => Promise<void>): () => void {
  let stopped = false;
  const loop = async () => {
    while (!stopped) {
      const began = Date.now();
      const at = time();
      if (at !== null) {
        const jpeg = await cap.frame("image/jpeg", 0.85, BATTLE_WIDTH);
        if (!jpeg || stopped) break;
        try { await onFrame(jpeg, at); } catch { /* reported by onFrame; keep reading */ }
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
