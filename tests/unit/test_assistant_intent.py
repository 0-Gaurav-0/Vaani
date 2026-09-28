from vaani.assistant_intent import (
    classify_assistant_intent,
    extract_agent_handoff,
    extract_session_continue,
    looks_like_action,
    looks_like_agent_mutation,
    looks_like_play_request,
    looks_like_work_context,
    with_vaani_source_tag,
    with_work_context_hint,
)


def test_looks_like_play_request_requires_verb():
    from vaani.assistant_intent import clean_play_query, is_weak_play_query

    assert looks_like_play_request("play despacito")
    assert looks_like_play_request("despacito bajao")
    assert looks_like_play_request("gaana chalao")
    assert looks_like_play_request("play coldplay on youtube")
    # Live STT: "play sanghu tere" → "plesa sanghoote de"
    assert looks_like_play_request("plesa sanghoote de")
    assert clean_play_query("plesa sanghoote de") == "sanghoote de"
    assert not is_weak_play_query("sanghoote de", raw="plesa sanghoote de")
    assert not is_weak_play_query("sanghu tere", raw="play sanghu tere")
    assert is_weak_play_query("pyaara", raw="pyaara")
    assert is_weak_play_query("jhaala", raw="jhaala")
    assert not looks_like_play_request("play")
    assert not looks_like_play_request("play it")
    assert not looks_like_play_request("plesa")
    assert not looks_like_play_request("pyaara")
    assert not looks_like_play_request("Thank you")


def test_classify_action_question_paste_codex():
    assert classify_assistant_intent("open YouTube") == "action"
    assert looks_like_action("Chrome kholo")
    assert classify_assistant_intent("Who is the PM of India?") == "qa"
    assert classify_assistant_intent("what is the capital of France") == "qa"
    assert classify_assistant_intent("kya haal hai") == "qa"
    assert classify_assistant_intent("please send this to John") == "paste"
    assert classify_assistant_intent("fix the flaky test") == "codex"
    assert classify_assistant_intent("") == "paste"


def test_agent_handoff_phrases():
    assert extract_agent_handoff("say hi to the agent") == "hi"
    assert extract_agent_handoff("ask Vaani to summarize this thread") == (
        "summarize this thread"
    )
    assert extract_agent_handoff("ask vani open the sales dashboard") == (
        "open the sales dashboard"
    )
    assert extract_agent_handoff("tell Hermes fix the flaky test") == (
        "fix the flaky test"
    )
    assert extract_agent_handoff("agent: hello") == "hello"
    assert extract_agent_handoff("pass this to the agent: list my skills") == (
        "list my skills"
    )
    assert extract_agent_handoff("open YouTube") is None
    assert classify_assistant_intent("say hi to the agent") == "codex"
    # STT often mangles Vaani→Wani and inserts a pause as "."
    assert extract_agent_handoff("Ask Wani. Hi.") == "Hi"
    assert extract_agent_handoff("ask Vaani") == "hi"
    assert extract_agent_handoff("ask vani") == "hi"
    # Wake + question must NOT be treated as Hermes handoff.
    assert extract_agent_handoff(
        "Hey Vani, who is the President of India?"
    ) is None
    assert (
        classify_assistant_intent("Hey Vani, who is the President of India?")
        == "qa"
    )
    assert extract_agent_handoff("hey agent, list my skills") == "list my skills"
    assert extract_agent_handoff("hermes: summarize this") == "summarize this"


def test_recommend_and_help_requests_are_qa():
    assert classify_assistant_intent("recommend a good manga") == "qa"
    assert classify_assistant_intent("can you suggest a good anime") == "qa"
    assert classify_assistant_intent("tell me a good restaurant nearby") == "qa"
    assert classify_assistant_intent("any good books to read") == "qa"
    assert classify_assistant_intent("best manga for beginners") == "qa"
    assert classify_assistant_intent("manga recommend karo") == "qa"


def test_session_continue_phrases():
    assert extract_session_continue("continue") == ""
    assert extract_session_continue("continue with also check overdue") == (
        "also check overdue"
    )
    assert extract_session_continue("follow up on that") == ""
    assert extract_session_continue("what's the update on that") == ""
    assert extract_session_continue("in that session add a summary") == (
        "add a summary"
    )
    assert extract_session_continue("open YouTube") is None


def test_agent_mutation_detector():
    assert looks_like_agent_mutation("delete the todo")
    assert looks_like_agent_mutation("update the assignee")
    assert looks_like_agent_mutation("send this email")
    assert looks_like_agent_mutation("hata do yeh task")
    assert looks_like_agent_mutation("mark complete this todo")
    assert looks_like_agent_mutation("fix the flaky test")
    assert looks_like_agent_mutation("add a todo for standup")
    assert not looks_like_agent_mutation("what are my todos")
    assert not looks_like_agent_mutation("status of this todo")
    assert not looks_like_agent_mutation("what's the update on that")
    assert not looks_like_agent_mutation("summarize Basecamp assigned")
    hinted = with_vaani_source_tag("list my todos")
    assert hinted.startswith("[source: vaani]")
    assert "list my todos" in hinted
    assert "[Vaani policy]" not in hinted


def test_work_context_basecamp_hint():
    assert looks_like_work_context(
        "what is the status of this todo assigned to Himanshu"
    )
    assert looks_like_work_context("list all tasks Himanshu is working on")
    assert not looks_like_work_context("open YouTube")
    hinted = with_work_context_hint(
        "status of this todo", utterance="status of this todo"
    )
    assert hinted.startswith("[Vaani context]")
    assert "Basecamp" in hinted
    assert "status of this todo" in hinted
    # Idempotent
    assert with_work_context_hint(hinted) == hinted
