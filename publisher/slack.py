"""Slack ``#system`` notices (spec 006 T066; contracts/slack-messages.md, FR-013).

One line per published, failed, partial or interrupted job; a check that passes posts nothing (A-11).
The line is built from the job row only, with no email address and no URL query string. It goes to
``chat.postMessage`` for ``SLACK_SYSTEM_CHANNEL_ID`` with the bot token in the ``Authorization`` header and
nowhere else: one attempt plus one retry after 5 s, 10 s timeout each, on a background thread after the job's
final state is written. The outcome goes to ``notice_state`` and the job log; a failed post never changes a
job's state (spec US5 scenario 4). The opener is the service's egress opener (``slack.com`` only).
"""
from __future__ import annotations

import json
import logging
import re
import threading
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

log = logging.getLogger("rosart.slack")

POST_URL = "https://slack.com/api/chat.postMessage"
PREFIX = "[rosart-publisher] on fbsrv: "
TIMEOUT_S = 10
RETRY_AFTER_S = 5
MAX_PART = 200
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL_QUERY = re.compile(r"(https?://[^\s?]+)\?\S*")


def _safe(text: str) -> str:
    """No email address and no URL query string in anything that leaves for Slack."""
    return URL_QUERY.sub(r"\1", EMAIL.sub("[email address]", text or ""))[:MAX_PART]


class SlackNotifier:
    def __init__(self, config: Any, store: Any, opener: Any, sleep: Callable[[float], None],
                 scrubber: Optional[Any] = None) -> None:
        self._config = config
        self._store = store
        self._opener = opener
        self._sleep = sleep
        self._scrubber = scrubber

    # --- the text -----------------------------------------------------------------------

    def message(self, job: Any) -> Optional[str]:
        """The one-line notice for a finished job, or None when nothing is posted."""
        name = _safe(job.person_name or job.person_user)
        state = job.state
        reason = _safe(job.reason_text or "")
        if state == "published":
            counts = self._counts(job)
            text = (f"job {job.id} published by {name}: {counts['uploaded']} uploaded, {counts['deleted']} deleted, "
                    f"{_safe(counts['changes'])}; invalidation {job.invalidation_id}")
            if job.export_commit_error:
                text += "; history commit FAILED, owner to fix"
        elif state == "published-cache-pending":
            counts = self._counts(job)
            text = (f"job {job.id} published by {name} (cache refresh still running, invalidation "
                    f"{job.invalidation_id}): {counts['uploaded']} uploaded, {counts['deleted']} deleted")
            if job.export_commit_error:
                text += "; history commit FAILED, owner to fix"
        elif state == "failed" and job.kind == "check":
            text = f"job {job.id} check failed for {name}: {reason}"
        elif state == "failed":
            text = f"job {job.id} publish by {name} stopped before upload: {reason}"
        elif state == "publish-failed-partial":
            text = (f"job {job.id} publish by {name} FAILED part-way: the live site may be partly updated; "
                    f"the next Publish mirrors it fully. {reason}")
        elif state == "interrupted":
            text = f"job {job.id} ({job.kind} by {name}) was interrupted by a restart"
            if job.kind == "publish":
                text += "; the live site may be partly updated"
        else:
            return None
        return PREFIX + text

    @staticmethod
    def _counts(job: Any) -> dict[str, Any]:
        try:
            data = json.loads(job.summary_json or "{}")
        except ValueError:
            data = {}
        return {"uploaded": data.get("uploaded", 0), "deleted": data.get("deleted", 0),
                "changes": data.get("changes") or "no content changes"}

    # --- posting ------------------------------------------------------------------------

    def notify(self, job_id: int, log: Optional[Callable[[str], None]] = None) -> str:
        """Post the notice for a finished job once; returns the ``notice_state`` it ends in."""
        say = log or (lambda text: logging.getLogger("rosart.slack").info("%s", text))
        job = self._store.get(job_id)
        if job is None or job.state == "running":
            return "not-required"
        text = self.message(job)
        if text is None:
            return "not-required"
        if job.notice_state != "pending":
            return job.notice_state
        state = "posted" if self._post(text, say) else "failed"
        self._store.update(job_id, notice_state=state)
        return state

    def notify_async(self, job_id: int, log: Optional[Callable[[str], None]] = None) -> threading.Thread:
        thread = threading.Thread(target=self.notify, args=(job_id, log), name=f"slack-{job_id}", daemon=True)
        thread.start()
        return thread

    def _post(self, text: str, say: Callable[[str], None]) -> bool:
        token = self._config.slack_bot_token
        channel = self._config.slack_channel_id
        if token is None or not channel:
            say("the Slack notice could not be posted: no Slack token or channel is configured")
            return False
        for attempt in (1, 2):
            error = self._attempt(token.reveal(), channel, text)
            if error is None:
                say("the Slack notice was posted")
                return True
            say(f"the Slack notice could not be posted (attempt {attempt}): {self._clean(error)}")
            if attempt == 1:
                self._sleep(RETRY_AFTER_S)
        return False

    def _attempt(self, token: str, channel: str, text: str) -> Optional[str]:
        request = urllib.request.Request(
            POST_URL, data=json.dumps({"channel": channel, "text": text}).encode("utf-8"), method="POST",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"})
        try:
            with self._opener.open(request, timeout=TIMEOUT_S) as reply:
                body = json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return f"HTTP {exc.code}"
        except (OSError, ValueError) as exc:
            return type(exc).__name__
        if isinstance(body, dict) and body.get("ok") is True:
            return None
        return str(body.get("error") or "not ok") if isinstance(body, dict) else "unreadable answer"

    def _clean(self, text: str) -> str:
        return self._scrubber.scrub(text) if self._scrubber else text
