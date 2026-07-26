"""Computer-use pack — rung-7 keystroke macros (T5.2).

Behind the ``computer-use`` pack (off by default). Every successful keystroke
result carries the literal caveat from :data:`vaani.exec.input.TYPED_CAVEAT`.
Confirm UX for R2+ is pill Enter/Esc via the existing ConfirmEngine.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from vaani.context.focus import classify_role
from vaani.intent.grammar import Pattern, SlotRule
from vaani.intent.schema import (
    Context,
    Intent,
    Result,
    RiskClass,
    SlotSpec,
    Status,
    Support,
    Verb,
)
from vaani.platform.protocol import PlatformId
from vaani.verbs.registry import Registry

PACK_NAME = "computer-use"

# Spec §8 family rule / TERM-SESS — literal string required in every rung-7 detail.
# Defined here (L3) so packs never import L5 exec.
TYPED_CAVEAT = "typed into the focused terminal; outcome not verified"


def with_caveat(result: Result) -> Result:
    """Ensure ``TYPED_CAVEAT`` appears in ``result.detail`` (idempotent)."""
    detail = result.detail or ""
    if TYPED_CAVEAT in detail:
        return result
    stamped = f"{detail}; {TYPED_CAVEAT}" if detail else TYPED_CAVEAT
    return Result(
        status=result.status,
        summary=result.summary,
        detail=stamped,
        evidence=result.evidence,
        rung=result.rung if result.rung else 7,
        undo=result.undo,
        pending=result.pending,
        disambiguation=result.disambiguation,
        overlay=result.overlay,
        workspace=result.workspace,
        workspace_source=result.workspace_source,
    )

COMPUTER_USE_VERB_NAMES: frozenset[str] = frozenset(
    {
        "browser.tab.reload",
        "browser.tab.close",
        "editor.format",
        "editor.nav",
        "terminal.cd",
        "terminal.clear",
        "terminal.repeat",
        "terminal.env.export",
    }
)

_ALL_SUPPORT = {
    PlatformId.LINUX: Support.SUPPORTED,
    PlatformId.MACOS: Support.SUPPORTED,
    PlatformId.WINDOWS: Support.SUPPORTED,
}

InputGetter = Callable[[], Any | None]
PlatformGetter = Callable[[], PlatformId]
FocusRoleGetter = Callable[[Context], str]

_BROWSER_NAMES = frozenset(
    {
        "chrome",
        "google chrome",
        "chromium",
        "firefox",
        "safari",
        "edge",
        "microsoft edge",
        "brave",
        "opera",
        "vivaldi",
        "arc",
    }
)


def computer_use_patterns() -> tuple[Pattern, ...]:
    """Grammar for UI-BROW-05 / UI-EDIT-02/03 / TERM-SESS-02/03/04 / TERM-ENV-02."""
    return (
        Pattern(
            verb="browser.tab.reload",
            any_of=(
                (
                    "refresh this tab",
                    "reload this tab",
                    "refresh the tab",
                    "reload the tab",
                    "refresh tab",
                    "reload tab",
                ),
            ),
            exact=True,
            priority=52,
        ),
        Pattern(
            verb="browser.tab.close",
            any_of=(
                (
                    "close this tab",
                    "close the tab",
                    "close tab",
                ),
            ),
            exact=True,
            priority=52,
        ),
        Pattern(
            verb="editor.format",
            any_of=(
                (
                    "format this file",
                    "format the file",
                    "format file",
                    "format the current file",
                ),
            ),
            exact=True,
            # Lower than project.format (T5.3 ladder preference).
            priority=40,
        ),
        Pattern(
            verb="editor.nav",
            any_of=(
                (
                    "go to definition",
                    "goto definition",
                    "jump to definition",
                ),
            ),
            exact=True,
            priority=52,
            fixed_slots={"action": "definition"},
        ),
        Pattern(
            verb="editor.nav",
            any_of=(
                (
                    "find references",
                    "find all references",
                    "show references",
                ),
            ),
            exact=True,
            priority=52,
            fixed_slots={"action": "references"},
        ),
        Pattern(
            verb="terminal.cd",
            any_of=(
                (
                    "go to the project folder in the terminal",
                    "cd to the project in the terminal",
                    "cd to the workspace in the terminal",
                ),
            ),
            require=("terminal",),
            priority=54,
        ),
        Pattern(
            verb="terminal.cd",
            any_of=(
                (
                    "go to the vaani project folder in the terminal",
                    "cd to vaani in the terminal",
                ),
            ),
            priority=55,
            slots=(SlotRule(name="name", value="vaani"),),
        ),
        Pattern(
            verb="terminal.clear",
            any_of=(
                (
                    "clear the terminal",
                    "clear terminal",
                    "clear this terminal",
                ),
            ),
            exact=True,
            priority=52,
        ),
        Pattern(
            verb="terminal.repeat",
            any_of=(
                (
                    "run the last command again",
                    "rerun the last command",
                    "repeat the last command",
                    "run last command again",
                ),
            ),
            exact=True,
            priority=52,
        ),
        Pattern(
            verb="terminal.env.export",
            any_of=(
                (
                    "export the env from .env",
                    "export env from .env",
                    "source the env file",
                    "export the environment from .env",
                ),
            ),
            exact=True,
            priority=52,
        ),
    )


def _mod_key(platform: PlatformId) -> str:
    return "cmd" if platform is PlatformId.MACOS else "ctrl"


def _focus_role(context: Context) -> str:
    info = context.focus
    if info is None:
        return "other"
    role = classify_role(info.app_id, info.window_title)
    if role != "other":
        return role
    blob = " ".join(
        part for part in (info.app_id, info.window_title) if part
    ).casefold()
    for name in _BROWSER_NAMES:
        if name in blob:
            return "browser"
    return "other"


def _focus_precondition(
    context: Context,
    *,
    expected: str | Sequence[str],
    action: str,
) -> Result | None:
    """Return a failure Result when focus does not match; None when OK."""
    wanted = {expected} if isinstance(expected, str) else set(expected)
    info = context.focus
    if info is None:
        return Result(
            status=Status.FAILED,
            summary="Focus precondition failed",
            detail=f"{action} aborted: no focused window",
            evidence=("focus", "missing"),
            rung=7,
            workspace=context.workspace,
            workspace_source=context.workspace_source,
        )
    role = _focus_role(context)
    if role not in wanted:
        return Result(
            status=Status.FAILED,
            summary="Focus precondition failed",
            detail=(
                f"{action} aborted: focused {role or 'unknown'} "
                f"(need {'/'.join(sorted(wanted))})"
            ),
            evidence=("focus", role, info.app_id or ""),
            rung=7,
            workspace=context.workspace,
            workspace_source=context.workspace_source,
        )
    return None


def _missing_input(action: str) -> Result:
    return Result(
        status=Status.UNSUPPORTED,
        summary=f"{action} unsupported",
        detail="no InputSynth on this platform",
        evidence=("input", "missing"),
        rung=7,
    )


def _stamp(result: Result, context: Context) -> Result:
    stamped = with_caveat(result)
    return Result(
        status=stamped.status,
        summary=stamped.summary,
        detail=stamped.detail,
        evidence=stamped.evidence,
        rung=7,
        undo=stamped.undo,
        pending=stamped.pending,
        disambiguation=stamped.disambiguation,
        overlay=stamped.overlay,
        workspace=context.workspace,
        workspace_source=context.workspace_source,
    )


def materialize_computer_use_argv(
    verb_name: str,
    slots: Mapping[str, Any],
    *,
    platform: PlatformId,
    context: Context | None = None,
) -> tuple[str, ...] | None:
    """Dry-run / confirm shapes for rung-7 macros (never secret values)."""
    mod = _mod_key(platform)
    if verb_name == "browser.tab.reload":
        return ("hotkey", mod, "r")
    if verb_name == "browser.tab.close":
        return ("hotkey", mod, "w")
    if verb_name == "editor.format":
        if platform is PlatformId.MACOS:
            return ("hotkey", "shift", "alt", "f")
        if platform is PlatformId.WINDOWS:
            return ("hotkey", "shift", "alt", "f")
        return ("hotkey", "ctrl", "shift", "i")
    if verb_name == "editor.nav":
        action = str(slots.get("action") or "definition")
        if action == "references":
            return ("hotkey", "shift", "f12")
        return ("hotkey", "f12")
    if verb_name == "terminal.cd":
        path = "<workspace>"
        if context is not None and context.workspace is not None:
            path = str(context.workspace)
        name = slots.get("name")
        if name:
            path = f"<workspace>/{name}" if path == "<workspace>" else path
        return ("type_text", f"cd {path}", "enter")
    if verb_name == "terminal.clear":
        return ("hotkey", "ctrl", "l")
    if verb_name == "terminal.repeat":
        return ("refused", "terminal.repeat")
    if verb_name == "terminal.env.export":
        # Never materialize secret-bearing paths/contents.
        return ("terminal.env.export", "<redacted>")
    _ = slots
    return None


def build_computer_use_verbs(
    *,
    get_input: InputGetter | None = None,
    get_platform: PlatformGetter | None = None,
    focus_role: FocusRoleGetter | None = None,
) -> tuple[Verb, ...]:
    role_fn = focus_role or _focus_role

    def _platform(context: Context) -> PlatformId:
        if get_platform is not None:
            return get_platform()
        return context.platform

    def _input() -> Any | None:
        return get_input() if get_input is not None else None

    def handle_tab_reload(intent: Intent, context: Context) -> Result:
        _ = intent
        bad = _focus_precondition(context, expected="browser", action="browser.tab.reload")
        if bad is not None:
            return bad
        synth = _input()
        if synth is None:
            return _missing_input("browser.tab.reload")
        mod = _mod_key(_platform(context))
        return _stamp(synth.hotkey(mod, "r"), context)

    def handle_tab_close(intent: Intent, context: Context) -> Result:
        _ = intent
        bad = _focus_precondition(context, expected="browser", action="browser.tab.close")
        if bad is not None:
            return bad
        synth = _input()
        if synth is None:
            return _missing_input("browser.tab.close")
        mod = _mod_key(_platform(context))
        return _stamp(synth.hotkey(mod, "w"), context)

    def handle_editor_format(intent: Intent, context: Context) -> Result:
        _ = intent
        bad = _focus_precondition(context, expected="editor", action="editor.format")
        if bad is not None:
            return bad
        synth = _input()
        if synth is None:
            return _missing_input("editor.format")
        platform = _platform(context)
        if platform is PlatformId.LINUX:
            keys = ("ctrl", "shift", "i")
        else:
            keys = ("shift", "alt", "f")
        return _stamp(synth.hotkey(*keys), context)

    def handle_editor_nav(intent: Intent, context: Context) -> Result:
        bad = _focus_precondition(context, expected="editor", action="editor.nav")
        if bad is not None:
            return bad
        synth = _input()
        if synth is None:
            return _missing_input("editor.nav")
        action = str(intent.slots.get("action") or "definition")
        if action == "references":
            return _stamp(synth.hotkey("shift", "f12"), context)
        return _stamp(synth.hotkey("f12"), context)

    def handle_terminal_cd(intent: Intent, context: Context) -> Result:
        bad = _focus_precondition(context, expected="terminal", action="terminal.cd")
        if bad is not None:
            return bad
        synth = _input()
        if synth is None:
            return _missing_input("terminal.cd")
        workspace = context.workspace
        if workspace is None:
            return Result(
                status=Status.REFUSED,
                summary="No workspace",
                detail="terminal.cd requires a workspace",
                evidence=("terminal.cd",),
                rung=7,
                workspace=context.workspace,
                workspace_source=context.workspace_source,
            )
        path = Path(workspace)
        name = intent.slots.get("name")
        if name:
            candidate = path / str(name)
            if candidate.is_dir():
                path = candidate
        typed = f"cd {path}"
        typed_result = synth.type_text(typed + "\n")
        if typed_result.status is not Status.OK:
            # Fallback: type then Enter as separate hotkey.
            first = synth.type_text(typed)
            if first.status is not Status.OK:
                return _stamp(first, context)
            enter = synth.hotkey("enter")
            return _stamp(enter if enter.status is Status.OK else first, context)
        return _stamp(typed_result, context)

    def handle_terminal_clear(intent: Intent, context: Context) -> Result:
        _ = intent
        bad = _focus_precondition(context, expected="terminal", action="terminal.clear")
        if bad is not None:
            return bad
        synth = _input()
        if synth is None:
            return _missing_input("terminal.clear")
        return _stamp(synth.hotkey("ctrl", "l"), context)

    def handle_terminal_repeat(intent: Intent, context: Context) -> Result:
        _ = intent
        # TERM-SESS-04: Vaani cannot know what it would re-run.
        return Result(
            status=Status.REFUSED,
            summary="Refused: cannot re-run unknown command",
            detail=(
                "terminal.repeat is refused by default (TERM-SESS-04) — "
                "Vaani cannot know what the last command was"
            ),
            evidence=("terminal.repeat", "refused"),
            rung=7,
            workspace=context.workspace,
            workspace_source=context.workspace_source,
        )

    def handle_env_export(intent: Intent, context: Context) -> Result:
        _ = intent
        # R4 blocked by default; never log secret-bearing content.
        return Result(
            status=Status.REFUSED,
            summary="Blocked: env export is R4",
            detail=(
                "terminal.env.export is blocked by default (R4) and never logged"
            ),
            evidence=("terminal.env.export", "<redacted>"),
            rung=7,
            workspace=context.workspace,
            workspace_source=context.workspace_source,
        )

    _ = role_fn  # reserved for injectable focus-role overrides in tests

    return (
        Verb(
            name="browser.tab.reload",
            title="Reload the focused browser tab",
            slots={},
            rung=7,
            risk=RiskClass.R0,
            requires=frozenset({"focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_tab_reload,
        ),
        Verb(
            name="browser.tab.close",
            title="Close the focused browser tab",
            slots={},
            rung=7,
            risk=RiskClass.R2,
            requires=frozenset({"focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_tab_close,
        ),
        Verb(
            name="editor.format",
            title="Format via editor keystroke macro",
            slots={},
            rung=7,
            risk=RiskClass.R1,
            requires=frozenset({"focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_editor_format,
        ),
        Verb(
            name="editor.nav",
            title="Editor navigation keystroke macro",
            slots={"action": SlotSpec(type="str", required=False, default="definition")},
            rung=7,
            risk=RiskClass.R0,
            requires=frozenset({"focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_editor_nav,
        ),
        Verb(
            name="terminal.cd",
            title="Type cd into the focused terminal",
            slots={"name": SlotSpec(type="str", required=False)},
            rung=7,
            risk=RiskClass.R2,
            requires=frozenset({"focus", "workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_terminal_cd,
        ),
        Verb(
            name="terminal.clear",
            title="Clear the focused terminal",
            slots={},
            rung=7,
            risk=RiskClass.R0,
            requires=frozenset({"focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_terminal_clear,
        ),
        Verb(
            name="terminal.repeat",
            title="Re-run the last terminal command",
            slots={},
            rung=7,
            risk=RiskClass.R3,
            requires=frozenset({"focus"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_terminal_repeat,
        ),
        Verb(
            name="terminal.env.export",
            title="Export .env into the focused terminal",
            slots={},
            rung=7,
            risk=RiskClass.R4,
            requires=frozenset({"focus", "workspace"}),
            support=_ALL_SUPPORT,
            undo=None,
            pack=PACK_NAME,
            handler=handle_env_export,
        ),
    )


def register_computer_use_pack(
    registry: Registry,
    *,
    get_input: InputGetter | None = None,
    get_platform: PlatformGetter | None = None,
) -> tuple[Pattern, ...]:
    """Register rung-7 computer-use verbs; return grammar patterns."""
    for verb in build_computer_use_verbs(
        get_input=get_input,
        get_platform=get_platform,
    ):
        registry.register(verb)
    return computer_use_patterns()


# Re-export for tests that assert the caveat literal.
__all__ = [
    "COMPUTER_USE_VERB_NAMES",
    "PACK_NAME",
    "TYPED_CAVEAT",
    "build_computer_use_verbs",
    "computer_use_patterns",
    "materialize_computer_use_argv",
    "register_computer_use_pack",
]
