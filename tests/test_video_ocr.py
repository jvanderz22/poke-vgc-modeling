from __future__ import annotations

from pathlib import Path

import pytest

FRAMES = Path(__file__).parent / "fixtures" / "video"


def test_every_session_is_two_threads_and_does_not_spin():
    """Spinning threads were most of the reader's CPU on the deployed machine's shared vCPUs. The
    setting reaches the sessions only by wrapping RapidOCR's own, so a RapidOCR upgrade that moves it
    has to fail here rather than quietly spin again."""
    pytest.importorskip("rapidocr")
    import numpy as np
    from PIL import Image

    from vgc.video.ocr import THREADS
    from vgc.video.screen import _engine

    engine = _engine()
    # The models load on first use; a frame with text loads all three.
    engine(np.asarray(Image.open(FRAMES / "r5-choosing.jpg").convert("RGB")))
    for part in (engine.text_det, engine.text_cls, engine.text_rec):
        opts = part.session.session.get_session_options()
        assert opts.intra_op_num_threads == THREADS
        assert opts.get_session_config_entry("session.intra_op.allow_spinning") == "0"
        assert opts.get_session_config_entry("session.inter_op.allow_spinning") == "0"
