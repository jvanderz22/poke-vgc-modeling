// RapidOCR's pipeline (rapidocr 3.10, PP-OCRv6 small), in the browser: the steps the server's
// `screen._lines` runs, so the browser can read a region instead of the server. The models are the
// server's own files (GET /api/video/ocr/...); this file only reproduces what RapidOCR does around
// them. Each step names the RapidOCR function it follows. Nothing here touches the DOM.

/** An image as rows of pixels: 3 (RGB) or 4 (RGBA, from a canvas) bytes each. */
export type Img = { data: Uint8Array | Uint8ClampedArray; width: number; height: number; channels: 3 | 4 };
export type Tensor = { data: Float32Array; dims: readonly number[] };
/** One model run: the input tensor in, its first output out. */
export type Run = (input: Tensor) => Promise<Tensor>;
/** The recognizer run, reduced to what CTCLabelDecode takes from its output: each step's likeliest
 *  character (`argmax(axis=2)`) and that character's probability (`max(axis=2)`), batch × steps. */
export type RecRun = (input: Tensor) => Promise<Best>;
export type Best = { best: Int32Array; p: Float32Array; steps: number };
export type Engine = { det: Run; rec: RecRun; keys: string[] };
/** A line as `screen._lines` gives it: the text and its box, in the image's own pixels. */
export type Line = [text: string, x0: number, y0: number, x1: number, y1: number];
type Pt = [number, number];
type Quad = [Pt, Pt, Pt, Pt];

// Global, Det and Rec settings: rapidocr's config.yaml, with the detector capped as screen.py caps it.
const TEXT_SCORE = 0.5;
const MIN_SIDE_LEN = 30;
const MAX_SIDE_LEN = 2000;
const WIDTH_HEIGHT_RATIO = 8;
const MIN_HEIGHT = 30;
const DET_THRESH = 0.3;
const BOX_THRESH = 0.5;
const MAX_CANDIDATES = 1000;
const UNCLIP_RATIO = 1.6;
const MIN_SIZE = 3;
const BOX_SORT_Y = 10;
const REC_H = 48;
const REC_W = 320;
const REC_BATCH = 6;

/** Python's round(): halves go to the even neighbour. */
function pyRound(x: number): number {
  const r = Math.round(x);
  return Math.abs(x % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
}

function rgb(img: Img): Img {
  if (img.channels === 3) return img;
  const out = new Uint8Array(img.width * img.height * 3);
  for (let i = 0, j = 0; i < out.length; i += 3, j += 4) {
    out[i] = img.data[j]; out[i + 1] = img.data[j + 1]; out[i + 2] = img.data[j + 2];
  }
  return { data: out, width: img.width, height: img.height, channels: 3 };
}

export function crop(img: Img, x: number, y: number, w: number, h: number): Img {
  const c = img.channels;
  const out = new Uint8Array(w * h * c);
  for (let r = 0; r < h; r++) out.set(img.data.subarray(((y + r) * img.width + x) * c, ((y + r) * img.width + x + w) * c), r * w * c);
  return { data: out, width: w, height: h, channels: img.channels };
}

/** cv2.resize with INTER_LINEAR: pixel centres aligned, edges clamped, no smoothing on the way down. */
function resize(img: Img, w: number, h: number): Img {
  const { width: sw, height: sh, data: s } = img;
  const out = new Uint8Array(w * h * 3);
  const xs = new Int32Array(w), xa = new Float32Array(w);
  const kx = sw / w, ky = sh / h;
  for (let x = 0; x < w; x++) {
    let f = (x + 0.5) * kx - 0.5, i = Math.floor(f);
    f -= i;
    if (i < 0) { i = 0; f = 0; }
    if (i >= sw - 1) { i = sw - 1; f = 0; }
    xs[x] = i; xa[x] = f;
  }
  for (let y = 0; y < h; y++) {
    let g = (y + 0.5) * ky - 0.5, j = Math.floor(g);
    g -= j;
    if (j < 0) { j = 0; g = 0; }
    if (j >= sh - 1) { j = sh - 1; g = 0; }
    const r0 = j * sw * 3, r1 = Math.min(j + 1, sh - 1) * sw * 3;
    for (let x = 0; x < w; x++) {
      const i0 = xs[x] * 3, i1 = Math.min(xs[x] + 1, sw - 1) * 3, f = xa[x];
      for (let c = 0; c < 3; c++) {
        const top = s[r0 + i0 + c] * (1 - f) + s[r0 + i1 + c] * f;
        const bot = s[r1 + i0 + c] * (1 - f) + s[r1 + i1 + c] * f;
        out[(y * w + x) * 3 + c] = Math.round(top * (1 - g) + bot * g);
      }
    }
  }
  return { data: out, width: w, height: h, channels: 3 };
}

/** utils/process_img.resize_image_within_bounds: the long side to at most 2000, the short side to
 *  at least 30, each to a multiple of 32. Returns the factors that map back to the original. */
function withinBounds(img: Img): { img: Img; rh: number; rw: number } {
  let out = img, rh = 1, rw = 1;
  const { width: w0, height: h0 } = img;
  if (Math.max(h0, w0) > MAX_SIDE_LEN) {
    const ratio = h0 > w0 ? MAX_SIDE_LEN / h0 : MAX_SIDE_LEN / w0;
    out = resize(out, pyRound(Math.trunc(w0 * ratio) / 32) * 32, pyRound(Math.trunc(h0 * ratio) / 32) * 32);
  }
  const { width: w, height: h } = out;
  if (Math.min(h, w) < MIN_SIDE_LEN) {
    const ratio = h < w ? MIN_SIDE_LEN / h : MIN_SIDE_LEN / w;
    const nw = pyRound(Math.trunc(w * ratio) / 32) * 32, nh = pyRound(Math.trunc(h * ratio) / 32) * 32;
    out = resize(out, nw, nh);
    rh = h / nh; rw = w / nw;
  }
  return { img: out, rh, rw };
}

/** utils/process_img.apply_vertical_padding: a strip wider than 8:1 or 30 px or shorter is padded
 *  with black above and below, to a height of twice the larger of width/8 and 30. */
function verticalPadding(img: Img): { img: Img; top: number } {
  const { width: w, height: h } = img;
  if (!(h <= MIN_HEIGHT || w / h > WIDTH_HEIGHT_RATIO)) return { img, top: 0 };
  const newH = Math.max(Math.trunc(w / WIDTH_HEIGHT_RATIO), MIN_HEIGHT) * 2;
  const pad = Math.trunc(Math.abs(newH - h) / 2);
  const out = new Uint8Array(w * (h + 2 * pad) * 3);
  out.set(img.data.subarray(0, w * h * 3), pad * w * 3);
  return { img: { data: out, width: w, height: h + 2 * pad, channels: 3 }, top: pad };
}

/** CHW float32, (x/255 − 0.5)/0.5, the channels in the order given: RapidOCR passes an array
 *  through unconverted, and screen.py hands it RGB. */
function normalize(img: Img, into: Float32Array, offset: number, width: number): void {
  const { width: w, height: h, data } = img;
  const plane = width * h;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const s = (y * w + x) * 3, d = offset + y * width + x;
      into[d] = (data[s] / 255 - 0.5) / 0.5;
      into[d + plane] = (data[s + 1] / 255 - 0.5) / 0.5;
      into[d + 2 * plane] = (data[s + 2] / 255 - 0.5) / 0.5;
    }
  }
}

// --- geometry -------------------------------------------------------------------------------

function hull(points: Pt[]): Pt[] {
  const p = [...points].sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  if (p.length < 3) return p;
  const cross = (o: Pt, a: Pt, b: Pt) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const lower: Pt[] = [], upper: Pt[] = [];
  for (const q of p) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], q) <= 0) lower.pop();
    lower.push(q);
  }
  for (let i = p.length - 1; i >= 0; i--) {
    const q = p[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], q) <= 0) upper.pop();
    upper.push(q);
  }
  return lower.slice(0, -1).concat(upper.slice(0, -1));
}

/** cv2.minAreaRect + cv2.boxPoints: the smallest rectangle around the points, as its corners and
 *  its two side lengths. An edge of the smallest rectangle lies along an edge of the hull. */
function minAreaRect(points: Pt[]): { corners: Pt[]; sides: [number, number] } {
  const h = hull(points);
  if (h.length < 3) {
    const xs = points.map((q) => q[0]), ys = points.map((q) => q[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    return { corners: [[x0, y1], [x0, y0], [x1, y0], [x1, y1]], sides: [x1 - x0, y1 - y0] };
  }
  let best: { area: number; corners: Pt[]; sides: [number, number] } | null = null;
  for (let i = 0; i < h.length; i++) {
    const a = h[i], b = h[(i + 1) % h.length];
    const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (!len) continue;
    const ux = (b[0] - a[0]) / len, uy = (b[1] - a[1]) / len;
    let lo = Infinity, hi = -Infinity, nlo = Infinity, nhi = -Infinity;
    for (const q of h) {
      const u = q[0] * ux + q[1] * uy, v = -q[0] * uy + q[1] * ux;
      lo = Math.min(lo, u); hi = Math.max(hi, u); nlo = Math.min(nlo, v); nhi = Math.max(nhi, v);
    }
    const area = (hi - lo) * (nhi - nlo);
    if (!best || area < best.area - 1e-9) {
      const at = (u: number, v: number): Pt => [u * ux - v * uy, u * uy + v * ux];
      best = { area, corners: [at(lo, nlo), at(hi, nlo), at(hi, nhi), at(lo, nhi)], sides: [hi - lo, nhi - nlo] };
    }
  }
  return best!;
}

/** DBPostProcess.get_mini_boxes: the rectangle's corners as top-left, top-right, bottom-right,
 *  bottom-left, and its shorter side. */
function miniBox(points: Pt[]): { box: Quad; sside: number } {
  const { corners, sides } = minAreaRect(points);
  const p = [...corners].sort((a, b) => a[0] - b[0]);
  const [i1, i4] = p[1][1] > p[0][1] ? [0, 1] : [1, 0];
  const [i2, i3] = p[3][1] > p[2][1] ? [2, 3] : [3, 2];
  return { box: [p[i1], p[i2], p[i3], p[i4]], sside: Math.min(...sides) };
}

/** DBPostProcess.box_score_fast: the mean probability inside the box. */
function boxScore(pred: Float32Array, w: number, h: number, box: Quad): number {
  const xs = box.map((q) => q[0]), ys = box.map((q) => q[1]);
  const clip = (v: number, hi: number) => Math.min(Math.max(v, 0), hi);
  const xmin = clip(Math.floor(Math.min(...xs)), w - 1), xmax = clip(Math.ceil(Math.max(...xs)), w - 1);
  const ymin = clip(Math.floor(Math.min(...ys)), h - 1), ymax = clip(Math.ceil(Math.max(...ys)), h - 1);
  // cv2.fillPoly on the corners cast to int, boundary included.
  const poly = box.map((q): Pt => [Math.trunc(q[0] - xmin), Math.trunc(q[1] - ymin)]);
  let sum = 0, n = 0;
  for (let y = 0; y <= ymax - ymin; y++) {
    for (let x = 0; x <= xmax - xmin; x++) {
      if (!inside(poly, x, y)) continue;
      sum += pred[(y + ymin) * w + x + xmin];
      n++;
    }
  }
  return n ? sum / n : 0;
}

function inside(poly: Pt[], x: number, y: number): boolean {
  let sign = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    const c = (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]);
    if (Math.abs(c) < 1e-6) continue;
    const s = Math.sign(c);
    if (sign && s !== sign) return false;
    sign = s;
  }
  return true;
}

/** DBPostProcess.unclip: the box grown by area × 1.6 / perimeter on every side (pyclipper's round
 *  offset, whose smallest rectangle is the box grown by that much; pyclipper works in integers). */
function unclip(box: Quad): Pt[] {
  let area = 0, length = 0;
  for (let i = 0; i < 4; i++) {
    const a = box[i], b = box[(i + 1) % 4];
    area += a[0] * b[1] - b[0] * a[1];
    length += Math.hypot(b[0] - a[0], b[1] - a[1]);
  }
  const d = (Math.abs(area) / 2) * UNCLIP_RATIO / length;
  const q = box.map((p): Pt => [Math.trunc(p[0]), Math.trunc(p[1])]);
  const out: Pt[] = [];
  for (let i = 0; i < 4; i++) {
    const prev = q[(i + 3) % 4], cur = q[i], next = q[(i + 1) % 4];
    const n1 = normal(prev, cur), n2 = normal(cur, next);
    for (const n of [n1, n2]) out.push([Math.round(cur[0] + n[0] * d), Math.round(cur[1] + n[1] * d)]);
  }
  return out;
}

/** The outward normal of edge a→b on a clockwise (y down) quad. */
function normal(a: Pt, b: Pt): Pt {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
  return [(b[1] - a[1]) / len, -(b[0] - a[0]) / len];
}

/** DBPostProcess.order_points_clockwise and clip_det_res. */
function orderAndClip(box: Quad, w: number, h: number): Quad {
  const xs = [...box].sort((a, b) => a[0] - b[0]);
  const [tl, bl] = [xs[0], xs[1]].sort((a, b) => a[1] - b[1]);
  const [tr, br] = [xs[2], xs[3]].sort((a, b) => a[1] - b[1]);
  const c = (p: Pt): Pt => [Math.trunc(Math.min(Math.max(p[0], 0), w - 1)), Math.trunc(Math.min(Math.max(p[1], 0), h - 1))];
  return [c(tl), c(tr), c(br), c(bl)];
}

// --- detection ------------------------------------------------------------------------------

/** 8-connected components of a binary map, each as its pixels: the outer contours findContours
 *  traces. */
function components(bitmap: Uint8Array, w: number, h: number): Pt[][] {
  const label = new Int32Array(w * h).fill(-1);
  const out: Pt[][] = [];
  const stack: number[] = [];
  for (let start = 0; start < w * h; start++) {
    if (!bitmap[start] || label[start] >= 0) continue;
    const pts: Pt[] = [];
    label[start] = out.length;
    stack.push(start);
    while (stack.length) {
      const i = stack.pop()!;
      const x = i % w, y = (i - x) / w;
      pts.push([x, y]);
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          const nx = x + dx, ny = y + dy;
          if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
          const j = ny * w + nx;
          if (bitmap[j] && label[j] < 0) { label[j] = out.length; stack.push(j); }
        }
      }
    }
    out.push(pts);
  }
  return out;
}

/** ch_ppocr_det: TextDetector with DetPreProcess (long side capped) and DBPostProcess. Boxes are in
 *  `img`'s pixels, top to bottom and left to right. */
async function detect(img: Img, run: Run, limitSideLen: number): Promise<Quad[]> {
  const { width: w0, height: h0 } = img;
  const ratio = Math.max(h0, w0) > limitSideLen ? limitSideLen / Math.max(h0, w0) : 1;
  const rh = pyRound(Math.trunc(h0 * ratio) / 32) * 32, rw = pyRound(Math.trunc(w0 * ratio) / 32) * 32;
  if (rh <= 0 || rw <= 0) return [];
  const input = new Float32Array(3 * rh * rw);
  normalize(resize(img, rw, rh), input, 0, rw);
  const pred = (await run({ data: input, dims: [1, 3, rh, rw] })).data;

  // Threshold, then cv2.dilate with a 2×2 kernel (its anchor at the lower right).
  const seg = new Uint8Array(rw * rh);
  for (let i = 0; i < seg.length; i++) seg[i] = pred[i] > DET_THRESH ? 1 : 0;
  const bitmap = new Uint8Array(rw * rh);
  for (let y = 0; y < rh; y++) {
    for (let x = 0; x < rw; x++) {
      bitmap[y * rw + x] = seg[y * rw + x] | (x > 0 ? seg[y * rw + x - 1] : 0)
        | (y > 0 ? seg[(y - 1) * rw + x] : 0) | (x > 0 && y > 0 ? seg[(y - 1) * rw + x - 1] : 0);
    }
  }

  const boxes: Quad[] = [];
  for (const pts of components(bitmap, rw, rh).slice(0, MAX_CANDIDATES)) {
    const first = miniBox(pts);
    if (first.sside < MIN_SIZE) continue;
    if (boxScore(pred, rw, rh, first.box) < BOX_THRESH) continue;
    const grown = miniBox(unclip(first.box));
    if (grown.sside < MIN_SIZE + 2) continue;
    const scaled = grown.box.map((p): Pt => [
      Math.min(Math.max(pyRound(p[0] / rw * w0), 0), w0),
      Math.min(Math.max(pyRound(p[1] / rh * h0), 0), h0),
    ]) as Quad;
    // filter_det_res
    const box = orderAndClip(scaled, w0, h0);
    const bw = Math.trunc(Math.hypot(box[0][0] - box[1][0], box[0][1] - box[1][1]));
    const bh = Math.trunc(Math.hypot(box[0][0] - box[3][0], box[0][1] - box[3][1]));
    if (bw <= 3 || bh <= 3) continue;
    boxes.push(box);
  }
  // TextDetector.sorted_boxes: by the top-left corner's y (stably), into lines where that y jumps
  // by 10 or more, then by x within a line.
  const byY = boxes.map((b, i) => ({ b, i })).sort((a, b) => a.b[0][1] - b.b[0][1] || a.i - b.i);
  let line = 0;
  const keyed = byY.map((e, k) => {
    if (k > 0 && e.b[0][1] - byY[k - 1].b[0][1] >= BOX_SORT_Y) line++;
    return { ...e, line, k };
  });
  keyed.sort((a, b) => a.line - b.line || a.b[0][0] - b.b[0][0] || a.k - b.k);
  return keyed.map((e) => e.b);
}

/** utils/process_img.get_rotate_crop_image: the box straightened into its own image, turned a
 *  quarter if it is tall. The boxes here are rectangles, so the perspective map is affine. */
function cropBox(img: Img, box: Quad): Img {
  const d = (a: Pt, b: Pt) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const w = Math.trunc(Math.max(d(box[0], box[1]), d(box[2], box[3])));
  const h = Math.trunc(Math.max(d(box[0], box[3]), d(box[1], box[2])));
  const out = new Uint8Array(w * h * 3);
  const [p0, p1, , p3] = box;
  const { width: sw, height: sh, data: s } = img;
  for (let v = 0; v < h; v++) {
    for (let u = 0; u < w; u++) {
      const fx = p0[0] + (u / w) * (p1[0] - p0[0]) + (v / h) * (p3[0] - p0[0]);
      const fy = p0[1] + (u / w) * (p1[1] - p0[1]) + (v / h) * (p3[1] - p0[1]);
      const x0 = Math.floor(fx), y0 = Math.floor(fy), ax = fx - x0, ay = fy - y0;
      const X0 = Math.min(Math.max(x0, 0), sw - 1), X1 = Math.min(Math.max(x0 + 1, 0), sw - 1);
      const Y0 = Math.min(Math.max(y0, 0), sh - 1), Y1 = Math.min(Math.max(y0 + 1, 0), sh - 1);
      for (let c = 0; c < 3; c++) {
        const top = s[(Y0 * sw + X0) * 3 + c] * (1 - ax) + s[(Y0 * sw + X1) * 3 + c] * ax;
        const bot = s[(Y1 * sw + X0) * 3 + c] * (1 - ax) + s[(Y1 * sw + X1) * 3 + c] * ax;
        out[(v * w + u) * 3 + c] = Math.round(top * (1 - ay) + bot * ay);
      }
    }
  }
  const straight: Img = { data: out, width: w, height: h, channels: 3 };
  return h / w >= 1.5 ? rot90(straight) : straight;
}

/** np.rot90: a quarter turn anticlockwise. */
function rot90(img: Img): Img {
  const { width: w, height: h, data } = img;
  const out = new Uint8Array(w * h * 3);
  for (let i = 0; i < w; i++) {
    for (let j = 0; j < h; j++) {
      for (let c = 0; c < 3; c++) out[(i * h + j) * 3 + c] = data[(j * w + (w - 1 - i)) * 3 + c];
    }
  }
  return { data: out, width: h, height: w, channels: 3 };
}

// --- recognition ----------------------------------------------------------------------------

/** CTCLabelDecode's argmax and max over the recognizer's output (batch × steps × characters), the
 *  first of equal maxima as numpy's argmax takes it. */
export function ctcBest(preds: Tensor): Best {
  const [n, steps, classes] = preds.dims;
  const best = new Int32Array(n * steps), p = new Float32Array(n * steps);
  for (let s = 0; s < n * steps; s++) {
    const at = s * classes;
    let k0 = 0, v = preds.data[at];
    for (let k = 1; k < classes; k++) if (preds.data[at + k] > v) { v = preds.data[at + k]; k0 = k; }
    best[s] = k0; p[s] = v;
  }
  return { best, p, steps };
}

/** ch_ppocr_rec: TextRecognizer with CTCLabelDecode. Lines are read in batches of six, narrowest
 *  first, each batch padded to its widest. Returns each line's text and score, in order. */
async function recognize(imgs: Img[], run: RecRun, keys: string[]): Promise<[string, number][]> {
  const chars = ["blank", ...keys, " "];
  const ratios = imgs.map((im) => im.width / im.height);
  const order = ratios.map((r, i) => ({ r, i })).sort((a, b) => a.r - b.r || a.i - b.i).map((e) => e.i);
  const out: [string, number][] = imgs.map(() => ["", 0]);
  for (let beg = 0; beg < imgs.length; beg += REC_BATCH) {
    const batch = order.slice(beg, beg + REC_BATCH);
    const maxRatio = Math.max(REC_W / REC_H, ...batch.map((i) => ratios[i]));
    const width = Math.trunc(REC_H * maxRatio);
    // resize_norm_img: the padding is 0 after normalising, mid-grey.
    const input = new Float32Array(batch.length * 3 * REC_H * width);
    batch.forEach((i, n) => {
      const rw = Math.min(width, Math.ceil(REC_H * ratios[i]));
      normalize(resize(imgs[i], rw, REC_H), input, n * 3 * REC_H * width, width);
    });
    const { best: bests, p: ps, steps } = await run({ data: input, dims: [batch.length, 3, REC_H, width] });
    batch.forEach((i, n) => {
      let text = "", prev = -1;
      const confs: number[] = [];
      for (let t = 0; t < steps; t++) {
        const best = bests[n * steps + t], p = ps[n * steps + t];
        if (best !== prev && best !== 0) {
          text += chars[best] ?? "";
          confs.push(Math.round(p * 1e5) / 1e5);
        }
        prev = best;
      }
      const score = confs.length ? confs.reduce((a, b) => a + b, 0) / confs.length : 0;
      out[i] = [text, Math.round(score * 1e5) / 1e5];
    });
  }
  return out;
}

// --- the whole call -------------------------------------------------------------------------

/** RapidOCR.__call__ with the angle classifier left out (it turns a line only when it reads as
 *  upside down, which a broadcast's text never is), as `screen._lines` reduces it. */
export async function readLines(image: Img, engine: Engine, limitSideLen: number): Promise<Line[]> {
  const ori = rgb(image);
  const { img: pre, rh, rw } = withinBounds(ori);
  const { img, top } = verticalPadding(pre);
  const boxes = await detect(img, engine.det, limitSideLen);
  if (!boxes.length) return [];
  const read = await recognize(boxes.map((b) => cropBox(img, b)), engine.rec, engine.keys);
  const lines: Line[] = [];
  boxes.forEach((box, i) => {
    const [text, score] = read[i];
    if (!text.trim() || score < TEXT_SCORE) return;
    // utils/process_img.map_boxes_to_original: undo the padding, then the resize; clamp.
    const pts = box.map(([x, y]): Pt => [
      Math.min(Math.max(x * rw, 0), ori.width), Math.min(Math.max((y - top) * rh, 0), ori.height),
    ]);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    lines.push([text.trim(), Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]);
  });
  return lines;
}
