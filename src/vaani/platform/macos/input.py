"""macOS InputSynth — keystroke injection via pynput / osascript (T5.2)."""
from __future__ import annotations

import subprocess
from typing import Any, Callable

from vaani.intent.schema import Result, Status

Runner = Callable[..., Any]


def _default_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, **kwargs)


class MacInputSynth:
    """Thin macOS adapter. Injectable ``runner`` / ``controller`` for tests."""

    def __init__(
        self,
        *,
        runner: Runner | None = None,
        controller: Any | None = None,
    ) -> None:
        self._runner = runner or _default_runner
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
            if self._controller is not None:
                self._controller.type(text)
            else:
                self._type_pynput(text)
            return Result(
                status=Status.OK,
                summary="Typed text",
                detail="",
                evidence=("type_text", "<text>"),
                rung=7,
            )
        except Exception as exc:  # noqa: BLE001 — surface as FAILED
            return self._osascript_type(text, fallback_exc=exc)

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
            return self._osascript_hotkey(keys, fallback_exc=exc)

    def _type_pynput(self, text: str) -> None:
        from pynput.keyboard import Controller

        Controller().type(text)

    def _hotkey_pynput(self, keys: tuple[str, ...]) -> None:
        from pynput.keyboard import Controller, Key

        mapping = {
            "cmd": Key.cmd,
            "command": Key.cmd,
            "ctrl": Key.ctrl,
            "control": Key.ctrl,
            "alt": Key.alt,
            "option": Key.alt,
            "shift": Key.shift,
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

    def _osascript_type(self, text: str, *, fallback_exc: Exception) -> Result:
        # Escape for AppleScript string literal.
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        argv = [
            "osascript",
            "-e",
            f'tell application "System Events" to keystroke "{escaped}"',
        ]
        completed = self._runner(
            argv,
            check=False,
            capture_output=True,
            text=True,
        )
        if getattr(completed, "returncode", 1) != 0:
            return Result(
                status=Status.FAILED,
                summary="Could not type text",
                detail=f"{type(fallback_exc).__name__}; osascript failed",
                evidence=("type_text", "<text>"),
                rung=7,
            )
        return Result(
            status=Status.OK,
            summary="Typed text",
            detail="",
            evidence=("type_text", "<text>"),
            rung=7,
        )

    def _osascript_hotkey(
        self, keys: tuple[str, ...], *, fallback_exc: Exception
    ) -> Result:
        mods = []
        char = None
        for key in keys:
            low = key.casefold()
            if low in {"cmd", "command"}:
                mods.append("command down")
            elif low in {"ctrl", "control"}:
                mods.append("control down")
            elif low in {"alt", "option"}:
                mods.append("option down")
            elif low == "shift":
                mods.append("shift down")
            elif low in {"enter", "return"}:
                char = "return"
            else:
                char = key
        if char is None:
            return Result(
                status=Status.FAILED,
                summary="Could not send hotkey",
                detail=f"{type(fallback_exc).__name__}; no key character",
                evidence=("hotkey", *keys),
                rung=7,
            )
        using = f" using {{{', '.join(mods)}}}" if mods else ""
        if char == "return":
            script = f'tell application "System Events" to key code 36{using}'
        else:
            escaped = char.replace("\\", "\\\\").replace('"', '\\"')
            script = (
                f'tell application "System Events" to keystroke "{escaped}"{using}'
            )
        completed = self._runner(
            ["osascript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if getattr(completed, "returncode", 1) != 0:
            return Result(
                status=Status.FAILED,
                summary="Could not send hotkey",
                detail=f"{type(fallback_exc).__name__}; osascript failed",
                evidence=("hotkey", *keys),
                rung=7,
            )
        return Result(
            status=Status.OK,
            summary="Sent hotkey",
            detail="",
            evidence=("hotkey", *keys),
            rung=7,
        )

    def click(self, x: float, y: float) -> Result:
        """Click at global display coordinates (AppKit point space)."""
        try:
            if self._controller is not None and hasattr(self._controller, "click"):
                self._controller.click(x, y)
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
