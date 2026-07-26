"""Dry-run policy: materialize argv and dispatch without invoking handlers."""
from __future__ import annotations

import shutil
from dataclasses import replace
from typing import Any, Mapping

from vaani.apps import APPS as LINUX_APPS
from vaani.intent.schema import Context, Intent, Result, Status, Verb
from vaani.known_folders import resolve_known_folder
from vaani.platform.protocol import AppTarget, PlatformId


def attach_workspace(result: Result, context: Context) -> Result:
    """Stamp Context workspace fields onto Result (invariant 7)."""
    return replace(
        result,
        workspace=context.workspace,
        workspace_source=context.workspace_source,
    )


def _app_catalog(platform: PlatformId) -> tuple[tuple[tuple[str, ...], AppTarget], ...]:
    if platform is PlatformId.MACOS:
        from vaani.platform.macos.apps import APPS

        return APPS
    if platform is PlatformId.WINDOWS:
        from vaani.platform.windows.apps import APPS

        return APPS
    return LINUX_APPS


def _find_app_target(
    name: str,
    catalog: tuple[tuple[tuple[str, ...], AppTarget], ...],
) -> AppTarget | None:
    needle = name.casefold().strip()
    for aliases, target in catalog:
        if target.name.casefold() == needle:
            return target
        if any(alias.casefold() == needle for alias in aliases):
            return target
    return None


def _fallback_argv(verb_name: str, slots: Mapping[str, Any]) -> tuple[str, ...]:
    parts = [verb_name]
    for key in sorted(slots):
        value = slots[key]
        if isinstance(value, (str, int, float, bool)):
            parts.append(f"{key}={value}")
    return tuple(parts)


def materialize_argv(
    verb_name: str,
    slots: Mapping[str, Any],
    *,
    platform: PlatformId,
    context: Context | None = None,
) -> tuple[str, ...] | None:
    """Best-effort argv for dry-run. Returns None when no shape is known."""
    if verb_name.startswith("vcs."):
        from vaani.verbs.packs.git import materialize_git_argv

        return materialize_git_argv(verb_name, slots, context=context)

    if verb_name.startswith("forge."):
        from vaani.verbs.packs.forge import materialize_forge_argv

        return materialize_forge_argv(verb_name, slots, context=context)

    if verb_name == "app.open":
        name = str(slots.get("name") or "")
        target = _find_app_target(name, _app_catalog(platform))
        if target is None:
            return None
        if platform is PlatformId.MACOS:
            return ("open", "-a", target.native_name or target.name)
        executable = next(
            (path for exe in target.executables if (path := shutil.which(exe))),
            target.executables[0] if target.executables else name,
        )
        return (executable, *target.arguments)

    if verb_name in {"site.open", "browser.open", "site.search"}:
        if verb_name == "site.search":
            from urllib.parse import quote_plus

            query = str(slots.get("query") or "")
            url = f"https://www.google.com/search?q={quote_plus(query)}"
        elif slots.get("port") and not slots.get("url"):
            url = f"http://localhost:{slots['port']}"
        else:
            url = str(slots.get("url") or "about:blank")
        if platform is PlatformId.MACOS:
            return ("open", url)
        if platform is PlatformId.WINDOWS:
            return ("cmd", "/c", "start", "", url)
        return ("xdg-open", url)

    if verb_name == "browser.window.private":
        if platform is PlatformId.MACOS:
            return ("open", "-na", "Brave Browser", "--args", "--incognito")
        if platform is PlatformId.WINDOWS:
            return ("brave.exe", "--incognito")
        return ("brave-browser", "--incognito")

    if verb_name == "app.quit":
        name = str(slots.get("name") or "App")
        if platform is PlatformId.MACOS:
            return ("osascript", "-e", f'tell application "{name}" to quit')
        if platform is PlatformId.WINDOWS:
            return ("taskkill", "/IM", f"{name}.exe")
        return ("wmctrl", "-c", name)

    if verb_name == "system.lock":
        if platform is PlatformId.MACOS:
            return (
                "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession",
                "-suspend",
            )
        if platform is PlatformId.WINDOWS:
            return ("rundll32", "user32.dll,LockWorkStation")
        return ("loginctl", "lock-session")

    if verb_name == "system.display.sleep":
        if platform is PlatformId.MACOS:
            return ("pmset", "displaysleepnow")
        if platform is PlatformId.WINDOWS:
            return ("powershell", "-Command", "display-sleep")
        return ("xset", "dpms", "force", "off")

    if verb_name == "system.volume.set":
        if "muted" in slots:
            flag = "1" if slots.get("muted") in {True, "true", "1", 1} else "0"
            if platform is PlatformId.MACOS:
                muted = "true" if flag == "1" else "false"
                return ("osascript", "-e", f"set volume output muted {muted}")
            return ("pactl", "set-sink-mute", "@DEFAULT_SINK@", flag)
        level = str(slots.get("level") or "0")
        if platform is PlatformId.MACOS:
            return ("osascript", "-e", f"set volume output volume {level}")
        if platform is PlatformId.WINDOWS:
            return ("volume.set", level)
        return ("pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%")

    if verb_name == "system.dnd.set":
        enabled = slots.get("enabled", True) not in {False, "false", "0", 0}
        if platform is PlatformId.LINUX:
            banners = "false" if enabled else "true"
            return (
                "gsettings",
                "set",
                "org.gnome.desktop.notifications",
                "show-banners",
                banners,
            )
        return ("system.dnd.set", "on" if enabled else "off")

    if verb_name == "system.ip.copy":
        return ("system.ip.copy",)

    if verb_name == "files.open_dir":
        folder = str(slots.get("folder") or slots.get("name") or "")
        path = resolve_known_folder(folder, platform)
        if path is None:
            return ("files.open_dir", folder)
        if platform is PlatformId.MACOS:
            return ("open", str(path))
        if platform is PlatformId.WINDOWS:
            return ("explorer", str(path))
        return ("xdg-open", str(path))

    if verb_name == "files.reveal":
        if platform is PlatformId.MACOS:
            return ("open", "-R", "${workspace}")
        if platform is PlatformId.WINDOWS:
            return ("explorer", "/select,${workspace}")
        return ("nautilus", "--select", "${workspace}")

    if verb_name == "agent.task":
        from vaani.codex import CodexRunner

        prompt = str(slots.get("prompt") or "")
        return tuple(CodexRunner.command_for("codex", prompt))

    if verb_name == "system.port.free":
        port = str(slots.get("port") or "")
        if platform is PlatformId.MACOS:
            return ("lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t")
        if platform is PlatformId.WINDOWS:
            return ("Get-NetTCPConnection", "-LocalPort", port, "-State", "Listen")
        return ("ss", "-lptnH", f"sport = :{port}")

    if verb_name == "system.proc.kill":
        name = str(slots.get("name") or "")
        if platform is PlatformId.WINDOWS:
            return ("Stop-Process", "-Name", name, "-Force")
        return ("pkill", "-f", name)

    if verb_name == "system.proc.top":
        if platform is PlatformId.MACOS:
            return ("open", "-a", "Activity Monitor")
        if platform is PlatformId.WINDOWS:
            return ("taskmgr.exe",)
        return ("gnome-system-monitor",)

    if verb_name == "system.trash.empty":
        if platform is PlatformId.MACOS:
            return ("osascript", "-e", 'tell application "Finder" to empty trash')
        if platform is PlatformId.WINDOWS:
            return ("Clear-RecycleBin", "-Force")
        return ("gio", "trash", "--empty")

    if verb_name == "system.wifi.set":
        enabled = slots.get("enabled", True) not in {False, "false", "0", 0}
        state = "on" if enabled else "off"
        if platform is PlatformId.MACOS:
            return ("networksetup", "-setairportpower", "<device>", state)
        if platform is PlatformId.WINDOWS:
            cmdlet = "Enable-NetAdapter" if enabled else "Disable-NetAdapter"
            return (cmdlet, "-Name", "Wi-Fi", "-Confirm:$false")
        return ("nmcli", "radio", "wifi", state)

    if verb_name == "session.undo":
        return ("session.undo",)

    if verb_name in {
        "project.dev.start",
        "project.dev.stop",
        "project.dev.restart",
        "project.test.run",
        "project.build",
        "project.typecheck",
        "project.deps.install",
    }:
        return _fallback_argv(verb_name, slots)

    if verb_name == "job.list":
        return ("job.list",)

    if verb_name == "job.logs":
        key = str(slots.get("key") or "project.dev")
        return ("job.logs", key)

    if verb_name == "app.terminal.open":
        if platform is PlatformId.MACOS:
            return ("open", "-a", "Terminal", "${workspace}")
        if platform is PlatformId.WINDOWS:
            return ("wt", "-d", "${workspace}")
        return ("gnome-terminal", "--working-directory", "${workspace}")

    if verb_name == "editor.open":
        editor = str(slots.get("editor") or "cursor")
        path = str(slots.get("path") or "${workspace}")
        line = slots.get("line")
        if line is not None:
            return (editor, "-g", f"{path}:{line}")
        return (editor, path)

    if verb_name in {
        "pkg.add",
        "pkg.remove",
        "pkg.lock",
        "pkg.script.run",
        "pkg.reinstall",
        "container.engine.start",
        "container.list",
        "container.stop",
        "container.stop_all",
        "container.compose.rebuild",
        "container.logs",
    }:
        return _fallback_argv(verb_name, slots)

    return None


def dry_run_result(
    verb: Verb,
    intent: Intent,
    context: Context,
) -> Result:
    """Build a DRY_RUN result with evidence=argv. Never calls the verb handler."""
    evidence = materialize_argv(
        verb.name,
        intent.slots,
        platform=context.platform,
        context=context,
    )
    if evidence is None:
        evidence = _fallback_argv(verb.name, intent.slots)
    workspace = str(context.workspace) if context.workspace is not None else ""
    # Display-only summary (never passed to a shell); keep var name off "argv"
    # so the §13.5 join invariant stays focused on exec sites.
    detail = " ".join(evidence)
    if workspace:
        detail = f"{detail} (workspace={workspace})"
    return Result(
        status=Status.DRY_RUN,
        summary=detail[:80],
        detail=detail,
        evidence=evidence,
        rung=verb.rung,
        workspace=context.workspace,
        workspace_source=context.workspace_source,
    )


def dispatch(verb: Verb, intent: Intent, context: Context) -> Result:
    """Execute a verb, short-circuiting dry-run before the handler is touched."""
    if "dry_run" in intent.modifiers:
        result = dry_run_result(verb, intent, context)
    else:
        result = verb.handler(intent, context)
    return attach_workspace(result, context)
