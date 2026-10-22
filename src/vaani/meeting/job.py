"""On-disk meeting job state (``job.json``); every step resumes from here."""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

JOB_FILE = "job.json"

# Ordered pipeline states. A worker advances one state at a time and saves.
RECORDING = "recording"
RECORDED = "recorded"
PREPARED = "prepared"
TRANSCRIBED = "transcribed"
WRITTEN = "written"
FAILED = "failed"
STATES = (RECORDING, RECORDED, PREPARED, TRANSCRIBED, WRITTEN)


@dataclass
class MeetingJob:
    dir: Path
    state: str = RECORDING
    started: float = 0.0
    stopped: float = 0.0
    output: str = "speaker"  # "speaker" (mic only) or "headphones" (mix both)
    pids: list[int] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)
    error: str = ""
    retry_after: float = 0.0
    attempts: int = 0

    @property
    def path(self) -> Path:
        return self.dir / JOB_FILE

    def save(self) -> None:
        data = asdict(self)
        data["dir"] = str(self.dir)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @classmethod
    def load(cls, directory: Path) -> "MeetingJob":
        data = json.loads((Path(directory) / JOB_FILE).read_text(encoding="utf-8"))
        data["dir"] = Path(directory)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def advance(self, state: str, **changes) -> None:
        self.state = state
        self.error = ""
        for key, value in changes.items():
            setattr(self, key, value)
        self.save()

    def fail(self, error: str, *, retry_after: float = 0.0) -> None:
        """Record an error without losing progress; ``retry_after`` = epoch seconds."""
        self.error = error
        self.retry_after = retry_after
        self.attempts += 1
        self.save()

    def due(self, now: float | None = None) -> bool:
        return self.state not in (RECORDING, WRITTEN, FAILED) and (now or time.time()) >= self.retry_after


def find_jobs(root: Path) -> list[MeetingJob]:
    jobs: list[MeetingJob] = []
    if not root.is_dir():
        return jobs
    for entry in sorted(root.iterdir()):
        if (entry / JOB_FILE).is_file():
            try:
                jobs.append(MeetingJob.load(entry))
            except (OSError, ValueError, TypeError):
                continue
    return jobs
