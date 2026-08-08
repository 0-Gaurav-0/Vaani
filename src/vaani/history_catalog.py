"""Efficient local catalog of Chrome/Brave history for fast voice open.

Sync copies Chromium History DBs, queries the last 30 days, and writes a small
owner-only SQLite catalog. Assistant open matches against that catalog and
prefers the source browser. No network, no agent, no always-on watcher.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .sites import SiteTarget

LOGGER = logging.getLogger("vaani")

RECENT_DAYS = 30
MAIN_MIN_VISITS = 5
MAIN_STALE_DAYS = 90

# Chromium stores visit times as µs since 1601-01-01 UTC.
_CHROMIUM_EPOCH_OFFSET_S = 11644473600

_TRACKING_QUERY_RE = re.compile(
    r"^(utm_|fbclid|gclid|mc_|igshid|vero_id|ref$|_ga|_gl)",
    re.I,
)

_DEFAULT_EXCLUDE_SUBSTR = (
    "chrome-extension://",
    "chrome://",
    "brave://",
    "edge://",
    "devtools://",
    "view-source:",
    "file://",
    "javascript:",
    "data:",
    "blob:",
    "about:",
    "://localhost",
    "127.0.0.1",
    "accounts.google.com",
    "/signin",
    "/login",
    "oauth",
)

_INTERESTING_PATH_RE = re.compile(
    r"/(dashboard|dashboards|project|projects|collection|question|card|board|doc|docs|wiki)/",
    re.I,
)


@dataclass(frozen=True)
class HistorySource:
    browser: str
    history_path: Path


def default_catalog_path() -> Path:
    override = (os.environ.get("VAANI_HISTORY_CATALOG") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "vaani" / "history_catalog.sqlite"


def discover_history_sources() -> tuple[HistorySource, ...]:
    home = Path.home()
    candidates = (
        HistorySource("brave", home / ".config/BraveSoftware/Brave-Browser/Default/History"),
        HistorySource("chrome", home / ".config/google-chrome/Default/History"),
        HistorySource("chrome", home / ".config/chromium/Default/History"),
    )
    found: list[HistorySource] = []
    seen: set[Path] = set()
    for src in candidates:
        try:
            resolved = src.history_path.resolve()
        except OSError:
            continue
        if resolved in seen or not src.history_path.is_file():
            continue
        seen.add(resolved)
        found.append(src)
    return tuple(found)


def _chromium_micros_cutoff(days: int, *, now: float | None = None) -> int:
    now_s = time.time() if now is None else now
    unix_cut = now_s - (days * 86400)
    return int((unix_cut + _CHROMIUM_EPOCH_OFFSET_S) * 1_000_000)


def _chromium_micros_to_unix(micros: int) -> float:
    return (micros / 1_000_000) - _CHROMIUM_EPOCH_OFFSET_S


def normalize_url(url: str) -> str | None:
    """Return a safe https?/http URL with tracking noise stripped, or None."""
    raw = (url or "").strip()
    if not raw or len(raw) > 2048:
        return None
    lower = raw.casefold()
    for bad in _DEFAULT_EXCLUDE_SUBSTR:
        if bad in lower:
            return None
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    scheme = (parsed.scheme or "").casefold()
    if scheme not in {"http", "https"}:
        return None
    host = (parsed.hostname or "").casefold()
    if not host or host in {"localhost"}:
        return None
    # Drop userinfo / fragments; filter tracking query params.
    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not _TRACKING_QUERY_RE.match(k)
    ]
    query = urlencode(query_pairs, doseq=True)
    path = parsed.path or "/"
    # Collapse trivial trailing slash on root.
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    cleaned = urlunparse((scheme, host, path, "", query, ""))
    return cleaned


def _is_interesting_path(path: str) -> bool:
    if not path or path == "/":
        return False
    if _INTERESTING_PATH_RE.search(path):
        return True
    # Non-root path with at least two segments often means a deep link.
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


def _copy_history_db(src: Path) -> Path:
    fd, dest = tempfile.mkstemp(prefix="vaani-hist-", suffix=".sqlite")
    os.close(fd)
    dest_path = Path(dest)
    try:
        shutil.copy2(src, dest_path)
    except OSError:
        # Fallback: sqlite backup via URI readonly (may fail if locked hard).
        dest_path.unlink(missing_ok=True)
        fd, dest = tempfile.mkstemp(prefix="vaani-hist-", suffix=".sqlite")
        os.close(fd)
        dest_path = Path(dest)
        src_uri = src.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(src_uri, uri=True) as src_con:
            with sqlite3.connect(dest_path) as dst_con:
                src_con.backup(dst_con)
    return dest_path


def _read_visits(copy_path: Path, *, cutoff_micros: int) -> list[tuple[str, str, int, int]]:
    """Return (url, title, visit_count_in_window, last_visit_micros)."""
    rows: list[tuple[str, str, int, int]] = []
    with sqlite3.connect(copy_path) as con:
        con.execute("PRAGMA query_only=ON")
        cur = con.execute(
            """
            SELECT u.url,
                   COALESCE(u.title, '') AS title,
                   COUNT(v.id) AS visits,
                   MAX(v.visit_time) AS last_visit
            FROM urls u
            JOIN visits v ON v.url = u.id
            WHERE v.visit_time >= ?
              AND IFNULL(u.hidden, 0) = 0
            GROUP BY u.id
            """,
            (cutoff_micros,),
        )
        rows = [(str(r[0]), str(r[1]), int(r[2]), int(r[3])) for r in cur.fetchall()]
    return rows


def _secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass


def _init_catalog(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS entries (
            url TEXT NOT NULL,
            browser TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            host TEXT NOT NULL,
            tier TEXT NOT NULL,
            visit_count INTEGER NOT NULL,
            last_visit REAL NOT NULL,
            PRIMARY KEY (url, browser)
        );
        CREATE INDEX IF NOT EXISTS idx_entries_tier ON entries(tier);
        CREATE INDEX IF NOT EXISTS idx_entries_host ON entries(host);
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def sync_history_catalog(
    *,
    catalog_path: Path | None = None,
    sources: tuple[HistorySource, ...] | None = None,
    recent_days: int = RECENT_DAYS,
    main_min_visits: int = MAIN_MIN_VISITS,
    now: float | None = None,
) -> dict[str, int]:
    """Rebuild catalog from browser history. Returns simple counters."""
    now_s = time.time() if now is None else now
    catalog = catalog_path or default_catalog_path()
    _secure_mkdir(catalog.parent)
    sources = sources if sources is not None else discover_history_sources()
    cutoff = _chromium_micros_cutoff(recent_days, now=now_s)
    main_stale_before = now_s - (MAIN_STALE_DAYS * 86400)

    aggregated: dict[tuple[str, str], dict[str, object]] = {}
    scanned = 0
    for src in sources:
        if not src.history_path.is_file():
            continue
        copy_path: Path | None = None
        try:
            copy_path = _copy_history_db(src.history_path)
            for url, title, visits, last_micros in _read_visits(copy_path, cutoff_micros=cutoff):
                scanned += 1
                cleaned = normalize_url(url)
                if cleaned is None:
                    continue
                parsed = urlparse(cleaned)
                path = parsed.path or "/"
                # Prefer deep links when interesting; else keep host root for bulk noise.
                if not _is_interesting_path(path) and visits < main_min_visits:
                    cleaned = urlunparse((parsed.scheme, parsed.hostname or "", "/", "", "", ""))
                    path = "/"
                key = (cleaned, src.browser)
                prev = aggregated.get(key)
                last_unix = _chromium_micros_to_unix(last_micros)
                title_clean = " ".join((title or "").split())[:180]
                if prev is None:
                    aggregated[key] = {
                        "title": title_clean,
                        "host": (parsed.hostname or "").casefold(),
                        "visit_count": visits,
                        "last_visit": last_unix,
                    }
                else:
                    prev["visit_count"] = int(prev["visit_count"]) + visits
                    if last_unix >= float(prev["last_visit"]):
                        prev["last_visit"] = last_unix
                        if title_clean:
                            prev["title"] = title_clean
        except Exception as exc:
            LOGGER.warning(
                "event=history_sync_source_failed browser=%s detail=%s",
                src.browser,
                type(exc).__name__,
            )
        finally:
            if copy_path is not None:
                try:
                    copy_path.unlink(missing_ok=True)
                except OSError:
                    pass

    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix="vaani-catalog-", suffix=".sqlite", dir=str(catalog.parent)
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_name)
    main_n = recent_n = 0
    try:
        with sqlite3.connect(tmp_path) as con:
            _init_catalog(con)
            for (url, browser), data in aggregated.items():
                visits = int(data["visit_count"])
                last_visit = float(data["last_visit"])
                if last_visit < (now_s - recent_days * 86400):
                    continue
                tier = "main" if visits >= main_min_visits else "recent"
                # Drop stale main-tier if somehow older than stale window.
                if tier == "main" and last_visit < main_stale_before:
                    tier = "recent"
                if tier == "main":
                    main_n += 1
                else:
                    recent_n += 1
                con.execute(
                    """
                    INSERT INTO entries(url, browser, title, host, tier, visit_count, last_visit)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        url,
                        browser,
                        str(data["title"]),
                        str(data["host"]),
                        tier,
                        visits,
                        last_visit,
                    ),
                )
            con.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES ('synced_at', ?)",
                (str(int(now_s)),),
            )
            con.commit()
        os.replace(tmp_path, catalog)
        try:
            os.chmod(catalog, 0o600)
        except OSError:
            pass
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

    LOGGER.info(
        "event=history_sync_done sources=%s scanned=%s main=%s recent=%s path=%s",
        len(sources),
        scanned,
        main_n,
        recent_n,
        catalog,
    )
    return {
        "sources": len(sources),
        "scanned": scanned,
        "main": main_n,
        "recent": recent_n,
    }


_STOP_TOKENS = frozenset(
    {
        "open",
        "launch",
        "show",
        "visit",
        "browse",
        "surf",
        "navigate",
        "kholo",
        "khol",
        "dikhao",
        "dikha",
        "jao",
        "chalo",
        "the",
        "and",
        "for",
        "with",
        "www",
        "http",
        "https",
        "com",
        "org",
        "net",
    }
)


def _tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]{3,}", (text or "").casefold())
        if t not in _STOP_TOKENS
    }


def resolve_from_catalog(
    utterance: str,
    *,
    catalog_path: Path | None = None,
    browser_hint: str | None = None,
) -> SiteTarget | None:
    """Match spoken open request against catalog; prefer main over recent."""
    normalized = " ".join((utterance or "").casefold().strip().split())
    if not normalized:
        return None
    catalog = catalog_path or default_catalog_path()
    if not catalog.is_file():
        return None
    spoken_tokens = _tokens(normalized)
    if not spoken_tokens:
        return None

    best: tuple[int, str, str, str] | None = None  # score, name, url, browser
    try:
        # Plain open + query_only (URI mode=ro breaks on some WAL/replace setups).
        with sqlite3.connect(catalog) as con:
            con.execute("PRAGMA query_only=ON")
            cur = con.execute(
                """
                SELECT url, browser, title, host, tier, visit_count
                FROM entries
                ORDER BY CASE tier WHEN 'main' THEN 0 ELSE 1 END,
                         visit_count DESC,
                         last_visit DESC
                LIMIT 2000
                """
            )
            for url, browser, title, host, tier, visit_count in cur:
                if browser_hint and browser != browser_hint:
                    # Still allow if hint missing match entirely later — skip for now
                    # only when hint set: prefer same browser rows by scoring boost.
                    pass
                host_label = host.split(".")[0] if host else ""
                host_hit = bool(host_label and host_label in spoken_tokens)
                # Light stem: "car" ↔ "cars"
                if not host_hit and host_label:
                    host_hit = any(
                        host_label.startswith(tok) or tok.startswith(host_label)
                        for tok in spoken_tokens
                        if len(tok) >= 3
                    )
                title_hits = len(spoken_tokens & _tokens(title))
                if not host_hit and title_hits == 0 and not (
                    host and _contains_host(normalized, host)
                ):
                    continue
                score = title_hits * 12
                if title_hits:
                    score += 15
                if host_hit:
                    score += 30
                if host and _contains_host(normalized, host):
                    score += 40
                if tier == "main":
                    score += 5
                score += min(int(visit_count), 20)
                if browser_hint and browser == browser_hint:
                    score += 15
                name = (title or host or url).strip()[:120] or host
                candidate = (score, name, str(url), str(browser))
                if best is None or candidate[0] > best[0]:
                    best = candidate
    except sqlite3.Error as exc:
        LOGGER.warning("event=history_catalog_read_failed detail=%s", type(exc).__name__)
        return None

    if best is None or best[0] < 25:
        return None
    _score, name, url, browser = best
    if normalize_url(url) is None:
        return None
    return SiteTarget(name, url, browser)


def _contains_host(normalized: str, host: str) -> bool:
    host = host.casefold()
    if host in normalized:
        return True
    # "metabase saleshandy" ↔ metabase.saleshandy.com
    labels = [p for p in host.split(".") if p and p not in {"com", "org", "net", "io", "app"}]
    if not labels:
        return False
    return all(label in normalized for label in labels[:2])


def list_catalog_matches(
    query: str,
    *,
    catalog_path: Path | None = None,
    limit: int = 8,
) -> list[SiteTarget]:
    """Return several catalog matches for 'what sites about X' style asks."""
    normalized = " ".join((query or "").casefold().strip().split())
    if not normalized:
        return []
    catalog = catalog_path or default_catalog_path()
    if not catalog.is_file():
        return []
    tokens = _tokens(normalized)
    out: list[SiteTarget] = []
    try:
        with sqlite3.connect(catalog) as con:
            con.execute("PRAGMA query_only=ON")
            cur = con.execute(
                """
                SELECT url, browser, title, host, visit_count
                FROM entries
                ORDER BY visit_count DESC, last_visit DESC
                LIMIT 4000
                """
            )
            for url, browser, title, host, _visits in cur:
                hay_tokens = _tokens(f"{title} {host}")
                if not (tokens & hay_tokens) and not _contains_host(normalized, host):
                    continue
                if normalize_url(url) is None:
                    continue
                out.append(SiteTarget((title or host)[:120], url, browser))
                if len(out) >= limit:
                    break
    except sqlite3.Error:
        return []
    return out


def main(argv: list[str] | None = None) -> int:
    """CLI: ``vaani-history-sync`` — rebuild the local history catalog."""
    import argparse

    parser = argparse.ArgumentParser(description="Sync Chrome/Brave history into Vaani catalog")
    parser.add_argument(
        "--catalog",
        type=Path,
        default=None,
        help="Catalog sqlite path (default: ~/.local/share/vaani/history_catalog.sqlite)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = sync_history_catalog(catalog_path=args.catalog)
    print(
        f"history_sync sources={stats['sources']} scanned={stats['scanned']} "
        f"main={stats['main']} recent={stats['recent']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
