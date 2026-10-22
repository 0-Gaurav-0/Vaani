"""Process recorded meetings: ``python -m vaani.meeting.worker [ROOT]``.

Runs as its own niced process (spawned via ``systemd-run --user`` so Vaani
restarts don't kill it); the main dictation process imports none of this.
Each job advances one saved state at a time and resumes after a crash. A
free-tier 429 pauses the job (``retry_after``); Vaani re-spawns the worker
later. Audio is never deleted on failure.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import sys
import time
from pathlib import Path

from .assemble import assemble, to_markdown
from .gemini import GeminiTranscriber, QuotaError
from .job import FAILED, PREPARED, RECORDED, RECORDING, TRANSCRIBED, WRITTEN, MeetingJob, find_jobs
from .prepare import prepare
from .util import default_root, gemini_key, notify

LOGGER = logging.getLogger("vaani")

AUDIO_RETENTION_S = 30 * 24 * 3600
MAX_ATTEMPTS = 8


def run_job(job: MeetingJob, transcriber: GeminiTranscriber | None) -> None:
    if job.state == RECORDED:
        parts = prepare(job.dir, mix_system=job.output == "headphones")
        job.advance(PREPARED, parts=parts)
        LOGGER.info("event=meeting_prepared dir=%s parts=%s", job.dir.name, len(parts))
    if job.state == PREPARED:
        if transcriber is None:
            raise RuntimeError("GEMINI_API_KEY missing")
        for part in job.parts:
            out = job.dir / "parts" / (Path(part["file"]).stem + ".json")
            if out.is_file():
                continue  # done in an earlier run; never spend quota twice
            body = transcriber.transcribe(job.dir / "parts" / part["file"])
            tmp = out.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, out)
        job.advance(TRANSCRIBED)
    if job.state == TRANSCRIBED:
        bodies = [
            json.loads((job.dir / "parts" / (Path(p["file"]).stem + ".json")).read_text(encoding="utf-8"))
            for p in job.parts
        ]
        turns = assemble(job.parts, bodies)
        title = f"Meeting {job.dir.name}"
        (job.dir / "transcript.md").write_text(to_markdown(title, turns), encoding="utf-8")
        job.advance(WRITTEN)
        speakers = len({t.speaker for t in turns})
        LOGGER.info("event=meeting_written dir=%s turns=%s speakers=%s", job.dir.name, len(turns), speakers)
        notify("Meeting transcript ready", str(job.dir / "transcript.md"))


def sweep_old_audio(root: Path, now: float | None = None) -> None:
    now = now or time.time()
    for job in find_jobs(root):
        if job.state == WRITTEN and job.stopped and now - job.stopped > AUDIO_RETENTION_S:
            for sub in ("audio", "parts"):
                shutil.rmtree(job.dir / sub, ignore_errors=True)


def process_all(root: Path, transcriber: GeminiTranscriber | None) -> int:
    """Run every due job. Returns the number still pending."""
    pending = 0
    for job in find_jobs(root):
        if not job.due():
            pending += 0 if job.state in (WRITTEN, FAILED, RECORDING) else 1
            continue
        lock = open(job.dir / ".lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            continue  # another worker has it
        try:
            job = MeetingJob.load(job.dir)  # re-read under the lock
            run_job(job, transcriber)
        except QuotaError as exc:
            job.fail(str(exc), retry_after=time.time() + exc.retry_s)
            LOGGER.warning("event=meeting_paused dir=%s retry_s=%.0f", job.dir.name, exc.retry_s)
            pending += 1
        except Exception as exc:
            backoff = min(6 * 3600, 300 * 2 ** job.attempts)
            job.fail(f"{type(exc).__name__}: {exc}"[:500], retry_after=time.time() + backoff)
            LOGGER.exception("event=meeting_job_failed dir=%s attempts=%s", job.dir.name, job.attempts)
            if job.attempts >= MAX_ATTEMPTS:
                job.advance(FAILED, error=job.error)
                notify("Meeting processing failed", f"Audio kept in {job.dir}")
            else:
                pending += 1
        finally:
            lock.close()
    return pending


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0]) if argv else default_root()
    from ..config import Settings

    # Own file: two processes rotating vaani.log would clobber each other.
    log_dir = Settings.from_home().log_dir
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    logging.basicConfig(
        filename=log_dir / "meeting.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        os.nice(10)
    except OSError:
        pass
    key = gemini_key()
    transcriber = GeminiTranscriber(key) if key else None
    sweep_old_audio(root)
    return 1 if process_all(root, transcriber) else 0


if __name__ == "__main__":
    raise SystemExit(main())
