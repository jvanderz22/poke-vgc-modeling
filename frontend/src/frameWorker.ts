// Encodes the captured player's frames off the page's thread (watch.ts). Drawing a frame into a
// canvas and encoding it there held the page for 16–34 ms a frame, at four frames a second; here
// it holds the page for under 3.

export type FrameJob =
  | { id: number; kind: "encode"; bitmap: ImageBitmap; type: string; quality: number }
  | { id: number; kind: "thumb"; bitmap: ImageBitmap };

export type FrameDone =
  | { id: number; blob: Blob }
  | { id: number; thumb: Float32Array }
  | { id: number; error: string };

let canvas: OffscreenCanvas | null = null;

self.onmessage = async ({ data: job }: MessageEvent<FrameJob>) => {
  const { bitmap } = job;
  try {
    if (!canvas) canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
    // Setting a canvas's size reallocates it, even to the size it already has.
    if (canvas.width !== bitmap.width) canvas.width = bitmap.width;
    if (canvas.height !== bitmap.height) canvas.height = bitmap.height;
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
  } catch (e) {
    self.postMessage({ id: job.id, error: String(e) } satisfies FrameDone);
  } finally {
    bitmap.close();
  }
};
