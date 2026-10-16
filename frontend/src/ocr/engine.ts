// The server's OCR models, run on the page's GPU (onnxruntime-web's WebGPU backend). Measured in
// Chrome on a laptop's Radeon Pro 555X: a message strip's detection 52 ms, three lines recognised
// 55 ms; on WASM the same took 404 and 446 ms, too slow to keep up, so without WebGPU the server
// reads the frames as before.

import type { Engine, Run } from "./rapid.ts";
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

  async function session(url: string): Promise<Run> {
    const s = await ort.InferenceSession.create(url, { executionProviders: ["webgpu"] });
    return async (x) => {
      const out = await s.run({ [s.inputNames[0]]: new ort.Tensor("float32", x.data, x.dims) });
      const y = out[s.outputNames[0]];
      return { data: (await y.getData()) as Float32Array, dims: y.dims };
    };
  }
  const engine: Engine = { det: await session(config.det), rec: await session(config.rec), keys: config.keys };
  // The first run of each shape compiles its shaders, about a second: done here, not on a frame.
  await engine.det({ data: new Float32Array(3 * 96 * 960), dims: [1, 3, 96, 960] });
  await engine.rec({ data: new Float32Array(3 * 48 * 320), dims: [1, 3, 48, 320] });
  return engine;
}
