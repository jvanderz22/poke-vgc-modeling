// The browser's OCR (src/ocr/rapid.ts) as a line-at-a-time server, for
// scripts/analysis/browser_ocr_parity.py: it runs the same code the watch page runs, on
// onnxruntime-web's WASM backend, so the Python side can put it where RapidOCR was and compare.
//
//   node frontend/scripts/ocr-parity.ts <det.onnx> <rec.onnx> <keys.json>
//
// Each request on stdin is one JSON line {"path", "width", "height", "limit"} naming a file of raw
// RGB bytes; each reply on stdout is one JSON line {"lines", "ms"}.
import { readFileSync } from "node:fs";
import { createInterface } from "node:readline";
import * as ort from "onnxruntime-web";
import { readLines, type Run } from "../src/ocr/rapid.ts";

ort.env.wasm.numThreads = 1;
const [det, rec, keys] = process.argv.slice(2);

async function session(path: string): Promise<Run> {
  const s = await ort.InferenceSession.create(readFileSync(path), { executionProviders: ["wasm"] });
  return async (x) => {
    const out = await s.run({ [s.inputNames[0]]: new ort.Tensor("float32", x.data, x.dims) });
    const y = out[s.outputNames[0]];
    return { data: y.data as Float32Array, dims: y.dims };
  };
}

const engine = { det: await session(det), rec: await session(rec), keys: JSON.parse(readFileSync(keys, "utf8")) as string[] };
const lines = createInterface({ input: process.stdin });
for await (const line of lines) {
  const q = JSON.parse(line) as { path: string; width: number; height: number; limit: number };
  const data = new Uint8Array(readFileSync(q.path));
  const t = performance.now();
  const read = await readLines({ data, width: q.width, height: q.height, channels: 3 }, engine, q.limit);
  process.stdout.write(JSON.stringify({ lines: read, ms: performance.now() - t }) + "\n");
}
