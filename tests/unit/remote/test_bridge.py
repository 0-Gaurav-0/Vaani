"""Unit tests for the localhost control bridge (T8.2)."""
from __future__ import annotations

import json
import threading
import time
from http.client import HTTPConnection
from typing import Any

import pytest

from vaani.history import HistoryStore
from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.policy.confirm import ConfirmEngine
from vaani.remote.bridge import (
    ALLOWED_BIND_HOSTS,
    BindError,
    BridgeServer,
    BridgeService,
    assert_loopback_host,
)
from vaani.verbs.packs.registry import PackRegistry
from vaani.verbs.registry import Registry


def _context() -> Context:
    return Context(
        platform=PlatformId.LINUX,
        workspace=None,
        workspace_source="home",
        repo=None,
        project=None,
        focus=None,
        screen=None,
        session=None,
    )


def _registry() -> Registry:
    calls: dict[str, list[Intent]] = {"r0": [], "r2": []}

    def r0_handler(intent: Intent, _ctx: Context) -> Result:
        calls["r0"].append(intent)
        return Result(status=Status.OK, summary="opened", rung=1)

    def r2_handler(intent: Intent, _ctx: Context) -> Result:
        calls["r2"].append(intent)
        if "confirmed" not in intent.modifiers:
            return Result(
                status=Status.FAILED,
                summary="handler ran without confirm",
                rung=1,
            )
        return Result(status=Status.OK, summary="quit done", rung=1)

    reg = Registry()
    reg.register(
        Verb(
            name="app.open",
            title="Open",
            slots={"name": SlotSpec(type="str", required=True)},
            rung=1,
            risk=RiskClass.R0,
            requires=frozenset(),
            support={
                PlatformId.LINUX: Support.SUPPORTED,
                PlatformId.MACOS: Support.SUPPORTED,
                PlatformId.WINDOWS: Support.SUPPORTED,
            },
            undo=None,
            pack="core",
            handler=r0_handler,
        )
    )
    reg.register(
        Verb(
            name="app.quit",
            title="Quit",
            slots={"name": SlotSpec(type="str", required=True)},
            rung=1,
            risk=RiskClass.R2,
            requires=frozenset(),
            support={
                PlatformId.LINUX: Support.SUPPORTED,
                PlatformId.MACOS: Support.SUPPORTED,
                PlatformId.WINDOWS: Support.SUPPORTED,
            },
            undo=None,
            pack="core",
            handler=r2_handler,
        )
    )
    object.__setattr__(reg, "_calls", calls)  # type: ignore[attr-defined]
    return reg


def _service(tmp_path, *, token: str = "test-token") -> BridgeService:
    history = HistoryStore(tmp_path / "history.sqlite3")
    packs = PackRegistry(tmp_path / "packs.json")
    return BridgeService(
        token=token,
        registry=_registry(),
        packs=packs,
        confirm=ConfirmEngine(id_factory=lambda: "pending-1"),
        history=history,
        platform=PlatformId.LINUX,
        context_factory=lambda _p: _context(),
    )


def _start_server(service: BridgeService) -> tuple[BridgeServer, str, int]:
    server = BridgeServer("127.0.0.1", 0, service)
    host, port = server.server_address[:2]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, str(host), int(port)


def _request(
    host: str,
    port: int,
    method: str,
    path: str,
    *,
    token: str | None = "test-token",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, Any] | str]:
    conn = HTTPConnection(host, port, timeout=5)
    try:
        hdrs = dict(headers or {})
        if token is not None:
            hdrs["Authorization"] = f"Bearer {token}"
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
            hdrs["Content-Length"] = str(len(payload))
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read().decode("utf-8")
        try:
            return resp.status, json.loads(raw)
        except json.JSONDecodeError:
            return resp.status, raw
    finally:
        conn.close()


def test_refuses_public_bind() -> None:
    with pytest.raises(BindError):
        assert_loopback_host("0.0.0.0")
    with pytest.raises(BindError):
        assert_loopback_host("192.168.1.10")
    assert assert_loopback_host("127.0.0.1") == "127.0.0.1"
    assert assert_loopback_host("localhost") == "127.0.0.1"
    assert "127.0.0.1" in ALLOWED_BIND_HOSTS


def test_bearer_required(tmp_path) -> None:
    service = _service(tmp_path)
    server, host, port = _start_server(service)
    try:
        code, payload = _request(host, port, "GET", "/v1/caps", token=None)
        assert code == 401
        assert payload["error"] == "unauthorized"
        code, payload = _request(host, port, "GET", "/v1/caps", token="wrong")
        assert code == 401
        code, payload = _request(host, port, "GET", "/v1/caps")
        assert code == 200
        assert "verbs" in payload
        assert "app.quit" in payload["verbs"]
    finally:
        server.shutdown()
        server.server_close()
        service.history.close()


def test_r0_intent_runs_immediately(tmp_path) -> None:
    service = _service(tmp_path)
    server, host, port = _start_server(service)
    try:
        code, payload = _request(
            host,
            port,
            "POST",
            "/v1/intent",
            body={"verb": "app.open", "slots": {"name": "Terminal"}},
            headers={"X-Vaani-Caller": "phone-1"},
        )
        assert code == 200
        assert payload["status"] == "ok"
        calls = service.registry._calls  # type: ignore[attr-defined]
        assert len(calls["r0"]) == 1
        rows = service.history.list()
        assert rows
        assert rows[0]["mode"] == "remote"
        assert "phone-1" in (rows[0].get("cleanup_status") or "")
    finally:
        server.shutdown()
        server.server_close()
        service.history.close()


def test_remote_r2_requires_same_confirmation_as_local(tmp_path) -> None:
    """Hard rule: remote R2+ stages ConfirmEngine; handler runs only after approve."""
    service = _service(tmp_path)
    server, host, port = _start_server(service)
    try:
        code, payload = _request(
            host,
            port,
            "POST",
            "/v1/intent",
            body={
                "verb": "app.quit",
                "slots": {"name": "Slack"},
                # Smuggled confirm must be ignored.
                "modifiers": ["confirmed", "yes"],
                "yes": True,
            },
            headers={"X-Vaani-Caller": "tailscale-phone"},
        )
        assert code == 200
        assert payload["status"] == "needs_confirm"
        assert payload["pending"]["id"] == "pending-1"
        assert payload["pending"]["risk"] == "R2"
        calls = service.registry._calls  # type: ignore[attr-defined]
        assert calls["r2"] == []

        rows = service.history.list()
        assert rows
        assert "tailscale-phone" in (rows[0].get("cleanup_status") or "")

        # Approve via bridge confirm endpoint.
        code, approved = _request(
            host,
            port,
            "POST",
            "/v1/confirm/pending-1",
            body={"action": "approve"},
            headers={"X-Vaani-Caller": "tailscale-phone"},
        )
        assert code == 200
        assert approved["status"] == "ok"
        assert approved.get("confirmed") is True
        assert len(calls["r2"]) == 1
        assert "confirmed" in calls["r2"][0].modifiers

        # Audit records caller on confirm path too.
        rows = service.history.list()
        assert any(
            "confirmed_by=tailscale-phone" in (r.get("cleanup_status") or "")
            or "tailscale-phone" in (r.get("cleanup_status") or "")
            for r in rows
        )
    finally:
        server.shutdown()
        server.server_close()
        service.history.close()


def test_confirm_reject(tmp_path) -> None:
    service = _service(tmp_path)
    server, host, port = _start_server(service)
    try:
        code, payload = _request(
            host,
            port,
            "POST",
            "/v1/intent",
            body={"verb": "app.quit", "slots": {"name": "Slack"}},
        )
        assert payload["status"] == "needs_confirm"
        code, rejected = _request(
            host,
            port,
            "POST",
            "/v1/confirm/pending-1",
            body={"action": "reject"},
        )
        assert code == 200
        assert rejected["status"] == "refused"
        calls = service.registry._calls  # type: ignore[attr-defined]
        assert calls["r2"] == []
    finally:
        server.shutdown()
        server.server_close()
        service.history.close()


def test_jobs_and_overlay_stub(tmp_path) -> None:
    service = _service(tmp_path)
    server, host, port = _start_server(service)
    try:
        code, payload = _request(host, port, "GET", "/v1/jobs")
        assert code == 200
        assert payload == {"jobs": []}

        code, payload = _request(
            host, port, "POST", "/v1/overlay", body={"kind": "clear"}
        )
        assert code == 200
        assert payload["stub"] is True

        code, payload = _request(
            host, port, "POST", "/v1/overlay", body={"kind": "point", "x": 1, "y": 2}
        )
        assert code == 501
        assert payload["error"] == "overlay_not_implemented"
    finally:
        server.shutdown()
        server.server_close()
        service.history.close()


def test_sse_events(tmp_path) -> None:
    import socket

    service = _service(tmp_path)
    server, host, port = _start_server(service)
    try:
        sock = socket.create_connection((host, port), timeout=5)
        sock.sendall(
            (
                f"GET /events HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Authorization: Bearer test-token\r\n"
                "Accept: text/event-stream\r\n"
                "\r\n"
            ).encode("utf-8")
        )
        sock.settimeout(2.0)
        buf = b""
        while b"event: hello" not in buf:
            chunk = sock.recv(4096)
            assert chunk, "SSE connection closed before hello"
            buf += chunk

        def fire() -> None:
            _request(
                host,
                port,
                "POST",
                "/v1/intent",
                body={"verb": "app.open", "slots": {"name": "Terminal"}},
            )

        t = threading.Thread(target=fire, daemon=True)
        t.start()
        deadline = time.time() + 2.0
        while time.time() < deadline and b"event: intent" not in buf:
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
        t.join(timeout=1)
        sock.close()
        assert b"200" in buf.split(b"\r\n", 1)[0]
        assert b"text/event-stream" in buf
        assert b"event: intent" in buf
    finally:
        server.shutdown()
        server.server_close()
        service.history.close()


def test_cli_bridge_refuses_public_host(monkeypatch: pytest.MonkeyPatch) -> None:
    from vaani.cli import main

    code = main(["bridge", "--host", "0.0.0.0", "--token", "x"])
    assert code == 2
