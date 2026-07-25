"""Deterministic voice aliases for public and locally configured sites."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class SiteTarget:
    name: str
    url: str
    browser: str | None = None


PUBLIC_SITES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("gmail", "mail"), "Gmail", "https://mail.google.com/mail/u/0/#inbox"),
    (("basecamp",), "Basecamp", "https://app.basecamp.com/"),
    (("paddle",), "Paddle", "https://vendors.paddle.com/"),
    (("stripe dashboard", "stripe"), "Stripe", "https://dashboard.stripe.com/"),
    (("postmark",), "Postmark", "https://account.postmarkapp.com/"),
    (("agent skills", "skills website", "skills dot sh"), "Agent Skills", "https://www.skills.sh/"),
    (("chatgpt", "chat gpt"), "ChatGPT", "https://chatgpt.com/"),
    (("claude",), "Claude", "https://claude.ai/new"),
    (("gemini",), "Gemini", "https://gemini.google.com/app"),
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def load_local_sites(path: Path | None = None) -> tuple[tuple[tuple[str, ...], str, str], ...]:
    config_path = path or Path.home() / ".config" / "vaani" / "sites.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(payload, list):
        return ()

    sites = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        aliases = item.get("aliases")
        name = item.get("name")
        url = item.get("url")
        if (
            not isinstance(aliases, list)
            or not aliases
            or not all(isinstance(alias, str) and alias.strip() for alias in aliases)
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(url, str)
            or not _valid_url(url)
        ):
            continue
        sites.append((tuple(alias.casefold().strip() for alias in aliases), name.strip(), url))
    return tuple(sites)


def resolve_site(command: str, *, config_path: Path | None = None) -> SiteTarget | None:
    normalized = " ".join(command.casefold().strip().split())
    if not re.search(r"\b(open|launch|go to|show)\b", normalized):
        return None
    browser = "brave" if "brave" in normalized else "chrome" if "chrome" in normalized else None
    for aliases, name, url in (*load_local_sites(config_path), *PUBLIC_SITES):
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            return SiteTarget(name, url, browser)
    return None
