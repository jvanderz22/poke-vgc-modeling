"""L1 — the pinned local Showdown: server lifecycle, validator bridge, legality export."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path

from vgc import paths
from vgc.regulation import Regulation

PID_FILE = paths.ROOT / ".vgc" / "showdown.pid"
LOG_FILE = paths.ROOT / ".vgc" / "showdown.log"
# Phase 0: the default of 1 caps throughput at ~19 battles/s; 4 gives ~36/s at 8 workers.
DEFAULT_SIMULATORS = 4


class ShowdownError(RuntimeError):
    pass


def _require_build() -> None:
    if not (paths.SHOWDOWN / "dist" / "sim").exists():
        raise ShowdownError(
            f"Showdown isn't built at {paths.SHOWDOWN}. Run: "
            "git submodule update --init && (cd vendor/pokemon-showdown && npm i && node build)"
        )


def head_sha() -> str:
    return subprocess.run(
        ["git", "-C", str(paths.SHOWDOWN), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


def check_pin(reg: Regulation) -> None:
    sha = head_sha()
    if sha != reg.showdown_sha:
        raise ShowdownError(f"vendor/pokemon-showdown is at {sha[:10]}, but {reg.id} pins {reg.showdown_sha[:10]}")


def write_config(simulators: int = DEFAULT_SIMULATORS) -> Path:
    """Showdown's config/config.js is gitignored and generated on first start; set the
    simulator subprocess count in it."""
    cfg = paths.SHOWDOWN / "config" / "config.js"
    if not cfg.exists():
        cfg.write_text((paths.SHOWDOWN / "config" / "config-example.js").read_text())
    text, n = re.subn(r"(?m)^(\tsimulator:\s*)\d+,", rf"\g<1>{simulators},", cfg.read_text())
    if n != 1:
        raise ShowdownError(f"couldn't find the simulator subprocess setting in {cfg}")
    cfg.write_text(text)
    return cfg


def _port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def server_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text())
        os.kill(pid, 0)
        return pid
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def start_server(port: int = 8000, simulators: int = DEFAULT_SIMULATORS, timeout: float = 60) -> int:
    _require_build()
    if (pid := server_pid()) is not None:
        return pid
    if _port_open(port):
        raise ShowdownError(f"port {port} is already in use by another process")
    write_config(simulators)
    PID_FILE.parent.mkdir(exist_ok=True)
    with LOG_FILE.open("w") as log:
        proc = subprocess.Popen(
            ["node", "pokemon-showdown", "start", "--no-security", str(port)],
            cwd=paths.SHOWDOWN, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
    PID_FILE.write_text(str(proc.pid))
    deadline = time.monotonic() + timeout
    while not _port_open(port):
        if proc.poll() is not None:
            raise ShowdownError(f"Showdown exited with {proc.returncode}; see {LOG_FILE}")
        if time.monotonic() > deadline:
            raise ShowdownError(f"Showdown didn't open port {port} within {timeout}s; see {LOG_FILE}")
        time.sleep(0.5)
    return proc.pid


def stop_server() -> bool:
    pid = server_pid()
    if pid is None:
        return False
    os.killpg(pid, signal.SIGTERM)  # the server forks simulator subprocesses into its group
    PID_FILE.unlink(missing_ok=True)
    return True


def validate_with_showdown(team_text: str, reg: Regulation) -> list[str]:
    """Run Showdown's own validator. Returns its problem lines (empty = accepted)."""
    _require_build()
    res = subprocess.run(
        ["node", "pokemon-showdown", "validate-team", reg.showdown_format],
        cwd=paths.SHOWDOWN, input=team_text, capture_output=True, text=True,
    )
    if res.returncode == 0:
        return []
    # Problems go to stderr with exit 1; anything else (or an empty report) is a crash.
    lines = [ln for ln in res.stderr.splitlines() if ln.strip()]
    if res.returncode != 1 or not lines:
        raise ShowdownError(res.stderr.strip() or f"validate-team exited {res.returncode}")
    return lines


def export_dex(reg: Regulation, out: Path | None = None) -> Path:
    """Write the regulation's legality snapshot from the pinned Showdown build."""
    _require_build()
    check_pin(reg)
    res = subprocess.run(
        ["node", str(paths.SIDECAR / "showdown" / "export-dex.js"), str(paths.SHOWDOWN), reg.showdown_format],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise ShowdownError(res.stderr.strip())
    data = json.loads(res.stdout)
    data["meta"] = {
        "regulation": reg.id,
        "showdown_format": reg.showdown_format,
        "showdown_sha": reg.showdown_sha,
        "exported_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "format": data.pop("format"),
    }
    out = out or reg.dex_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    return out
