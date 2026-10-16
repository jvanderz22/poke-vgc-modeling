"""Writes frontend/src/ocr/ctc-head.onnx: the part of RapidOCR's CTCLabelDecode that reads the
recognizer's output, each step's likeliest character and its probability (`preds.argmax(axis=2)`,
`preds.max(axis=2)`), as a model the watch page runs on the GPU after the recognizer.

The recognizer's output is batch × steps × 18,710 characters, about 18 MB for six short lines.
Reduced on the GPU, two numbers a step come back instead of that, and the page's JavaScript
doesn't scan it (frontend/src/ocr/engine.ts). The page checks the head against its own scan when
the models load, and reads the full output as before if the two disagree.

    python scripts/ctc_head.py           # write the model
    python scripts/ctc_head.py --check   # and compare it with numpy on the recognizer's output
"""

from __future__ import annotations

import argparse
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "ocr" / "ctc-head.onnx"


def build() -> bytes:
    import onnx
    from onnx import TensorProto, helper

    preds = helper.make_tensor_value_info("preds", TensorProto.FLOAT, ["n", "steps", "classes"])
    best = helper.make_tensor_value_info("best", TensorProto.INT64, ["n", "steps"])
    p = helper.make_tensor_value_info("p", TensorProto.FLOAT, ["n", "steps"])
    graph = helper.make_graph(
        # ArgMax keeps the first of equal maxima, as numpy's argmax does.
        [helper.make_node("ArgMax", ["preds"], ["best"], axis=2, keepdims=0, select_last_index=0),
         helper.make_node("ReduceMax", ["preds"], ["p"], axes=[2], keepdims=0)],
        "ctc_head", [preds], [best, p])
    # Opset 13: ReduceMax still takes its axes as an attribute, which every backend supports.
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)],
                              producer_name="scripts/ctc_head.py")
    model.ir_version = 7
    onnx.checker.check_model(model)
    return model.SerializeToString()


def check(model: bytes) -> None:
    """The head against numpy on the recognizer's real output, for a few strips of noise."""
    import numpy as np
    import onnxruntime as ort

    from vgc.video import ocr, screen

    rec = ort.InferenceSession(str(ocr.browser_models(screen._engine())["rec"]))
    head = ort.InferenceSession(model)
    rng = np.random.default_rng(0)
    for width in (320, 640, 960):
        x = rng.uniform(-1, 1, (6, 3, 48, width)).astype(np.float32)
        preds = rec.run(None, {rec.get_inputs()[0].name: x})[0]
        best, p = head.run(None, {"preds": preds})
        assert (best == preds.argmax(axis=2)).all() and (p == preds.max(axis=2)).all(), width
        print(f"width {width}: {preds.shape} -> {best.shape}, same as numpy "
              f"({preds.nbytes / 1e6:.1f} MB reduced to {(best.size * 2 * 4) / 1e3:.1f} kB)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="compare with numpy on the recognizer's output")
    args = ap.parse_args()
    model = build()
    OUT.write_bytes(model)
    print(f"wrote {OUT} ({len(model)} bytes)")
    if args.check:
        check(model)


if __name__ == "__main__":
    main()
