"""Localhost control bridge (T8.2 / ROADMAP P3-01, P3-02).

stdlib ``http.server`` bound to loopback only. Bearer token required.
Same ConfirmEngine policy as local voice / ``vaani do`` — remote R2+ never
auto-approves.
"""
from __future__ import annotations

import json
import os
import queue
import secrets
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from vaani.cli import (
    _caps_json,
    _make_context,
    _packs_for_settings,
    build_registry,
)
from vaani.config import Settings
from vaani.exec.supervisor import Supervisor
from vaani.history import HistoryStore
from vaani.intent.schema import (
    Context,
    Intent,
    PendingAction,
    Result,
    RiskClass,
    Status,
    Verb,
)
from vaani.platform import detect_os
from vaani.platform.protocol import PlatformId
from vaani.policy.confirm import ConfirmEngine, requires_confirm
from vaani.policy.dryrun import dispatch, materialize_argv
from vaani.remote.audit import audit_remote
from vaani.verbs.packs.registry import PackRegistry
from vaani.verbs.registry import Registry

ALLOWED_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_PORT = 32123
SSE_KEEPALIVE_SECONDS = 15.0


class BindError(ValueError):
    """Raised when the bridge would bind to a non-loopback address."""


def assert_loopback_host(host: str) -> str:
    normalized = (host or "").strip().casefold()
    # Strip IPv6 brackets if present.
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized not in {h.casefold() for h in ALLOWED_BIND_HOSTS}:
        raise BindError(
            f"refusing public bind: host must be loopback "
            f"({', '.join(sorted(ALLOWED_BIND_HOSTS))}), got {host!r}"
        )
    # Prefer numeric loopback for the actual bind.
    if normalized in {"localhost"}:
        return "127.0.0.1"
    if normalized == "::1":
        return "::1"
    return "127.0.0.1"


def result_payload(result: Result, *, verb: str = "", slots: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": result.status.value,
        "summary": result.summary,
        "detail": result.detail,
        "evidence": list(result.evidence),
        "rung": result.rung,
        "workspace": str(result.workspace) if result.workspace is not None else None,
        "workspace_source": result.workspace_source,
    }
    if verb:
        payload["verb"] = verb
    if slots is not None:
        payload["slots"] = {k: slots[k] for k in sorted(slots)}
    if result.pending is not None:
        payload["pending"] = _pending_payload(result.pending)
    if result.disambiguation is not None:
        d = result.disambiguation
        payload["disambiguation"] = {
            "id": d.id,
            "question": d.question,
            "verb": d.verb,
            "options": [
                {"key": o.key, "label": o.label, "payload": dict(o.payload)}
                for o in d.options
            ],
        }
    return payload


def _pending_payload(pending: PendingAction) -> dict[str, Any]:
    return {
        "id": pending.id,
        "verb": pending.verb,
        "slots": dict(pending.slots),
        "materialized": list(pending.materialized),
        "risk": pending.risk.value,
        "expires_at": pending.expires_at,
    }


def _job_payload(job: Any) -> dict[str, Any]:
    if hasattr(job, "__dataclass_fields__"):
        data = asdict(job)
        argv = data.get("argv")
        if isinstance(argv, tuple):
            data["argv"] = list(argv)
        return data
    return {
        "key": getattr(job, "key", ""),
        "verb": getattr(job, "verb", ""),
        "status": getattr(job, "status", ""),
        "pid": getattr(job, "pid", 0),
    }


class EventBus:
    """Fan-out queue for SSE subscribers."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[queue.Queue[dict[str, Any] | None]] = []

    def publish(self, event: str, data: Mapping[str, Any] | None = None) -> None:
        message = {"event": event, "data": dict(data or {}), "ts": time.time()}
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(message)
            except queue.Full:
                pass

    def subscribe(self, *, maxsize: int = 64) -> queue.Queue[dict[str, Any] | None]:
        q: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue[dict[str, Any] | None]) -> None:
        with self._lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass
        try:
            q.put_nowait(None)
        except queue.Full:
            pass


class BridgeService:
    """Policy-aware handlers shared by the HTTP layer."""

    def __init__(
        self,
        *,
        token: str,
        registry: Registry | None = None,
        packs: PackRegistry | None = None,
        confirm: ConfirmEngine | None = None,
        supervisor: Supervisor | None = None,
        history: Any | None = None,
        platform: PlatformId | None = None,
        context_factory: Callable[[PlatformId], Context] | None = None,
        events: EventBus | None = None,
    ) -> None:
        if not token:
            raise ValueError("bridge token must be non-empty")
        self.token = token
        self.platform = platform if platform is not None else detect_os()
        self.packs = packs if packs is not None else _packs_for_settings()
        self.registry = (
            registry if registry is not None else build_registry(self.platform, packs=self.packs)
        )
        self.confirm = confirm if confirm is not None else ConfirmEngine()
        self.supervisor = supervisor
        self.history = history
        self._context_factory = context_factory or _make_context
        self.events = events if events is not None else EventBus()
        self._lock = threading.RLock()

    def check_auth(self, authorization: str | None) -> bool:
        if not authorization:
            return False
        scheme, _, value = authorization.partition(" ")
        if scheme.casefold() != "bearer":
            return False
        return secrets.compare_digest(value.strip(), self.token)

    def caps(self) -> dict[str, Any]:
        return _caps_json(self.registry, self.packs)

    def jobs(self) -> list[dict[str, Any]]:
        if self.supervisor is None:
            return []
        try:
            return [_job_payload(j) for j in self.supervisor.list_jobs()]
        except Exception:
            return []

    def overlay(self, body: Mapping[str, Any] | None) -> tuple[int, dict[str, Any]]:
        """Stub until T6.2 overlay bus lands.

        ``clear`` is accepted as a no-op success; any other op returns 501.
        """
        payload = dict(body or {})
        kind = str(payload.get("kind") or payload.get("op") or "").casefold()
        if kind in {"", "clear"}:
            self.events.publish("overlay", {"kind": "clear", "stub": True})
            return 200, {"status": "ok", "overlay": "cleared", "stub": True}
        return 501, {
            "error": "overlay_not_implemented",
            "detail": "T6.2 overlay bus deferred; only clear is accepted as no-op",
        }

    def handle_intent(
        self,
        body: Mapping[str, Any],
        *,
        caller: str = "remote",
    ) -> tuple[int, dict[str, Any]]:
        verb_name = str(body.get("verb") or "").strip()
        if not verb_name:
            return 400, {"error": "missing_verb"}

        verb = self.registry.get(verb_name)
        if verb is None:
            return 404, {
                "error": "unknown_verb",
                "verb": verb_name,
                "catalog": sorted(self.registry.matrix()),
            }

        slots = dict(body.get("slots") or {})
        # Remote must never smuggle a confirm bypass.
        modifiers: set[str] = set()
        if body.get("dry_run") is True or "dry_run" in (body.get("modifiers") or []):
            modifiers.add("dry_run")

        utterance = str(body.get("utterance") or body.get("text") or "")
        if not utterance:
            utterance = " ".join(
                [verb_name, *[f"{k}={slots[k]}" for k in sorted(slots)]]
            )

        intent = Intent(
            verb=verb_name,
            slots=slots,
            rung=verb.rung,
            confidence=1.0,
            source="remote",
            mode="act",
            utterance=utterance,
            raw_utterance=utterance,
            modifiers=frozenset(modifiers),
            brain=None,
        )
        context = self._context_factory(self.platform)

        if "dry_run" in intent.modifiers:
            result = dispatch(verb, intent, context)
            self._audit(
                intent,
                result,
                caller=caller,
                confirmed_by=None,
            )
            self.events.publish(
                "intent",
                result_payload(result, verb=verb_name, slots=slots),
            )
            return 200, result_payload(result, verb=verb_name, slots=slots)

        # Hard rule: R2+ stages ConfirmEngine — same as local. No remote --yes.
        if requires_confirm(verb.risk):
            return self._stage_confirm(intent, verb, context, caller=caller)

        result = dispatch(verb, intent, context)
        # Handlers may still request confirm (slugify, dirty checkout, …).
        if result.status is Status.NEEDS_CONFIRM:
            staged_slots = (
                dict(result.pending.slots)
                if result.pending is not None
                else dict(intent.slots)
            )
            staged_intent = Intent(
                verb=intent.verb,
                slots=staged_slots,
                rung=intent.rung,
                confidence=intent.confidence,
                source=intent.source,
                mode=intent.mode,
                utterance=intent.utterance,
                raw_utterance=intent.raw_utterance,
                modifiers=intent.modifiers,
                brain=intent.brain,
            )
            materialized = (
                result.pending.materialized
                if result.pending is not None
                else self._materialize(intent, context)
            )
            return self._stage_confirm(
                staged_intent,
                verb,
                context,
                caller=caller,
                materialized=materialized,
                summary=result.summary,
                detail=result.detail,
            )

        self._audit(intent, result, caller=caller, confirmed_by=None)
        payload = result_payload(result, verb=verb_name, slots=slots)
        self.events.publish("intent", payload)
        return 200, payload

    def handle_confirm(
        self,
        action_id: str,
        body: Mapping[str, Any] | None = None,
        *,
        caller: str = "remote",
    ) -> tuple[int, dict[str, Any]]:
        payload = dict(body or {})
        action = str(payload.get("action") or "approve").casefold()
        if action in {"reject", "deny", "cancel"}:
            rejected = self.confirm.reject(action_id)
            if rejected is None:
                return 404, {"error": "pending_not_found", "id": action_id}
            self._audit_pending(
                rejected,
                status="refused",
                caller=caller,
                confirmed_by=None,
                summary=f"Rejected {rejected.verb}",
            )
            out = {"status": "refused", "pending": _pending_payload(rejected)}
            self.events.publish("confirm_rejected", out)
            return 200, out

        if action not in {"approve", "confirm", "yes", "ok"}:
            return 400, {"error": "invalid_action", "action": action}

        # Same policy gate as local: via=remote (not agent); R2+ allowed.
        approved = self.confirm.approve(action_id, via="remote")
        if approved is None:
            return 404, {"error": "pending_not_found_or_expired", "id": action_id}

        bundle = self.confirm.claim_execution()
        if bundle is None:
            return 409, {"error": "execution_claim_failed", "id": action_id}

        intent, verb, context, pending = bundle
        intent = Intent(
            verb=intent.verb,
            slots=intent.slots,
            rung=intent.rung,
            confidence=intent.confidence,
            source=intent.source,
            mode=intent.mode,
            utterance=intent.utterance,
            raw_utterance=intent.raw_utterance,
            modifiers=frozenset(set(intent.modifiers) | {"confirmed"}),
            brain=intent.brain,
        )
        result = dispatch(verb, intent, context)
        self._audit(
            intent,
            result,
            caller=caller,
            confirmed_by=caller,
            risk=pending.risk.value,
        )
        out = result_payload(result, verb=verb.name, slots=dict(intent.slots))
        out["confirmed"] = True
        out["pending"] = _pending_payload(pending)
        self.events.publish("confirm_approved", out)
        return 200, out

    def _stage_confirm(
        self,
        intent: Intent,
        verb: Verb,
        context: Context,
        *,
        caller: str,
        materialized: Sequence[str] | None = None,
        summary: str | None = None,
        detail: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        argv = (
            tuple(materialized)
            if materialized is not None
            else self._materialize(intent, context)
        )
        with self._lock:
            pending = self.confirm.stage(intent, verb, argv, context=context)
        result = Result(
            status=Status.NEEDS_CONFIRM,
            summary=summary or f"Confirm {verb.title}?",
            detail=detail or " ".join(pending.materialized),
            evidence=pending.materialized,
            rung=verb.rung,
            pending=pending,
            workspace=context.workspace,
            workspace_source=context.workspace_source,
        )
        self._audit(
            intent,
            result,
            caller=caller,
            confirmed_by=None,
            risk=verb.risk.value,
        )
        payload = result_payload(
            result, verb=verb.name, slots=dict(intent.slots)
        )
        self.events.publish("needs_confirm", payload)
        return 200, payload

    def _materialize(self, intent: Intent, context: Context) -> tuple[str, ...]:
        try:
            argv = materialize_argv(
                intent.verb,
                dict(intent.slots),
                platform=context.platform,
                context=context,
            )
        except Exception:
            argv = None
        if argv is None:
            argv = (
                intent.verb,
                *[f"{k}={intent.slots[k]}" for k in sorted(intent.slots)],
            )
        return tuple(argv)

    def _audit(
        self,
        intent: Intent,
        result: Result,
        *,
        caller: str,
        confirmed_by: str | None,
        risk: str | None = None,
    ) -> None:
        audit_remote(
            self.history,
            raw_text=intent.raw_utterance or intent.utterance,
            final_text=result.detail or result.summary,
            caller=caller,
            verb=intent.verb,
            rung=result.rung or intent.rung,
            risk=risk,
            status=result.status.value,
            confirmed_by=confirmed_by,
            workspace=str(result.workspace) if result.workspace is not None else None,
            workspace_source=result.workspace_source or None,
            evidence=result.evidence,
        )

    def _audit_pending(
        self,
        pending: PendingAction,
        *,
        status: str,
        caller: str,
        confirmed_by: str | None,
        summary: str,
    ) -> None:
        audit_remote(
            self.history,
            raw_text=pending.verb,
            final_text=summary,
            caller=caller,
            verb=pending.verb,
            risk=pending.risk.value if isinstance(pending.risk, RiskClass) else str(pending.risk),
            status=status,
            confirmed_by=confirmed_by,
            evidence=pending.materialized,
        )


class _BridgeHandler(BaseHTTPRequestHandler):
    server: "BridgeServer"  # type: ignore[assignment]

    def log_message(self, fmt: str, *args: Any) -> None:
        # Quiet by default; bridge logs via EventBus / stderr only on errors.
        if os.environ.get("VAANI_BRIDGE_DEBUG", "").strip() in {"1", "true", "yes"}:
            super().log_message(fmt, *args)

    def _auth_ok(self) -> bool:
        return self.server.service.check_auth(self.headers.get("Authorization"))

    def _send_json(self, code: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid_json: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("json_object_required")
        return data

    def _caller(self) -> str:
        return (
            self.headers.get("X-Vaani-Caller")
            or self.headers.get("X-Forwarded-User")
            or "remote"
        ).strip() or "remote"

    def do_GET(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._send_json(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        if path == "/v1/caps":
            self._send_json(200, self.server.service.caps())
            return
        if path == "/v1/jobs":
            self._send_json(200, {"jobs": self.server.service.jobs()})
            return
        if path == "/events":
            self._sse()
            return
        self._send_json(404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        if not self._auth_ok():
            self._send_json(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            body = self._read_json()
        except ValueError as exc:
            self._send_json(400, {"error": "bad_request", "detail": str(exc)})
            return

        caller = self._caller()
        if path == "/v1/intent":
            code, payload = self.server.service.handle_intent(body, caller=caller)
            self._send_json(code, payload)
            return
        if path == "/v1/overlay":
            code, payload = self.server.service.overlay(body)
            self._send_json(code, payload)
            return
        if path.startswith("/v1/confirm/"):
            action_id = path[len("/v1/confirm/") :].strip("/")
            if not action_id:
                self._send_json(400, {"error": "missing_confirm_id"})
                return
            code, payload = self.server.service.handle_confirm(
                action_id, body, caller=caller
            )
            self._send_json(code, payload)
            return
        self._send_json(404, {"error": "not_found", "path": path})

    def _sse(self) -> None:
        bus = self.server.service.events
        q = bus.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            self.wfile.write(b"event: hello\ndata: {\"ok\":true}\n\n")
            self.wfile.flush()
            while True:
                try:
                    message = q.get(timeout=SSE_KEEPALIVE_SECONDS)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                if message is None:
                    break
                event = str(message.get("event") or "message")
                data = json.dumps(
                    message.get("data") or {},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                chunk = f"event: {event}\ndata: {data}\n\n".encode("utf-8")
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            bus.unsubscribe(q)


class BridgeServer(ThreadingHTTPServer):
    """Threading HTTP server that refuses non-loopback binds."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, host: str, port: int, service: BridgeService) -> None:
        bind_host = assert_loopback_host(host)
        self.service = service
        self.bind_host = bind_host
        super().__init__((bind_host, port), _BridgeHandler)

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}"


def serve_bridge(
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    token: str | None = None,
    settings: Settings | None = None,
) -> int:
    """Build defaults and serve forever. Returns process exit code."""
    secret = (token or os.environ.get("VAANI_BRIDGE_TOKEN") or "").strip()
    if not secret:
        raise SystemExit(
            "bridge token required: pass --token or set VAANI_BRIDGE_TOKEN"
        )
    assert_loopback_host(host)

    cfg = settings if settings is not None else Settings.from_home()
    try:
        cfg.prepare()
    except Exception:
        pass

    history = HistoryStore(cfg.history_db)
    supervisor: Supervisor | None
    try:
        supervisor = Supervisor(cfg)
        supervisor.adopt_or_clear()
    except Exception:
        supervisor = None

    service = BridgeService(
        token=secret,
        supervisor=supervisor,
        history=history,
        packs=_packs_for_settings(cfg),
    )
    server = BridgeServer(host, port, service)
    print(
        f"Vaani bridge listening on {server.url} (loopback only; bearer auth required)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbridge stopped", flush=True)
    finally:
        server.server_close()
        history.close()
    return 0
