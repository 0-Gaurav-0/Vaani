from vaani.waveform import WaveformBuffer, boost_level


def test_boost_expands_quiet_speech():
    assert boost_level(0.0) == 0.0
    assert boost_level(0.002) == 0.0  # true silence floor
    # Typical soft macOS mic RMS must become clearly visible.
    assert boost_level(0.01) > 0.25
    assert boost_level(0.05) > 0.6
    assert boost_level(1.0) == 1.0


def test_waveform_buffer_tracks_mic():
    buf = WaveformBuffer(bars=15)
    for _ in range(20):
        buf.push(0.08)
    bars = buf.bars_now()
    assert len(bars) == 15
    assert max(bars) > 0.35
    mid = bars[len(bars) // 2]
    assert mid >= bars[0]
    assert mid >= bars[-1]
