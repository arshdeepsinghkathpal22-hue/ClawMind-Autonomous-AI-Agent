import pytest

from app import memory
from app.db import Memory, get_session
from app.redact import find_secret, redact


@pytest.fixture(autouse=True)
def clean_memories():
    with get_session() as db:
        db.query(Memory).delete()
        db.commit()


def test_remember_search_forget():
    saved = memory.add_memory("Remember that my project is called ClawMind.")
    assert saved["content"] == "My project is called ClawMind."

    hits = memory.search_memories("What is my project called?")
    assert hits and hits[0]["id"] == saved["id"]

    assert memory.search_memories("favourite pizza topping") == []
    assert memory.delete_memory(saved["id"])
    assert memory.list_memories() == []
    assert not memory.delete_memory(saved["id"])


def test_duplicates_are_not_saved_twice():
    first = memory.add_memory("I study at JIIT")
    second = memory.add_memory("i study at jiit")
    assert first["id"] == second["id"]
    assert len(memory.list_memories()) == 1


@pytest.mark.parametrize("text", [
    "my password is hunter2",
    "Remember my API key sk-proj-abcdefghijklmnopqrstuvwx",
    "The github token is ghp_abcdefghijklmnopqrstuvwxyz0123",
    "my card is 4111 1111 1111 1111",
    "aws key AKIAIOSFODNN7EXAMPLE",
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...",
    "my security question answer is Rex",
    "session token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abc123def456",
    "otp is 123456",
])
def test_secrets_are_refused(text):
    with pytest.raises(memory.MemoryRejected):
        memory.add_memory(text)
    assert memory.list_memories() == []


def test_normal_numbers_are_fine():
    assert find_secret("I was born in 2005 and my roll number is 9924") is None
    assert memory.add_memory("My exam is on 12 Nov 2026")


def test_redact_hides_secrets():
    text = "key=sk-abcdefghijklmnopqrstuv card 4111-1111-1111-1111 password: hunter2"
    cleaned = redact(text)
    assert "sk-abcdef" not in cleaned
    assert "4111" not in cleaned
    assert "hunter2" not in cleaned


def test_short_term_messages():
    memory.save_message("mem-test", "user", "hello")
    memory.save_message("mem-test", "assistant", "hi there", ["calculator"])
    memory.save_message("mem-test", "notification", "⏰ Reminder: x")
    rows = memory.recent_messages("mem-test")
    assert [r.role for r in rows] == ["user", "assistant"]
    assert memory.message_to_dict(rows[1])["tools_used"] == ["calculator"]


def test_keyword_fallback_when_embeddings_fail(monkeypatch):
    class BrokenLLM:
        can_embed = True

        def embed(self, texts):
            from app.llm import LLMError
            raise LLMError("embedding server down")

    monkeypatch.setattr(memory, "get_llm", lambda: BrokenLLM())
    saved = memory.add_memory("My favourite language is Python")
    assert memory.search_memories("which language do I like? python")[0]["id"] == saved["id"]


def test_semantic_search_with_embeddings(monkeypatch):
    vectors = {
        "My dog is named Bruno": [1.0, 0.0, 0.0],
        "I work at DeepSolv": [0.0, 1.0, 0.0],
        "what's my pet called": [0.9, 0.1, 0.0],
    }

    class FakeEmbedder:
        can_embed = True

        def embed(self, texts):
            return [vectors[t] for t in texts]

    monkeypatch.setattr(memory, "get_llm", lambda: FakeEmbedder())
    dog = memory.add_memory("My dog is named Bruno")
    memory.add_memory("I work at DeepSolv")
    hits = memory.search_memories("what's my pet called")
    assert hits[0]["id"] == dog["id"]
