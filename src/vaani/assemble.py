"""Shared controller assembly for all OS runtimes."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .codex import CodexRunner, ResultWindow
from .controller import Controller
from .groq import GroqClient
from .history import HistoryStore
from .observability import configure_logging
from .platform.protocol import PlatformBundle
from .secrets import effective_key


@dataclass(frozen=True)
class Assembly:
    controller: Controller
    history: HistoryStore
    groq: GroqClient
    logger: logging.Logger


def assemble(
    bundle: PlatformBundle,
    *,
    delivery: Any | None = None,
    target: Any | None = None,
    hotkeys: Any | None = None,
    logger: logging.Logger | None = None,
) -> Assembly:
    """Wire HistoryStore, GroqClient, Controller, CodexRunner, and ResultWindow.

    OS runtimes keep display/hotkey/signal loops and call this for shared wiring.
    Optional ``delivery`` / ``target`` / ``hotkeys`` override bundle placeholders
    (e.g. Linux live X11 delivery). ``target`` is accepted for API parity with
    callers that construct probe + delivery together; delivery owns the probe.
    """
    _ = target
    settings = bundle.settings
    resolved_delivery = bundle.delivery if delivery is None else delivery
    resolved_hotkeys = bundle.hotkeys if hotkeys is None else hotkeys
    resolved_logger = logger or configure_logging(
        settings.log_dir, debug=bool(getattr(settings, "debug", False))
    )

    history = HistoryStore(settings.history_db)
    groq = GroqClient()
    store = bundle.key_store
    controller = Controller(
        recorder=bundle.recorder,
        groq=groq,
        delivery=resolved_delivery,
        history=history,
        feedback=bundle.feedback,
        key_provider=lambda: effective_key(store).value,
        hotkeys=resolved_hotkeys,
        logger=resolved_logger,
        amplitude_path=settings.amplitude_path,
        indicator_control_path=settings.indicator_control_path,
        browser_launcher=bundle.browser,
        app_launcher=bundle.apps,
        vocab_path=getattr(settings, "vocab_path", None),
    )
    assistant = CodexRunner()

    def show_assistant_result(text: str) -> None:
        message = (text or "").strip() or "Assistant returned no output."
        bundle.feedback.notify("paste", message[:160])

    controller.codex = assistant
    controller.result_window = ResultWindow(show_assistant_result)
    return Assembly(
        controller=controller,
        history=history,
        groq=groq,
        logger=resolved_logger,
    )
