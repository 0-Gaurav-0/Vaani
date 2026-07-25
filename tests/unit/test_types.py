from vaani.types import AppState, DictationMode, GroqModelSettings
def test_public_types():
    assert AppState.IDLE.value == "Idle"
    assert DictationMode.SMART.value == "smart"
    assert GroqModelSettings().transcription_model == "whisper-large-v3-turbo"
