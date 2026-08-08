"""Pick YouTube songs/mixes from the user's real listening history.

Browser history + past Vaani plays beat scraping the first random search hit.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .sites import SiteTarget

logger = logging.getLogger("vaani.play_library")

_MUSIC_HINTS = (
    "song",
    "songs",
    "lyrics",
    "mashup",
    "bollywood",
    "album",
    "track",
    "gana",
    "gaana",
    "9xm",
    "arijit",
    "official music",
    "remix",
    "lofi",
    "lovesick",
    "romantic",
    "full video",
    "pov:",
    "music video",
    "ost",
    "soundtrack",
)
_EXCLUDE_HINTS = (
    "live:",
    "media byte",
    "news",
    "how to",
    "tutorial",
    "challenge",
    "karate",
    "martial",
    "interview",
    "press conference",
    "breaking",
    "/shorts/",
    "youtube.com/shorts",
    "#shorts",
    " song edit",
    "edit #",
    "whatsapp status",
    "yt short",
    "youtube short",
)

_PLAYLIST_INTENT_RE = re.compile(
    r"\b(playlist|play\s*list|mix(?:tape)?)\b",
    re.I,
)

_VAGUE_PLAYLIST_RE = re.compile(
    r"\b(?:"
    r"(?:different|another|other|new|change(?:\s+the)?|alag(?:\s+wali)?)\s+playlist|"
    r"playlist\s+(?:change|alag|badlo|badal(?:o|do)?)"
    r")\b",
    re.I,
)

# After stripping play verbs + "song/music", only filler left → vague.
_VAGUE_RESIDUE_RE = re.compile(
    r"^(?:"
    r"(?:a|an|the|some|any|me|my|koi|aur|good|nice|random|other|another|different|new)\s*"
    r")+$",
    re.I,
)


@dataclass(frozen=True)
class HistoryClip:
    title: str
    url: str
    visit_count: int
    last_visit: float
    video_id: str


def play_memory_path() -> Path:
    return Path.home() / ".local" / "share" / "vaani" / "play_memory.jsonl"


def is_playlist_intent(text: str) -> bool:
    return bool(_PLAYLIST_INTENT_RE.search(text or "") or _VAGUE_PLAYLIST_RE.search(text or ""))


def is_vague_playlist(text: str) -> bool:
    """True for 'different playlist' — not 'play workout playlist'."""
    return bool(_VAGUE_PLAYLIST_RE.search(text or ""))


def is_vague_play(text: str) -> bool:
    """True when the user wants *some* music, not a named title."""
    normalized = " ".join((text or "").casefold().split())
    if not normalized:
        return False
    if is_vague_playlist(normalized):
        return True

    cleaned = re.sub(
        r"^(?:please|can you|could you|would you|will you)(?:\s+please)?\s+",
        "",
        normalized,
    )
    cleaned = re.sub(
        r"^(?:play|bajao|chalao|suno|watch|ple|plesa)\s+",
        "",
        cleaned,
    )
    cleaned = re.sub(
        r"\s+(?:bajao|baja\s*do|play\s*karo|chalao|chala\s*do|suno)\s*$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\b(?:on\s+)?(?:you\s*tube|youtube)(?:\s+pe)?\b", " ", cleaned)
    cleaned = re.sub(
        r"\b(?:gaana|gana|song|songs|music|track|video|wala|wali|ko)\b",
        " ",
        cleaned,
    )
    cleaned = " ".join(cleaned.split()).strip(" .,!?'\"")
    if not cleaned:
        return True
    if _VAGUE_RESIDUE_RE.match(cleaned + " "):
        return True
    # "me some other" / "some other" / "good" left after stripping song
    if cleaned in {
        "me some other",
        "some other",
        "other",
        "another",
        "different",
        "good",
        "nice",
        "random",
        "any",
        "koi",
        "something",
        "music",
    }:
        return True
    return False


def _video_id(url: str) -> str:
    try:
        parsed = urlparse(url)
        if "youtu.be" in (parsed.netloc or ""):
            return (parsed.path or "").strip("/").split("/")[0][:11]
        qs = parse_qs(parsed.query or "")
        vids = qs.get("v") or []
        if vids:
            return vids[0][:11]
    except Exception:
        pass
    return ""


def _is_short_or_clip(title: str, url: str) -> bool:
    blob = f"{title} {url}".casefold()
    if "/shorts/" in blob or "youtube.com/shorts" in blob or "#shorts" in blob:
        return True
    if re.search(r"\b(song\s+edit|status\s+video|reels?)\b", blob):
        return True
    if " edit #" in blob or blob.endswith(" edit"):
        return True
    return False


def _looks_like_music(title: str, url: str) -> bool:
    blob = f"{title} {url}".casefold()
    if _is_short_or_clip(title, url):
        return False
    if any(x in blob for x in _EXCLUDE_HINTS):
        return False
    if "list=rd" in blob or "list=rdmm" in blob or "playlist?list=" in blob:
        return True
    return any(h in blob for h in _MUSIC_HINTS)


def _brave_history_path() -> Path | None:
    home = Path.home()
    for path in (
        home / ".config/BraveSoftware/Brave-Browser/Default/History",
        home / ".config/google-chrome/Default/History",
        home / ".config/chromium/Default/History",
    ):
        if path.is_file():
            return path
    return None


def _read_browser_youtube_history(limit: int = 80) -> list[HistoryClip]:
    src = _brave_history_path()
    if src is None:
        return []
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
            tmp_path = tmp.name
        shutil.copy2(src, tmp_path)
        con = sqlite3.connect(tmp_path)
        rows = list(
            con.execute(
                """
                SELECT IFNULL(title,''), url, visit_count, last_visit_time
                FROM urls
                WHERE url LIKE '%youtube.com/watch?v=%'
                   OR url LIKE '%youtu.be/%'
                   OR url LIKE '%youtube.com/playlist?list=%'
                ORDER BY last_visit_time DESC
                LIMIT ?
                """,
                (limit * 3,),
            )
        )
        con.close()
    except OSError as exc:
        logger.info("event=play_library_history_failed detail=%s", type(exc).__name__)
        return []
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

    out: list[HistoryClip] = []
    seen: set[str] = set()
    for title, url, visits, last in rows:
        vid = _video_id(url) or url
        if vid in seen:
            continue
        if not _looks_like_music(title or "", url or ""):
            continue
        seen.add(vid)
        # Chromium timestamp → rough unix (enough for ordering).
        last_unix = float(last or 0) / 1_000_000 - 11644473600
        out.append(
            HistoryClip(
                title=title or "",
                url=url,
                visit_count=int(visits or 1),
                last_visit=last_unix,
                video_id=_video_id(url),
            )
        )
        if len(out) >= limit:
            break
    return out


def _read_play_memory(limit: int = 40) -> list[HistoryClip]:
    path = play_memory_path()
    if not path.is_file():
        return []
    clips: list[HistoryClip] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in reversed(lines[-200:]):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        url = str(obj.get("url") or "")
        title = str(obj.get("title") or obj.get("query") or "")
        if not url or _is_short_or_clip(title, url):
            continue
        clips.append(

            HistoryClip(
                title=title,
                url=url,
                visit_count=int(obj.get("count") or 1),
                last_visit=float(obj.get("ts") or 0),
                video_id=_video_id(url),
            )
        )
        if len(clips) >= limit:
            break
    return clips


def record_play(*, url: str, title: str = "", query: str = "") -> None:
    if not url or (
        "youtube" not in url.casefold() and "youtu.be" not in url.casefold()
    ):
        return
    if _is_short_or_clip(title, url):
        return
    path = play_memory_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "url": url,
                        "title": title,
                        "query": query,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass


def current_youtube_video_id() -> str | None:
    try:
        from .platform.linux.brave_cdp import _eval_youtube
    except Exception:
        return None
    res = _eval_youtube(
        "({href: location.href, title: document.title})",
        await_promise=False,
    )
    if not isinstance(res, dict):
        return None
    return _video_id(str(res.get("href") or "")) or None


def match_history_for_query(query: str) -> SiteTarget | None:
    """If the spoken title matches a recently watched music video, use that URL."""
    tokens = [
        t
        for t in re.findall(r"[a-z0-9]+", (query or "").casefold())
        if t
        not in {
            "the",
            "a",
            "an",
            "song",
            "gana",
            "gaana",
            "full",
            "video",
            "official",
            "from",
            "film",
            "movie",
            "youtube",
        }
        and len(t) > 2
    ]
    if not tokens:
        return None
    qfold = " ".join((query or "").casefold().split())
    clips = _read_play_memory() + _read_browser_youtube_history()
    best: HistoryClip | None = None
    best_score = 0
    for clip in clips:
        if _is_short_or_clip(clip.title, clip.url):
            continue
        title = clip.title.casefold()
        # Our own SiteTarget labels ("YouTube: <query>") are not real titles.
        if title.startswith("youtube:"):
            continue
        title_core = title.replace(" - youtube", "").strip()
        if title_core == qfold:
            continue
        score = sum(1 for t in tokens if t in title)
        if score != len(tokens):
            continue
        # One-token matches ("malang") are too loose — need a full-track title.
        if len(tokens) == 1 and not re.search(
            r"\b(full\s+song|lyrics|official|music\s+video|ost)\b",
            title,
        ):
            continue
        if best is None or score > best_score or clip.visit_count >= best.visit_count:
            best = clip
            best_score = score
    if best is None or best_score < max(1, len(tokens)):
        return None
    label = best.title.split(" - YouTube")[0].strip() or query
    logger.info(
        "event=play_library_match query=%r title=%r",
        query,
        label[:80],
    )
    return SiteTarget(f"YouTube: {label}", best.url, "brave")


def pick_listening_target(
    *,
    prefer_playlist: bool = False,
    exclude_video_ids: set[str] | None = None,
) -> SiteTarget | None:
    """Choose a liked/recent music video or mix for vague play / playlist change."""
    exclude = set(exclude_video_ids or ())
    current = current_youtube_video_id()
    if current:
        exclude.add(current)

    clips = _read_play_memory() + _read_browser_youtube_history()
    if not clips:
        return None

    def score(clip: HistoryClip) -> tuple:
        url_l = clip.url.casefold()
        title_l = clip.title.casefold()
        is_mix = (
            "list=rd" in url_l
            or "playlist" in title_l
            or "mashup" in title_l
            or "pov:" in title_l
            or "collection" in title_l
            or "best of" in title_l
        )
        music_bonus = 2 if _looks_like_music(clip.title, clip.url) else 0
        mix_bonus = 5 if prefer_playlist and is_mix else (1 if is_mix else 0)
        return (mix_bonus + music_bonus, clip.visit_count, clip.last_visit)

    ranked = sorted(
        (c for c in clips if c.video_id not in exclude and c.url),
        key=score,
        reverse=True,
    )
    if not ranked:
        # Nothing left to exclude against — allow current's neighbors.
        ranked = sorted(clips, key=score, reverse=True)
    if not ranked:
        return None
    pick = ranked[0]
    label = pick.title.split(" - YouTube")[0].strip() or "your music"
    kind = "mix" if prefer_playlist else "song"
    logger.info(
        "event=play_library_pick kind=%s title=%r url=%s",
        kind,
        label[:80],
        pick.url[:80],
    )
    return SiteTarget(f"YouTube: {label}", pick.url, "brave")
