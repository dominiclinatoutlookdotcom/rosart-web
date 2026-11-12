"""The failure translator (spec 006 T071; research R-15, FR-006a).

Seam: ``publisher.translate`` (``from_output`` for the raw lines of a failed check step, ``from_exception`` for a
raised error, ``for_code`` for a bare code). One case per row of the publish skill's failure table
(``.claude/skills/publish/SKILL.md:77-91``) with the real message text the site's checks print.
"""
import pytest

from publisher import steps
from shopify_admin import ShopifyAdminError

# The enum of contracts/check-result.schema.json (failures[].code) and of failures[].action.
SCHEMA_CODES = {
    "email-rule", "collection-collision", "broken-reference", "theme-fidelity", "image-too-large", "shopify-access",
    "shopify-not-permitted", "shopify-write-scope", "theme-missing", "theme-not-live", "theme-checksum",
    "egress-refused", "aws-before-upload", "aws-identity-mismatch", "new-deletions", "snapshot-hash",
    "snapshot-stale", "site-changed", "partial-sync", "disk-full", "deadline", "interrupted", "worker-died", "unknown"}
ACTIONS = {"fix-in-shopify", "contact-owner", "check-again", "publish-again"}


@pytest.fixture()
def scrubber():
    from publisher.scrub import Scrubber

    scrubber = Scrubber()
    scrubber.register("shpat_planted_token_0123456789")
    return scrubber


def one(output, scrubber):
    from publisher.translate import from_output

    failures = from_output(output, scrubber)
    assert len(failures) == 1, failures
    return failures[0]


# --- one case per row of the skill's failure table ---------------------------------------------------

def test_another_email_address_on_a_page_names_the_page_and_the_shopify_fix(scrubber):
    output = ("dist check failed with 1 problem(s):\n"
              "  - contact/index.html (page /contact/): email address info@deniva.com.au is not the site email "
              "info@rosart.com.au\n")
    failure = one(output, scrubber)
    assert failure["code"] == "email-rule" and failure["action"] == "fix-in-shopify"
    assert failure["where"] == "/contact/"
    assert "/contact/" in failure["text"]
    assert "change the email to info@rosart.com.au in the Shopify admin" in failure["text"]


def test_an_email_address_in_a_file_that_is_not_a_page_still_names_the_file(scrubber):
    output = "  - robots.txt: email address admin@other.com.au is not the site email info@rosart.com.au\n"
    failure = one(output, scrubber)
    assert failure["code"] == "email-rule" and failure["where"] == "robots.txt"
    assert "robots.txt" in failure["text"]


def test_two_collections_with_one_web_address_say_to_rename_one(scrubber):
    output = ('Error: Collections "face-care" and "face_care" both map to /collections/face-care/\n'
              "    at slugFor (file:///work/src/lib/urls.ts:66:13)\n    at build (file:///work/src/pages.ts:9:1)\n")
    failure = one(output, scrubber)
    assert failure["code"] == "collection-collision" and failure["action"] == "fix-in-shopify"
    assert '"face-care"' in failure["text"] and '"face_care"' in failure["text"]
    assert "Rename one" in failure["text"]


@pytest.mark.parametrize("line, where", [
    ("  - products/a/index.html: broken reference /nope/", "products/a/index.html"),
    ("  - _astro/site.css: broken reference /_astro/nofont.woff2", "_astro/site.css"),
    ("  - missing page /collections/face-care/ (expected collections/face-care/index.html)",
     "/collections/face-care/"),
])
def test_a_broken_reference_or_missing_page_is_named_with_the_shopify_fix_or_the_owner(line, where, scrubber):
    failure = one(line + "\n", scrubber)
    assert failure["code"] == "broken-reference" and failure["action"] == "fix-in-shopify"
    assert failure["where"] == where and where in failure["text"]
    assert "restore" in failure["text"] and "contact the owner" in failure["text"]


@pytest.mark.parametrize("line", [
    "theme s2 [section] is missing on the site",
    "site renders featured [product-list] after s1, the theme has no such node here",
    "no fidelity rule for theme section type new-banner (s9)",
    'data-allowed="made-up" is not in ALLOWED_DIFFERENCES',
    'content outside any theme section: text "Extra words"',
])
def test_an_unsupported_theme_change_says_contact_the_owner(line, scrubber):
    failure = one(f" FAIL  src/lib/fidelity.test.ts > theme fidelity\nAssertionError: {line}\n", scrubber)
    assert failure["code"] == "theme-fidelity" and failure["action"] == "contact-owner"
    assert "theme editor" in failure["text"] and "contact the owner" in failure["text"]


def test_an_oversized_image_names_the_image_and_says_contact_the_owner(scrubber):
    failure = one("  - _astro/huge.png: image is 700 KB, over the 600 KB limit\n", scrubber)
    assert failure["code"] == "image-too-large" and failure["action"] == "contact-owner"
    assert failure["where"] == "_astro/huge.png" and "_astro/huge.png" in failure["text"]


def test_anything_else_quotes_the_key_line_and_says_contact_the_owner(scrubber):
    output = ("> astro build\nSomething odd happened in the renderer\n"
              "TypeError: cannot read properties of undefined (reading 'title')\n"
              "    at render (file:///work/src/pages/index.astro:12:5)\n"
              "    at async build (file:///work/node_modules/astro/dist/core.js:88:3)\n")
    failure = one(output, scrubber)
    assert failure["code"] == "unknown" and failure["action"] == "contact-owner"
    assert "contact the owner" in failure["text"]
    assert failure["key_line"] == "TypeError: cannot read properties of undefined (reading 'title')"
    assert "at render" not in failure["text"] and "node_modules" not in failure["text"], "no stack trace"


def test_the_key_line_is_scrubbed_and_cut_to_500_characters(scrubber):
    output = "Error: " + "x" * 800 + " shpat_planted_token_0123456789\n"
    failure = one(output, scrubber)
    assert len(failure["key_line"]) <= 500 and "shpat_planted" not in failure["key_line"]
    assert "shpat_planted" not in failure["text"]


def test_empty_output_still_gives_contact_the_owner(scrubber):
    failure = one("", scrubber)
    assert failure["code"] == "unknown" and failure["action"] == "contact-owner"


def test_several_problems_give_one_failure_each_without_repeats(scrubber):
    output = ("  - a/index.html (page /a/): email address x@other.com.au is not the site email info@rosart.com.au\n"
              "  - b/index.html (page /b/): email address y@other.com.au is not the site email info@rosart.com.au\n"
              "  - a/index.html (page /a/): email address x@other.com.au is not the site email info@rosart.com.au\n"
              "  - _astro/huge.png: image is 700 KB, over the 600 KB limit\n")
    from publisher.translate import from_output

    failures = from_output(output, scrubber)
    assert [f["code"] for f in failures] == ["email-rule", "email-rule", "image-too-large"]
    assert [f.get("where") for f in failures] == ["/a/", "/b/", "_astro/huge.png"]


# --- every schema code has plain words and an action --------------------------------------------------

def test_the_codes_the_steps_know_are_exactly_the_schema_codes():
    assert steps.FAILURE_CODES == SCHEMA_CODES


@pytest.mark.parametrize("code", sorted(SCHEMA_CODES))
def test_every_schema_code_maps_to_plain_text_and_an_action(code):
    from publisher.translate import for_code

    failure = for_code(code)
    assert failure["code"] == code and failure["action"] in ACTIONS
    assert failure["text"].strip() and failure["text"][0].isupper()
    assert "Traceback" not in failure["text"] and "Error:" not in failure["text"]


def test_an_unknown_code_is_unknown_and_contact_the_owner():
    from publisher.translate import for_code

    failure = for_code("not-a-code")
    assert failure["code"] == "unknown" and failure["action"] == "contact-owner"


# --- exceptions: the shared Phase 2 codes get distinct plain words -------------------------------------

def from_exc(exc, phase, scrubber):
    from publisher.translate import from_exception

    return from_exception(exc, phase, scrubber)


def test_missing_and_extra_shopify_scopes_read_differently(scrubber):
    from shopify_admin import check_scopes

    with pytest.raises(ShopifyAdminError) as extra:
        check_scopes(["read_products", "read_content", "read_online_store_navigation", "read_files", "read_themes",
                      "read_metaobjects", "write_products"])
    with pytest.raises(ShopifyAdminError) as missing:
        check_scopes(["read_products"])
    a = from_exc(extra.value, "token", scrubber)
    b = from_exc(missing.value, "token", scrubber)
    assert a["code"] == b["code"] == "shopify-write-scope" and a["action"] == b["action"] == "contact-owner"
    assert a["text"] != b["text"]
    assert "more access" in a["text"] and "write_products" not in a["text"]
    assert "missing" in b["text"]


@pytest.mark.parametrize("message, needle, action", [
    ("the theme has a file with an unsafe name: '../x'", "unsafe", "contact-owner"),
    ("theme file a.json has an invalid Base64 body", "unreadable", "check-again"),
    ("theme file a.json came back without a body", "without", "check-again"),
])
def test_theme_download_problems_read_differently(message, needle, action, scrubber):
    failure = from_exc(ShopifyAdminError("theme-checksum", message), "theme", scrubber)
    assert failure["code"] == "theme-checksum" and failure["action"] == action
    assert needle in failure["text"]


def test_the_theme_checksum_texts_are_all_different(scrubber):
    texts = {from_exc(ShopifyAdminError("theme-checksum", m), "theme", scrubber)["text"] for m in (
        "theme file x has an invalid Base64 body", "theme file x came back without a body",
        "the theme has a file with an unsafe name: 'x'")}
    assert len(texts) == 3


def test_shopify_refusals_say_the_access_is_not_working_and_contact_the_owner(scrubber):
    for code in ("shopify-access", "shopify-not-permitted"):
        failure = from_exc(ShopifyAdminError(code, "Shopify refused the website's access (HTTP 401 x)"), "token",
                           scrubber)
        assert failure["code"] == code and failure["action"] == "contact-owner"
        assert "the website's shopify access is not working: contact the owner" in failure["text"].lower()


def test_shopify_not_answering_is_check_again(scrubber):
    failure = from_exc(ShopifyAdminError("shopify-access", "Shopify is not answering: try again later"), "export",
                       scrubber)
    assert failure["action"] == "check-again" and "not answering" in failure["text"]


def test_an_aws_refusal_before_upload_says_nothing_was_uploaded(scrubber):
    import publish

    exc = publish.PublishError("aws sts get-caller-identity failed (exit 254): InvalidClientTokenId")
    failure = from_exc(exc, "aws-identity", scrubber)
    assert failure["code"] == "aws-before-upload" and failure["action"] == "contact-owner"
    assert "nothing was uploaded" in failure["text"] and "AWS access" in failure["text"]


def test_a_failed_pass_says_the_live_site_may_be_partly_updated_and_a_new_publish_mirrors_it(scrubber):
    import publish

    exc = publish.PublishError("aws s3 sync failed (exit 254): InvalidClientTokenId\nsync 3/6 failed")
    failure = from_exc(exc, "pass-3", scrubber)
    assert failure["code"] == "partial-sync" and failure["action"] == "publish-again"
    assert "may be partly updated" in failure["text"] and "new publish will mirror the site fully" in failure["text"]


def test_an_exception_text_is_scrubbed_and_never_a_stack_trace(scrubber):
    failure = from_exc(RuntimeError("boom shpat_planted_token_0123456789\nTraceback (most recent call last)"),
                       "export", scrubber)
    assert failure["code"] == "unknown" and failure["action"] == "contact-owner"
    assert "shpat_planted" not in repr(failure) and "Traceback" not in failure["text"]
    assert failure["key_line"].startswith("RuntimeError: boom")
