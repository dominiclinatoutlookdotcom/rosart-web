"""Secret scrubbing for everything the service writes (spec 006 T040; FR-016).

``Scrubber`` replaces every registered secret value with ``[redacted]`` in text, bytes, exception
descriptions and streams (a value split by a line break or across chunks is still caught).
``JobLog`` is the only way a job's log file is written, so nothing reaches it unscrubbed.
"""
from __future__ import annotations

import logging
import re
import threading
import traceback
from pathlib import Path
from typing import Union

from shopify_admin import Secret

REDACTED = "[redacted]"
MIN_LENGTH = 4          # shorter values would redact ordinary text and are never real credentials


class Scrubber:
    def __init__(self) -> None:
        self._values: set[str] = set()
        self._str_pattern: re.Pattern | None = None
        self._bytes_pattern: re.Pattern | None = None
        self._longest = 0
        self._lock = threading.Lock()

    def register(self, secret: Union[Secret, str]) -> None:
        value = secret.reveal() if isinstance(secret, Secret) else str(secret)
        if len(value) < MIN_LENGTH:
            return
        with self._lock:
            self._values.add(value)
            ordered = sorted(self._values, key=len, reverse=True)
            self._longest = len(ordered[0])
            self._str_pattern = re.compile("|".join(self._regex(v) for v in ordered))
            self._bytes_pattern = re.compile(b"|".join(self._regex(v).encode("utf-8") for v in ordered))

    @staticmethod
    def _regex(value: str) -> str:
        # A line break may have been inserted anywhere inside the value.
        return r"(?:\r?\n)?".join(re.escape(ch) for ch in value)

    def scrub(self, text: str) -> str:
        pattern = self._str_pattern
        return pattern.sub(REDACTED, text) if pattern else text

    def scrub_bytes(self, data: bytes) -> bytes:
        pattern = self._bytes_pattern
        return pattern.sub(REDACTED.encode("ascii"), data) if pattern else data

    def describe_exception(self, exc: BaseException) -> str:
        """The exception type and message, scrubbed."""
        return self.scrub(f"{type(exc).__name__}: {exc}")

    def format_traceback(self, exc: BaseException) -> str:
        return self.scrub("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))

    def stream(self) -> "StreamScrubber":
        return StreamScrubber(self)

    @property
    def holdback(self) -> int:
        """How much of a stream's tail may still be the start of a value (the value, plus its line breaks)."""
        return self._longest * 3


class StreamScrubber:
    """Scrubs text that arrives in chunks: keeps a short tail until more arrives or ``flush``."""

    def __init__(self, scrubber: Scrubber) -> None:
        self._scrubber = scrubber
        self._pending = ""

    def feed(self, chunk: str) -> str:
        self._pending = self._scrubber.scrub(self._pending + chunk)
        keep = self._scrubber.holdback
        if keep == 0:
            out, self._pending = self._pending, ""
            return out
        if len(self._pending) <= keep:
            return ""
        out, self._pending = self._pending[:-keep], self._pending[-keep:]
        return out

    def flush(self) -> str:
        out, self._pending = self._scrubber.scrub(self._pending), ""
        return out


class JobLog:
    """A job's log file; every write is scrubbed first."""

    def __init__(self, path: Path, scrubber: Scrubber) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._scrubber = scrubber
        self._handle = open(self.path, "a", encoding="utf-8", newline="\n")
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        with self._lock:
            self._handle.write(self._scrubber.scrub(text))
            self._handle.flush()

    def line(self, text: str) -> None:
        self.write(text.rstrip("\n") + "\n")

    def exception(self, message: str, exc: BaseException) -> None:
        self.line(f"{message}: {self._scrubber.describe_exception(exc)}")

    def logging_handler(self) -> logging.Handler:
        return _JobLogHandler(self, self._scrubber)

    def close(self) -> None:
        with self._lock:
            self._handle.close()


class _JobLogHandler(logging.Handler):
    def __init__(self, log: JobLog, scrubber: Scrubber) -> None:
        super().__init__()
        self._log = log
        self._scrubber = scrubber
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._log.line(self.format(record))     # includes the traceback; JobLog scrubs it
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)
