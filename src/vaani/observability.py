import logging
import os
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

KEY_RE = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|groq[_-]?api[_-]?key|token)\s*[:=]\s*[^\s,;]+")
CANARY_RE = re.compile(r"CANARY_TRANSCRIPT")
PAYLOAD_RE = re.compile(r"(?i)\b(?:transcript|raw_text|final_text|clipboard(?:_text)?)\b\s*(?:[:=]\s*|\s+)([^\n,;]+)")
def sanitize(value: object) -> str:
    text = KEY_RE.sub("[REDACTED]", str(value))
    text = PAYLOAD_RE.sub(lambda match: match.group(0)[:match.start(1)-match.start()] + "[REDACTED]", text)
    return CANARY_RE.sub("[REDACTED]", text)

class SanitizingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = sanitize(record.getMessage())
        record.args = ()
        return True

def configure_logging(log_dir: Path, *, debug: bool = False) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        log_dir.chmod(0o700)
    except OSError:
        pass
    logger = logging.getLogger("vaani")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False
    handler = RotatingFileHandler(log_dir / "vaani.log", maxBytes=1024 * 1024, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(SanitizingFilter())
    logger.addHandler(handler)
    if debug:
        stderr = logging.StreamHandler(sys.stderr)
        stderr.setLevel(logging.DEBUG)
        stderr.addFilter(SanitizingFilter())
        stderr.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(stderr)
    return logger

def exception_category(exc: BaseException) -> str:
    name = type(exc).__name__.lower()
    if "timeout" in name: return "timeout"
    if "permission" in name: return "permission"
    if "connection" in name or "network" in name: return "network"
    return "internal"
