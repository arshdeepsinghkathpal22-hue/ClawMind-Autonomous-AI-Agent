import json

import httpx
import pytest

from app.llm import LLMClient, LLMError


def make_client(handler):
    return LLMClient("http://llm.test/v1", "test-key", "test-model", transport=httpx.MockTransport(handler))


def test_parses_tool_calls_and_strips_thinking():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "<think>secret reasoning</think>Let me check.",
            "tool_calls": [
                {"id": "call_1", "type": "function", "function": {"name": "calculator", "arguments": '{"expression": "2+2"}'}},
                {"type": "function", "function": {"name": "get_datetime", "arguments": {}}},
            ],
        }}]})

    reply = make_client(handler).chat([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    assert reply["content"] == "Let me check."
    assert reply["tool_calls"][0] == {"id": "call_1", "name": "calculator", "arguments": {"expression": "2+2"}}
    assert reply["tool_calls"][1]["name"] == "get_datetime" and reply["tool_calls"][1]["id"]
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["model"] == "test-model" and seen["body"]["tool_choice"] == "auto"


def test_bad_json_arguments_are_flagged():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "x", "function": {"name": "calculator", "arguments": "{broken"}}]}}]})

    reply = make_client(handler).chat([])
    assert "_invalid_json" in reply["tool_calls"][0]["arguments"]


@pytest.mark.parametrize("status, text", [(401, "LLM_API_KEY"), (404, "LLM_MODEL"), (429, "rate limiting"), (500, "HTTP 500")])
def test_http_errors_become_friendly_messages(status, text):
    client = make_client(lambda request: httpx.Response(status, text="error body"))
    with pytest.raises(LLMError, match=text):
        client.chat([{"role": "user", "content": "hi"}])


def test_connection_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    with pytest.raises(LLMError, match="Could not connect"):
        make_client(handler).chat([])


def test_unconfigured_client():
    client = LLMClient("", "", "")
    assert not client.available
    with pytest.raises(LLMError):
        client.chat([])


def test_embeddings():
    def handler(request):
        assert request.url.path.endswith("/embeddings")
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    client = LLMClient("http://llm.test/v1", "", "m", embedding_model="embed", transport=httpx.MockTransport(handler))
    assert client.embed(["hello"]) == [[0.1, 0.2]]
