"""Cached, rate-limited, retrying downloader. Never parses. Transport is injectable (tests use recorded fixtures).

A host that refuses (egress policy / DNS / repeated 5xx) degrades its ISINs to SOURCE_UNAVAILABLE; the run continues.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


@dataclass
class Response:
    status: int
    content: bytes
    headers: dict


Transport = Callable[[str, dict], Response]


def requests_transport(timeout: float = 60.0) -> Transport:          # pragma: no cover - network
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (research; RMBS payment history)"

    def _get(url: str, headers: dict) -> Response:
        r = s.get(url, headers=headers, timeout=timeout)
        return Response(r.status_code, r.content, dict(r.headers))
    return _get


class Fetcher:
    def __init__(self, cache_dir: Path, transport: Transport | None = None, min_spacing: float = 0.4,
                 retries: int = 3, backoff: float = 2.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, max_host_failures: int = 3):
        self.cache = Path(cache_dir)
        self.transport = transport
        self.min_spacing, self.retries, self.backoff = min_spacing, retries, backoff
        self.sleep, self.clock = sleep, clock
        self.last_hit: dict[str, float] = {}
        self.host_failures: dict[str, int] = {}
        self.max_host_failures = max_host_failures
        self.log: list[dict] = []
        self.index_path = self.cache / "index.json"
        self.index: dict = json.loads(self.index_path.read_text()) if self.index_path.exists() else {}

    def dead_hosts(self) -> set[str]:
        return {h for h, n in self.host_failures.items() if n >= self.max_host_failures}

    def _space(self, host: str) -> None:
        t = self.last_hit.get(host)
        if t is not None:
            wait = self.min_spacing - (self.clock() - t)
            if wait > 0:
                self.sleep(wait)
        self.last_hit[host] = self.clock()

    def get(self, url: str, dest: Path, expect_pdf: bool = True) -> dict:
        """Return a log record {url, status, bytes, sha256, path, event}. Cached files are not re-downloaded."""
        dest = Path(dest)
        host = urlparse(url).netloc
        if dest.exists() and dest.stat().st_size > 0:
            rec = {"url": url, "status": "CACHED", "bytes": dest.stat().st_size,
                   "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "path": str(dest), "event": "cached"}
            self.log.append(rec)
            return rec
        if self.transport is None or host in self.dead_hosts():
            rec = {"url": url, "status": "SOURCE_UNAVAILABLE", "bytes": 0, "sha256": "", "path": "",
                   "event": "offline" if self.transport is None else "host degraded"}
            self.log.append(rec)
            return rec
        headers = {}
        etag = self.index.get(url, {}).get("etag")
        if etag:
            headers["If-None-Match"] = etag
        err = ""
        for attempt in range(self.retries):
            self._space(host)
            try:
                r = self.transport(url, headers)
            except Exception as e:  # network error -> retry
                err = f"ERR {type(e).__name__}: {str(e)[:80]}"
                self.sleep(self.backoff ** attempt)
                continue
            if r.status >= 500 or r.status == 429:
                err = f"HTTP {r.status}"
                self.sleep(self.backoff ** attempt)
                continue
            ok = r.status == 200 and (not expect_pdf or r.content[:5] == b"%PDF-")
            sha = hashlib.sha256(r.content).hexdigest() if ok else ""
            if ok:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                prev = self.index.get(url, {}).get("sha256")
                self.index[url] = {"sha256": sha, "etag": r.headers.get("ETag", ""), "path": str(dest)}
                self._save_index()
                event = "RESTATED" if prev and prev != sha else "downloaded"
            else:
                event = "not a PDF" if r.status == 200 else f"HTTP {r.status}"
            self.host_failures[host] = 0
            rec = {"url": url, "status": r.status, "bytes": len(r.content), "sha256": sha,
                   "path": str(dest) if ok else "", "event": event}
            self.log.append(rec)
            return rec
        self.host_failures[host] = self.host_failures.get(host, 0) + 1
        rec = {"url": url, "status": "SOURCE_UNAVAILABLE", "bytes": 0, "sha256": "", "path": "", "event": err}
        self.log.append(rec)
        return rec

    def _save_index(self) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        self.index_path.write_text(json.dumps(self.index, indent=1, sort_keys=True))
