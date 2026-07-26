"""Windows SystemControl — volume/DND/lock/network/trash (T1.2)."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Final

from vaani.exec.runner import Command, Completed, powershell, run
from vaani.intent.schema import Result, Status, Support

Runner = Callable[[Command], Completed]

_VOLUME_REASON = "no built-in volume CLI; needs pycaw or a bundled helper"
_DND_REASON = "Focus Assist has no stable public API"

# Monitor power-off via SendMessage(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2).
_DISPLAY_SLEEP_SCRIPT = (
    "Add-Type -Name VaaniDisplay -Namespace VaaniWin -MemberDefinition '"
    "[DllImport(\"user32.dll\")] public static extern int SendMessage("
    "int hWnd, int hMsg, int wParam, int lParam);'; "
    "[VaaniWin.VaaniDisplay]::SendMessage(-1, 0x0112, 0xF170, 2)"
)

_LOCAL_IP_SCRIPT = (
    "(Get-NetIPAddress -AddressFamily IPv4 |"
    " Where-Object {"
    "  $_.IPAddress -notlike '127.*' -and $_.PrefixOrigin -ne 'WellKnown'"
    " } |"
    " Select-Object -First 1 -ExpandProperty IPAddress)"
)


class WindowsSystemControl:
    """Windows leaf for ``PlatformBundle.system``."""

    def __init__(self, *, runner: Runner | None = None) -> None:
        self._run: Runner = runner or run

    def support(self, op: str) -> tuple[Support, str]:
        """Per-method Support cell + reason (empty reason when fully supported)."""
        try:
            return _SUPPORT[op]
        except KeyError as exc:
            raise KeyError(f"unknown SystemControl op: {op}") from exc

    def volume_set(self, pct: int) -> Result:
        _ = pct
        return _degraded("volume.set", _VOLUME_REASON)

    def mute(self, enabled: bool) -> Result:
        _ = enabled
        return _degraded("volume.mute", _VOLUME_REASON)

    def dnd(self, enabled: bool) -> Result:
        _ = enabled
        return _degraded("dnd.set", _DND_REASON)

    def lock(self) -> Result:
        argv = ("rundll32", "user32.dll,LockWorkStation")
        return _from_completed(
            self._run(Command(argv=argv)),
            ok_summary="Locked the screen.",
            fail_summary="Could not lock the screen.",
        )

    def display_sleep(self) -> Result:
        argv = powershell([_DISPLAY_SLEEP_SCRIPT])
        return _from_completed(
            self._run(Command(argv=argv)),
            ok_summary="Display sleeping.",
            fail_summary="Could not sleep the display.",
        )

    def wifi(self, enabled: bool) -> Result:
        cmdlet = "Enable-NetAdapter" if enabled else "Disable-NetAdapter"
        argv = powershell([cmdlet, "-Name", "Wi-Fi", "-Confirm:$false"])
        action = "enabled" if enabled else "disabled"
        return _from_completed(
            self._run(Command(argv=argv)),
            ok_summary=f"Wi-Fi {action}.",
            fail_summary=f"Could not {action[:-1]} Wi-Fi.",
        )

    def dns_flush(self) -> Result:
        argv = ("ipconfig", "/flushdns")
        return _from_completed(
            self._run(Command(argv=argv)),
            ok_summary="Flushed DNS cache.",
            fail_summary="Could not flush DNS.",
        )

    def trash_empty(self) -> Result:
        argv = powershell(["Clear-RecycleBin", "-Force", "-ErrorAction", "Stop"])
        return _from_completed(
            self._run(Command(argv=argv)),
            ok_summary="Emptied the Recycle Bin.",
            fail_summary="Could not empty the Recycle Bin.",
        )

    def local_ip(self) -> str:
        argv = powershell([_LOCAL_IP_SCRIPT])
        completed = self._run(Command(argv=argv))
        if completed.returncode != 0 or completed.timed_out or completed.cancelled:
            return ""
        return (completed.stdout or "").strip().splitlines()[0].strip() if completed.stdout else ""


def _degraded(op: str, reason: str) -> Result:
    """Honest refusal for DEGRADED cells — never escalates rung."""
    return Result(
        status=Status.UNSUPPORTED,
        summary=f"Windows {op} unavailable.",
        detail=reason,
        evidence=(),
        rung=0,
    )


def _from_completed(completed: Completed, *, ok_summary: str, fail_summary: str) -> Result:
    evidence = completed.argv
    if completed.timed_out:
        return Result(
            status=Status.FAILED,
            summary=fail_summary,
            detail="timed out",
            evidence=evidence,
            rung=0,
        )
    if completed.cancelled:
        return Result(
            status=Status.FAILED,
            summary=fail_summary,
            detail="cancelled",
            evidence=evidence,
            rung=0,
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        return Result(
            status=Status.FAILED,
            summary=fail_summary,
            detail=detail,
            evidence=evidence,
            rung=0,
        )
    return Result(
        status=Status.OK,
        summary=ok_summary,
        detail=(completed.stdout or "").strip(),
        evidence=evidence,
        rung=0,
    )


_SUPPORT: Final[Mapping[str, tuple[Support, str]]] = {
    "volume_set": (Support.DEGRADED, _VOLUME_REASON),
    "mute": (Support.DEGRADED, _VOLUME_REASON),
    "dnd": (Support.DEGRADED, _DND_REASON),
    "lock": (Support.SUPPORTED, ""),
    "display_sleep": (Support.SUPPORTED, ""),
    "wifi": (Support.SUPPORTED, "Disable/Enable-NetAdapter typically needs elevation"),
    "dns_flush": (Support.SUPPORTED, ""),
    "trash_empty": (Support.SUPPORTED, ""),
    "local_ip": (Support.SUPPORTED, ""),
}
