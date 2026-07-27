import json
from threading import Event

import httpx
import pytest

from vaani.groq import GroqClient, GroqError
from vaani.types import GroqModelSettings


def client(handler, settings=None):
    return GroqClient(
        settings=settings,
        transport=httpx.MockTransport(handler),
        sleep=lambda _: None,
    )


def test_parse_intent_returns_fenced_json_content():
    plan = '{"action":"plan","steps":[{"verb":"site.search","slots":{"query":"Zapto"}}]}'

    def h(req):
        payload = json.loads(req.read())
        assert payload["model"] == "llama-3.1-8b-instant"
        assert payload["temperature"] == 0
        assert payload["max_tokens"] == 512
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["messages"][0]["content"] == "system prompt"
        assert payload["messages"][1]["content"] == "user utterance"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": f"```json\n{plan}\n```"}}]},
        )

    result = client(h).parse_intent("system prompt", "user utterance", "k")
    assert result == plan


def test_parse_intent_uses_parse_model_override():
    def h(req):
        payload = json.loads(req.read())
        assert payload["model"] == "parse-special"
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action":"refuse"}'}}]},
        )

    settings = GroqModelSettings(parse_model="parse-special")
    result = client(h, settings).parse_intent("sys", "user", "k")
    assert result == '{"action":"refuse"}'


def test_parse_intent_http_error_returns_none():
    def h(req):
        return httpx.Response(500, json={"error": "boom"})

    assert client(h).parse_intent("sys", "user", "k") is None


def test_parse_intent_malformed_returns_none():
    def h(req):
        return httpx.Response(200, json={"choices": []})

    assert client(h).parse_intent("sys", "user", "k") is None


def test_parse_intent_cancel_raises():
    e = Event()
    e.set()

    def h(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    with pytest.raises(GroqError) as exc:
        client(h).parse_intent("sys", "user", "k", cancel=e)
    assert exc.value.category == "cancelled"
