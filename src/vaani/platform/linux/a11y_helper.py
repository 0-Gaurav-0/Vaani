"""AT-SPI helper for computer use (runs under /usr/bin/python3 — needs gi).

JSON lines on stdin → JSON lines on stdout. One request, one reply.

  {"cmd": "snapshot"}                → {"ok", "app", "title", "bbox", "elements": [...]}
  {"cmd": "click", "id": 3}          → {"ok", "via": "action"|"xtest"}
  {"cmd": "focus", "id": 3}          → {"ok"}
  {"cmd": "screenshot", "max": 1280} → {"ok", "png_b64", "scale", "bbox"}

Element ids index the most recent snapshot. Only the *active* window is read
or captured — never other windows or the whole screen.
"""
from __future__ import annotations

import base64
import json
import sys

INTERACTIVE = {
    "push button", "toggle button", "check box", "radio button", "menu item", "check menu item",
    "radio menu item", "menu", "link", "entry", "text", "password text", "combo box", "list item",
    "page tab", "tree item", "table cell", "spin button", "slider", "icon", "tool bar button",
    "document text", "terminal", "heading", "label",
}
MAX_ELEMENTS = 160
MAX_NODES = 6000


def _atspi():
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    return Atspi


class Helper:
    def __init__(self) -> None:
        self.Atspi = _atspi()
        self.nodes: list = []

    # ---- window --------------------------------------------------------
    def _x11_active(self):
        """(pid, title, [x, y, w, h]) of the window X11 says is in front.

        AT-SPI's ACTIVE state is not trustworthy on its own: Electron/Chromium
        apps keep reporting ACTIVE after losing focus. X11 is the authority.
        """
        try:
            from Xlib import X, display as xdisplay

            d = getattr(self, "_dpy", None) or xdisplay.Display()
            self._dpy = d
            root = d.screen().root
            wid = root.get_full_property(d.intern_atom("_NET_ACTIVE_WINDOW"), X.AnyPropertyType)
            wid = int(wid.value[0]) if wid is not None and wid.value else 0
            if not wid:
                return None
            win = d.create_resource_object("window", wid)
            pid_prop = win.get_full_property(d.intern_atom("_NET_WM_PID"), X.AnyPropertyType)
            pid = int(pid_prop.value[0]) if pid_prop is not None and pid_prop.value else 0
            name = win.get_full_property(d.intern_atom("_NET_WM_NAME"), 0)
            title = name.value.decode("utf-8", "replace") if name is not None and isinstance(name.value, bytes) else ""
            geo = win.get_geometry()
            pos = win.translate_coords(root, 0, 0)
            box = [-int(pos.x), -int(pos.y), int(geo.width), int(geo.height)]
            return pid, title, box
        except Exception:
            return None

    def _active_frame(self):
        A = self.Atspi
        front = self._x11_active()
        if front is None:
            return None, None
        pid, title, _box = front
        desktop = A.get_desktop(0)
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            try:
                if pid and app.get_process_id() != pid:
                    continue
                wins = [app.get_child_at_index(j) for j in range(app.get_child_count())]
                wins = [w for w in wins if w is not None]
                # Same process may own several windows: prefer the title match.
                for w in wins:
                    if title and " ".join((w.get_name() or "").split()) == " ".join(title.split()):
                        return app, w
                for w in wins:
                    if w.get_state_set().contains(A.StateType.ACTIVE):
                        return app, w
                if wins:
                    return app, wins[0]
            except Exception:
                continue
        return None, None

    def _bbox(self, node):
        try:
            ext = node.get_extents(self.Atspi.CoordType.SCREEN)
            return [int(ext.x), int(ext.y), int(ext.width), int(ext.height)]
        except Exception:
            return None

    # ---- commands ------------------------------------------------------
    def snapshot(self, _req):
        A = self.Atspi
        front = self._x11_active()
        app, win = self._active_frame()
        if win is None:
            if front is None:
                return {"ok": False, "error": "no active window"}
            return {"ok": True, "app": "", "title": front[1][:120], "bbox": front[2], "elements": []}
        wbox = (front[2] if front else None) or self._bbox(win) or [0, 0, 0, 0]
        self.nodes = []
        elements = []
        budget = [MAX_NODES]

        def visit(node, depth):
            if budget[0] <= 0 or depth > 60 or len(elements) >= MAX_ELEMENTS:
                return
            budget[0] -= 1
            try:
                states = node.get_state_set()
                if not states.contains(A.StateType.SHOWING):
                    return
                role = node.get_role_name()
                name = " ".join((node.get_name() or "").split())[:80]
                if role in INTERACTIVE:
                    box = self._bbox(node)
                    editable = states.contains(A.StateType.EDITABLE)
                    usable = name or editable or role in {"entry", "text", "document text", "terminal"}
                    if box and box[2] > 1 and box[3] > 1 and usable and not (role == "label" and not name):
                        self.nodes.append(node)
                        elements.append({
                            "id": len(self.nodes) - 1,
                            "role": role,
                            "name": name,
                            "bbox": box,
                            "focused": states.contains(A.StateType.FOCUSED),
                            "editable": editable,
                        })
                for k in range(min(node.get_child_count(), 400)):
                    visit(node.get_child_at_index(k), depth + 1)
            except Exception:
                return

        visit(win, 0)
        return {
            "ok": True,
            "app": app.get_name() if app else "",
            "title": " ".join((win.get_name() or "").split())[:120],
            "bbox": wbox,
            "elements": elements,
        }

    def _node(self, req):
        idx = int(req.get("id", -1))
        if idx < 0 or idx >= len(self.nodes):
            raise IndexError(f"unknown element id {idx}")
        return self.nodes[idx]

    def click(self, req):
        node = self._node(req)
        try:
            count = node.get_n_actions()
            for k in range(count):
                if node.get_action_name(k).lower() in {"click", "press", "activate", "jump", "toggle", "select"}:
                    if node.do_action(k):
                        return {"ok": True, "via": "action"}
        except Exception:
            pass
        box = self._bbox(node)
        if not box:
            return {"ok": False, "error": "element has no position"}
        return {"ok": True, "via": "xtest", "xy": [box[0] + box[2] // 2, box[1] + box[3] // 2]}

    def focus(self, req):
        node = self._node(req)
        try:
            comp = node.get_component_iface() if hasattr(node, "get_component_iface") else node
            if comp is not None and comp.grab_focus():
                return {"ok": True}
        except Exception:
            pass
        box = self._bbox(node)
        return {"ok": True, "via": "xtest", "xy": [box[0] + box[2] // 2, box[1] + box[3] // 2]} if box else {"ok": False}

    def screenshot(self, req):
        import gi

        gi.require_version("Gdk", "3.0")
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import Gdk, GdkPixbuf

        front = self._x11_active()
        box = front[2] if front else None
        if not box or box[2] < 10 or box[3] < 10:
            return {"ok": False, "error": "no active window to capture"}
        root = Gdk.get_default_root_window()
        x, y, w, h = box
        sw, sh = root.get_width(), root.get_height()
        x, y = max(0, x), max(0, y)
        w, h = min(w, sw - x), min(h, sh - y)
        pb = Gdk.pixbuf_get_from_window(root, x, y, w, h)
        if pb is None:
            return {"ok": False, "error": "capture failed"}
        limit = int(req.get("max", 1280))
        scale = min(1.0, limit / float(max(w, h)))
        if scale < 1.0:
            pb = pb.scale_simple(max(1, int(w * scale)), max(1, int(h * scale)), GdkPixbuf.InterpType.BILINEAR)
        ok, buf = pb.save_to_bufferv("png", [], [])
        if not ok:
            return {"ok": False, "error": "encode failed"}
        return {"ok": True, "png_b64": base64.b64encode(buf).decode(), "scale": scale, "bbox": [x, y, w, h]}


def main() -> int:
    helper = Helper()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            fn = getattr(helper, str(req.get("cmd")), None)
            if fn is None or str(req.get("cmd")).startswith("_"):
                out = {"ok": False, "error": "unknown command"}
            else:
                out = fn(req)
        except Exception as exc:  # never die mid-task
            out = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
