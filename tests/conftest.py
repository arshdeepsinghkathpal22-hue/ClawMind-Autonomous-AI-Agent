import os
import shutil
import tempfile
from pathlib import Path

import pytest

# Settings are read when app.config is imported, so set everything up first.
TEST_ROOT = Path(tempfile.mkdtemp(prefix="clawmind_tests_"))
os.environ.update({
    "DATA_DIR": str(TEST_ROOT / "data"),
    "WORKSPACE_DIR": str(TEST_ROOT / "workspace"),
    "LLM_BASE_URL": "",
    "LLM_MODEL": "",
    "LLM_API_KEY": "",
    "EMBEDDING_MODEL": "",
    "HOST": "127.0.0.1",
    "AUTH_ENABLED": "false",
    "AUTH_PASSWORD_HASH": "",
    "ENABLE_BROWSER": "false",
    "ENABLE_PYTHON_TOOL": "true",
    "PYTHON_TIMEOUT": "10",
    "SEARCH_PROVIDER": "duckduckgo",
    "CHAT_RATE_LIMIT": "1000",
    "DEBUG": "false",
})

from app import auth, main  # noqa: E402
from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402
from app.tools.load import load_tools  # noqa: E402

settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.workspace_dir.mkdir(parents=True, exist_ok=True)
init_db(settings.database_url)
load_tools()


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


@pytest.fixture
def workspace():
    root = settings.workspace_dir
    for item in root.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item)
        else:
            item.unlink()
    return root


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    main.chat_limiter.reset()
    main.login_limiter.reset()
    main.write_limiter.reset()
    auth.clear_sessions()
    with TestClient(main.app, base_url="http://127.0.0.1:8000") as c:
        yield c


class FakeLLM:
    """Stands in for the real model. Each scripted step is a reply dict or a function(messages)."""

    available = True
    can_embed = False

    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def chat(self, messages, tools=None, temperature=0.2):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        if not self.script:
            return {"content": "No more scripted replies.", "tool_calls": []}
        step = self.script.pop(0)
        return step(messages) if callable(step) else step


def tool_call(name, arguments, call_id=None):
    return {"content": "", "tool_calls": [{"id": call_id or f"call_{name}", "name": name, "arguments": arguments}]}


def answer(text):
    return {"content": text, "tool_calls": []}


@pytest.fixture
def fake_llm(monkeypatch):
    def install(script):
        llm = FakeLLM(script)
        monkeypatch.setattr("app.agent.get_llm", lambda: llm)
        return llm

    return install
