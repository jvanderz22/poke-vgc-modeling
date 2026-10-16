// Encodes the captured player's frames off the page's thread (watch.ts). Drawing a frame into a
// canvas and encoding it there held the page for 16–34 ms a frame, at four frames a second; here
// it holds the page for under 3. A battle's frames are also read here, on the GPU, once the OCR is
// set up (src/ocr), so the server needn't.

import type { RegionLines, OcrConfig } from "./ocr/screen.ts";

export type FrameJob =
  | { id: number; kind: "encode"; bitmap: ImageBitmap; type: string; quality: number }
  | { id: number; kind: "thumb"; bitmap: ImageBitmap }
  | { id: number; kind: "battle"; bitmap: ImageBitmap; quality: number }
  | { id: number; kind: "ocr"; config: OcrConfig }
  | { id: number; kind: "ocr-reset" };

export type FrameDone =
  | { id: number; blob: Blob }
  | { id: number; thumb: Float32Array }
  | { id: number; blob: Blob; read: RegionLines | null }
  | { id: number; ocr: true }
  | { id: number; error: string };

let canvas: OffscreenCanvas | null = null;
// A battle frame is drawn on a canvas of its own: its OCR is awaited, and a thumbnail drawn on the
// shared canvas meanwhile would be the picture it encodes.
let battleCanvas: OffscreenCanvas | null = null;
let reader: import("./ocr/screen.ts").RegionReader | null = null;

function sized(c: OffscreenCanvas | null, w: number, h: number): OffscreenCanvas {
  if (!c) return new OffscreenCanvas(w, h);
  // Setting a canvas's size reallocates it, even to the size it already has.
  if (c.width !== w) c.width = w;
  if (c.height !== h) c.height = h;
  return c;
}

async function battle(job: Extract<FrameJob, { kind: "battle" }>): Promise<FrameDone> {
  const { bitmap } = job;
  battleCanvas = sized(battleCanvas, bitmap.width, bitmap.height);
  const ctx = battleCanvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(bitmap, 0, 0);
  const pixels = reader ? ctx.getImageData(0, 0, bitmap.width, bitmap.height) : null;
  const blob = await battleCanvas.convertToBlob({ type: "image/jpeg", quality: job.quality });
  if (!reader || !pixels) return { id: job.id, blob, read: null };
  const read = await reader.read({ data: pixels.data, width: pixels.width, height: pixels.height, channels: 4 });
  return { id: job.id, blob, read };
}

self.onmessage = async ({ data: job }: MessageEvent<FrameJob>) => {
  try {
    if (job.kind === "ocr") {
      const [{ webgpuEngine }, { RegionReader }] = await Promise.all([import("./ocr/engine.ts"), import("./ocr/screen.ts")]);
      reader = new RegionReader(job.config, await webgpuEngine(job.config));
      self.postMessage({ id: job.id, ocr: true } satisfies FrameDone);
      return;
    }
    if (job.kind === "ocr-reset") {
      reader?.reset();
      self.postMessage({ id: job.id, ocr: true } satisfies FrameDone);
      return;
    }
    if (job.kind === "battle") {
      try {
        self.postMessage(await battle(job) satisfies FrameDone);
      } finally {
        job.bitmap.close();
      }
      return;
    }
    const { bitmap } = job;
    try {
      canvas = sized(canvas, bitmap.width, bitmap.height);
      const ctx = canvas.getContext("2d", { willReadFrequently: job.kind === "thumb" })!;
      ctx.drawImage(bitmap, 0, 0);
      if (job.kind === "thumb") {
        const px = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
        const thumb = new Float32Array(bitmap.width * bitmap.height);
        for (let i = 0; i < thumb.length; i++) thumb[i] = px[4 * i] * 0.299 + px[4 * i + 1] * 0.587 + px[4 * i + 2] * 0.114;
        self.postMessage({ id: job.id, thumb } satisfies FrameDone);
      } else {
        const blob = await canvas.convertToBlob({ type: job.type, quality: job.quality });
        self.postMessage({ id: job.id, blob } satisfies FrameDone);
      }
    } finally {
      bitmap.close();
    }
  } catch (e) {
    self.postMessage({ id: job.id, error: String(e) } satisfies FrameDone);
  }
};
