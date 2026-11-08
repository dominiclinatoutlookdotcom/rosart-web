"""Secret scrubbing of logs, exception text and child output (spec 006 T039; FR-016).

Seam: ``publisher.scrub.Scrubber`` (str, bytes, exceptions, streams) and ``JobLog``, the only way
the service writes a job's log file.
"""
import logging

import pytest

from shopify_admin import Secret

TOKEN = "shpat_planted_token_0123456789"
AWS = "AKIAPLANTED0000000000"


@pytest.fixture()
def scrubber():
    from publisher.scrub import Scrubber

    scrubber = Scrubber()
    scrubber.register(Secret(TOKEN))
    scrubber.register(AWS)
    return scrubber


def test_registered_values_are_replaced_and_other_text_is_untouched(scrubber):
    text = f"token={TOKEN} key={AWS} again {TOKEN}; nothing else changes"

    assert scrubber.scrub(text) == "token=[redacted] key=[redacted] again [redacted]; nothing else changes"
    assert scrubber.scrub("no secrets here") == "no secrets here"


def test_a_value_split_across_a_line_break_is_still_replaced(scrubber):
    assert TOKEN[:10] not in scrubber.scrub(f"out: {TOKEN[:10]}\n{TOKEN[10:]} done")
    assert TOKEN[:10] not in scrubber.scrub(f"out: {TOKEN[:10]}\r\n{TOKEN[10:]} done")
    assert scrubber.scrub(f"x {TOKEN[:10]}\n{TOKEN[10:]} y") == "x [redacted] y"


def test_bytes_are_scrubbed_the_same_way(scrubber):
    data = f"out {TOKEN} and {TOKEN[:5]}\n{TOKEN[5:]}".encode("utf-8") + b"\xff\xfe"

    cleaned = scrubber.scrub_bytes(data)

    assert isinstance(cleaned, bytes)
    assert TOKEN.encode() not in cleaned and TOKEN[:5].encode() + b"\n" not in cleaned
    assert cleaned == b"out [redacted] and [redacted]\xff\xfe"


def test_exception_text_is_scrubbed(scrubber):
    try:
        raise RuntimeError(f"upstream said no for {TOKEN}")
    except RuntimeError as exc:
        text = scrubber.describe_exception(exc)

    assert TOKEN not in text and "RuntimeError" in text and "[redacted]" in text


def test_a_value_registered_later_is_scrubbed_from_then_on(scrubber):
    late = "late-secret-value-123"
    assert scrubber.scrub(late) == late

    scrubber.register(late)

    assert scrubber.scrub(late) == "[redacted]"


def test_empty_and_very_short_values_are_never_registered(scrubber):
    scrubber.register("")
    scrubber.register("ab")

    assert scrubber.scrub("a b ab cab") == "a b ab cab"


def test_a_stream_never_lets_a_value_through_however_it_is_chunked(scrubber):
    text = f"line one\nuse {TOKEN} now\nthen {AWS}\nend\n"
    for size in (1, 2, 3, 7, 50):
        stream = scrubber.stream()
        out = "".join(stream.feed(text[i:i + size]) for i in range(0, len(text), size)) + stream.flush()

        assert TOKEN not in out and AWS not in out
        assert out == "line one\nuse [redacted] now\nthen [redacted]\nend\n"


def test_a_stream_holds_back_only_a_short_tail_so_output_keeps_flowing(scrubber):
    stream = scrubber.stream()
    out = stream.feed("x" * 500)

    assert len(out) > 400
    assert out + stream.flush() == "x" * 500


def test_the_job_log_writes_only_scrubbed_text(scrubber, tmp_path):
    from publisher.scrub import JobLog

    path = tmp_path / "logs" / "rosart" / "7.log"
    log = JobLog(path, scrubber)
    log.line(f"calling with {TOKEN}")
    log.write(f"child output {AWS[:6]}\n{AWS[6:]}\n")
    try:
        raise ValueError(f"bad {TOKEN}")
    except ValueError as exc:
        log.exception("step failed", exc)
    log.close()

    content = path.read_bytes().decode("utf-8")
    assert TOKEN not in content and AWS not in content and AWS[:6] + "\n" not in content
    assert "calling with [redacted]" in content and "step failed" in content and "ValueError" in content


def test_the_job_log_handler_scrubs_standard_logging_records(scrubber, tmp_path):
    from publisher.scrub import JobLog

    log = JobLog(tmp_path / "1.log", scrubber)
    logger = logging.getLogger("rosart.test_scrub")
    logger.setLevel(logging.INFO)
    handler = log.logging_handler()
    logger.addHandler(handler)
    try:
        logger.info("token %s used", TOKEN)
        try:
            raise KeyError(TOKEN)
        except KeyError:
            logger.exception("lookup failed for %s", AWS)
    finally:
        logger.removeHandler(handler)
        log.close()

    content = (tmp_path / "1.log").read_text(encoding="utf-8")
    assert TOKEN not in content and AWS not in content
    assert "token [redacted] used" in content and "Traceback" in content
