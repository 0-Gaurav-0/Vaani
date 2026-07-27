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
    (("youtube", "you tube"), "YouTube", "https://www.youtube.com/"),
)

RICKROLL_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
RICKROLL_PHRASES = (
    "rickroll",
    "rick roll",
    "rick and roll",
    "rick & roll",
    "reck and roll",
    "reck roll",
    "wreck and roll",
    "wreck roll",
    "ric and roll",
    "never gonna give you up",
    "rick astley",
)


def _contains_phrase(text: str, phrase: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _looks_like_rickroll(text: str) -> bool:
    """Tolerate Whisper mis-hearings like 'reck and roll'."""
    normalized = " ".join(text.casefold().strip().split())
    if any(_contains_phrase(normalized, phrase) for phrase in RICKROLL_PHRASES):
        return True
    return bool(re.search(r"\b(rick|reck|wreck|ric)\b.{0,16}\broll\b", normalized))


def _first_youtube_watch_url(query: str, *, timeout: float = 2.5) -> str | None:
    """Resolve the first watch URL for a search query (play, not search page)."""
    from urllib.parse import quote_plus
    from urllib.request import Request, urlopen

    req = Request(
        f"https://www.youtube.com/results?search_query={quote_plus(query)}",
        headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Vaani/0.1"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "ignore")
    except Exception:
        return None
    match = re.search(r"watch\?v=([A-Za-z0-9_-]{11})", html)
    if not match:
        return None
    return f"https://www.youtube.com/watch?v={match.group(1)}"


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
    if not re.search(r"\b(open|launch|go to|show|kholo|khol)\b", normalized):
        return None
    browser = "brave" if "brave" in normalized else "chrome" if "chrome" in normalized else None
    for aliases, name, url in (*load_local_sites(config_path), *PUBLIC_SITES):
        if any(_contains_phrase(normalized, alias) for alias in aliases):
            return SiteTarget(name, url, browser)
    return None


def _youtube_query_clean(query: str) -> str:
    query = query.strip(" .,!?\"'")
    query = re.sub(r"^(the|a|an|yeh|woh|mera|meri)\s+", "", query).strip()
    query = re.sub(r"\b(gaana|gana|song|video|wala|wali|ko)\b", " ", query)
    return " ".join(query.split()).strip(" .,!?\"'")


def resolve_youtube(command: str) -> SiteTarget | None:
    """Map play/watch YouTube voice commands to a browser URL (skip Codex).

    Codex runs ephemeral/read-only and cannot open the user's browser, so media
    play requests must be handled here. ``play``/``watch``/Hinglish play verbs
    open a watch URL (known aliases or first search hit); ``search`` opens results.
    """
    from urllib.parse import quote_plus

    normalized = " ".join(command.casefold().strip().split())
    if _looks_like_rickroll(normalized):
        return SiteTarget("Rickroll on YouTube", RICKROLL_URL, None)

    if not re.search(r"\byou\s*tube\b|\byoutube\b", normalized):
        return None

    action = "play"
    query = ""

    match = re.search(
        r"(?:(play|watch|search(?:\s+for)?))\s+(.+?)\s+on\s+(?:you\s*tube|youtube)\b",
        normalized,
    )
    if match is None:
        match = re.search(
            r"(?:you\s*tube|youtube)\s+(?:(play|watch|search(?:\s+for)?))\s+(.+)$",
            normalized,
        )
    if match is not None:
        action, query = match.group(1), match.group(2)
    else:
        # Hinglish: "youtube pe kesariya chalao", "kesariya youtube pe play karo"
        match = re.search(
            r"(?:you\s*tube|youtube)\s+pe\s+(.+?)\s+"
            r"(?:chalao|chala\s*do|play\s*karo|suno|search\s*karo|dhoondo|dhundo)\b",
            normalized,
        )
        if match is not None:
            query = match.group(1)
            if re.search(r"\b(search|dhoondo|dhundo)\b", normalized):
                action = "search"
        else:
            match = re.search(
                r"(.+?)\s+(?:on\s+)?(?:you\s*tube|youtube)\s+pe\s+"
                r"(?:chalao|chala\s*do|play\s*karo|suno|search\s*karo|dhoondo|dhundo)\b",
                normalized,
            )
            if match is not None:
                query = match.group(1)
                query = re.sub(r"^(play|watch|chalao|suno)\s+", "", query).strip()
                if re.search(r"\b(search|dhoondo|dhundo)\b", normalized):
                    action = "search"
            else:
                match = re.search(
                    r"(?:play|watch|chalao|suno)\s+(.+?)\s+(?:on\s+)?(?:you\s*tube|youtube)(?:\s+pe)?\b",
                    normalized,
                )
                if match is not None:
                    query = match.group(1)

    if not query:
        return None

    query = _youtube_query_clean(query)
    if not query:
        return None
    if _looks_like_rickroll(query):
        return SiteTarget("Rickroll on YouTube", RICKROLL_URL, None)

    search_only = str(action).startswith("search")
    if not search_only:
        watch = _first_youtube_watch_url(query)
        if watch:
            return SiteTarget(f"YouTube: {query}", watch, None)

    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    return SiteTarget(f"YouTube: {query}", url, None)