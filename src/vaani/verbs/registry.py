"""Verb catalog: register, lookup, platform filter, capability matrix."""
from __future__ import annotations

from vaani.intent.schema import Support, Verb
from vaani.platform.protocol import PlatformId


class Registry:
    def __init__(self) -> None:
        self._verbs: dict[str, Verb] = {}
        self._disabled_packs: set[str] = set()

    def register(self, verb: Verb) -> None:
        self._verbs[verb.name] = verb

    def get(self, name: str) -> Verb | None:
        return self._verbs.get(name)

    def enabled(self, platform: PlatformId) -> tuple[Verb, ...]:
        """Verbs whose pack is on and support for ``platform`` is not UNSUPPORTED."""
        out: list[Verb] = []
        for verb in self._verbs.values():
            if verb.pack in self._disabled_packs:
                continue
            support = verb.support.get(platform, Support.UNSUPPORTED)
            if support is Support.UNSUPPORTED:
                continue
            out.append(verb)
        return tuple(out)

    def matrix(self) -> dict[str, dict[str, tuple[Support, str]]]:
        """Capability matrix for ``vaani caps``: verb → platform → (support, note)."""
        platforms = (PlatformId.LINUX, PlatformId.MACOS, PlatformId.WINDOWS)
        result: dict[str, dict[str, tuple[Support, str]]] = {}
        for name, verb in sorted(self._verbs.items()):
            row: dict[str, tuple[Support, str]] = {}
            for platform in platforms:
                support = verb.support.get(platform, Support.UNSUPPORTED)
                note = "" if support is Support.SUPPORTED else verb.title
                row[platform.value] = (support, note)
            result[name] = row
        return result
