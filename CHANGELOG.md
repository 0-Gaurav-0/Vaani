# Changelog

## 2026-10-26 repository update

This update publishes the local changes made after the October 8 snapshot.
It does not install or activate a desktop release.

### Added

- Linux/X11 meeting capture through TrackPoint plus R.
- A separate meeting pill with waveform, elapsed time, pause, resume, and stop.
- Background Gemini transcription with speaker labels and Markdown timestamps.
- Saved meeting jobs with retry, crash recovery, and separate audio retention.
- A 48-hour dictation chunk archive with FLAC audio and transcript text.
- Recovery of available recordings after crashes and processing failures.

### Fixed

- PipeWire capture tail loss when recording stops.
- Dictation loss when capture reaches the ten-minute limit.
- Recorder cleanup that could stop another live Vaani process's recorder.
- Wrong-song assistant fallback for play commands.
- Audio startup and level-read overhead.
- Cancellation now removes the active temporary WAV.

### Documentation

- Updated the README for the current shortcuts, providers, meeting mode,
  release workflow, and local data retention.
- Clarified that logs and local archives can contain private speech data.
