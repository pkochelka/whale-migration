"""HTTP helpers: retrying session, per-host rate limiting, cached file downloads with manifests."""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

_last_call: dict[str, float] = {}
_lock = threading.Lock()


class HttpClient:
    def __init__(self, cfg: dict[str, Any] | None = None):
        cfg = cfg or {}
        self.timeout = cfg.get("timeout_s", 120)
        self.retries = cfg.get("retries", 5)
        self.backoff = cfg.get("backoff_s", 5)
        self.min_interval = cfg.get("min_interval_s", 0.5)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = cfg.get("user_agent", "whale-pipeline/0.1")

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        with _lock:
            wait = self.min_interval - (time.monotonic() - _last_call.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            _last_call[host] = time.monotonic()

    def _bounded(self, method: str, url: str, **kw) -> requests.Response:
        """Non-streamed request with a wall-clock cap.

        requests' read timeout restarts on every received chunk, so a server trickling bytes can
        hold a call for an hour. Bodies are read in chunks against a deadline instead.
        """
        deadline = time.monotonic() + 3 * self.timeout
        r = self.session.request(method, url, stream=True, **kw)
        chunks = []
        for chunk in r.iter_content(chunk_size=1 << 16):
            chunks.append(chunk)
            if time.monotonic() > deadline:
                r.close()
                raise requests.Timeout(f"body of {url} not received within {3 * self.timeout}s")
        r._content = b"".join(chunks)
        r._content_consumed = True
        return r

    def request(self, method: str, url: str, **kw) -> requests.Response:
        kw.setdefault("timeout", (30, self.timeout))
        last_exc: Exception | None = None
        attempt, rate_limited = 0, 0
        # Rate limiting (429) is not a failure of the server: it gets its own, more patient budget.
        while attempt < self.retries and rate_limited < 2 * self.retries:
            self._throttle(url)
            try:
                r = (self.session.request(method, url, **kw) if kw.get("stream")
                     else self._bounded(method, url, **kw))
                if r.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"{r.status_code} for {url}", response=r)
                r.raise_for_status()
                return r
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as e:
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and status < 500 and status != 429:
                    raise  # client errors are not retried
                last_exc = e
                if status == 429:
                    rate_limited += 1
                    n = rate_limited
                else:
                    attempt += 1
                    n = attempt
                delay = min(self.backoff * 2 ** (n - 1), 300)
                retry_after = getattr(getattr(e, "response", None), "headers", {}).get("Retry-After")
                if retry_after and str(retry_after).isdigit():
                    delay = max(delay, int(retry_after))
                log.warning("%s (%s); retrying in %ss", "rate limited" if status == 429 else
                            f"attempt {attempt}/{self.retries} failed", e, delay)
                time.sleep(delay)
        raise RuntimeError(f"giving up on {url}") from last_exc

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw) -> Any:
        """GET and decode JSON; a truncated/invalid body (dropped connection) is retried."""
        for attempt in range(1, self.retries + 1):
            r = self.get(url, **kw)
            try:
                return r.json()
            except ValueError as e:
                if attempt == self.retries:
                    raise
                log.warning("invalid JSON from %s (%s); retry %d/%d", url, e, attempt, self.retries)
                time.sleep(self.backoff * attempt)

    def download(self, url: str, dest: Path, force: bool = False, meta: dict | None = None) -> Path:
        """Download to `dest` unless it already exists (cache). Writes `<dest>.manifest.json`."""
        dest = Path(dest)
        manifest = dest.with_name(dest.name + ".manifest.json")
        if dest.exists() and manifest.exists() and not force:
            log.info("cached: %s", dest)
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        log.info("downloading %s -> %s", url, dest)
        r = self.get(url, stream=True)
        h = hashlib.sha256()
        size = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                h.update(chunk)
                size += len(chunk)
        shutil.move(tmp, dest)
        write_manifest(manifest, url=url, sha256=h.hexdigest(), bytes=size, **(meta or {}))
        return dest


def write_manifest(path: Path, **fields: Any) -> None:
    fields.setdefault("retrieved_utc", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    Path(path).write_text(json.dumps(fields, indent=2, default=str), encoding="utf-8")

