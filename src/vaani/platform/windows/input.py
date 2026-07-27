"""Windows InputSynth — keystroke injection via pynput (T5.2)."""
from __future__ import annotations

from typing import Any

from vaani.intent.schema import Result, Status


class WindowsInputSynth:
    """Thin Windows adapter. Injectable ``controller`` for tests."""

    def __init__(self, *, controller: Any | None = None) -> None:
        self._controller = controller

    def type_text(self, text: str) -> Result:
        if not text:
            return Result(
                status=Status.FAILED,
                summary="Nothing to type",
                detail="empty text",
                evidence=("type_text",),
                rung=7,
            )
        try:
            ctrl = self._controller
            if ctrl is None:
                from pynput.keyboard import Controller

                ctrl = Controller()
            ctrl.type(text)
            return Result(
                status=Status.OK,
                summary="Typed text",
                detail="",
                evidence=("type_text", "<text>"),
                rung=7,
            )
        except Exception as exc:  # noqa: BLE001
            return Result(
                status=Status.FAILED,
                summary="Could not type text",
                detail=f"{type(exc).__name__}: {exc}",
                evidence=("type_text", "<text>"),
                rung=7,
            )

    def hotkey(self, *keys: str) -> Result:
        if not keys:
            return Result(
                status=Status.FAILED,
                summary="No hotkey",
                detail="empty hotkey",
                evidence=("hotkey",),
                rung=7,
            )
        try:
            if self._controller is not None and hasattr(self._controller, "hotkey"):
                self._controller.hotkey(*keys)
            else:
                self._hotkey_pynput(keys)
            return Result(
                status=Status.OK,
                summary="Sent hotkey",
                detail="",
                evidence=("hotkey", *keys),
                rung=7,
            )
        except Exception as exc:  # noqa: BLE001
            return Result(
                status=Status.FAILED,
                summary="Could not send hotkey",
                detail=f"{type(exc).__name__}: {exc}",
                evidence=("hotkey", *keys),
                rung=7,
            )

    def _hotkey_pynput(self, keys: tuple[str, ...]) -> None:
        from pynput.keyboard import Controller, Key

        mapping = {
            "cmd": Key.ctrl,  # Windows: Ctrl is the browser/editor chord base
            "command": Key.ctrl,
            "ctrl": Key.ctrl,
            "control": Key.ctrl,
            "alt": Key.alt,
            "shift": Key.shift,
            "win": Key.cmd,
            "super": Key.cmd,
            "enter": Key.enter,
            "return": Key.enter,
            "tab": Key.tab,
            "escape": Key.esc,
            "esc": Key.esc,
            "f12": Key.f12,
            "space": Key.space,
        }
        controller = self._controller or Controller()
        resolved = [mapping.get(k.casefold(), k) for k in keys]
        modifiers = [k for k in resolved if not isinstance(k, str)]
        chars = [k for k in resolved if isinstance(k, str)]
        for mod in modifiers:
            controller.press(mod)
        try:
            for char in chars:
                controller.press(char)
                controller.release(char)
        finally:
            for mod in reversed(modifiers):
                controller.release(mod)

    def click(self, x: float, y: float) -> Result:
        try:
            from pynput.mouse import Button, Controller

            mouse = Controller()
            mouse.position = (int(round(x)), int(round(y)))
            mouse.click(Button.left, 1)
            return Result(
                status=Status.OK,
                summary="Clicked",
                detail="",
                evidence=("click", f"{x},{y}"),
                rung=7,
            )
        except Exception as exc:  # noqa: BLE001
            return Result(
                status=Status.FAILED,
                summary="Could not click",
                detail=str(exc),
                evidence=("click", f"{x},{y}"),
                rung=7,
            )
