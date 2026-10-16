// The battle reader's change check and OCR, run on the page (vgc.video.screen.ScreenReader._region
// and _text): each frame, a region is read only when its text changed, and again on the frame
// after. What it reads goes to the server with the frame, which reads nothing itself. The layout,
// threshold and detector cap come from the server (GET /api/video/ocr), so the two can't drift.

import { crop, readLines, type Engine, type Img, type Line } from "./rapid.ts";

export type OcrConfig = {
  version: string;
  det: string;
  rec: string;
  keys: string[];
  limit: number;
  work_width: number;
  changed: number;
  regions: Record<string, [number, number, number, number]>;
};

/** Lines by region, for the regions read on this frame; a region left out is unchanged. */
export type RegionLines = Record<string, Line[]>;

/** A square min (erode) or max (dilate) filter of side `k` on a 0/1 image, the window clipped to
 *  the image as OpenCV's default border does for both. Separable, and each pass counts the ones in
 *  the window from a running sum, so it costs the same for any `k`: a frame's regions take a few
 *  milliseconds, where comparing every pixel of every window took 100. */
function morph(src: Uint8Array, w: number, h: number, k: number, max: boolean): Uint8Array {
  const r = k >> 1;
  const tmp = new Uint8Array(w * h), out = new Uint8Array(w * h);
  const sums = new Int32Array(Math.max(w, h) + 1);
  for (let y = 0; y < h; y++) {
    const row = y * w;
    for (let x = 0; x < w; x++) sums[x + 1] = sums[x] + src[row + x];
    for (let x = 0; x < w; x++) {
      const lo = x - r < 0 ? 0 : x - r, hi = x + r >= w ? w - 1 : x + r;
      const n = sums[hi + 1] - sums[lo];
      tmp[row + x] = max ? (n > 0 ? 1 : 0) : (n === hi - lo + 1 ? 1 : 0);
    }
  }
  for (let x = 0; x < w; x++) {
    for (let y = 0; y < h; y++) sums[y + 1] = sums[y] + tmp[y * w + x];
    for (let y = 0; y < h; y++) {
      const lo = y - r < 0 ? 0 : y - r, hi = y + r >= h ? h - 1 : y + r;
      const n = sums[hi + 1] - sums[lo];
      out[y * w + x] = max ? (n > 0 ? 1 : 0) : (n === hi - lo + 1 ? 1 : 0);
    }
  }
  return out;
}

/** screen._text: near-white strokes (gone after a 5×5 opening) with something dark within 4 px. */
export function textMask(img: Img): Uint8Array {
  const { width: w, height: h, data, channels: c } = img;
  const white = new Uint8Array(w * h), darkPx = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const r = data[i * c], g = data[i * c + 1], b = data[i * c + 2];
    white[i] = r > 200 && g > 200 && b > 200 ? 1 : 0;  // min > 200
    darkPx[i] = r < 90 && g < 90 && b < 90 ? 1 : 0;    // max < 90
  }
  const shape = morph(morph(morph(white, w, h, 5, false), w, h, 5, true), w, h, 3, true);
  const dark = morph(darkPx, w, h, 9, true);
  const out = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) out[i] = white[i] & (1 - shape[i]) & dark[i];
  return out;
}

export class RegionReader {
  private masks = new Map<string, { mask: Uint8Array; w: number; h: number }>();
  // Whether a region's last read was on the frame after its change, not the first one.
  private checked = new Map<string, boolean>();

  constructor(private config: OcrConfig, private engine: Engine) {}

  /** Read every region on the next frame: the server has lost what it was sent. */
  reset(): void {
    this.masks.clear();
    this.checked.clear();
  }

  /** The regions of `frame` (the frame the server is sent, at the same size) whose text changed. */
  async read(frame: Img): Promise<RegionLines> {
    const { width: W, height: H } = frame;
    const out: RegionLines = {};
    for (const [key, [x0, y0, x1, y1]] of Object.entries(this.config.regions)) {
      // Python's int() on the same doubles: the same pixels as the server's crop.
      const cx = Math.trunc(x0 * W), cy = Math.trunc(y0 * H);
      const region = crop(frame, cx, cy, Math.trunc(x1 * W) - cx, Math.trunc(y1 * H) - cy);
      const mask = textMask(region);
      const prev = this.masks.get(key);
      let same = false;
      if (prev && prev.w === region.width && prev.h === region.height) {
        let moved = 0;
        for (let i = 0; i < mask.length; i++) moved += prev.mask[i] ^ mask[i];
        same = moved < this.config.changed * mask.length;
      }
      // The first frame of new text is often mid-fade, so the frame after a change is read again.
      if (same && this.checked.get(key)) continue;
      this.checked.set(key, same);
      this.masks.set(key, { mask, w: region.width, h: region.height });
      out[key] = await readLines(region, this.engine, this.config.limit);
    }
    return out;
  }
}
