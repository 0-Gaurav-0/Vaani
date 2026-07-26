"""macOS SystemControl — volume, lock, wifi, trash, IP (T1.2 / spec §7.2)."""
from __future__ import annotations

import re
import subprocess
from typing import Any, Callable, Mapping

from vaani.intent.schema import Result, Status, Support

# Classic lock path; argv only — never shell-joined.
_CGSESSION = (
    "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession"
)

_WIFI_DEVICE_RE = re.compile(
    r"Hardware Port:\s*(?:Wi-Fi|AirPort)\s*\n\s*Device:\s*(\S+)",
    re.IGNORECASE,
)

# Per-method capability honesty for packs / ``vaani caps`` notes (Support has no
# attached reason field — notes live alongside the enum here).
_SUPPORT: dict[str, tuple[Support, str]] = {
    "volume_set": (Support.SUPPORTED, ""),
    "mute": (Support.SUPPORTED, ""),
    "dnd": (
        Support.DEGRADED,
        "no supported Focus/DND API since Ventura",
    ),
    "lock": (Support.SUPPORTED, ""),
    "display_sleep": (Support.SUPPORTED, ""),
    "wifi": (Support.SUPPORTED, ""),
    "dns_flush": (
        Support.UNSUPPORTED,
        "needs sudo — R4 disabled by default",
    ),
    "trash_empty": (Support.SUPPORTED, ""),
    "local_ip": (Support.SUPPORTED, ""),
}


def _default_runner(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, **kwargs)


class MacSystemControl:
    """Thin argv adapter for macOS system verbs. Injectable ``runner`` for tests."""

    def __init__(
        self,
        *,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        self._runner = runner or _default_runner

    def support(self) -> Mapping[str, tuple[Support, str]]:
        """Capability matrix cells for this surface (support, note)."""
        return dict(_SUPPORT)

    def volume_set(self, pct: int) -> Result:
        level = max(0, min(100, int(pct)))
        argv = ["osascript", "-e", f"set volume output volume {level}"]
        return self._run_ok(
            argv,
            summary=f"Volume set to {level}%",
            fail_summary="Could not set volume",
            rung=2,
        )

    def mute(self, enabled: bool) -> Result:
        flag = "true" if enabled else "false"
        argv = ["osascript", "-e", f"set volume output muted {flag}"]
        summary = "Output muted" if enabled else "Output unmuted"
        fail = "Could not mute output" if enabled else "Could not unmute output"
        return self._run_ok(
            argv,
            summary=summary,
            fail_summary=fail,
            rung=2,
        )

    def dnd(self, enabled: bool) -> Result:
        """Honest DEGRADED: no stable Focus API since Ventura — do not fake success."""
        _ = enabled
        support, note = _SUPPORT["dnd"]
        assert support is Support.DEGRADED
        return Result(
            status=Status.UNSUPPORTED,
            summary="Do Not Disturb is not available",
            detail=note,
            rung=1,
        )

    def lock(self) -> Result:
        return self._run_ok(
            [_CGSESSION, "-suspend"],
            summary="Screen locked",
            fail_summary="Could not lock the screen",
            rung=1,
        )

    def display_sleep(self) -> Result:
        return self._run_ok(
            ["pmset", "displaysleepnow"],
            summary="Display sleeping",
            fail_summary="Could not sleep the display",
            rung=1,
        )

    def wifi(self, enabled: bool) -> Result:
        device = self._wifi_device()
        if not device:
            return Result(
                status=Status.FAILED,
                summary="Could not find a Wi-Fi device",
                detail="networksetup -listallhardwareports found no Wi-Fi port",
                rung=2,
            )
        power = "on" if enabled else "off"
        argv = ["networksetup", "-setairportpower", device, power]
        action = "on" if enabled else "off"
        return self._run_ok(
            argv,
            summary=f"Wi-Fi turned {action}",
            fail_summary=f"Could not turn Wi-Fi {action}",
            rung=2,
            evidence=(f"device={device}",),
        )

    def dns_flush(self) -> Result:
        """R4 / disabled: macOS flush needs sudo — refuse without escalating."""
        support, note = _SUPPORT["dns_flush"]
        assert support is Support.UNSUPPORTED
        return Result(
            status=Status.REFUSED,
            summary="DNS flush needs administrator access",
            detail=note,
            rung=1,
        )

    def trash_empty(self) -> Result:
        argv = ["osascript", "-e", 'tell application "Finder" to empty trash']
        return self._run_ok(
            argv,
            summary="Trash emptied",
            fail_summary="Could not empty the trash",
            rung=1,
        )

    def local_ip(self) -> str:
        for iface in ("en0", "en1", "en2"):
            try:
                completed = self._runner(
                    ["ipconfig", "getifaddr", iface],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5.0,
                )
            except Exception:
                continue
            if getattr(completed, "returncode", 1) != 0:
                continue
            ip = (getattr(completed, "stdout", None) or "").strip()
            if ip:
                return ip
        return ""

    def _wifi_device(self) -> str | None:
        try:
            completed = self._runner(
                ["networksetup", "-listallhardwareports"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
            )
        except Exception:
            return None
        if getattr(completed, "returncode", 1) != 0:
            return None
        text = getattr(completed, "stdout", None) or ""
        match = _WIFI_DEVICE_RE.search(text)
        return match.group(1) if match else None

    def _run_ok(
        self,
        argv: list[str],
        *,
        summary: str,
        fail_summary: str,
        rung: int,
        evidence: tuple[str, ...] = (),
    ) -> Result:
        try:
            completed = self._runner(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=20.0,
            )
        except Exception as exc:
            return Result(
                status=Status.FAILED,
                summary=fail_summary,
                detail=str(exc),
                evidence=evidence or (argv[0],),
                rung=rung,
            )
        code = getattr(completed, "returncode", 1)
        if code != 0:
            stderr = (getattr(completed, "stderr", None) or "").strip()
            return Result(
                status=Status.FAILED,
                summary=fail_summary,
                detail=stderr or f"exit {code}",
                evidence=evidence or tuple(argv[:3]),
                rung=rung,
            )
        return Result(
            status=Status.OK,
            summary=summary,
            evidence=evidence or tuple(argv[:3]),
            rung=rung,
        )
