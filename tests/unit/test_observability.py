import logging
from vaani.observability import _redact, configure_logging, exception_category, sanitize
def test_sanitizes_key():
    assert "secret" not in sanitize("api_key=secret")
    assert exception_category(TimeoutError()) == "timeout"

def test_stream_redact_keeps_key_name():
    assert _redact("api_key=supersecret") == "api_key=[REDACTED]"
    assert _redact("password: hunter2") == "password=[REDACTED]"
    assert "hunter2" not in _redact("token=hunter2")

def test_file_and_debug_stderr_redact(tmp_path, capsys):
    logger = configure_logging(tmp_path, debug=True)
    logger.info("api_key=CANARY_KEY transcript=CANARY_TRANSCRIPT")
    for h in logger.handlers: h.flush()
    assert "CANARY_KEY" not in (tmp_path / "vaani.log").read_text()
    assert "CANARY_TRANSCRIPT" not in (tmp_path / "vaani.log").read_text()
    assert "CANARY_KEY" not in capsys.readouterr().err

def test_debug_stderr_absent_when_disabled(tmp_path, capsys):
    configure_logging(tmp_path, debug=False).info("normal")
    assert capsys.readouterr().err == ""

def test_canary_boundaries():
    canary = "CANARY_KEY"
    values = ["notify api_key=CANARY_KEY", "--token=CANARY_KEY", "report transcript=CANARY_TRANSCRIPT", "diff api_key=CANARY_KEY"]
    assert all(canary not in sanitize(v) for v in values)
