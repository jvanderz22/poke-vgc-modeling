"""The browser's OCR (frontend/src/ocr/rapid.ts) against RapidOCR, on footage.

The watch page reads the battle's regions itself, on WebGPU, and sends the server the lines. This
runs the same TypeScript in Node (onnxruntime-web, WASM) on the same models the server loads, and
puts it in RapidOCR's place. Two screen readers watch each window side by side: one reads with
RapidOCR, the other with the browser's lines for the very same crops (both use the same change
check, so they read the same regions on the same frames). It reports how the two compare per crop
(the lines' text and boxes) and per event (what each reader settled).

    .venv/bin/python scripts/analysis/browser_ocr_parity.py
    .venv/bin/python scripts/analysis/browser_ocr_parity.py --window louisville-r5-g1:420:480

Needs `npm install` in frontend/ and the gitignored clips in data/video/.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from vgc import paths

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = ["louisville-r6-g1:200:260", "louisville-r5-g1:420:480"]
BOX_TOLERANCE = 3  # pixels a line's box may differ by and still be the same line


def _event_key(e) -> tuple[str, str]:
    return e.kind, json.dumps(e.data, sort_keys=True)


def compare_events(ref: list, got: list) -> tuple[list, list]:
    """The events only one reader settled: same kind and value within 1.5 s count as one."""
    used: set[int] = set()
    lost = []
    for e in ref:
        m = next((i for i, o in enumerate(got) if i not in used and _event_key(o) == _event_key(e)
                  and abs(o.t - e.t) <= 1.5), None)
        if m is None:
            lost.append(e)
        else:
            used.add(m)
    return lost, [o for i, o in enumerate(got) if i not in used]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", action="append", help="clip:start:end, a clip in data/video (repeatable)")
    ap.add_argument("--fps", type=float, default=4.0)
    args = ap.parse_args()

    from vgc.video import ocr, screen
    from vgc.video.clip import frames

    # One thread: with the browser's side waiting its turn, the whole check takes about one core.
    ocr.THREADS = 1
    engine = screen._engine()
    engine(np.zeros((64, 256, 3), np.uint8) + 255)  # loads the detector
    engine(np.asarray(__import__("PIL.Image", fromlist=["Image"]).open(ROOT / "tests/fixtures/video/r5-choosing.jpg").convert("RGB")))
    det = engine.text_det.session.session._model_path
    rec = engine.text_rec.session.session._model_path
    keys = engine.text_rec.postprocess_op.character[1:-1]  # without the blank and the space it adds

    tmp = Path(tempfile.mkdtemp(prefix="ocr-parity-"))
    (tmp / "keys.json").write_text(json.dumps(keys))
    node = subprocess.Popen(["node", str(ROOT / "frontend/scripts/ocr-parity.ts"), det, rec, str(tmp / "keys.json")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT / "frontend")

    reference = screen._lines
    crops: list[dict[str, Any]] = []
    stash: list[list] = []

    def browser(crop: np.ndarray) -> list:
        path = tmp / "crop.rgb"
        path.write_bytes(np.ascontiguousarray(crop).tobytes())
        node.stdin.write(json.dumps({"path": str(path), "width": crop.shape[1], "height": crop.shape[0],
                                     "limit": 960}) + "\n")
        node.stdin.flush()
        reply = json.loads(node.stdout.readline())
        return [tuple(ln) for ln in reply["lines"]], reply["ms"]

    def both(crop: np.ndarray) -> list:
        ref = reference(crop)
        got, ms = browser(crop)
        stash.append(got)
        crops.append({"ref": ref, "got": got, "ms": ms, "shape": crop.shape[:2]})
        return ref

    def from_stash(crop: np.ndarray) -> list:
        return stash.pop(0)

    total_lost = total_added = 0
    for spec in args.window or WINDOWS:
        clip, start, end = spec.split(":")
        a, b = screen.ScreenReader(), screen.ScreenReader()
        sa, sb = screen.Settler(), screen.Settler()
        ev_a, ev_b = [], []
        for f in frames(paths.DATA / "video" / f"{clip}.mov", every=1 / args.fps, start=float(start), end=float(end)):
            img = screen.scale(f.image)
            screen._lines = both
            ev_a += sa.feed(f.clip_s, a.observe(img))
            screen._lines = from_stash
            ev_b += sb.feed(f.clip_s, b.observe(img))
        screen._lines = reference
        assert not stash, "the two readers read different regions"
        lost, added = compare_events(ev_a, ev_b)
        total_lost += len(lost)
        total_added += len(added)
        print(f"{spec}: RapidOCR settled {len(ev_a)} events, the browser's lines {len(ev_b)}; "
              f"{len(ev_a) - len(lost)} the same, {len(lost)} only RapidOCR's, {len(added)} only the browser's")
        for tag, es in (("- RapidOCR only", lost), ("+ browser only", added)):
            for e in es:
                print(f"    {tag} {e.t:8.2f} {e.kind:8} {json.dumps(e.data)}")

    node.stdin.close()
    node.wait()

    same_text = sum(1 for c in crops if [t for t, *_ in c["ref"]] == [t for t, *_ in c["got"]])
    boxes_ok = sum(1 for c in crops if len(c["ref"]) == len(c["got"]) and all(
        max(abs(p - q) for p, q in zip(r[1:], g[1:])) <= BOX_TOLERANCE for r, g in zip(c["ref"], c["got"])))
    lines_ref = sum(len(c["ref"]) for c in crops)
    lines_got = sum(len(c["got"]) for c in crops)
    print(f"\n{len(crops)} crops read: the same lines in {same_text} ({same_text / max(1, len(crops)):.1%}), "
          f"the same boxes (±{BOX_TOLERANCE} px) in {boxes_ok}; {lines_ref} lines from RapidOCR, {lines_got} from the browser")
    shown = 0
    for c in crops:
        if [t for t, *_ in c["ref"]] != [t for t, *_ in c["got"]] and shown < 25:
            print(f"    {c['shape']}  RapidOCR {[t for t, *_ in c['ref']]}  browser {[t for t, *_ in c['got']]}")
            shown += 1
    ms = [c["ms"] for c in crops]
    if ms:
        print(f"browser OCR on WASM in Node: median {np.median(ms):.0f} ms a crop, p90 {np.percentile(ms, 90):.0f}")
    return 0 if total_lost == total_added == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
