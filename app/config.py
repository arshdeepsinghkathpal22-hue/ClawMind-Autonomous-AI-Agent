import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Real environment variables win over values in .env
load_dotenv(BASE_DIR / ".env", override=False)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _str(name, default=""):
    return os.getenv(name, default).strip()


def _bool(name, default=False):
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    return value in ("1", "true", "yes", "on")


def _int(name, default):
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


def _path(name, default):
    raw = _str(name)
    path = Path(raw) if raw else Path(default)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def _list(name):
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


@dataclass
class Settings:
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    embedding_model: str = ""
    llm_timeout: int = 90

    host: str = "127.0.0.1"
    port: int = 8000
    debug: bool = False
    allowed_hosts: list = field(default_factory=list)
    cors_origins: list = field(default_factory=list)

    auth_enabled: bool = False
    auth_password_hash: str = ""
    cookie_secure: bool = False
    docker_local_only: bool = False

    data_dir: Path = BASE_DIR / "data"
    workspace_dir: Path = BASE_DIR / "workspace"

    max_agent_steps: int = 12
    max_tool_calls: int = 20

    search_provider: str = "duckduckgo"
    brave_api_key: str = ""
    searxng_url: str = ""
    fetch_timeout: int = 15
    fetch_max_bytes: int = 2_000_000

    enable_python: bool = True
    python_timeout: int = 20

    enable_browser: bool = False
    browser_executable_path: str = ""
    browser_timeout: int = 20

    max_request_bytes: int = 64_000
    chat_rate_limit: int = 30

    @property
    def llm_available(self):
        return bool(self.llm_base_url and self.llm_model)

    @property
    def database_url(self):
        return f"sqlite:///{(self.data_dir / 'clawmind.db').as_posix()}"


def load_settings():
    return Settings(
        llm_api_key=_str("LLM_API_KEY"),
        llm_base_url=_str("LLM_BASE_URL").rstrip("/"),
        llm_model=_str("LLM_MODEL"),
        embedding_model=_str("EMBEDDING_MODEL"),
        llm_timeout=_int("LLM_TIMEOUT", 90),
        host=_str("HOST", "127.0.0.1") or "127.0.0.1",
        port=_int("PORT", 8000),
        debug=_bool("DEBUG"),
        allowed_hosts=_list("ALLOWED_HOSTS"),
        cors_origins=_list("CORS_ORIGINS"),
        auth_enabled=_bool("AUTH_ENABLED"),
        auth_password_hash=_str("AUTH_PASSWORD_HASH"),
        cookie_secure=_bool("COOKIE_SECURE"),
        docker_local_only=_bool("DOCKER_LOCAL_ONLY"),
        data_dir=_path("DATA_DIR", "data"),
        workspace_dir=_path("WORKSPACE_DIR", "workspace"),
        max_agent_steps=_int("MAX_AGENT_STEPS", 12),
        max_tool_calls=_int("MAX_TOOL_CALLS", 20),
        search_provider=_str("SEARCH_PROVIDER", "duckduckgo").lower() or "duckduckgo",
        brave_api_key=_str("BRAVE_API_KEY"),
        searxng_url=_str("SEARXNG_URL").rstrip("/"),
        fetch_timeout=_int("FETCH_TIMEOUT", 15),
        fetch_max_bytes=_int("FETCH_MAX_BYTES", 2_000_000),
        enable_python=_bool("ENABLE_PYTHON_TOOL", True),
        python_timeout=_int("PYTHON_TIMEOUT", 20),
        enable_browser=_bool("ENABLE_BROWSER"),
        browser_executable_path=_str("BROWSER_EXECUTABLE_PATH"),
        browser_timeout=_int("BROWSER_TIMEOUT", 20),
        max_request_bytes=_int("MAX_REQUEST_BYTES", 64_000),
        chat_rate_limit=_int("CHAT_RATE_LIMIT", 30),
    )


settings = load_settings()


def startup_problems(s=None):
    """Return reasons the app should refuse to start. Empty list means OK."""
    s = s or settings
    problems = []

    is_local = s.host in LOOPBACK_HOSTS
    if not is_local and not s.auth_enabled and not s.docker_local_only:
        problems.append(
            f"HOST is set to {s.host}, which exposes ClawMind to the network, but "
            "AUTH_ENABLED is false. Enable authentication (python run.py --set-password) "
            "or bind to 127.0.0.1."
        )
    if s.auth_enabled and not s.auth_password_hash:
        problems.append(
            "AUTH_ENABLED is true but AUTH_PASSWORD_HASH is empty. "
            "Run: python run.py --set-password"
        )

    workspace = s.workspace_dir.resolve()
    if workspace == BASE_DIR or BASE_DIR.is_relative_to(workspace):
        problems.append("WORKSPACE_DIR must not be the project folder or one of its parents.")
    if s.data_dir.resolve().is_relative_to(workspace):
        problems.append("DATA_DIR must not be inside WORKSPACE_DIR (the agent could read the database).")

    return problems
