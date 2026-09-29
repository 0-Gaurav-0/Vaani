from pathlib import Path
from types import SimpleNamespace

from vaani.app_typing import parse_type_request, wait_for_app_window
from vaani.controller import Controller
from vaani.delivery import DeliveryStatus
from vaani.projects import ProjectIndex, parse_editor_request


# ---- parsing ----------------------------------------------------------------


def test_parse_editor_requests():
    cases = {
        "open vaani project in vs code": ("vaani project", "code"),
        "open trueforge in cursor": ("trueforge", "cursor"),
        "flow agent ko cursor mein kholo": ("flow agent", "cursor"),
        "hermes agent vs code me open karo": ("hermes agent", "code"),
        "open buzz repo with zed": ("buzz repo", "zed"),
        "open api navbar project": ("api navbar", None),
    }
    for phrase, (proj, editor) in cases.items():
        req = parse_editor_request(phrase)
        assert req is not None, phrase
        assert req.project == proj
        assert (req.editor.key if req.editor else None) == editor
    for phrase in ("open vs code", "open downloads folder", "open gmail in brave", "play lofi"):
        assert parse_editor_request(phrase) is None, phrase


def test_parse_type_requests():
    assert parse_type_request("open text editor and write buy milk").text == "buy milk"
    assert parse_type_request("open notes and type: meeting at 5 pm").text == "meeting at 5 pm"
    r = parse_type_request("notepad kholo aur usme buy milk likh do")
    assert (r.app, r.text) == ("notepad", "buy milk")
    assert parse_type_request("text editor kholo aur likh do ki mujhe call karna hai").text == "mujhe call karna hai"
    assert parse_type_request("open vs code") is None
    assert parse_type_request("write hello world") is None


def test_project_index_fuzzy_and_prefers_real_projects(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    root = tmp_path / "code"
    (root / "Saleshandy-brain" / ".git").mkdir(parents=True)
    (root / "saleshandy-notes").mkdir()
    (root / "API Navbaar").mkdir()
    (root / "API Navbaar" / "package.json").write_text("{}")
    (root / "node_modules" / "saleshandy-brain").mkdir(parents=True)
    idx = ProjectIndex([(root, 3)])
    assert idx.find("sales handy brain").path == root / "Saleshandy-brain"
    assert idx.find("api navbar").path == root / "API Navbaar"
    assert idx.find("totally unrelated words") is None


def test_project_alias_file_wins(tmp_path, monkeypatch):
    import json

    target = tmp_path / "somewhere" / "deep"
    target.mkdir(parents=True)
    (tmp_path / "cfg" / "vaani").mkdir(parents=True)
    (tmp_path / "cfg" / "vaani" / "projects.json").write_text(json.dumps({"work": str(target)}))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert ProjectIndex([(tmp_path / "empty", 2)]).find("work").path == target


def test_wait_for_app_window_needs_matching_class():
    seq = iter([(1, "brave-browser"), (1, "brave-browser"), (2, "gedit org.gnome.gedit"), (2, "gedit org.gnome.gedit")])
    watcher = SimpleNamespace(active=lambda: next(seq, (2, "gedit")))
    t = [0.0]
    assert wait_for_app_window(watcher, ("gedit",), before=1, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))
    never = SimpleNamespace(active=lambda: (1, "brave-browser"))
    t = [0.0]
    assert not wait_for_app_window(never, ("gedit",), before=1, timeout=2, clock=lambda: t[0], sleep=lambda s: t.__setitem__(0, t[0] + s))


# ---- controller -------------------------------------------------------------


class _Rec:
    def start(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def stop(self):
        return SimpleNamespace(path=Path("/tmp/a.wav"), duration_seconds=1)

    def cleanup(self):
        pass


class _Groq:
    def __init__(self, text):
        self.text = text

    def transcribe(self, *a, **k):
        return SimpleNamespace(text=self.text, language="en")


class _History:
    def __init__(self):
        self.rows = []

    def insert(self, **kw):
        self.rows.append(kw)


class _Window:
    def __init__(self):
        self.texts = []

    def show_text(self, t):
        self.texts.append(t)


class _Launcher:
    def __init__(self):
        self.launched = []

    def resolve(self, text):
        return None

    def launch(self, target):
        self.launched.append(target)
        return f"Opened {target.name}."


class _Delivery:
    def __init__(self):
        self.delivered = []
        self.clipboard = SimpleNamespace(set_text=lambda t: self.delivered.append(("clip", t)))

    def deliver(self, text):
        self.delivered.append(("paste", text))
        return DeliveryStatus.PASTE_DISPATCHED


def _run(c):
    assert c.trigger_assistant()
    assert c.stop()
    c._worker.join(3)


def _quiet(monkeypatch):
    monkeypatch.setattr("vaani.controller.is_silent_wav", lambda p: False)


def test_controller_opens_project_in_named_editor(monkeypatch, tmp_path):
    _quiet(monkeypatch)
    opened = []
    monkeypatch.setattr(
        "vaani.controller.project_index",
        lambda: SimpleNamespace(find=lambda s: SimpleNamespace(name="trueforge", path=tmp_path, score=1.0)),
    )
    monkeypatch.setattr(
        "vaani.controller.open_in_editor",
        lambda m, e: opened.append((m.path, e.key)) or f"Opened {m.name} in {e.name}.",
    )
    h, w = _History(), _Window()
    c = Controller(recorder=_Rec(), groq=_Groq("open trueforge in vs code"), delivery=None, history=h,
                   key_provider=lambda: "k", result_window=w, app_launcher=_Launcher())
    _run(c)
    assert opened == [(tmp_path, "code")]
    assert h.rows[-1]["cleanup_status"] == "project_action"


def test_controller_unknown_project_with_editor_reports_not_found(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr("vaani.controller.project_index", lambda: SimpleNamespace(find=lambda s: None))
    h, w = _History(), _Window()
    launcher = _Launcher()
    c = Controller(recorder=_Rec(), groq=_Groq("open zorblax in cursor"), delivery=None, history=h,
                   key_provider=lambda: "k", result_window=w, app_launcher=launcher)
    _run(c)
    assert "Couldn't find a project" in w.texts[-1]
    assert launcher.launched == []  # did not just open bare Cursor


def test_controller_open_app_and_type_pastes_after_focus(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr("vaani.controller.wait_for_app_window", lambda *a, **k: True)
    delivery, launcher, h = _Delivery(), _Launcher(), _History()
    c = Controller(recorder=_Rec(), groq=_Groq("open text editor and write buy milk"), delivery=delivery,
                   history=h, key_provider=lambda: "k", result_window=_Window(), app_launcher=launcher)
    c.window_watcher_factory = lambda: SimpleNamespace(active=lambda: (1, "brave"), close=lambda: None)
    _run(c)
    assert launcher.launched[0].name == "Text Editor"
    assert delivery.delivered == [("paste", "buy milk")]
    assert h.rows[-1]["cleanup_status"] == "app_type_action"


def test_controller_open_app_and_type_never_types_into_wrong_window(monkeypatch):
    _quiet(monkeypatch)
    monkeypatch.setattr("vaani.controller.wait_for_app_window", lambda *a, **k: False)
    delivery, w = _Delivery(), _Window()
    c = Controller(recorder=_Rec(), groq=_Groq("open text editor and write buy milk"), delivery=delivery,
                   history=_History(), key_provider=lambda: "k", result_window=w, app_launcher=_Launcher())
    c.window_watcher_factory = lambda: SimpleNamespace(active=lambda: (1, "brave"), close=lambda: None)
    _run(c)
    assert delivery.delivered == [("clip", "buy milk")]
    assert "press Ctrl+V" in w.texts[-1]
