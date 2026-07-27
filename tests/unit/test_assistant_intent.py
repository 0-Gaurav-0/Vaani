from vaani.assistant_intent import classify_assistant_intent, looks_like_action


def test_classify_action_question_paste_codex():
    assert classify_assistant_intent("open YouTube") == "action"
    assert looks_like_action("Chrome kholo")
    assert classify_assistant_intent("Who is the PM of India?") == "qa"
    assert classify_assistant_intent("what is the capital of France") == "qa"
    assert classify_assistant_intent("kya haal hai") == "qa"
    assert classify_assistant_intent("please send this to John") == "paste"
    assert classify_assistant_intent("fix the flaky test") == "codex"
    assert classify_assistant_intent("") == "paste"


def test_recommend_and_help_requests_are_qa():
    assert classify_assistant_intent("recommend a good manga") == "qa"
    assert classify_assistant_intent("can you suggest a good anime") == "qa"
    assert classify_assistant_intent("tell me a good restaurant nearby") == "qa"
    assert classify_assistant_intent("any good books to read") == "qa"
    assert classify_assistant_intent("best manga for beginners") == "qa"
    assert classify_assistant_intent("manga recommend karo") == "qa"
