"""Shared setup for the publisher tests: the network guard, the `posix` marker, import paths.

The guard fails any test that resolves or connects to a non-loopback host (FR-023, constitution
VII), so no test here can reach Shopify, AWS, Slack, rosart.com.au or the storefront.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
for _path in (REPO_ROOT / "tools", REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "ip6-localhost"}


class NetworkBlocked(AssertionError):
    """A test tried to reach a host outside loopback."""


def _host_text(host) -> str:
    return host.decode("ascii", "replace") if isinstance(host, bytes) else str(host)


def _is_loopback(host) -> bool:
    if host is None or host == "":
        return True
    text = _host_text(host).split("%", 1)[0].strip("[]").lower()
    if text in LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def _refuse(action: str, host) -> None:
    raise NetworkBlocked(f"test tried to {action} {_host_text(host)!r}: tests may only use loopback "
                         "(fakes only, FR-023)")


@pytest.fixture(autouse=True)
def network_guard(monkeypatch):
    real_getaddrinfo = socket.getaddrinfo
    real_create_connection = socket.create_connection
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def getaddrinfo(host, *args, **kwargs):
        if not _is_loopback(host):
            _refuse("resolve", host)
        return real_getaddrinfo(host, *args, **kwargs)

    def create_connection(address, *args, **kwargs):
        if not _is_loopback(address[0]):
            _refuse("connect to", address[0])
        return real_create_connection(address, *args, **kwargs)

    def _check_address(address) -> None:
        if isinstance(address, tuple) and not _is_loopback(address[0]):
            _refuse("connect to", address[0])

    def connect(self, address):
        _check_address(address)
        return real_connect(self, address)

    def connect_ex(self, address):
        _check_address(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


def pytest_configure(config):
    config.addinivalue_line("markers", "posix: needs process groups or signals; skipped on Windows only")


def _require_posix() -> bool:
    return os.environ.get("PUBLISHER_REQUIRE_POSIX") == "1"


def pytest_collection_modifyitems(config, items):
    if sys.platform != "win32":
        return
    skip = pytest.mark.skip(reason="posix-only test (run under WSL or make image-test)")
    for item in items:
        if "posix" in item.keywords:
            item.add_marker(skip)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if _require_posix() and report.skipped and "posix" in item.keywords:
        report.outcome = "failed"
        report.longrepr = "a posix test was skipped but PUBLISHER_REQUIRE_POSIX=1 requires it to run"


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def web(request, tmp_path):
    """A running publisher server; every web test runs under the default prefix and under /other."""
    import web_harness

    harness = web_harness.start(tmp_path, base_path=request.param)
    yield harness
    harness.close()
