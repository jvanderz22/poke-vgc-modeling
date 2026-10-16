"""The OCR engine the readers share: RapidOCR on onnxruntime, held to a small, idle-when-idle pool.

By default onnxruntime gives a session one thread per core and lets them spin while they wait for
work. On the deployed machine's shared vCPUs that spinning is CPU the machine is billed for and
throttled on: a frame of the battle reader cost 2.25 CPU-seconds for 0.42 s of wall time. With
spinning off and two threads it cost 0.76 CPU-seconds for 0.43 s (60 s of louisville-r6 at 4 fps,
on an x86 laptop, 2026-10-15). The models and their answers are the same.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

THREADS = 2
_SPIN_OFF = {"session.intra_op.allow_spinning": "0", "session.inter_op.allow_spinning": "0"}


def _quiet_sessions() -> None:
    """Turn spinning off in every session RapidOCR makes. RapidOCR doesn't expose the setting."""
    from rapidocr.inference_engine.onnxruntime.main import OrtInferSession

    init = OrtInferSession.__dict__["_init_sess_opts"].__func__
    if getattr(init, "_spin_off", False):
        return

    def init_sess_opts(cfg: Any) -> Any:
        opts = init(cfg)
        for key, value in _SPIN_OFF.items():
            opts.add_session_config_entry(key, value)
        return opts

    init_sess_opts._spin_off = True  # type: ignore[attr-defined]
    OrtInferSession._init_sess_opts = staticmethod(init_sess_opts)


def browser_models(engine: Any) -> dict[str, Any]:
    """What the watch page needs to run `engine`'s models itself (frontend/src/ocr/rapid.ts): the
    detector's and recognizer's files, as RapidOCR resolved them, and the recognizer's characters
    without the blank and the space RapidOCR adds around them."""
    det, rec = engine._load_det_model(), engine._load_rec_model()
    return {"det": Path(det.session.session._model_path), "rec": Path(rec.session.session._model_path),
            "keys": list(rec.postprocess_op.character[1:-1])}


def rapid_ocr(limit_side_len: int, needed_for: str) -> Any:
    """A RapidOCR engine whose detector caps the image's long side at `limit_side_len`."""
    try:
        from rapidocr import RapidOCR
    except ImportError as e:  # an optional dependency: pip install -e '.[video]'
        raise RuntimeError(f"{needed_for} needs RapidOCR: pip install -e '.[video]'") from e
    _quiet_sessions()
    return RapidOCR(params={
        "Det.limit_side_len": limit_side_len, "Det.limit_type": "max", "Global.log_level": "error",
        "EngineConfig.onnxruntime.intra_op_num_threads": THREADS,
        "EngineConfig.onnxruntime.inter_op_num_threads": 1,
    })
