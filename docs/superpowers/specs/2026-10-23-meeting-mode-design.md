# Meeting mode — design

Date: 2026-10-23. Status: approved in chat; implementation follows.

## Goal

Record a whole meeting (minutes to a few hours) with one shortcut, then —
after it ends, no live transcription — produce an accurate, speaker-labelled,
multilingual transcript, an English translation of non-English lines, and a
summary. Cost must be **$0**. Normal dictation must not get slower or heavier.

## Decisions (from the user)

| Topic | Decision |
|---|---|
| Processing | Cloud, free only: Gemini 3.5 Transcribe free tier. Free-tier data may be used by Google — accepted. |
| Fallback | Groq Whisper (free tier, already used by Vaani). |
| Output | Transcript + summary + English translation. |
| Speakers | Auto-guess names from the conversation; the user is labelled by name from a one-time voice sample; guesses marked `(?)`. |
| Capture | Mic + laptop audio output (in-room and online meetings). |

## Verified facts (probe on 2026-10-23 with the user's key)

- `POST /v1beta/interactions`, model `gemini-3.5-transcribe`, audio by Files API
  URI, works on the free key. ~25 audio tokens/s.
- `transcription_config: {language_codes: [], mode: {type: "verbatim",
  diarization_mode: "speaker"}, timestamp_granularities: ["word"]}` returns
  `steps[].content[].annotations[]` of `word_info` with `text`, `speaker`
  (`spk:0`), `start_offset`/`end_offset` (`"1.400s"`).
- Docs: ≤30 min per request with diarization; ≤8 speakers, 3+ "experimental".
  Free-tier quotas are not published.
- Accuracy on the user's 52 s dictation: Gemini misheard one phrase ("I had a
  phone question" for "I have this one question"). A span Groq had and Gemini
  lacked turned out to be **silence** (frame RMS at noise floor, 31–38 s):
  Whisper hallucinated it. So Whisper must never fill gaps by text coverage.
- 503 "high demand" happens on the free tier; retried in-request.
- Machine: 12 cores, 15 GB RAM, no GPU, no ffmpeg, no torch.

## Shortcut

- Start: hold TrackPoint middle button + tap **R** once.
- Stop: hold TrackPoint middle button + tap **R R** (two taps ≤0.5 s apart).
- A single R tap during a meeting is ignored (pill flashes) — no accidental stop.
- Esc does not touch a meeting. No discard shortcut in v1 (delete the folder).
- R is only intercepted while the middle button is held; no stray "r" typed.
- Dictation keeps working during a meeting (separate recorder).

## Components

All under `src/vaani/meeting/`; each testable alone. Slice 1 (shipped first):
recorder, job, prepare, Gemini, assemble (speaker linking via 30 s part
overlap), worker, control, hotkey. Slice 2: coverage, speakers (enrollment),
postprocess (names, translation, summary), recording pill.

1. `recorder.py` — `MeetingRecorder`: two `parec` processes (mic =
   default source, system = `@DEFAULT_MONITOR@`), 16 kHz mono s16 raw each,
   `--latency-msec` and `--client-name=vaani-meeting` (the dictation reaper
   never matches it). Each runs in its own `systemd-run --user --scope`, so a
   Vaani crash/restart doesn't stop it; the next Vaani adopts it from
   `job.json`. No echo cancellation: on laptop speakers the mix is the mic only
   (it already hears remote voices); on headphones/Bluetooth, mic + system.
   4 h cap with warning; cap → stop *and process*.
2. `job.py` — `MeetingJob`: on-disk state machine in `job.json`:
   `recording → recorded → prepared → transcribed(part i) → linked → written`.
   Each step idempotent; restart resumes from last completed step. Failure
   records the error and keeps all audio.
3. `prepare.py` — mix mic + system (sum, soft-clip), light cleanup in numpy
   (high-pass 80 Hz, loudness normalise; no ffmpeg), FLAC-encode tracks and
   mix, split mix into ≤25 min parts at pauses (reuse `stt_chunks.find_cut`
   logic), 0 overlap.
4. `gemini_transcribe.py` — Files API upload + interaction call per part;
   parses word annotations into `Word(text, start, end, speaker)`. Retries with
   backoff; HTTP 429 → job pauses (`retry_after`), resumed by a timer.
5. `coverage.py` (slice 2) — find gaps locally: windows where mix frame RMS
   shows speech for ≥2 s but Gemini returned no words. Send only those windows
   to Groq Whisper (shares the dictation quota, so keep it small) and insert the
   text marked `[whisper]`. Never fill silent windows.
6. `speakers.py` — sherpa-onnx speaker embedding (CPU, ~28 MB model downloaded
   once to `~/.local/share/vaani/models/`). Per part, average embedding per
   Gemini speaker → greedy cosine clustering to global speakers across parts.
   User voice enrolled once (`vaani meeting-enroll`, 30 s) → the matching
   global speaker is named from config. Speakers whose energy is mostly on the
   system track are tagged `remote`.
7. `postprocess.py` — Gemini free text model (one call per ~20 min of
   transcript): guess names (`(?)`), translate non-English lines to English,
   then one call for title + summary (decisions, action items with owner, open
   questions). Fallback: `codex exec`. If both fail, transcript is still written
   without names/translation/summary.
8. `writer.py` — writes `transcript.md`, `summary.md`, keeps `audio/`.
9. `worker.py` — `python -m vaani.meeting.worker <dir>`: runs the job in a
   **separate process** with `nice 10`, so the main Vaani process gains no
   imports, RAM or CPU. Notifies via `notify-send` with the folder path.
10. Hotkey: extend `MiddleButtonHotkeyManager` with an R-tap detector while the
    middle button is held; controller gains `toggle_meeting`.

## Output

`~/Documents/Vaani Meetings/<YYYY-MM-DD HHMM> – <title>/`
- `transcript.md` — `**Name** [hh:mm:ss]` line, original text, and an
  indented English line under non-English text.
- `summary.md` — title, participants, decisions, action items (owner), open
  questions.
- `audio/mic.flac`, `audio/system.flac`, `audio/mix.flac`.
- `job.json`.

Retention: text kept; `audio/` deleted after 30 days (swept at worker start).

## Resource budget

- During meeting: two `parec` (~1 % core each) + optional WebRTC AEC (a few %).
  No transcription, no Python work beyond a 1 Hz pill update.
- Disk while recording: ~230 MB/h raw; after prepare ~100 MB/h FLAC.
- Processing: separate niced process after stop. Main dictation path untouched.

## Error handling

- Never delete audio on failure. Every step logs `event=meeting_*`.
- Gemini quota exhausted → pause, retry later; after 6 h total, that part falls
  back to Whisper-only text (single speaker label `Unknown`).
- Crash during recording → startup finds `job.json` in `recording`, marks
  `recorded` (raw WAVs are valid PCM) and queues processing.

## Testing

Unit: R-tap / R-R detection; job state resume; part splitting; Gemini response
parsing (recorded fixture); coverage insertion; speaker clustering with
synthetic embeddings; markdown writer. Integration: worker on a fixture
recording with Gemini/Groq/LLM mocked. Manual: one real meeting.

## Out of scope (v1)

Live transcript, calendar integration, editing UI, >8 speakers, discard UI,
Wayland/macOS/Windows.
