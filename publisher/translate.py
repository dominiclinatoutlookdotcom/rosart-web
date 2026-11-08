"""The failure translator: plain words for staff, never a stack trace (spec 006 T072; research R-15, FR-006a).

Two entry points turn a failure into the records of ``failures_json`` (``code``, ``text``, ``action`` and, when
known, ``where`` and ``key_line``; contracts/check-result.schema.json):

* ``from_output`` reads the raw output of a failed check step (``npm run build``, ``npm test``,
  ``npm run check:dist``) and recognises the rows of the publish skill's failure table
  (``.claude/skills/publish/SKILL.md:77-91``) with the real message text of the site's checks;
* ``from_exception`` translates an error raised inside a job. The Phase 2 Shopify code reports different problems
  under one code (missing and extra scopes are both ``shopify-write-scope``; size, MD5, unsafe name and body
  problems are all ``theme-checksum``): the schema enum has no codes for them, so they are told apart here by the
  detail of the message.

The raw lines stay in the job log. A line nobody recognises gives "contact the owner" with the scrubbed key line
quoted (at most 500 characters). The wrong email address found on a page is not repeated in the text (the schema
allows only the site's enquiry address there); the page and the staff action are named instead.
"""
from __future__ import annotations

import errno
import re
from typing import Any, Optional

import publish

from publisher.runner import ChildTimeout

KEY_LINE_MAX = 500
MAX_FAILURES = 10
SITE_EMAIL = "info@rosart.com.au"

OWNER = "contact-owner"
FIX = "fix-in-shopify"
AGAIN = "check-again"
PUBLISH_AGAIN = "publish-again"

PARTLY = "the live site may be partly updated"
MIRROR = "a new publish will mirror the site fully"

# The plain words and staff action of every failure code (contracts/check-result.schema.json).
CODE_TEXT: dict[str, tuple[str, str]] = {
    "email-rule": (f"A page shows an email address other than {SITE_EMAIL}: change the email to {SITE_EMAIL} in the "
                   "Shopify admin, then check again", FIX),
    "collection-collision": ("Two collections have titles that make the same web address: rename one of them in "
                             "Shopify, then check again", FIX),
    "broken-reference": ("A page links to something that is not on the site. If something was deleted or renamed in "
                         "Shopify, restore or fix it there and check again; otherwise contact the owner", FIX),
    "theme-fidelity": ("The theme was changed in the Shopify theme editor in a way the website does not support yet: "
                       "contact the owner and say what was changed", OWNER),
    "image-too-large": ("An image is too big for the website: contact the owner", OWNER),
    "shopify-access": ("The website's Shopify access is not working: contact the owner", OWNER),
    "shopify-not-permitted": ("The website's Shopify access is not working: contact the owner (Shopify refused the "
                              "website's app for this store)", OWNER),
    "shopify-write-scope": ("The website's Shopify access has the wrong permissions: contact the owner", OWNER),
    "theme-missing": ("The Shopify theme the website reads was not found in the store: contact the owner", OWNER),
    "theme-not-live": ("The Shopify theme the website reads is no longer the live theme: contact the owner", OWNER),
    "theme-checksum": ("A file of the Shopify theme did not arrive intact, so the check stopped: check again, and "
                       "tell the owner if it keeps happening", AGAIN),
    "egress-refused": ("The publisher refused to contact an address it is not allowed to use: contact the owner",
                       OWNER),
    "aws-before-upload": ("The website's AWS access is not working: contact the owner (nothing was uploaded)", OWNER),
    "aws-identity-mismatch": ("The website's AWS access is not the deploy user it should be: contact the owner "
                              "(nothing was uploaded)", OWNER),
    "new-deletions": ("The website's files changed since the check and a publish would now delete pages the check "
                      "did not show: run Check again (nothing was uploaded)", AGAIN),
    "snapshot-hash": ("The checked files changed after the check, so nothing was uploaded: run Check again", AGAIN),
    "snapshot-stale": ("The checked files are gone: run Check again", AGAIN),
    "site-changed": ("The live site changed since this check: run Check again (nothing was uploaded)", AGAIN),
    "partial-sync": (f"The upload stopped part-way: {PARTLY}. {MIRROR[0].upper() + MIRROR[1:]}", PUBLISH_AGAIN),
    "disk-full": ("The publisher ran out of disk space: contact the owner", OWNER),
    "deadline": ("The job took too long and was stopped; check again, and tell the owner if it keeps happening",
                 AGAIN),
    "interrupted": ("The job was interrupted by a restart: press the button again", AGAIN),
    "worker-died": ("The job stopped unexpectedly and was cleaned up: check again, and tell the owner if it keeps "
                    "happening", AGAIN),
    "unknown": ("The publisher does not recognise this problem: contact the owner", OWNER),
}


def for_code(code: str, **extra: Optional[str]) -> dict[str, Any]:
    """The failure record for a bare code; an unknown code is ``unknown``. ``extra`` adds ``where``/``key_line``."""
    if code not in CODE_TEXT:
        code = "unknown"
    text, action = CODE_TEXT[code]
    failure: dict[str, Any] = {"code": code, "text": text, "action": action}
    failure.update({name: value for name, value in extra.items() if value})
    return failure


def interrupted_failure(kind: str) -> dict[str, Any]:
    """The failure of a job a restart cut short (start-up recovery); the page words the next step by the window."""
    if kind == "check":
        return {"code": "interrupted", "action": AGAIN,
                "text": "The check was interrupted by a restart: press Check changes again"}
    return {"code": "interrupted", "action": PUBLISH_AGAIN,
            "text": f"The publish was interrupted by a restart: {PARTLY}. {MIRROR[0].upper() + MIRROR[1:]}"}


# --- the raw output of a failed check step ------------------------------------------------------------

EMAIL_LINE = re.compile(r"(?:(?P<file>\S+(?: \(page (?P<page>[^)]*)\))?): )?email address (?P<address>\S+) is not "
                        r"the site email (?P<site>\S+)")
COLLECTION_LINE = re.compile(r'Collections "(?P<a>[^"]+)" and "(?P<b>[^"]+)" both map to (?P<path>\S+)')
BROKEN_LINE = re.compile(r"(?:(?P<file>\S+): )?broken reference (?P<target>\S+)")
MISSING_PAGE_LINE = re.compile(r"missing page (?P<page>\S+)")
IMAGE_LINE = re.compile(r"(?P<file>\S+): image is (?P<size>\d+) KB, over the (?P<limit>\d+) KB limit")
THEME_LINES = (
    re.compile(r"theme .*\bis missing on the site"),
    re.compile(r"site renders .*, the theme has no such node here"),
    re.compile(r"no fidelity rule for theme"),
    re.compile(r"is not in ALLOWED_DIFFERENCES"),
    re.compile(r"content outside any theme section"),
    re.compile(r"identical to a file in export/theme/"),
)
STACK_LINE = re.compile(r"^\s*at\s")


def _recognise(line: str) -> Optional[dict[str, Any]]:
    found = EMAIL_LINE.search(line)
    if found:
        where = found.group("page") or found.group("file") or ""
        failure = for_code("email-rule", where=where)
        failure["text"] = (f"{'The page ' + where if found.group('page') else where or 'A page'} shows an email "
                           f"address other than {found.group('site')}: change the email to {found.group('site')} in "
                           "the Shopify admin, then check again")
        return failure
    found = COLLECTION_LINE.search(line)
    if found:
        failure = for_code("collection-collision", where=found.group("path"))
        failure["text"] = (f'The Shopify collections "{found.group("a")}" and "{found.group("b")}" have titles that '
                           f"make the same web address ({found.group('path')}). Rename one of them in Shopify, then "
                           "check again")
        return failure
    found = IMAGE_LINE.search(line)
    if found:
        failure = for_code("image-too-large", where=found.group("file"))
        failure["text"] = (f"The image {found.group('file')} is {found.group('size')} KB, over the "
                           f"{found.group('limit')} KB limit: contact the owner")
        return failure
    found = MISSING_PAGE_LINE.search(line)
    if found:
        return _broken(found.group("page"), f"The page {found.group('page')} is missing from the site")
    found = BROKEN_LINE.search(line)
    if found:
        where = found.group("file") or found.group("target")
        return _broken(where, f"{where} links to {found.group('target')}, which is not on the site"
                       if found.group("file") else f"A link to {found.group('target')} points nowhere")
    if any(pattern.search(line) for pattern in THEME_LINES):
        return for_code("theme-fidelity")
    return None


def _broken(where: str, what: str) -> dict[str, Any]:
    failure = for_code("broken-reference", where=where)
    failure["text"] = (f"{what}. If something was deleted or renamed in Shopify, restore or fix it there and check "
                       "again; otherwise contact the owner")
    return failure


def key_line_of(output: str) -> str:
    """The last meaningful line of a tool's output (not blank, not a stack frame), at most 500 characters."""
    lines = [line.strip() for line in output.splitlines() if line.strip() and not STACK_LINE.match(line)]
    return (lines[-1] if lines else "")[:KEY_LINE_MAX]


def unknown_failure(key_line: str, scrubber: Any) -> dict[str, Any]:
    key_line = scrubber.scrub(key_line)[:KEY_LINE_MAX]
    failure = for_code("unknown")
    failure["text"] = (f"The publisher does not recognise this problem: contact the owner. The key line was: {key_line}"
                       if key_line else "The check stopped without saying why: contact the owner")
    if key_line:
        failure["key_line"] = key_line
    return failure


def from_output(output: str, scrubber: Any) -> list[dict[str, Any]]:
    """The failures in the raw output of a failed check step, one per distinct problem; ``unknown`` when none match."""
    failures: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for line in output.splitlines():
        failure = _recognise(line)
        if failure is None:
            continue
        marker = (failure["code"], failure["text"])
        if marker in seen:
            continue
        seen.add(marker)
        failures.append(failure)
        if len(failures) >= MAX_FAILURES:
            break
    if not failures:
        return [unknown_failure(key_line_of(output), scrubber)]
    for failure in failures:
        failure["text"] = scrubber.scrub(failure["text"])
    return failures


# --- an exception raised inside a job -----------------------------------------------------------------

AWS_PHASES = ("aws-identity", "marker-read", "dry-run")
PARTIAL_PHASES = ("marker-write", "invalidate")


def _theme_checksum(message: str) -> tuple[str, str]:
    lowered = message.lower()
    if "unsafe name" in lowered:
        return ("The Shopify theme has a file with an unsafe name, so the check stopped: contact the owner", OWNER)
    if "md5" in lowered:
        return ("A file of the Shopify theme did not match its checksum, so the check stopped: check again, and "
                "tell the owner if it keeps happening", AGAIN)
    if "invalid base64" in lowered:
        return ("A file of the Shopify theme was unreadable, so the check stopped: check again, and tell the owner "
                "if it keeps happening", AGAIN)
    if "without a body" in lowered:
        return ("A file of the Shopify theme arrived without its contents, so the check stopped: check again, and "
                "tell the owner if it keeps happening", AGAIN)
    if " bytes, shopify says" in lowered:
        return ("A file of the Shopify theme arrived with the wrong size, so the check stopped: check again, and "
                "tell the owner if it keeps happening", AGAIN)
    return CODE_TEXT["theme-checksum"]


def _refined(code: str, message: str) -> tuple[str, str]:
    """The plain words and action for ``code``, using the detail of ``message`` where one code covers several."""
    lowered = message.lower()
    if code == "shopify-write-scope":
        if "write scopes" in lowered:
            return ("The website's Shopify access has more access than it should (it can write to the store): "
                    "contact the owner", OWNER)
        if "missing read scopes" in lowered:
            return ("The website's Shopify access is missing a permission it needs to read the store: contact the "
                    "owner", OWNER)
    if code == "theme-checksum":
        return _theme_checksum(message)
    if code == "shopify-access" and ("not answering" in lowered or "throttling" in lowered):
        return ("Shopify is not answering right now: check again in a few minutes, and tell the owner if it keeps "
                "happening", AGAIN)
    return CODE_TEXT[code]


def code_of(exc: BaseException, phase: str) -> str:
    """The failure code of an exception raised in ``phase`` (``unknown`` when nothing fits)."""
    code = getattr(exc, "code", None)
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return "disk-full"
    if isinstance(exc, ChildTimeout):
        return "deadline"
    if isinstance(exc, publish.PublishError) and phase in AWS_PHASES:
        return "aws-before-upload"
    if isinstance(exc, publish.PublishError) and (phase.startswith("pass-") or phase in PARTIAL_PHASES):
        return "partial-sync"
    return code if code in CODE_TEXT else "unknown"


def from_exception(exc: BaseException, phase: str, scrubber: Any) -> dict[str, Any]:
    """The failure record for an exception raised in ``phase``; its text is plain words, never a stack trace."""
    code = code_of(exc, phase)
    if code == "unknown":
        first = next((line.strip() for line in str(exc).splitlines() if line.strip()), "")
        return unknown_failure(f"{type(exc).__name__}: {first}" if first else type(exc).__name__, scrubber)
    text, action = _refined(code, str(exc))
    return {"code": code, "text": scrubber.scrub(text), "action": action}
