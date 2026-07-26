"""Integration: bind an ephemeral LISTEN port, free it via system.port.free."""
from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest

from vaani.intent.schema import Context, Intent, Status
from vaani.platform import detect_os
from vaani.platform.protocol import PlatformId
from vaani.verbs.packs.procs import build_procs_verbs


def _context(platform: PlatformId) -> Context:
    return Context(
        platform=platform,
        workspace=None,
        workspace_source="integration",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _intent(port: int, *, confirmed: bool = False) -> Intent:
    mods = frozenset({"confirmed"}) if confirmed else frozenset()
    return Intent(
        verb="system.port.free",
        slots={"port": port, "signal": "term"},
        rung=2,
        confidence=1.0,
        source="test",
        mode="act",
        utterance=f"free port {port}",
        raw_utterance=f"free port {port}",
        modifiers=mods,
        brain=None,
    )


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
        return False


@pytest.mark.skipif(
    detect_os() is not PlatformId.MACOS and detect_os() is not PlatformId.LINUX,
    reason="ephemeral port-free integration is POSIX (lsof/ss) for this host",
)
def test_port_free_releases_ephemeral_listener() -> None:
    platform = detect_os()
    # Child owns the LISTEN socket so killing it cannot take down pytest.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as binder:
        binder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        binder.bind(("127.0.0.1", 0))
        port = binder.getsockname()[1]

    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import socket, time\n"
                "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
                "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                f"s.bind(('127.0.0.1', {port}))\n"
                "s.listen(1)\n"
                "time.sleep(60)\n"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not _port_in_use(port):
            time.sleep(0.05)
        assert _port_in_use(port), f"listener never bound port {port}"

        if platform is PlatformId.MACOS:
            from vaani.platform.macos.system import MacSystemControl

            system = MacSystemControl()
        else:
            from vaani.platform.linux.system import LinuxSystemControl

            system = LinuxSystemControl()

        verbs = {
            v.name: v
            for v in build_procs_verbs(
                get_system=lambda: system,
                get_platform=lambda: platform,
            )
        }
        pending = verbs["system.port.free"].handler(
            _intent(port, confirmed=False),
            _context(platform),
        )
        assert pending.status is Status.NEEDS_CONFIRM
        assert _port_in_use(port)

        result = verbs["system.port.free"].handler(
            _intent(port, confirmed=True),
            _context(platform),
        )
        assert result.status is Status.OK, (result.status, result.summary, result.detail)

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and _port_in_use(port):
            time.sleep(0.05)
        assert not _port_in_use(port), f"port {port} still held after free"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
