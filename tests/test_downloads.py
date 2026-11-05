"""Tests for the image downloader with a fake fetch: no network is ever used."""
import hashlib

import pytest

from downloads import CdnOnlyRedirectHandler, FetchError, download_images
from images import DisallowedHost

URL_A = "https://cdn.shopify.com/s/files/1/x/files/a.png?v=1"
URL_B = "https://cdn.shopify.com/s/files/1/x/files/b.jpg?v=1"


class FakeFetch:
    def __init__(self, bodies, failures=None):
        self.bodies = bodies
        self.failures = dict(failures or {})   # url -> list of exceptions to raise first
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        pending = self.failures.get(url)
        if pending:
            raise pending.pop(0)
        return self.bodies[url]


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def test_downloads_each_file_and_records_hash_and_size(tmp_path):
    fetch, sleep = FakeFetch({URL_A: b"aaa", URL_B: b"bbbb"}), FakeSleep()
    result = download_images({URL_A: "a.png", URL_B: "b.jpg"}, tmp_path, fetch, sleep)
    assert (tmp_path / "a.png").read_bytes() == b"aaa"
    assert result.records["b.jpg"] == {"url": URL_B, "sha256": sha(b"bbbb"), "bytes": 4}
    assert result.downloaded == ["a.png", "b.jpg"] and result.skipped == [] and result.failed == {}


def test_pauses_half_a_second_between_downloads_in_sorted_order(tmp_path):
    fetch, sleep = FakeFetch({URL_A: b"a", URL_B: b"b"}), FakeSleep()
    download_images({URL_B: "b.jpg", URL_A: "a.png"}, tmp_path, fetch, sleep)
    assert fetch.calls == [URL_A, URL_B]
    assert sleep.calls == [0.5]


def test_rerun_skips_files_already_present_with_the_same_hash_and_size(tmp_path):
    first = download_images({URL_A: "a.png"}, tmp_path, FakeFetch({URL_A: b"aaa"}), FakeSleep())
    fetch = FakeFetch({URL_A: b"aaa"})
    second = download_images({URL_A: "a.png"}, tmp_path, fetch, FakeSleep(), previous=first.records)
    assert fetch.calls == []
    assert second.skipped == ["a.png"]
    assert second.records == first.records


def test_rerun_downloads_again_when_the_file_on_disk_was_changed(tmp_path):
    first = download_images({URL_A: "a.png"}, tmp_path, FakeFetch({URL_A: b"aaa"}), FakeSleep())
    (tmp_path / "a.png").write_bytes(b"tampered")
    fetch = FakeFetch({URL_A: b"aaa"})
    download_images({URL_A: "a.png"}, tmp_path, fetch, FakeSleep(), previous=first.records)
    assert fetch.calls == [URL_A]
    assert (tmp_path / "a.png").read_bytes() == b"aaa"


def test_rerun_downloads_again_when_the_source_url_changed(tmp_path):
    first = download_images({URL_A: "a.png"}, tmp_path, FakeFetch({URL_A: b"aaa"}), FakeSleep())
    new_url = URL_A.replace("v=1", "v=2")
    fetch = FakeFetch({new_url: b"new"})
    result = download_images({new_url: "a.png"}, tmp_path, fetch, FakeSleep(), previous=first.records)
    assert fetch.calls == [new_url]
    assert result.records["a.png"]["sha256"] == sha(b"new")


def test_retries_a_retryable_failure_then_succeeds(tmp_path):
    fetch = FakeFetch({URL_A: b"aaa"}, failures={URL_A: [FetchError("503", retryable=True)]})
    result = download_images({URL_A: "a.png"}, tmp_path, fetch, FakeSleep(), attempts=3)
    assert fetch.calls == [URL_A, URL_A]
    assert result.downloaded == ["a.png"]


def test_gives_up_after_the_attempt_limit_and_reports_the_failure(tmp_path):
    errors = [FetchError("timeout", retryable=True) for _ in range(3)]
    fetch = FakeFetch({URL_B: b"b"}, failures={URL_A: errors})
    result = download_images({URL_A: "a.png", URL_B: "b.jpg"}, tmp_path, fetch, FakeSleep(), attempts=3)
    assert fetch.calls.count(URL_A) == 3
    assert "timeout" in result.failed["a.png"]
    assert result.downloaded == ["b.jpg"]
    assert not (tmp_path / "a.png").exists()


def test_does_not_retry_a_permanent_failure(tmp_path):
    fetch = FakeFetch({}, failures={URL_A: [FetchError("404 Not Found", retryable=False)]})
    result = download_images({URL_A: "a.png"}, tmp_path, fetch, FakeSleep(), attempts=3)
    assert fetch.calls == [URL_A]
    assert "404" in result.failed["a.png"]


def test_refuses_a_non_cdn_url_before_fetching_anything(tmp_path):
    fetch = FakeFetch({})
    with pytest.raises(DisallowedHost):
        download_images({"https://evil.example/a.png": "a.png"}, tmp_path, fetch, FakeSleep())
    assert fetch.calls == []


def test_refuses_an_empty_body(tmp_path):
    result = download_images({URL_A: "a.png"}, tmp_path, FakeFetch({URL_A: b""}), FakeSleep(), attempts=1)
    assert "empty" in result.failed["a.png"]


def test_redirect_handler_refuses_a_redirect_off_the_cdn():
    handler = CdnOnlyRedirectHandler()
    with pytest.raises(DisallowedHost):
        handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/a.png")
