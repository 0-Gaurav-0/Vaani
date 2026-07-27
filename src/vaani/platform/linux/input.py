"""Linux InputSynth — X11 via pynput/xdotool; Wayland → UNSUPPORTED (T5.2)."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any, Callable

from vaani.intent.schema import Result, Status

Runner = Callable[..., Any]


def _default_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, **kwargs)


def _is_wayland() -> bool:
    session = (os.environ.get("XDG_SESSION_TYPE") or "").casefold()
    if session == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY")) and not os.environ.get("DISPLAY")


class LinuxInputSynth:
    """Thin Linux adapter. Wayland without XWayland reports UNSUPPORTED."""

    def __init__(
        self,
        *,
        runner: Runner | None = None,
        controller: Any | None = None,
        wayland: bool | None = None,
    ) -> None:
        self._runner = runner or _default_runner
        self._controller = controller
        self._wayland = _is_wayland() if wayland is None else wayland

    def type_text(self, text: str) -> Result:
        blocked = self._wayland_block()
        if blocked is not None:
            return blocked
        if not text:
            return Result(
                status=Status.FAILED,
                summary="Nothing to type",
                detail="empty text",
                evidence=("type_text",),
                rung=7,
            )
        try:
            if self._controller is not None:
                self._controller.type(text)
                return Result(
                    status=Status.OK,
                    summary="Typed text",
                    detail="",
                    evidence=("type_text", "<text>"),
                    rung=7,
                )
            if shutil.which("xdotool"):
                completed = self._runner(
                    ["xdotool", "type", "--", text],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if getattr(completed, "returncode", 1) != 0:
                    raise RuntimeError("xdotool type failed")
            else:
                from pynput.keyboard import Controller

                Controller().type(text)
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
        blocked = self._wayland_block()
        if blocked is not None:
            return blocked
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
            elif shutil.which("xdotool"):
                # xdotool wants key names like ctrl+r
                chord = "+".join(self._xdotool_key(k) for k in keys)
                completed = self._runner(
                    ["xdotool", "key", chord],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if getattr(completed, "returncode", 1) != 0:
                    raise RuntimeError(f"xdotool key failed: {chord}")
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

    def _wayland_block(self) -> Result | None:
        if not self._wayland:
            return None
        return Result(
            status=Status.UNSUPPORTED,
            summary="Keystrokes unsupported",
            detail="input synthesis unsupported on Wayland (no XWayland display)",
            evidence=("input", "wayland"),
            rung=7,
        )

    @staticmethod
    def _xdotool_key(key: str) -> str:
        low = key.casefold()
        return {
            "cmd": "ctrl",
            "command": "ctrl",
            "ctrl": "ctrl",
            "control": "ctrl",
            "alt": "alt",
            "shift": "shift",
            "enter": "Return",
            "return": "Return",
            "esc": "Escape",
            "escape": "Escape",
            "f12": "F12",
            "space": "space",
            "l": "l",
            "r": "r",
            "w": "w",
            "shift+alt+f": "shift+alt+f",
        }.get(low, key)

    def _hotkey_pynput(self, keys: tuple[str, ...]) -> None:
        from pynput.keyboard import Controller, Key

        mapping = {
            "cmd": Key.ctrl,
            "command": Key.ctrl,
            "ctrl": Key.ctrl,
            "control": Key.ctrl,
            "alt": Key.alt,
            "shift": Key.shift,
            "enter": Key.enter,
            "return": Key.enter,
            "tab": Key.tab,
            "escape": Key.esc,
            "esc": Key.esc,
            "f12": Key.f12,
            "space": Key.space,
        }
        controller = Controller()
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
        blocked = self._wayland_block()
        if blocked is not None:
            return Result(
                status=Status.UNSUPPORTED,
                summary="Click unsupported",
                detail=blocked.detail,
                evidence=("click", f"{x},{y}"),
                rung=7,
            )
        try:
            if shutil.which("xdotool"):
                completed = self._runner(
                    ["xdotool", "mousemove", "--", str(int(round(x))), str(int(round(y))), "click", "1"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if getattr(completed, "returncode", 1) != 0:
                    raise RuntimeError("xdotool click failed")
            else:
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
