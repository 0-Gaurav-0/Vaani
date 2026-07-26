"""Linux ``SystemControl`` — volume, DND, lock, wifi, DNS, trash, IP, ports/procs.

Mechanisms follow spec §7.2 / T2.4. All subprocess calls go through an injectable
``runner`` (default ``subprocess.run``) so unit tests never mutate the host.
Missing tools return honest ``UNSUPPORTED`` / ``DEGRADED`` results with reasons.
"""
from __future__ import annotations

import os
import re
import shutil
import signal as signal_mod
import subprocess
import time
from collections.abc import Sequence
from typing import Any, Callable, Mapping

from ...intent.schema import Result, Status, Support
from ...platform.protocol import ProcInfo

Runner = Callable[..., Any]
Whicher = Callable[[str], str | None]
Getenv = Callable[[str], str | None]

_SS_USERS_RE = re.compile(
    r'users:\(\("([^"]*)",pid=(\d+)',
)
_KILL_WAIT_S = 2.0


def _clamp_pct(pct: int) -> int:
    return max(0, min(100, int(pct)))


class LinuxSystemControl:
    """Thin Linux leaf implementing :class:`SystemControl`."""

    def __init__(
        self,
        *,
        runner: Runner = subprocess.run,
        which: Whicher | None = None,
        getenv: Getenv | None = None,
        sleeper: Callable[[float], None] | None = None,
        killer: Callable[[int, int], None] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
    ) -> None:
        self._runner = runner
        self._which = which or shutil.which
        self._getenv = getenv or (lambda key: os.environ.get(key))
        self._sleep = sleeper or time.sleep
        self._killer = killer or os.kill
        self._pid_alive = pid_alive or _pid_alive

    # --- capability honesty -------------------------------------------------

    def support(self, action: str) -> tuple[Support, str]:
        """Declare Support + reason for a protocol action name."""
        probes: Mapping[str, Callable[[], tuple[Support, str]]] = {
            "volume_set": self._support_volume,
            "mute": self._support_volume,
            "dnd": self._support_dnd,
            "lock": lambda: self._tool_support(
                "loginctl", "loginctl lock-session"
            ),
            "display_sleep": self._support_display_sleep,
            "wifi": lambda: self._tool_support(
                "nmcli", "nmcli radio wifi on|off"
            ),
            "dns_flush": lambda: self._tool_support(
                "resolvectl", "resolvectl flush-caches"
            ),
            "trash_empty": lambda: self._tool_support(
                "gio", "gio trash --empty"
            ),
            "local_ip": self._support_local_ip,
            "list_listeners": self._support_list_listeners,
            "list_named": lambda: self._tool_support("pgrep", "pgrep -af"),
            "kill_pids": lambda: (
                Support.SUPPORTED,
                "SIGTERM then SIGKILL after 2s",
            ),
            "open_process_monitor": self._support_process_monitor,
        }
        probe = probes.get(action)
        if probe is None:
            return Support.UNSUPPORTED, f"unknown system action: {action}"
        return probe()

    def _tool_support(self, tool: str, mechanism: str) -> tuple[Support, str]:
        if self._which(tool):
            return Support.SUPPORTED, mechanism
        return Support.UNSUPPORTED, f"{tool} not found ({mechanism})"

    def _support_volume(self) -> tuple[Support, str]:
        if self._which("pactl"):
            return Support.SUPPORTED, "pactl set-sink-volume/mute"
        if self._which("wpctl"):
            return Support.SUPPORTED, "wpctl set-volume/mute (PipeWire)"
        return Support.UNSUPPORTED, "neither pactl nor wpctl found"

    def _support_dnd(self) -> tuple[Support, str]:
        if self._which("gsettings"):
            # GNOME-only schema — honest DEGRADED even when the binary exists.
            return (
                Support.DEGRADED,
                "GNOME gsettings org.gnome.desktop.notifications show-banners",
            )
        return Support.UNSUPPORTED, "gsettings not found (GNOME DND)"

    def _support_display_sleep(self) -> tuple[Support, str]:
        session = (self._getenv("XDG_SESSION_TYPE") or "").lower()
        if session == "wayland":
            return Support.UNSUPPORTED, "display sleep unsupported on Wayland"
        if self._which("xset"):
            return Support.DEGRADED, "xset dpms force off (X11 only)"
        return Support.UNSUPPORTED, "xset not found (X11 display sleep)"

    def _support_local_ip(self) -> tuple[Support, str]:
        if self._which("hostname"):
            return Support.SUPPORTED, "hostname -I"
        if self._which("ip"):
            return Support.SUPPORTED, "ip -4 -o addr show scope global"
        return Support.UNSUPPORTED, "neither hostname nor ip found"

    def _support_list_listeners(self) -> tuple[Support, str]:
        if self._which("ss"):
            return Support.SUPPORTED, "ss -lptnH sport = :<port>"
        if self._which("lsof"):
            return Support.SUPPORTED, "lsof -nP -iTCP:<port> -sTCP:LISTEN"
        return Support.UNSUPPORTED, "neither ss nor lsof found"

    def _support_process_monitor(self) -> tuple[Support, str]:
        for tool in (
            "gnome-system-monitor",
            "plasma-systemmonitor",
            "xfce4-taskmanager",
            "mate-system-monitor",
        ):
            if self._which(tool):
                return Support.SUPPORTED, tool
        return Support.UNSUPPORTED, "no system monitor found"

    # --- protocol methods ---------------------------------------------------

    def volume_set(self, pct: int) -> Result:
        level = _clamp_pct(pct)
        support, reason = self._support_volume()
        if support is Support.UNSUPPORTED:
            return self._unsupported("volume", reason, rung=2)
        if self._which("pactl"):
            argv = ("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%")
        else:
            fraction = f"{level / 100:.2f}"
            argv = ("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", fraction)
        return self._run(argv, ok_summary=f"Volume set to {level}%", rung=2)

    def mute(self, enabled: bool) -> Result:
        support, reason = self._support_volume()
        if support is Support.UNSUPPORTED:
            return self._unsupported("mute", reason, rung=2)
        flag = "1" if enabled else "0"
        if self._which("pactl"):
            argv = ("pactl", "set-sink-mute", "@DEFAULT_SINK@", flag)
        else:
            argv = ("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", flag)
        label = "Muted" if enabled else "Unmuted"
        return self._run(argv, ok_summary=label, rung=2)

    def dnd(self, enabled: bool) -> Result:
        support, reason = self._support_dnd()
        if support is Support.UNSUPPORTED:
            return self._unsupported("dnd", reason, rung=1)
        # show-banners false == Do Not Disturb on.
        banners = "false" if enabled else "true"
        argv = (
            "gsettings",
            "set",
            "org.gnome.desktop.notifications",
            "show-banners",
            banners,
        )
        label = "Do Not Disturb on" if enabled else "Do Not Disturb off"
        return self._run(argv, ok_summary=label, rung=1)

    def lock(self) -> Result:
        support, reason = self.support("lock")
        if support is Support.UNSUPPORTED:
            return self._unsupported("lock", reason, rung=1)
        return self._run(
            ("loginctl", "lock-session"),
            ok_summary="Screen locked",
            rung=1,
        )

    def display_sleep(self) -> Result:
        support, reason = self._support_display_sleep()
        if support is Support.UNSUPPORTED:
            return self._unsupported("display sleep", reason, rung=1)
        return self._run(
            ("xset", "dpms", "force", "off"),
            ok_summary="Display sleeping",
            rung=1,
        )

    def wifi(self, enabled: bool) -> Result:
        support, reason = self.support("wifi")
        if support is Support.UNSUPPORTED:
            return self._unsupported("wifi", reason, rung=2)
        state = "on" if enabled else "off"
        return self._run(
            ("nmcli", "radio", "wifi", state),
            ok_summary=f"Wi-Fi {state}",
            rung=2,
        )

    def dns_flush(self) -> Result:
        support, reason = self.support("dns_flush")
        if support is Support.UNSUPPORTED:
            return self._unsupported("dns flush", reason, rung=1)
        return self._run(
            ("resolvectl", "flush-caches"),
            ok_summary="DNS caches flushed",
            rung=1,
        )

    def trash_empty(self) -> Result:
        support, reason = self.support("trash_empty")
        if support is Support.UNSUPPORTED:
            return self._unsupported("empty trash", reason, rung=1)
        return self._run(
            ("gio", "trash", "--empty"),
            ok_summary="Trash emptied",
            rung=1,
        )

    def local_ip(self) -> str:
        """Return the first global IPv4 address, or ``\"\"`` if unavailable."""
        support, _reason = self._support_local_ip()
        if support is Support.UNSUPPORTED:
            return ""
        if self._which("hostname"):
            completed = self._invoke(("hostname", "-I"))
            if completed.returncode == 0:
                parts = (completed.stdout or "").split()
                if parts:
                    return parts[0].strip()
        if self._which("ip"):
            completed = self._invoke(
                ("ip", "-4", "-o", "addr", "show", "scope", "global")
            )
            if completed.returncode == 0:
                for line in (completed.stdout or "").splitlines():
                    # e.g. "2: wlan0    inet 192.168.1.10/24 ..."
                    tokens = line.split()
                    for i, tok in enumerate(tokens):
                        if tok == "inet" and i + 1 < len(tokens):
                            return tokens[i + 1].split("/", 1)[0]
        return ""

    def list_listeners(self, port: int) -> tuple[ProcInfo, ...]:
        support, _reason = self._support_list_listeners()
        if support is Support.UNSUPPORTED:
            return ()
        port_i = int(port)
        if self._which("ss"):
            completed = self._invoke(
                ("ss", "-lptnH", f"sport = :{port_i}")
            )
            text = getattr(completed, "stdout", "") or ""
            parsed = _parse_ss_listeners(text)
            if parsed or getattr(completed, "returncode", 1) == 0:
                return parsed
        if self._which("lsof"):
            completed = self._invoke(
                (
                    "lsof",
                    "-nP",
                    f"-iTCP:{port_i}",
                    "-sTCP:LISTEN",
                    "-Fpcu",
                )
            )
            return _parse_lsof_fpcu(getattr(completed, "stdout", "") or "")
        return ()

    def list_named(self, name: str) -> tuple[ProcInfo, ...]:
        needle = name.strip()
        if not needle:
            return ()
        support, _reason = self.support("list_named")
        if support is Support.UNSUPPORTED:
            return ()
        completed = self._invoke(("pgrep", "-af", needle))
        if getattr(completed, "returncode", 1) not in {0, 1}:
            return ()
        out: list[ProcInfo] = []
        for line in (getattr(completed, "stdout", "") or "").splitlines():
            line = line.strip()
            if not line:
                continue
            pid_s, _, rest = line.partition(" ")
            try:
                pid = int(pid_s)
            except ValueError:
                continue
            cmdline = rest.strip() or needle
            base = os.path.basename(cmdline.split()[0]) if cmdline.split() else needle
            out.append(ProcInfo(pid=pid, name=base, detail=cmdline))
        return tuple(out)

    def kill_pids(
        self,
        pids: Sequence[int],
        *,
        signal: str = "term",
    ) -> Result:
        unique = tuple(dict.fromkeys(int(p) for p in pids if int(p) > 0))
        if not unique:
            return Result(
                status=Status.FAILED,
                summary="No PIDs to kill",
                detail="kill_pids requires at least one pid",
                evidence=(),
                rung=2,
            )
        sig_name = (signal or "term").casefold()
        immediate = sig_name in {"kill", "sigkill", "9"}
        first = signal_mod.SIGKILL if immediate else signal_mod.SIGTERM
        failed: list[str] = []
        for pid in unique:
            try:
                self._killer(pid, first)
            except ProcessLookupError:
                continue
            except PermissionError as exc:
                failed.append(f"{pid}:{exc}")
            except OSError as exc:
                failed.append(f"{pid}:{exc}")
        if not immediate:
            self._sleep(_KILL_WAIT_S)
            for pid in unique:
                if not self._pid_alive(pid):
                    continue
                try:
                    self._killer(pid, signal_mod.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError) as exc:
                    if not isinstance(exc, ProcessLookupError):
                        failed.append(f"{pid}:{exc}")
        evidence = tuple(str(p) for p in unique)
        if failed:
            return Result(
                status=Status.FAILED,
                summary="Could not kill all processes",
                detail="; ".join(failed),
                evidence=evidence,
                rung=2,
            )
        return Result(
            status=Status.OK,
            summary=f"Signaled PID {', '.join(evidence)}",
            detail=f"signal={sig_name}",
            evidence=evidence,
            rung=2,
        )

    def open_process_monitor(self) -> Result:
        support, reason = self._support_process_monitor()
        if support is Support.UNSUPPORTED:
            return self._unsupported("open process monitor", reason, rung=1)
        tool = reason.split()[0] if reason else "gnome-system-monitor"
        for candidate in (
            "gnome-system-monitor",
            "plasma-systemmonitor",
            "xfce4-taskmanager",
            "mate-system-monitor",
        ):
            if self._which(candidate):
                tool = candidate
                break
        return self._run((tool,), ok_summary="Opened system monitor", rung=1)

    # --- internals ----------------------------------------------------------

    def _unsupported(self, label: str, reason: str, *, rung: int) -> Result:
        return Result(
            status=Status.UNSUPPORTED,
            summary=f"Can't {label} on Linux",
            detail=reason,
            evidence=(reason,),
            rung=rung,
        )

    def _invoke(self, argv: tuple[str, ...]) -> Any:
        return self._runner(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )

    def _run(self, argv: tuple[str, ...], *, ok_summary: str, rung: int) -> Result:
        try:
            completed = self._invoke(argv)
        except Exception as exc:  # noqa: BLE001 — leaf must not raise to verbs
            return Result(
                status=Status.FAILED,
                summary=f"Failed: {ok_summary}",
                detail=str(exc),
                evidence=argv,
                rung=rung,
            )
        code = getattr(completed, "returncode", -1)
        stdout = getattr(completed, "stdout", "") or ""
        stderr = getattr(completed, "stderr", "") or ""
        if code == 0:
            return Result(
                status=Status.OK,
                summary=ok_summary,
                detail=stdout.strip(),
                evidence=argv,
                rung=rung,
            )
        err = (stderr or stdout or f"exit {code}").strip()
        return Result(
            status=Status.FAILED,
            summary=f"Failed: {ok_summary}",
            detail=err,
            evidence=argv,
            rung=rung,
        )


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _parse_ss_listeners(text: str) -> tuple[ProcInfo, ...]:
    seen: dict[int, ProcInfo] = {}
    for line in text.splitlines():
        match = _SS_USERS_RE.search(line)
        if match is None:
            continue
        name = match.group(1) or "unknown"
        pid = int(match.group(2))
        seen[pid] = ProcInfo(pid=pid, name=name, detail=line.strip())
    return tuple(seen.values())


def _parse_lsof_fpcu(text: str) -> tuple[ProcInfo, ...]:
    current_pid: int | None = None
    current_cmd = ""
    current_uid: int | None = None
    seen: dict[int, ProcInfo] = {}
    for raw in text.splitlines():
        if not raw:
            continue
        code, payload = raw[0], raw[1:]
        if code == "p":
            try:
                current_pid = int(payload)
            except ValueError:
                current_pid = None
            current_cmd = ""
            current_uid = None
        elif code == "c":
            current_cmd = payload
        elif code == "u":
            try:
                current_uid = int(payload)
            except ValueError:
                current_uid = None
        if current_pid is not None and current_cmd:
            seen[current_pid] = ProcInfo(
                pid=current_pid,
                name=current_cmd,
                uid=current_uid,
            )
    return tuple(seen.values())
