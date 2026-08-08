"""Control YouTube inside Brave via Chrome DevTools Protocol (no window focus).

Requires Brave started with ``--remote-debugging-port=…`` (this machine uses 9222).
Works while the window is minimized or another app is focused.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import socket
import struct
import time
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("vaani.brave_cdp")

_DEFAULT_PORTS = (9222, 9223, 9224)
_PORT_RE = re.compile(rb"--remote-debugging-port=(\d+)")


def _candidate_base_urls() -> list[str]:
    env = (os.environ.get("VAANI_CDP_URL") or "").strip().rstrip("/")
    urls: list[str] = []
    if env:
        urls.append(env)
    for port in _discover_ports():
        urls.append(f"http://127.0.0.1:{port}")
    for port in _DEFAULT_PORTS:
        urls.append(f"http://127.0.0.1:{port}")
    # Preserve order, drop dupes.
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _discover_ports() -> list[int]:
    ports: list[int] = []
    try:
        for name in os.listdir("/proc"):
            if not name.isdigit():
                continue
            try:
                with open(f"/proc/{name}/cmdline", "rb") as fh:
                    cmdline = fh.read()
            except OSError:
                continue
            low = cmdline.lower()
            if b"brave" not in low and b"chromium" not in low:
                continue
            m = _PORT_RE.search(cmdline)
            if m:
                ports.append(int(m.group(1)))
    except OSError:
        pass
    return ports


def _http_json(url: str, *, timeout: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def list_page_targets(base_url: str | None = None) -> list[dict[str, Any]]:
    bases = [base_url] if base_url else _candidate_base_urls()
    last_err: Exception | None = None
    for base in bases:
        if not base:
            continue
        try:
            tabs = _http_json(f"{base.rstrip('/')}/json")
            if isinstance(tabs, list):
                return [t for t in tabs if isinstance(t, dict) and t.get("type") == "page"]
        except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            continue
    if last_err:
        logger.info("event=cdp_list_failed detail=%s", type(last_err).__name__)
    return []


def _is_youtube_watch(url: str) -> bool:
    u = (url or "").casefold()
    return (
        "youtube.com/watch" in u
        or "youtube.com/shorts" in u
        or "youtu.be/" in u
        or "youtube.com/playlist" in u
        or "music.youtube.com" in u
    )


def _is_youtube_tab(url: str, title: str = "") -> bool:
    """Any YouTube tab — including the homepage (needed for navigate/play)."""
    u = (url or "").casefold()
    t = (title or "").casefold()
    if u.startswith(("chrome://", "brave://", "devtools://", "about:")):
        return False
    return (
        "youtube.com" in u
        or "youtu.be" in u
        or "music.youtube.com" in u
        or t == "youtube"
        or t.endswith(" - youtube")
        or "youtube" in t
    )


def find_youtube_target() -> dict[str, Any] | None:
    pages = list_page_targets()
    tabs = [
        t
        for t in pages
        if _is_youtube_tab(str(t.get("url") or ""), str(t.get("title") or ""))
    ]
    if not tabs:
        return None
    # Prefer a watch/shorts page; otherwise homepage is fine for Page.navigate.
    for t in tabs:
        if _is_youtube_watch(str(t.get("url") or "")):
            return t
    return tabs[0]


class _CdpSocket:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._next_id = 0

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def call(self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
        self._next_id += 1
        mid = self._next_id
        payload: dict[str, Any] = {"id": mid, "method": method}
        if params:
            payload["params"] = params
        _ws_send(self.sock, json.dumps(payload))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = _ws_recv(self.sock, timeout=max(0.05, deadline - time.time()))
            if raw is None:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == mid:
                return obj
        raise TimeoutError(method)


def _ws_connect(url: str) -> _CdpSocket:
    if not url.startswith("ws://"):
        raise ValueError("only ws:// CDP URLs supported")
    hostport, _, path = url[5:].partition("/")
    host, _, port_s = hostport.partition(":")
    port = int(port_s or 80)
    path = "/" + path
    sock = socket.create_connection((host, port), timeout=3.0)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {hostport}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    )
    sock.sendall(req.encode("ascii"))
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("CDP websocket handshake closed")
        buf += chunk
    status = buf.split(b"\r\n", 1)[0]
    if b"101" not in status:
        raise ConnectionError(f"CDP websocket upgrade failed: {status!r}")
    return _CdpSocket(sock)


def _ws_send(sock: socket.socket, payload: str) -> None:
    raw = payload.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    n = len(raw)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(raw))
    sock.sendall(header + masked)


def _ws_recv(sock: socket.socket, timeout: float = 3.0) -> str | None:
    sock.settimeout(timeout)
    try:
        hdr = _recvexact(sock, 2)
    except (OSError, TimeoutError):
        return None
    if hdr is None:
        return None
    opcode = hdr[0] & 0x0F
    masked = (hdr[1] & 0x80) != 0
    n = hdr[1] & 0x7F
    if n == 126:
        ext = _recvexact(sock, 2)
        if ext is None:
            return None
        n = struct.unpack("!H", ext)[0]
    elif n == 127:
        ext = _recvexact(sock, 8)
        if ext is None:
            return None
        n = struct.unpack("!Q", ext)[0]
    mask = _recvexact(sock, 4) if masked else b""
    if masked and mask is None:
        return None
    data = _recvexact(sock, n)
    if data is None:
        return None
    if masked and mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    if opcode == 0x8:
        return None
    if opcode != 0x1:
        return None
    return data.decode("utf-8", "replace")


def _recvexact(sock: socket.socket, n: int) -> bytes | None:
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (OSError, TimeoutError):
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def _eval_youtube(js: str, *, await_promise: bool = False) -> dict[str, Any] | None:
    target = find_youtube_target()
    if target is None:
        logger.info("event=cdp_youtube miss=no_tab")
        return None
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return None
    cdp = None
    try:
        cdp = _ws_connect(str(ws_url))
        res = cdp.call(
            "Runtime.evaluate",
            {
                "expression": js,
                "returnByValue": True,
                "awaitPromise": await_promise,
            },
            timeout=8.0 if await_promise else 5.0,
        )
        value = (
            res.get("result", {})
            .get("result", {})
            .get("value")
        )
        if isinstance(value, dict):
            return value
        return {"ok": False, "raw": value}
    except Exception as exc:
        logger.info("event=cdp_youtube_failed detail=%s", type(exc).__name__)
        return None
    finally:
        if cdp is not None:
            cdp.close()


_NEXT_JS = r"""
(() => {
  const click = (sel) => {
    const el = document.querySelector(sel);
    if (el) { el.click(); return sel; }
    return null;
  };
  let hit = click('a.ytp-next-button') || click('button.ytp-next-button') || click('.ytp-next-button');
  if (hit) return {ok: true, via: hit};
  const p = document.getElementById('movie_player');
  if (p && typeof p.nextVideo === 'function') {
    p.nextVideo();
    return {ok: true, via: 'movie_player.nextVideo'};
  }
  return {ok: false, href: location.href, hasPlayer: !!p};
})()
"""

# YouTube mixes often disable the prev button (CanGoPrevious / ytp-disabled).
# In that case history.back() returns to the prior watch URL in the same tab.
# When prev is enabled, the first click usually only restarts if t > ~2s.
_PREV_JS = r"""
(async () => {
  const before = location.href;
  const el = document.querySelector(
    'a.ytp-prev-button, button.ytp-prev-button, .ytp-prev-button'
  );
  const disabled = !el || el.classList.contains('ytp-disabled')
    || el.getAttribute('aria-disabled') === 'true';
  const v = document.querySelector('video');
  const t = (v && Number.isFinite(v.currentTime)) ? v.currentTime : 0;

  if (!disabled) {
    el.click();
    if (t > 2.5) {
      await new Promise((r) => setTimeout(r, 180));
      el.click();
    }
    await new Promise((r) => setTimeout(r, 500));
    if (location.href !== before) {
      return {ok: true, via: 'prev-button', double: t > 2.5, after: location.href};
    }
  }

  if (history.length > 1) {
    history.back();
    await new Promise((r) => setTimeout(r, 1100));
    return {
      ok: location.href !== before,
      via: 'history.back',
      before,
      after: location.href,
      disabled,
    };
  }
  return {ok: false, via: null, disabled, href: before};
})()
"""

_PAUSE_JS = r"""
(() => {
  const v = document.querySelector('video');
  if (v && !v.paused) { v.pause(); return {ok: true, via: 'video.pause'}; }
  const p = document.getElementById('movie_player');
  if (p && typeof p.pauseVideo === 'function') { p.pauseVideo(); return {ok: true, via: 'pauseVideo'}; }
  const btn = document.querySelector('button.ytp-play-button');
  if (btn && (btn.getAttribute('aria-label') || '').toLowerCase().includes('pause')) {
    btn.click(); return {ok: true, via: 'play-button'};
  }
  return {ok: false};
})()
"""

_PLAY_JS = r"""
(() => {
  const v = document.querySelector('video');
  if (v && v.paused) { v.play(); return {ok: true, via: 'video.play'}; }
  const p = document.getElementById('movie_player');
  if (p && typeof p.playVideo === 'function') { p.playVideo(); return {ok: true, via: 'playVideo'}; }
  const btn = document.querySelector('button.ytp-play-button');
  if (btn && (btn.getAttribute('aria-label') || '').toLowerCase().includes('play')) {
    btn.click(); return {ok: true, via: 'play-button'};
  }
  return {ok: false};
})()
"""

_PLAY_PAUSE_JS = r"""
(() => {
  const v = document.querySelector('video');
  if (v) {
    if (v.paused) { v.play(); return {ok: true, via: 'video.play'}; }
    v.pause(); return {ok: true, via: 'video.pause'};
  }
  const btn = document.querySelector('button.ytp-play-button');
  if (btn) { btn.click(); return {ok: true, via: 'play-button'}; }
  return {ok: false};
})()
"""


def _seek_js(seconds: float) -> str:
    # seconds may be negative.
    return f"""
(() => {{
  const delta = {float(seconds)};
  const v = document.querySelector('video');
  if (v && Number.isFinite(v.currentTime)) {{
    v.currentTime = Math.max(0, v.currentTime + delta);
    return {{ok: true, via: 'video.currentTime'}};
  }}
  const p = document.getElementById('movie_player');
  if (p && typeof p.seekTo === 'function' && typeof p.getCurrentTime === 'function') {{
    p.seekTo(Math.max(0, p.getCurrentTime() + delta), true);
    return {{ok: true, via: 'seekTo'}};
  }}
  return {{ok: false}};
}})()
"""


def youtube_cdp_action(method: str, *, times: int = 1) -> bool:
    """Run a transport action on the active YouTube tab via CDP.

    ``method``: next_track | previous_track | pause | play | play_pause |
    seek_forward | seek_backward
    """
    times = max(1, int(times))
    if method == "next_track":
        js = _NEXT_JS
    elif method == "previous_track":
        js = _PREV_JS
    elif method == "pause":
        js = _PAUSE_JS
    elif method == "play":
        js = _PLAY_JS
    elif method == "play_pause":
        js = _PLAY_PAUSE_JS
    elif method == "seek_forward":
        js = _seek_js(10.0 * times)
    elif method == "seek_backward":
        js = _seek_js(-10.0 * times)
    else:
        return False

    await_promise = method == "previous_track"
    result = _eval_youtube(js, await_promise=await_promise)
    ok = bool(result and result.get("ok"))
    logger.info(
        "event=cdp_youtube_action method=%s ok=%s via=%s double=%s",
        method,
        int(ok),
        (result or {}).get("via"),
        (result or {}).get("double"),
    )
    return ok


def cdp_available() -> bool:
    return find_youtube_target() is not None


_AUTOPLAY_JS = r"""
(async () => {
  const tryPlay = () => {
    const v = document.querySelector('video');
    const p = document.getElementById('movie_player');
    if (p && typeof p.playVideo === 'function') {
      try { p.playVideo(); } catch (e) {}
    }
    if (v) {
      const out = v.play();
      if (out && typeof out.then === 'function') {
        return out.then(() => !v.paused).catch(() => !v.paused);
      }
      return Promise.resolve(!v.paused);
    }
    const btn = document.querySelector('button.ytp-large-play-button, button.ytp-play-button');
    if (btn) { btn.click(); return Promise.resolve(true); }
    return Promise.resolve(false);
  };
  for (let i = 0; i < 24; i++) {
    const ok = await tryPlay();
    const v = document.querySelector('video');
    if (ok || (v && !v.paused && v.currentTime > 0)) {
      return {ok: true, href: location.href, t: v ? v.currentTime : 0};
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return {ok: false, href: location.href};
})()
"""


def navigate_youtube_url(url: str, *, autoplay: bool = True) -> bool:
    """Load a YouTube URL in the existing YouTube tab via CDP (no focus needed)."""
    if not url or ("youtube" not in url.casefold() and "youtu.be" not in url.casefold()):
        return False
    target = find_youtube_target()
    if target is None:
        logger.info("event=cdp_navigate miss=no_tab")
        return False
    ws_url = target.get("webSocketDebuggerUrl")
    if not ws_url:
        return False
    cdp = None
    try:
        cdp = _ws_connect(str(ws_url))
        res = cdp.call("Page.navigate", {"url": url}, timeout=8.0)
        if res.get("error"):
            logger.info("event=cdp_navigate_failed detail=error")
            return False
        played = False
        if autoplay:
            play_res = cdp.call(
                "Runtime.evaluate",
                {
                    "expression": _AUTOPLAY_JS,
                    "returnByValue": True,
                    "awaitPromise": True,
                },
                timeout=12.0,
            )
            value = (
                play_res.get("result", {})
                .get("result", {})
                .get("value")
            )
            played = bool(isinstance(value, dict) and value.get("ok"))
            if not played:
                # One more shot after the player finishes hydrating.
                time.sleep(0.8)
                play_res = cdp.call(
                    "Runtime.evaluate",
                    {
                        "expression": _AUTOPLAY_JS,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                    timeout=12.0,
                )
                value = (
                    play_res.get("result", {})
                    .get("result", {})
                    .get("value")
                )
                played = bool(isinstance(value, dict) and value.get("ok"))
        logger.info("event=cdp_navigate ok=1 played=%s", int(played))
        return True
    except Exception as exc:
        logger.info("event=cdp_navigate_failed detail=%s", type(exc).__name__)
        return False
    finally:
        if cdp is not None:
            cdp.close()


def ensure_youtube_playing() -> bool:
    """Force play on the current YouTube tab (after a keys-based navigate)."""
    result = _eval_youtube(_AUTOPLAY_JS, await_promise=True)
    ok = bool(result and result.get("ok"))
    logger.info("event=cdp_ensure_play ok=%s", int(ok))
    return ok
