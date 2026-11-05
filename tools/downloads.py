"""Sequential, polite, re-runnable image downloads from the Shopify CDN."""
from __future__ import annotations

import hashlib
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from images import assert_allowed_host

Fetch = Callable[[str], bytes]
Sleep = Callable[[float], None]

PAUSE_SECONDS = 0.5
TIMEOUT_SECONDS = 60
USER_AGENT = "rosart-web-export/1.0 (one-off content export by the store owner)"


class FetchError(Exception):
    """A download failed; ``retryable`` says whether trying again could help."""

    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class DownloadResult:
    records: dict[str, dict] = field(default_factory=dict)   # local name -> {url, sha256, bytes}
    downloaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)     # local name -> error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_current(path: Path, url: str, record: Optional[dict]) -> bool:
    if not record or record.get("url") != url or not path.is_file():
        return False
    return path.stat().st_size == record.get("bytes") and _sha256_file(path) == record.get("sha256")


def _fetch_with_retries(fetch: Fetch, url: str, sleep: Sleep, attempts: int) -> bytes:
    for attempt in range(1, attempts + 1):
        try:
            body = fetch(url)
        except FetchError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            sleep(2 ** (attempt - 1))   # 1 s, 2 s, 4 s ...
            continue
        if not body:
            raise FetchError("empty response body", retryable=False)
        return body
    raise FetchError("no attempts made", retryable=False)


def download_images(plan: dict[str, str], dest: Path, fetch: Fetch, sleep: Sleep,
                    previous: Optional[dict[str, dict]] = None, pause: float = PAUSE_SECONDS,
                    attempts: int = 3) -> DownloadResult:
    """Download ``plan`` (source URL -> local filename) into ``dest``.

    Every URL is checked against the CDN allowlist before anything is fetched. Files whose
    previous record has the same URL and whose bytes on disk still match its sha256 and size are
    skipped. Downloads run one at a time in filename order with ``pause`` seconds between them.
    """
    for url in plan:
        assert_allowed_host(url)
    previous = previous or {}
    dest.mkdir(parents=True, exist_ok=True)
    result = DownloadResult()
    fetched_before = False

    for url, name in sorted(plan.items(), key=lambda item: (item[1].lower(), item[1])):
        path = dest / name
        if _is_current(path, url, previous.get(name)):
            result.records[name] = dict(previous[name])
            result.skipped.append(name)
            continue
        if fetched_before:
            sleep(pause)
        fetched_before = True
        try:
            body = _fetch_with_retries(fetch, url, sleep, attempts)
        except FetchError as exc:
            result.failed[name] = str(exc)
            continue
        partial = path.with_name(path.name + ".part")
        partial.write_bytes(body)
        os.replace(partial, path)
        result.records[name] = {"url": url, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        result.downloaded.append(name)
    return result


class CdnOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only when they stay on the Shopify CDN."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib hook
        assert_allowed_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def make_urllib_fetch(timeout: int = TIMEOUT_SECONDS) -> Fetch:
    """A real fetch over HTTPS that refuses to leave cdn.shopify.com, even through a redirect."""
    opener = urllib.request.build_opener(CdnOnlyRedirectHandler())

    def fetch(url: str) -> bytes:
        assert_allowed_host(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with opener.open(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or exc.code >= 500
            raise FetchError(f"HTTP {exc.code} {exc.reason}", retryable=retryable) from exc
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise FetchError(f"network error: {exc}", retryable=True) from exc

    return fetch


def real_sleep(seconds: float) -> None:
    time.sleep(seconds)
