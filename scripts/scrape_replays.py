"""Every public replay newer than the cache, for a regulation's formats (PLAN-v5 steps 0 and 5).

`vgc meta scrape` walks a fixed number of pages and stops on the first error. This walks each
format newest-first until it reaches the range already cached, so a weekly run fetches exactly
what is new:

  stop       after `--stop` consecutive pages whose every replay is already in `data/replays/`.
             Replays already in the destination are skipped but do not count towards stopping, so a
             rerun walks past what an earlier run staged and resumes where it left off. Gaps in the
             cache from earlier page-limited scrapes are filled on the way.
  staged     into `data/replays-new/<format>/` by default, not the cache: several analyses sample
             from `data/replays/`, and a Kaggle merge replays against a packed list, so new files
             there would change their inputs silently. `--dest data/replays` writes to the cache.
  polite     one kept-alive HTTPS connection per format (0.08 s a fetch against 0.16 s with a new
             connection each, and no handshake per replay for the server), `--delay` between
             replays. Transient errors are retried with backoff on a fresh connection; a replay that
             404s (deleted or made private) is skipped and counted.

The formats run side by side, one thread each. 2026-10-04/05: 70,467 M-C replays (46,449 Bo1 and
24,018 Bo3, 2026-09-19 to 2026-10-04) at about 150 a minute per format.

    .venv/bin/python scripts/scrape_replays.py                      # both of reg_mc's formats
    .venv/bin/python scripts/scrape_replays.py --format bo3 --dest data/replays
"""

from __future__ import annotations

import argparse
import gzip
import http.client
import json
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vgc import paths
from vgc.meta.pool import formats_for
from vgc.meta.replays import BASE, REPLAYS, USER_AGENT
from vgc.regulation import load_regulation

HOST = BASE.split("://", 1)[1]
_print = threading.Lock()


def say(*parts: object) -> None:
    with _print:
        print(*parts, flush=True)


class Client:
    """One kept-alive connection to the replay server, reopened after any error."""

    def __init__(self, tries: int = 6):
        self.tries = tries
        self.conn: http.client.HTTPSConnection | None = None

    def get(self, path: str) -> object | None:
        """The JSON at `path`, or None for a 404."""
        for i in range(self.tries):
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPSConnection(HOST, timeout=30)
                self.conn.request("GET", path, headers={"User-Agent": USER_AGENT})
                r = self.conn.getresponse()
                body = r.read()
                if r.status == 404:
                    return None
                if r.status != 200:
                    raise RuntimeError(f"HTTP {r.status}")
                return json.loads(body)
            except Exception as e:  # a dropped connection, a timeout, a 5xx: retry on a fresh one
                say(f"  retry {i + 1} on {path}: {e}")
                if self.conn is not None:
                    self.conn.close()
                self.conn = None
                time.sleep(5 * 2 ** i)
        raise RuntimeError(f"gave up on {path}")


def scrape(fmt: str, dest: Path, stop: int = 3, delay: float = 0.3, max_pages: int | None = None) -> dict[str, int]:
    """Walk `fmt` newest-first into `dest/<fmt>/` until `stop` consecutive pages are wholly cached."""
    out = dest / fmt
    out.mkdir(parents=True, exist_ok=True)
    cache = REPLAYS / fmt
    client = Client()
    before: int | None = None
    new = gone = pages = cached_run = 0
    while max_pages is None or pages < max_pages:
        q = {"format": fmt} | ({"before": str(before)} if before else {})
        page = client.get(f"/search.json?{urllib.parse.urlencode(q)}")
        if not page:
            break
        assert isinstance(page, list)
        pages += 1
        uncached = 0
        for meta in page[:50]:  # the 51st item only signals that another page exists
            rid = meta["id"]
            if (cache / f"{rid}.json.gz").exists():
                continue
            uncached += 1
            path = out / f"{rid}.json.gz"
            if path.exists():
                continue
            data = client.get(f"/{rid}.json")
            if data is None:
                gone += 1
                continue
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(json.dumps(data).encode()))
            tmp.rename(path)  # a stop mid-write leaves no half file under the real name
            new += 1
            time.sleep(delay)
        cached_run = cached_run + 1 if uncached == 0 else 0
        last = time.strftime("%Y-%m-%d %H:%M", time.localtime(page[min(49, len(page) - 1)]["uploadtime"]))
        say(f"{fmt} page {pages} back to {last}: {new} new, {gone} gone")
        if cached_run >= stop or len(page) <= 50:
            break
        before = page[49]["uploadtime"]
        time.sleep(0.5)
    say(f"{fmt} DONE: {new} new, {gone} gone, {pages} pages")
    return {"new": new, "gone": gone, "pages": pages}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--regulation", "-r", default="reg_mc")
    ap.add_argument("--format", choices=["bo3", "bo1", "both"], default="both")
    ap.add_argument("--dest", type=Path, default=paths.DATA / "replays-new",
                    help="where new replays go, one folder per format (default: data/replays-new)")
    ap.add_argument("--stop", type=int, default=3, help="consecutive wholly cached pages that end a format")
    ap.add_argument("--delay", type=float, default=0.3, help="seconds between replays")
    ap.add_argument("--max-pages", type=int, help="a cap, for a first scrape of a regulation with no cache")
    a = ap.parse_args()

    reg = load_regulation(a.regulation)
    fmts = formats_for(reg)
    if a.format != "both":
        fmts = [f for f in fmts if f.endswith("bo3") == (a.format == "bo3")]
    with ThreadPoolExecutor(len(fmts)) as ex:
        done = dict(zip(fmts, ex.map(lambda f: scrape(f, a.dest, a.stop, a.delay, a.max_pages), fmts)))
    print(json.dumps(done, indent=1))


if __name__ == "__main__":
    main()
