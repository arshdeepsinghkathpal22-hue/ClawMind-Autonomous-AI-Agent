import pytest

from app import auth, main
from app.config import settings


def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_frontend_is_served_with_security_headers(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "ClawMind" in res.text
    assert "default-src 'self'" in res.headers["content-security-policy"]
    assert res.headers["x-frame-options"] == "DENY"
    assert res.headers["x-content-type-options"] == "nosniff"
    assert client.get("/app.js").status_code == 200


def test_chat_without_llm_explains_it(client):
    res = client.post("/api/chat", json={"message": "Explain what CAGR means", "session_id": "abc"})
    assert res.status_code == 200
    body = res.json()
    assert body["session_id"] == "abc"
    assert "AI reasoning is unavailable" in body["response"]
    assert body["tools_used"] == []


def test_offline_commands(client, workspace):
    calc = client.post("/api/chat", json={"message": "Calculate 125 * 42.", "session_id": "off"}).json()
    assert "5250" in calc["response"] and calc["tools_used"] == ["calculator"]

    time = client.post("/api/chat", json={"message": "What time is it?", "session_id": "off"}).json()
    assert time["tools_used"] == ["get_datetime"]

    saved = client.post("/api/chat", json={"message": "Remember that my project is called ClawMind", "session_id": "off"}).json()
    assert saved["tools_used"] == ["remember"]

    recall = client.post("/api/chat", json={"message": "What is my project called?", "session_id": "off"}).json()
    assert "My project is called ClawMind" in recall["response"]

    remind = client.post("/api/chat", json={"message": "Remind me tomorrow at 5 PM to study DSA", "session_id": "off"}).json()
    assert remind["tools_used"] == ["schedule_task"]
    assert any(t["title"] == "study DSA" for t in client.get("/api/tasks").json()["tasks"])


def test_chat_generates_session_id(client):
    body = client.post("/api/chat", json={"message": "hello"}).json()
    assert len(body["session_id"]) == 32


def test_session_history(client):
    client.post("/api/chat", json={"message": "calculate 2+2", "session_id": "hist"})
    messages = client.get("/api/sessions/hist/messages").json()["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert client.get("/api/sessions/bad id!/messages").status_code in (404, 422)


@pytest.mark.parametrize("payload", [
    {},
    {"message": ""},
    {"message": 123},
    {"message": ["a"]},
    {"message": None},
    {"message": "hi", "session_id": "../../etc"},
    {"message": "hi", "session_id": "a" * 65},
    {"message": "hi", "unexpected": True},
    {"message": "x" * 8001},
])
def test_chat_input_validation(client, payload):
    res = client.post("/api/chat", json=payload)
    assert res.status_code in (413, 422)
    body = res.json()
    assert "error" in body
    assert "Traceback" not in res.text


def test_invalid_json(client):
    res = client.post("/api/chat", content=b"{not json", headers={"Content-Type": "application/json"})
    assert res.status_code == 422
    assert res.json()["error"] == "Invalid request."


def test_huge_request_is_rejected(client):
    res = client.post("/api/chat", content=b"x" * 200_000, headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_chunked_huge_request_is_rejected(client):
    def body():
        for _ in range(100):
            yield b"x" * 10_000

    res = client.post("/api/chat", content=body(), headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_wrong_content_type_is_rejected(client):
    res = client.post("/api/chat", content='{"message": "hi"}', headers={"Content-Type": "text/plain"})
    assert res.status_code == 415


def test_cross_origin_post_is_blocked(client):
    res = client.post("/api/chat", json={"message": "hi"}, headers={"Origin": "https://evil.example"})
    assert res.status_code == 403
    same = client.post("/api/chat", json={"message": "hi"}, headers={"Origin": "http://127.0.0.1:8000"})
    assert same.status_code == 200


def test_unknown_host_header_is_blocked(client):
    res = client.get("/api/status", headers={"Host": "attacker.example"})
    assert res.status_code == 400


def test_rate_limit(client, monkeypatch):
    monkeypatch.setattr(main, "chat_limiter", auth.RateLimiter(3))
    codes = [client.post("/api/chat", json={"message": "hi"}).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3:] == [429, 429]


def test_status_has_no_secrets(client, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", "sk-super-secret-key-1234567890")
    res = client.get("/api/status")
    assert res.status_code == 200
    assert "sk-super-secret" not in res.text
    names = {t["name"]: t["permission"] for t in res.json()["tools"]}
    assert names["calculator"] == "SAFE" and names["delete_file"] == "DANGEROUS"


def test_memory_endpoints(client):
    res = client.post("/api/memory", json={"content": "I like dark mode"})
    assert res.status_code == 201
    memory_id = res.json()["id"]

    assert any(m["id"] == memory_id for m in client.get("/api/memory").json()["memories"])
    assert client.get("/api/memory", params={"q": "dark mode"}).json()["memories"][0]["id"] == memory_id

    secret = client.post("/api/memory", json={"content": "my password is hunter2"})
    assert secret.status_code == 400

    assert client.delete(f"/api/memory/{memory_id}").status_code == 200
    assert client.delete(f"/api/memory/{memory_id}").status_code == 404
    assert client.delete("/api/memory/abc").status_code == 422


def test_task_endpoints(client):
    res = client.post("/api/tasks", json={"title": "Stretch", "when": "every day at 7am"})
    assert res.status_code == 201
    task = res.json()
    assert task["schedule"] == "every day at 07:00"

    assert client.post("/api/tasks", json={"title": "x", "when": "someday"}).status_code == 400
    assert client.post("/api/tasks", json={"title": "x", "when": "tomorrow", "action": "hack"}).status_code == 422

    assert client.delete(f"/api/tasks/{task['id']}").status_code == 200
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 404


def test_notifications_endpoint(client):
    from app.memory import save_message

    save_message("notif", "notification", "⏰ Reminder: test")
    data = client.get("/api/notifications", params={"after": 0}).json()
    assert data["notifications"][-1]["content"] == "⏰ Reminder: test"
    last = data["notifications"][-1]["id"]
    assert client.get("/api/notifications", params={"after": last}).json()["notifications"] == []


def test_server_errors_do_not_leak_details(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database password is hunter2")

    monkeypatch.setattr(main.memory, "list_memories", boom)
    from fastapi.testclient import TestClient

    with TestClient(main.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False) as c:
        res = c.get("/api/memory")
    assert res.status_code == 500
    assert res.json() == {"error": "Something went wrong on the server."}
    assert "hunter2" not in res.text


def test_streaming_chat(client):
    with client.stream("POST", "/api/chat/stream", json={"message": "calculate 6*7", "session_id": "stream"}) as res:
        lines = [line for line in res.iter_lines() if line]
    import json

    events = [json.loads(line) for line in lines]
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "result"
    assert "42" in events[-1]["response"]


class TestAuth:
    @pytest.fixture
    def auth_on(self, monkeypatch):
        monkeypatch.setattr(settings, "auth_enabled", True)
        monkeypatch.setattr(settings, "auth_password_hash", auth.hash_password("correct horse battery", iterations=1000))

    def test_requires_login(self, client, auth_on):
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
        assert client.get("/health").status_code == 200

    def test_login_flow(self, client, auth_on):
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        res = client.post("/api/login", json={"password": "correct horse battery"})
        assert res.status_code == 200
        cookie = res.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie

        assert client.get("/api/status").status_code == 200
        token = res.json()["token"]
        assert client.get("/api/status", headers={"Authorization": f"Bearer {token}"}).status_code == 200

        client.post("/api/logout", json={})
        client.cookies.clear()
        assert client.get("/api/status").status_code == 401

    def test_login_is_rate_limited(self, client, auth_on):
        codes = [client.post("/api/login", json={"password": "nope"}).status_code for _ in range(7)]
        assert 429 in codes

    def test_password_hashing(self):
        stored = auth.hash_password("s3cret-password", iterations=1000)
        assert "s3cret" not in stored
        assert auth.verify_password("s3cret-password", stored)
        assert not auth.verify_password("other", stored)
        assert not auth.verify_password("x", "garbage")


def test_refuses_public_bind_without_auth():
    from app.config import Settings, startup_problems

    risky = Settings(host="0.0.0.0", auth_enabled=False, data_dir=settings.data_dir, workspace_dir=settings.workspace_dir)
    assert startup_problems(risky)
    safe = Settings(host="127.0.0.1", data_dir=settings.data_dir, workspace_dir=settings.workspace_dir)
    assert startup_problems(safe) == []
    no_hash = Settings(host="0.0.0.0", auth_enabled=True, data_dir=settings.data_dir, workspace_dir=settings.workspace_dir)
    assert any("AUTH_PASSWORD_HASH" in p for p in startup_problems(no_hash))
