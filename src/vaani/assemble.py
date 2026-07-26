"""Shared controller assembly for all OS runtimes."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from .codex import ResultWindow
from .controller import Controller
from .exec.agent import AgentRunner
from .exec.supervisor import Supervisor
from .groq import GroqClient
from .history import HistoryStore
from .observability import configure_logging
from .platform import detect_os
from .platform.protocol import PlatformBundle
from .secrets import effective_key
from .surface.result import make_assistant_sink


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
    """Wire HistoryStore, GroqClient, Controller, AgentRunner, and ResultWindow.

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
    supervisor = None
    if all(
        hasattr(settings, attr)
        for attr in ("jobs_path", "log_dir", "data_dir")
    ):
        try:
            supervisor = Supervisor(settings)
            supervisor.adopt_or_clear()
        except Exception:
            resolved_logger.exception("event=supervisor_adopt_failed")
            supervisor = None
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
        system=bundle.system,
        window=getattr(bundle, "window", None),
        terminal=getattr(bundle, "terminal", None),
        input_synth=getattr(bundle, "input", None),
        supervisor=supervisor,
        settings=settings if supervisor is not None else None,
        vocab_path=getattr(settings, "vocab_path", None),
    )
    result_window = ResultWindow(make_assistant_sink(bundle.feedback))
    controller.result_window = result_window
    agent = AgentRunner(
        registry=controller.registry,
        confirm=controller.confirm,
        platform=detect_os,
        on_progress=lambda text: result_window.show_text(text),
    )
    controller.codex = agent
    controller.agent = agent
    return Assembly(
        controller=controller,
        history=history,
        groq=groq,
        logger=resolved_logger,
    )
