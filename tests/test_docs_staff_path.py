"""The docs name the publish page as the staff path (sgs-server spec 006 T102/T103; R-6, Q-5, Q-6).

After cut-over staff publish from https://int.solagratia.com.au/rosart/ and the `publish` skill is the owner's
fallback (never while a box job runs). README.md, CLAUDE.md and the skill are what a session reads first, so each
must say so; the README also documents the box-side commands, the bucket marker and the image targets.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
CLAUDE = ROOT / "CLAUDE.md"
SKILL = ROOT / ".claude" / "skills" / "publish" / "SKILL.md"
STAFF_URL = "https://int.solagratia.com.au/rosart/"
DOCS = {"README.md": README, "CLAUDE.md": CLAUDE, "SKILL.md": SKILL}


def paragraphs(path: Path) -> list[str]:
    return [" ".join(block.split()) for block in re.split(r"\n\s*\n", path.read_text(encoding="utf-8"))]


@pytest.mark.parametrize("name", sorted(DOCS))
def test_every_doc_names_the_publish_page_as_the_staff_path(name):
    text = DOCS[name].read_text(encoding="utf-8")
    assert STAFF_URL in text, f"{name} does not name {STAFF_URL}"
    staff = [p for p in paragraphs(DOCS[name]) if STAFF_URL in p]
    assert any("staff" in p.lower() for p in staff), f"{name}: the paragraph naming the page does not mention staff"


@pytest.mark.parametrize("name", sorted(DOCS))
def test_every_doc_says_the_skill_is_the_owners_fallback(name):
    fallback = [p for p in paragraphs(DOCS[name]) if "fallback" in p.lower() and "owner" in p.lower()
                and "skill" in p.lower()]
    assert fallback, f"{name} does not say the publish skill is the owner's fallback"
    assert any("box" in p.lower() and ("never" in p.lower() or "not while" in p.lower()) for p in fallback), \
        f"{name}: the fallback paragraph must say never while a box job runs"


def test_the_docs_no_longer_tell_staff_to_publish_with_the_skill():
    stale = ("Staff publish from Claude Code with the project skill", "Staff publish with the project skill")
    for name, path in DOCS.items():
        text = " ".join(path.read_text(encoding="utf-8").split())
        for phrase in stale:
            assert phrase not in text, f"{name} still says: {phrase}"


def test_the_skill_sends_a_staff_member_to_the_page_before_anything_else():
    body = SKILL.read_text(encoding="utf-8")
    after_title = body.split("# Publish the Rosart website", 1)[1]
    first_step = after_title.index("## Step 1")
    assert STAFF_URL in after_title[:first_step], "the page must be named before Step 1 of the skill"


def test_the_readme_documents_the_box_commands_the_marker_and_the_image_targets():
    text = README.read_text(encoding="utf-8")
    for needed in ("--transport admin-api", "_publish.json", "make image", "make image-test"):
        assert needed in text, f"README.md does not mention {needed}"
    assert re.search(r"make image VERSION=v\d+\.\d+\.\d+", text)
