"""Filesystem locations, resolved relative to the repository root."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("VGC_ROOT", Path(__file__).resolve().parents[2]))
CONFIGS = ROOT / "configs" / "regulations"
REGULATION_DATA = ROOT / "data" / "regulations"
CHAMPIONS_DATA = ROOT / "data" / "champions-data"
SHOWDOWN = ROOT / "vendor" / "pokemon-showdown"
SIDECAR = ROOT / "sidecar"
