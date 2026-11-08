"""Server-rendered HTML for the publisher: no JavaScript, no external asset (spec 006 T046; FR-020).

``Pages.url`` is the one helper that puts the ``PUBLISHER_BASE_PATH`` prefix on every ``href``, ``src``,
form ``action`` and ``Location`` the service emits; nothing else builds a URL. Every piece of text from
outside (a product title, a person's name) goes through ``esc``.
"""
from __future__ import annotations

import html
from typing import Mapping, Optional

STATUS_HEADINGS = {
    400: "Bad request", 403: "Not allowed", 404: "Page not found", 405: "Method not allowed",
    409: "Already running", 410: "No longer available", 411: "Length required", 413: "Request too large",
    415: "Unsupported request", 500: "Something went wrong", 503: "Not available",
}


def esc(text: object) -> str:
    return html.escape("" if text is None else str(text), quote=True)


class Pages:
    def __init__(self, base_path: str) -> None:
        self.base_path = base_path

    def url(self, path: str) -> str:
        """``BASE_PATH + path``: path-absolute, never a scheme or host (so nothing depends on ``Host``)."""
        if not path.startswith("/"):
            raise ValueError(f"a service URL path must start with /: {path!r}")
        return self.base_path + path

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
