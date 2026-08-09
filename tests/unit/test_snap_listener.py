from vaani.snap_listener import (
    DoubleClapGate,
    detect_snap_frames,
    frame_impulse_features,
    snap_assistant_enabled,
)


def test_detect_snap_spike():
    baseline = 0.002
    # Quiet pad → short spike at the end → quiet again (snap/clap-like).
    frames = [0.001, 0.0012, 0.001, 0.0015, 0.001, 0.09, 0.02, 0.003]
    assert detect_snap_frames(
        frames, baseline=baseline, abs_threshold=0.06, ratio=8.0
    )


def test_reject_sustained_speech_energy():
    baseline = 0.01
    frames = [0.08] * 10
    assert not detect_snap_frames(
        frames, baseline=baseline, abs_threshold=0.06, ratio=8.0
    )


def test_reject_quiet():
    assert not detect_snap_frames(
        [0.001, 0.002, 0.0015, 0.001, 0.002, 0.001],
        baseline=0.002,
        abs_threshold=0.06,
        ratio=8.0,
    )


def test_reject_loud_ambient_music():
    assert not detect_snap_frames(
        [0.02, 0.02, 0.09, 0.03, 0.02, 0.02],
        baseline=0.015,
        abs_threshold=0.06,
        ratio=8.0,
    )


def test_reject_loud_voice_plosive_in_speech():
    # Elevated pre-energy + slow decay — shout / hard consonant mid-speech.
    frames = [0.018, 0.022, 0.025, 0.028, 0.11, 0.08, 0.06, 0.05]
    assert not detect_snap_frames(
        frames, baseline=0.003, abs_threshold=0.06, ratio=8.0
    )


def test_reject_gradual_shout():
    # Attack rises over several frames — not an impulse.
    frames = [0.002, 0.004, 0.01, 0.025, 0.05, 0.09, 0.07, 0.04]
    assert not detect_snap_frames(
        frames, baseline=0.002, abs_threshold=0.06, ratio=8.0
    )


def test_reject_low_crest_even_if_spike():
    frames = [0.001, 0.001, 0.001, 0.001, 0.001, 0.09, 0.02, 0.002]
    crests = [2.0, 2.0, 2.0, 2.0, 2.0, 2.1, 2.0, 2.0]
    assert not detect_snap_frames(
        frames,
        baseline=0.002,
        abs_threshold=0.06,
        ratio=8.0,
        frames_crest=crests,
    )


def test_accept_high_crest_impulse():
    frames = [0.001, 0.001, 0.001, 0.001, 0.001, 0.09, 0.02, 0.002]
    crests = [2.0, 2.0, 2.0, 2.0, 2.0, 6.0, 3.0, 2.0]
    assert detect_snap_frames(
        frames,
        baseline=0.002,
        abs_threshold=0.06,
        ratio=8.0,
        frames_crest=crests,
    )


def test_frame_impulse_features_impulse_like():
    # One-sample click in otherwise silence → high crest after HP.
    samples = [0] * 80 + [20000] + [0] * 79
    hp, crest, _zcr = frame_impulse_features(samples)
    assert hp > 0.01
    assert crest >= 3.0


def test_snap_enabled_default(monkeypatch):
    monkeypatch.delenv("VAANI_SNAP_ASSISTANT", raising=False)
    assert snap_assistant_enabled()
    monkeypatch.setenv("VAANI_SNAP_ASSISTANT", "0")
    assert not snap_assistant_enabled()


def test_double_clap_gate_ignores_single_and_fires_on_pair():
    gate = DoubleClapGate(window_s=0.75, settle_s=0.18, min_gap_s=0.12, cooldown_s=0.0)
    assert gate.note_impulse(1.0) == "armed"
    assert gate.poll(1.10) == "none"
    assert gate.note_impulse(1.05) == "armed"  # ring / too soon
    assert gate.note_impulse(1.40) == "pending"
    assert gate.poll(1.50) == "none"
    assert gate.poll(1.58) == "fire"


def test_double_clap_gate_single_expires_without_fire():
    gate = DoubleClapGate(window_s=0.75, settle_s=0.18, min_gap_s=0.12, cooldown_s=0.0)
    assert gate.note_impulse(1.0) == "armed"
    assert gate.poll(1.80) == "expired"
    # After expiry, a lone later impulse is a new arm — never fire alone.
    assert gate.note_impulse(2.0) == "armed"
    assert gate.poll(2.10) == "none"


def test_double_clap_gate_rejects_third_tap_during_settle():
    gate = DoubleClapGate(window_s=0.75, settle_s=0.18, min_gap_s=0.05, cooldown_s=0.0)
    assert gate.note_impulse(1.00) == "armed"
    assert gate.note_impulse(1.20) == "pending"
    assert gate.note_impulse(1.30) == "reject_burst"
    assert gate.poll(1.50) == "none"
