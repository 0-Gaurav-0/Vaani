"""Tests for efficient browser-history catalog sync + resolve."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from vaani.history_catalog import (
    HistorySource,
    list_catalog_matches,
    normalize_url,
    resolve_from_catalog,
    sync_history_catalog,
)

# Chromium epoch helpers (µs since 1601).
_OFFSET = 11644473600


def _micros(unix_s: float) -> int:
    return int((unix_s + _OFFSET) * 1_000_000)


def _write_chrome_history(path: Path, rows: list[tuple[str, str, int]]) -> None:
    """rows: (url, title, visit_unix)."""
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE urls (
                id INTEGER PRIMARY KEY,
                url TEXT,
                title TEXT,
                visit_count INTEGER DEFAULT 1,
                typed_count INTEGER DEFAULT 0,
                last_visit_time INTEGER,
                hidden INTEGER DEFAULT 0
            );
            CREATE TABLE visits (
                id INTEGER PRIMARY KEY,
                url INTEGER,
                visit_time INTEGER
            );
            """
        )
        for i, (url, title, visit_unix) in enumerate(rows, start=1):
            micros = _micros(visit_unix)
            con.execute(
                "INSERT INTO urls(id, url, title, visit_count, last_visit_time, hidden) "
                "VALUES (?, ?, ?, 1, ?, 0)",
                (i, url, title, micros),
            )
            con.execute(
                "INSERT INTO visits(id, url, visit_time) VALUES (?, ?, ?)",
                (i, i, micros),
            )
        con.commit()


def test_normalize_strips_tracking_and_blocks_schemes():
    assert normalize_url("https://example.com/a?utm_source=x&id=1") == "https://example.com/a?id=1"
    assert normalize_url("javascript:alert(1)") is None
    assert normalize_url("file:///etc/passwd") is None
    assert normalize_url("https://accounts.google.com/signin") is None


def test_sync_and_resolve_prefers_source_browser(tmp_path: Path):
    now = time.time()
    brave_hist = tmp_path / "brave-History"
    chrome_hist = tmp_path / "chrome-History"
    _write_chrome_history(
        brave_hist,
        [
            ("https://metabase.saleshandy.com/dashboard/42-sales", "Sales dashboard", now - 100),
            ("https://metabase.saleshandy.com/dashboard/42-sales", "Sales dashboard", now - 200),
            ("https://metabase.saleshandy.com/dashboard/42-sales", "Sales dashboard", now - 300),
            ("https://metabase.saleshandy.com/dashboard/42-sales", "Sales dashboard", now - 400),
            ("https://metabase.saleshandy.com/dashboard/42-sales", "Sales dashboard", now - 500),
        ],
    )
    # 5 separate visits need 5 visit rows — helper inserts one visit per row.
    # Rebuild with 5 visit rows for same url via direct SQL for visit_count.
    brave_hist.unlink()
    with sqlite3.connect(brave_hist) as con:
        con.executescript(
            """
            CREATE TABLE urls (
                id INTEGER PRIMARY KEY, url TEXT, title TEXT,
                visit_count INTEGER, typed_count INTEGER, last_visit_time INTEGER, hidden INTEGER
            );
            CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER);
            """
        )
        con.execute(
            "INSERT INTO urls VALUES (1, ?, ?, 5, 0, ?, 0)",
            (
                "https://metabase.saleshandy.com/dashboard/42-sales",
                "Sales dashboard",
                _micros(now),
            ),
        )
        for i in range(5):
            con.execute(
                "INSERT INTO visits VALUES (?, 1, ?)",
                (i + 1, _micros(now - i * 10)),
            )
        con.commit()

    _write_chrome_history(
        chrome_hist,
        [("https://cars.example.com/reviews", "Car reviews blog", now - 50)],
    )
    catalog = tmp_path / "catalog.sqlite"
    stats = sync_history_catalog(
        catalog_path=catalog,
        sources=(
            HistorySource("brave", brave_hist),
            HistorySource("chrome", chrome_hist),
        ),
        now=now,
    )
    assert stats["main"] >= 1
    assert stats["recent"] >= 1

    hit = resolve_from_catalog("open metabase sales dashboard", catalog_path=catalog)
    assert hit is not None
    assert hit.browser == "brave"
    assert "dashboard/42" in hit.url

    cars = resolve_from_catalog("open car reviews", catalog_path=catalog)
    assert cars is not None
    assert cars.browser == "chrome"

    listed = list_catalog_matches("cars", catalog_path=catalog)
    assert listed and listed[0].browser == "chrome"


def test_old_visits_excluded(tmp_path: Path):
    now = time.time()
    hist = tmp_path / "History"
    old = now - (40 * 86400)
    _write_chrome_history(hist, [("https://old.example.com/", "Old site", old)])
    catalog = tmp_path / "catalog.sqlite"
    stats = sync_history_catalog(
        catalog_path=catalog,
        sources=(HistorySource("brave", hist),),
        now=now,
    )
    assert stats["main"] + stats["recent"] == 0
