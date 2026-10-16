// The server's OCR models, run on the page's GPU (onnxruntime-web's WebGPU backend). Measured in
// Chrome on a laptop's Radeon Pro 555X: a message strip's detection 52 ms, three lines recognised
// 55 ms; on WASM the same took 404 and 446 ms, too slow to keep up, so without WebGPU the server
// reads the frames as before.

import type { InferenceSession, Tensor as OrtTensor } from "onnxruntime-web/webgpu";
import { ctcBest, type Best, type Engine, type RecRun, type Run, type Tensor } from "./rapid.ts";
import type { OcrConfig } from "./screen.ts";

type Gpu = { requestAdapter(): Promise<unknown | null> };

/** An engine on WebGPU, its shaders compiled; throws when there is no GPU to run it on. */
export async function webgpuEngine(config: OcrConfig): Promise<Engine> {
  const gpu = (navigator as unknown as { gpu?: Gpu }).gpu;
  if (!gpu || !(await gpu.requestAdapter())) throw new Error("this browser has no WebGPU");
  const ort = await import("onnxruntime-web/webgpu");
  // The page isn't cross-origin isolated (the YouTube player rules it out), so WASM, which still
  // runs the odd operator, gets one thread.
  ort.env.wasm.numThreads = 1;

  const create = (model: string | Uint8Array, options: InferenceSession.SessionOptions = {}) =>
    typeof model === "string"
      ? ort.InferenceSession.create(model, { executionProviders: ["webgpu"], ...options })
      : ort.InferenceSession.create(model, { executionProviders: ["webgpu"], ...options });
  const feed = (s: InferenceSession, x: Tensor) => ({ [s.inputNames[0]]: new ort.Tensor("float32", x.data, x.dims) });

  const detSession = await create(config.det);
  const det: Run = async (x) => {
    const y = (await detSession.run(feed(detSession, x)))[detSession.outputNames[0]];
    return { data: (await y.getData()) as Float32Array, dims: y.dims };
  };

  // The recognizer's output is batch × steps × 18,710 characters, 18 to 54 MB a batch, of which
  // CTCLabelDecode takes two numbers a step. It stays on the GPU, and the CTC head
  // (scripts/ctc_head.py) reduces it there. The head may not run anywhere but the GPU: on the
  // CPU it would need the whole output brought back, which is what it is there to save.
  const recSession = await create(config.rec, { preferredOutputLocation: "gpu-buffer" });
  let head: InferenceSession | null = null;
  try {
    // 273 bytes, which the build inlines as a data: URL.
    const bytes = new Uint8Array(await (await fetch(new URL("./ctc-head.onnx", import.meta.url))).arrayBuffer());
    head = await create(bytes, { extra: { session: { disable_cpu_ep_fallback: "1" } } });
  } catch (e) {
    console.warn("The CTC head can't run on this GPU; the recognizer's whole output is read back.", e);
  }
  const recOut = async (x: Tensor) => (await recSession.run(feed(recSession, x)))[recSession.outputNames[0]];
  const readBack = async (y: OrtTensor): Promise<Best> =>
    ctcBest({ data: (await y.getData(true)) as Float32Array, dims: y.dims });
  const onGpu = async (h: InferenceSession, y: OrtTensor): Promise<Best> => {
    const out = await h.run({ [h.inputNames[0]]: y });
    const best = Int32Array.from(await out.best.getData() as BigInt64Array, Number);
    return { best, p: (await out.p.getData()) as Float32Array, steps: y.dims[1] };
  };
  const rec: RecRun = async (x) => {
    const y = await recOut(x);
    if (!head) return readBack(y);
    try { return await onGpu(head, y); } finally { y.dispose(); }
  };

  // The first run of each shape compiles its shaders, about a second: done here, not on a frame.
  await det({ data: new Float32Array(3 * 96 * 960), dims: [1, 3, 96, 960] });
  // The recognizer's on noise, so the head is checked against the scan it replaces on an output
  // with something in it. A head that reads it differently isn't used.
  const noise: Tensor = { data: Float32Array.from({ length: 3 * 48 * 320 }, (_, i) => Math.sin(i * 12.9898) % 1), dims: [1, 3, 48, 320] };
  if (head) {
    const y = await recOut(noise);
    try {
      const fast = await onGpu(head, y), slow = await readBack(y);
      if (!fast.best.every((k, i) => k === slow.best[i]) || !fast.p.every((v, i) => v === slow.p[i])) {
        console.warn("The CTC head disagreed with reading the recognizer's output back; reading it back.");
        head = null;
      }
    } catch (e) {
      console.warn("The CTC head failed on the GPU; the recognizer's whole output is read back.", e);
      head = null;
      try { y.dispose(); } catch { /* already released by the read back */ }
    }
  } else {
    await rec(noise);
  }
  return { det, rec, keys: config.keys };
}
