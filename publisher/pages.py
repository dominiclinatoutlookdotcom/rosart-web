"""Server-rendered HTML for the publisher: no JavaScript, no external asset (spec 006 T046, T068; FR-020).

``Pages.url`` is the one helper that puts the ``PUBLISHER_BASE_PATH`` prefix on every ``href``, ``src``,
form ``action`` and ``Location`` the service emits; nothing else builds a URL. The single exception is the
link back to the SGS Portal home, ``Pages.portal_home_url()`` = ``/`` (owner request, 2026-11-08): it is a
plain link, never an asset or a form action. Every piece of text from outside (a product title, a person's
name) goes through ``esc``. Times are shown in the display zone (Australia/Melbourne).
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

STATUS_HEADINGS = {
    400: "Bad request", 403: "Not allowed", 404: "Page not found", 405: "Method not allowed",
    409: "Already running", 410: "No longer available", 411: "Length required", 413: "Request too large",
    415: "Unsupported request", 500: "Something went wrong", 503: "Not available",
}

PORTAL_HOME = "/"
SITE_URL = "https://rosart.com.au"

PHASE_WORDS = {
    "history-retry": "recording an earlier publish", "workspace": "preparing the workspace",
    "token": "signing in to Shopify", "theme": "reading the theme", "export": "reading the store content",
    "config": "reading the settings", "build": "building the site", "test": "running the site tests",
    "typecheck": "checking the code types", "check-dist": "checking the built pages",
    "snapshot": "saving the checked site", "preflight": "checking the files",
    "aws-identity": "checking the website access", "marker-read": "checking the live site",
    "dry-run": "working out what will change", "summary": "writing the summary", "verify": "saving the check",
    "marker-write": "recording the publish", "invalidate": "refreshing the cache",
    "wait": "waiting for the cache refresh", "commit": "recording the history",
}

STATE_WORDS = {
    "running": "Running", "passed": "Check passed", "failed": "Failed", "published": "Live",
    "published-cache-pending": "Published (cache refresh running)", "publish-failed-partial": "Publish failed part-way",
    "interrupted": "Interrupted by a restart",
}

PARTLY_UPDATED = "Publish failed: the live site may be partly updated. Press Publish again to finish it"


def esc(text: object) -> str:
    return html.escape("" if text is None else str(text), quote=True)


def phase_in_words(phase: Optional[str]) -> str:
    if not phase:
        return "starting"
    if phase.startswith("pass-"):
        return f"uploading (step {phase[len('pass-'):]} of 6)"
    return PHASE_WORDS.get(phase, phase)


class Pages:
    def __init__(self, base_path: str, tz_display: str = "Australia/Melbourne") -> None:
        self.base_path = base_path
        self._tz = ZoneInfo(tz_display)

    def url(self, path: str) -> str:
        """``BASE_PATH + path``: path-absolute, never a scheme or host (so nothing depends on ``Host``)."""
        if not path.startswith("/"):
            raise ValueError(f"a service URL path must start with /: {path!r}")
        return self.base_path + path

    @staticmethod
    def portal_home_url() -> str:
        """The one URL without the prefix: the SGS Portal home (a link only)."""
        return PORTAL_HOME

    # --- times --------------------------------------------------------------------------

    def local(self, iso: Optional[str]) -> Optional[datetime]:
        return datetime.fromisoformat(iso).astimezone(self._tz) if iso else None

    def hhmm(self, iso: Optional[str]) -> str:
        moment = self.local(iso)
        return moment.strftime("%H:%M") if moment else ""

    def when(self, iso: Optional[str]) -> str:
        moment = self.local(iso)
        return f"{moment.day} {moment:%b %H:%M}" if moment else ""

    # --- building blocks ----------------------------------------------------------------

    def layout(self, title: str, body: str, refresh_s: Optional[int] = None) -> str:
        """The base page. ``body`` is trusted HTML the caller built with ``esc``; ``title`` is escaped here."""
        refresh = f'<meta http-equiv="refresh" content="{int(refresh_s)}">\n' if refresh_s else ""
        return (
            "<!doctype html>\n"
            '<html lang="en-AU">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            f"{refresh}"
            f"<title>{esc(title)} - Rosart publisher</title>\n"
            f'<link rel="stylesheet" href="{esc(self.url("/static/app.css"))}">\n'
            "</head>\n<body>\n<main>\n"
            f"<h1>{esc(title)}</h1>\n{body}\n"
            "</main>\n</body>\n</html>\n"
        )

    def message_page(self, status: int, message: str, heading: Optional[str] = None) -> str:
        return self.layout(heading or STATUS_HEADINGS.get(status, "Error"), f"<p>{esc(message)}</p>")

    def form(self, action_path: str, label: str, token: str, hidden: Optional[Mapping[str, str]] = None) -> str:
        """A POST form with one labelled button and the CSRF field."""
        if not label.strip():
            raise ValueError("every button needs a label")
        fields = {"csrf": token, **(hidden or {})}
        inputs = "".join(f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">' for name, value in fields.items())
        return (f'<form method="post" action="{esc(self.url(action_path))}">{inputs}'
                f'<button type="submit">{esc(label)}</button></form>')

    def link(self, path: str, text: str) -> str:
        return f'<a href="{esc(self.url(path))}">{esc(text)}</a>'

    def portal_link(self) -> str:
        return f'<p><a href="{esc(self.portal_home_url())}">Back to the SGS Portal home</a></p>'

    # --- refusals -----------------------------------------------------------------------

    def busy_page(self, job: Any) -> str:
        who = job.person_name or job.person_user
        body = (f"<p>A {esc(job.kind)} started by {esc(who)} at {esc(self.hhmm(job.started_at))} is running; "
                f"wait for it to finish.</p><p>{self.link(f'/jobs/{job.id}', f'See job {job.id}')}</p>")
        return self.layout("Already running", body)

    def gone_page(self, token: str) -> str:
        body = ("<p>This check can no longer be published (a newer passed check exists, or its 60 minutes have "
                "passed); press Check changes again.</p>" + self.form("/check", "Check changes", token)
                + f"<p>{self.link('/', 'Back to the publisher')}</p>")
        return self.layout("No longer available", body)

    # --- the home page ------------------------------------------------------------------

    def home(self, *, token: str, running: Any, snapshot: Any, pending_label: Optional[str], last_publish: Any,
             recent: list[Any], log_tail: str, last_check: Any = None) -> str:
        parts = []
        if running is not None:
            parts.append(self._running_section(running, log_tail))
        else:
            parts.append("<h2>Check changes</h2><p>Read the changes from Shopify and check the site before "
                         f"publishing.</p>{self.form('/check', 'Check changes', token)}")
        if running is None and last_check is not None and last_check.state == "failed":
            parts.append(self._failed_check_section(last_check, snapshot))
        if snapshot is not None and running is None:
            parts.append(self._publishable_section(snapshot, token, pending_label))
        parts.append(self._last_publish_section(last_publish))
        parts.append(self._jobs_table(recent))
        parts.append(self.portal_link())
        return self.layout("Rosart website publisher", "\n".join(parts), refresh_s=5 if running is not None else None)

    def _running_section(self, job: Any, log_tail: str) -> str:
        who = job.person_name or job.person_user
        word = "Checking" if job.kind == "check" else "Publishing"
        return (f"<h2>{word}…</h2><p>{esc(who)} started this {esc(job.kind)} at {esc(self.hhmm(job.started_at))}; "
                f"now: {esc(phase_in_words(job.phase))}. {self.link(f'/jobs/{job.id}', f'See job {job.id}')}</p>"
                f"<pre>{esc(log_tail)}</pre>")

    def failures_html(self, job: Any) -> str:
        """Each recorded failure as a notice, in the plain words the translator wrote."""
        notices = []
        for failure in json.loads(job.failures_json or "[]"):
            notices.append(f'<p class="notice">{esc(failure.get("text", ""))}</p>')
        return "".join(notices)

    def _failed_check_section(self, job: Any, snapshot: Any) -> str:
        who = job.person_name or job.person_user
        earlier = ""
        if snapshot is not None:
            earlier = (f"<p>Meanwhile, the check from {esc(self.hhmm(snapshot.created_at))} can still be published "
                       f"until {esc(self.hhmm(snapshot.window_end))}.</p>")
        return (f"<h2>The last check failed</h2><p>Check {job.id} by {esc(who)} at {esc(self.when(job.finished_at))} "
                f"found a problem; nothing was published. {self.link(f'/jobs/{job.id}', f'See job {job.id}')}</p>"
                f"{self.failures_html(job)}{earlier}")

    def _publishable_section(self, snapshot: Any, token: str, pending_label: Optional[str]) -> str:
        summary = json.loads(snapshot.summary_json)
        note = (f'<p class="notice">Summary {esc(pending_label)}: it may list changes that are already live.</p>'
                if pending_label else "")
        nothing = ("<p>Nothing new to publish: the site already matches Shopify. You can still publish.</p>"
                   if summary.get("nothing_new") else "")
        return ("<h2>Ready to publish</h2>"
                f"<p>Publish by {esc(self.hhmm(snapshot.window_end))} (Melbourne time); "
                f"content as of {esc(self.hhmm(snapshot.created_at))}.</p>{note}{nothing}"
                f"<pre>{esc(summary.get('text', ''))}</pre>"
                f"{self.form('/publish', 'Publish', token, {'snapshot': snapshot.id})}")

    def _last_publish_section(self, job: Any) -> str:
        if job is None:
            return "<h2>Last publish</h2><p>Nothing has been published from this page yet.</p>"
        who = job.person_name or job.person_user
        return (f"<h2>Last publish</h2><p>{esc(STATE_WORDS.get(job.state, job.state))} by {esc(who)} at "
                f"{esc(self.when(job.finished_at))}. {self.link(f'/jobs/{job.id}', f'See job {job.id}')}</p>")

    def _jobs_table(self, jobs: list[Any]) -> str:
        rows = "".join(
            f"<tr><td>{self.link(f'/jobs/{job.id}', f'#{job.id}')}</td><td>{esc(job.kind)}</td>"
            f"<td>{esc(job.person_name or job.person_user)}</td><td>{esc(self.when(job.started_at))}</td>"
            f"<td>{esc(STATE_WORDS.get(job.state, job.state))}</td></tr>" for job in jobs)
        if not rows:
            return "<h2>Recent jobs</h2><p>No jobs yet.</p>"
        return ("<h2>Recent jobs</h2><table><thead><tr><th>Job</th><th>What</th><th>Who</th><th>Started</th>"
                f"<th>Result</th></tr></thead><tbody>{rows}</tbody></table>")

    # --- the job page -------------------------------------------------------------------

    def job_heading(self, job: Any, snapshot: Any, publishable: bool) -> str:
        """The heading for a job's state, in the words of contracts/http-routes.md."""
        state, kind = job.state, job.kind
        if state == "running":
            return "Checking…" if kind == "check" else "Publishing…"
        if kind == "check":
            if state == "passed":
                if publishable and snapshot is not None:
                    return f"Check passed: ready to publish until {self.hhmm(snapshot.window_end)}"
                return "Check passed"
            if state == "failed":
                return "Check failed: nothing was published"
            return "The check was interrupted by a restart; press Check changes again"
        if state == "published":
            return "Live"
        if state == "published-cache-pending":
            return "Published; the cache refresh is still running"
        if state == "failed":
            return "Publish stopped before anything was uploaded"
        return PARTLY_UPDATED

    def job_page(self, *, job: Any, snapshot: Any, publishable: bool, token: str, log_tail: str) -> str:
        who = job.person_name or job.person_user
        parts = [f"<p>Job {job.id}: {esc(job.kind)} by {esc(who)}, started {esc(self.when(job.started_at))}"
                 + (f", finished {esc(self.when(job.finished_at))}" if job.finished_at else "") + ".</p>"]
        if job.state == "running":
            parts.append(f"<p>Now: {esc(phase_in_words(job.phase))}.</p><pre>{esc(log_tail)}</pre>")
        parts.append(self.failures_html(job))
        summary = json.loads(snapshot.summary_json) if snapshot is not None else None
        if summary and job.kind == "check" and job.state == "passed":
            parts.append(f"<h2>What will change</h2><pre>{esc(summary.get('text', ''))}</pre>")
        if job.state in ("published", "published-cache-pending"):
            parts.append(f"<p>Invalidation ID: <code>{esc(job.invalidation_id)}</code>.</p>")
            if summary:
                urls = "".join(f"<li><code>{esc(SITE_URL + path)}</code></li>" for path in summary.get("spot_check_urls", []))
                parts.append(f"<h2>Pages to look at</h2><ul>{urls}</ul>")
        if publishable and snapshot is not None:
            parts.append(self.form("/publish", "Publish", token, {"snapshot": snapshot.id}))
        parts.append(f"<p>{self.link('/', 'Back to the publisher')}</p>")
        return self.layout(self.job_heading(job, snapshot, publishable), "\n".join(parts),
                           refresh_s=5 if job.state == "running" else None)
