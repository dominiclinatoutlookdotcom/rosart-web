"""The autouse socket guard (FR-023): no test in this folder reaches a non-loopback host."""
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).parent


def test_resolving_the_public_site_fails_naming_the_host():
    with pytest.raises(AssertionError, match=r"rosart\.com\.au"):
        socket.getaddrinfo("rosart.com.au", 443)


def test_connecting_to_a_public_address_fails_naming_the_host():
    with pytest.raises(AssertionError, match=r"198\.51\.100\.1"):
        socket.create_connection(("198.51.100.1", 443), timeout=0.2)


def test_a_raw_socket_connect_to_a_public_address_fails():
    with socket.socket() as sock, pytest.raises(AssertionError, match=r"198\.51\.100\.1"):
        sock.connect(("198.51.100.1", 443))


def test_loopback_listener_is_reachable():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        port = server.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            peer, _ = server.accept()
            peer.close()
            assert client.getpeername()[1] == port


def _run_pytest_in_temp(tmp_path: Path, require_posix: bool) -> subprocess.CompletedProcess:
    shutil.copy(HERE / "conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_marked.py").write_text(textwrap.dedent("""
        import pytest

        @pytest.mark.posix
        def test_needs_posix():
            pass
    """), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PUBLISHER_REQUIRE_POSIX"}
    if require_posix:
        env["PUBLISHER_REQUIRE_POSIX"] = "1"
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                           "--rootdir", str(tmp_path), str(tmp_path)],
                          cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="the posix marker only skips on Windows")
def test_posix_marked_tests_are_skipped_on_windows(tmp_path):
    result = _run_pytest_in_temp(tmp_path, require_posix=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="the posix marker only skips on Windows")
def test_a_skipped_posix_test_fails_when_posix_is_required(tmp_path):
    result = _run_pytest_in_temp(tmp_path, require_posix=True)
    assert result.returncode != 0, result.stdout
    assert "PUBLISHER_REQUIRE_POSIX" in result.stdout
