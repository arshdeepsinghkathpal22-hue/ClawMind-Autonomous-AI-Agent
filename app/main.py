import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Path, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import auth, memory, scheduler
from app.agent import confirm_action, run_agent
from app.config import BASE_DIR, LOOPBACK_HOSTS, settings, startup_problems
from app.db import Message, get_session, init_db
from app.redact import redact
from app.tools.load import load_tools
from app.tools.registry import TOOLS

log = logging.getLogger("clawmind")

FRONTEND_DIR = BASE_DIR / "frontend"
SESSION_ID = r"^[A-Za-z0-9_-]{1,64}$"
VERSION = "1.0.0"

chat_limiter = auth.RateLimiter(settings.chat_rate_limit)
login_limiter = auth.RateLimiter(5)
write_limiter = auth.RateLimiter(60)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def setup_logging():
    root = logging.getLogger()
    if getattr(root, "_clawmind_logging", False):
        return
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    formatter = RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(settings.data_dir / "clawmind.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"),
    ]
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    for noisy in ("httpx", "httpcore", "apscheduler", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._clawmind_logging = True


@asynccontextmanager
async def lifespan(_app):
    setup_logging()
    problems = startup_problems()
    if problems:
        for problem in problems:
            log.error(problem)
        raise RuntimeError("ClawMind refused to start: " + " ".join(problems))

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    init_db(settings.database_url)
    load_tools()
    scheduler.start()
    if not settings.llm_available:
        log.warning("No LLM configured (LLM_BASE_URL / LLM_MODEL). Running in offline mode.")
    log.info("ClawMind %s ready", VERSION)
    yield
    scheduler.shutdown()


app = FastAPI(
    title="ClawMind",
    version=VERSION,
    lifespan=lifespan,
    docs_url="/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.debug else None,
)


# ---------- middleware ----------

class BodyLimitMiddleware:
    """Reject request bodies larger than MAX_REQUEST_BYTES, even when chunked.

    Bodies are small JSON documents, so the whole body is read here first and
    then handed to the app.
    """

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return

        limit = settings.max_request_bytes
        too_large = JSONResponse({"error": "Request body is too large."}, status_code=413)
        for name, value in scope.get("headers", []):
            if name == b"content-length" and (not value.isdigit() or int(value) > limit):
                await too_large(scope, receive, send)
                return

        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > limit:
                await too_large(scope, receive, send)
                return
            more = message.get("more_body", False)

        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def _host_only(host_header):
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _allowed_hosts():
    hosts = set(LOOPBACK_HOSTS) | {h.lower() for h in settings.allowed_hosts}
    if settings.host not in ("0.0.0.0", "::"):
        hosts.add(settings.host.lower())
    return hosts


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    ),
}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Host check stops DNS-rebinding attacks against a local server
    if _host_only(request.headers.get("host")) not in _allowed_hosts():
        return JSONResponse({"error": "Invalid Host header."}, status_code=400)

    if request.url.path.startswith("/api/") and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Same-origin check stops other websites from driving the agent (CSRF)
        origin = request.headers.get("origin")
        if origin and origin != "null":
            same_origin = urlsplit(origin).netloc.lower() == (request.headers.get("host") or "").lower()
            if not same_origin and origin not in settings.cors_origins:
                return JSONResponse({"error": "Cross-origin request blocked."}, status_code=403)
        elif origin == "null":
            return JSONResponse({"error": "Cross-origin request blocked."}, status_code=403)
        if request.method != "DELETE":
            content_type = request.headers.get("content-type", "")
            if not content_type.startswith("application/json"):
                return JSONResponse({"error": "Content-Type must be application/json."}, status_code=415)

    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        if name == "Content-Security-Policy" and settings.debug and request.url.path in ("/docs", "/openapi.json"):
            continue
        response.headers.setdefault(name, value)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.add_middleware(BodyLimitMiddleware)

if settings.cors_origins:
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "Authorization"],
        allow_credentials=True,
    )


# ---------- error handlers ----------

@app.exception_handler(RequestValidationError)
async def validation_error(_request, exc):
    details = []
    for err in exc.errors()[:10]:
        field = ".".join(str(p) for p in err.get("loc", []) if p != "body")
        details.append({"field": field or "body", "problem": err.get("msg", "invalid value")})
    return JSONResponse({"error": "Invalid request.", "details": details}, status_code=422)


@app.exception_handler(StarletteHTTPException)
async def http_error(_request, exc):
    return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    log.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"error": "Something went wrong on the server."}, status_code=500)


# ---------- request models ----------

class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(StrictModel):
    message: StrictStr = Field(min_length=1, max_length=8000)
    session_id: StrictStr | None = Field(default=None, pattern=SESSION_ID)


class ConfirmRequest(StrictModel):
    run_id: StrictInt = Field(ge=1)
    approve: StrictBool
    session_id: StrictStr = Field(pattern=SESSION_ID)


class MemoryCreate(StrictModel):
    content: StrictStr = Field(min_length=1, max_length=500)


class TaskCreate(StrictModel):
    title: StrictStr = Field(min_length=1, max_length=500)
    when: StrictStr = Field(min_length=1, max_length=200)
    action: Literal["reminder", "agent"] = "reminder"
    session_id: StrictStr | None = Field(default=None, pattern=SESSION_ID)


class LoginRequest(StrictModel):
    password: StrictStr = Field(min_length=1, max_length=200)


# ---------- helpers ----------

def client_key(request):
    return request.client.host if request.client else "unknown"


def rate_limit(limiter, request):
    if not limiter.allow(client_key(request)):
        raise HTTPException(status_code=429, detail="Too many requests. Please slow down.")


def request_token(request):
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(auth.COOKIE_NAME)


def require_auth(request: Request):
    if settings.auth_enabled and not auth.check_session(request_token(request)):
        raise HTTPException(status_code=401, detail="Login required.")


# ---------- public routes ----------

@app.get("/health")
def health():
    return {"status": "ok", "version": VERSION}


@app.get("/api/auth")
def auth_status(request: Request):
    logged_in = not settings.auth_enabled or auth.check_session(request_token(request))
    return {"auth_enabled": settings.auth_enabled, "logged_in": logged_in}


@app.post("/api/login")
def login(body: LoginRequest, request: Request, response: Response):
    if not settings.auth_enabled:
        return {"ok": True, "auth_enabled": False}
    rate_limit(login_limiter, request)
    if not auth.verify_password(body.password, settings.auth_password_hash):
        log.warning("Failed login from %s", client_key(request))
        raise HTTPException(status_code=401, detail="Wrong password.")
    token = auth.create_session()
    response.set_cookie(
        auth.COOKIE_NAME, token, max_age=auth.SESSION_SECONDS, httponly=True,
        samesite="strict", secure=settings.cookie_secure, path="/",
    )
    return {"ok": True, "token": token}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    auth.end_session(request_token(request))
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"ok": True}


# ---------- protected routes ----------

api = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@api.get("/status")
def status():
    return {
        "version": VERSION,
        "llm_configured": settings.llm_available,
        "llm_model": settings.llm_model or None,
        "llm_host": urlsplit(settings.llm_base_url).hostname if settings.llm_base_url else None,
        "embeddings": bool(settings.embedding_model) and settings.llm_available,
        "search_provider": settings.search_provider,
        "python_tool": settings.enable_python,
        "browser_tool": settings.enable_browser,
        "auth_enabled": settings.auth_enabled,
        "max_agent_steps": settings.max_agent_steps,
        "max_tool_calls": settings.max_tool_calls,
        "tools": [
            {"name": t.name, "permission": t.permission.value, "enabled": t.enabled()}
            for t in sorted(TOOLS.values(), key=lambda t: t.name)
        ],
    }


@api.post("/chat")
async def chat(body: ChatRequest, request: Request):
    rate_limit(chat_limiter, request)
    session_id = body.session_id or uuid.uuid4().hex
    result = await run_in_threadpool(run_agent, body.message, session_id)
    return result.to_dict()


@api.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request):
    """Same as /api/chat but streams progress as newline-delimited JSON."""
    rate_limit(chat_limiter, request)
    session_id = body.session_id or uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    queue = asyncio.Queue()

    def push(event):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work():
        try:
            result = run_agent(body.message, session_id,
                               on_event=lambda text: push({"type": "activity", "text": text}))
            push({"type": "result", **result.to_dict()})
        except Exception:
            log.exception("Chat stream failed")
            push({"type": "error", "error": "Something went wrong on the server."})
        finally:
            push(None)

    loop.run_in_executor(None, work)

    async def events():
        yield json.dumps({"type": "start", "session_id": session_id}) + "\n"
        while True:
            event = await queue.get()
            if event is None:
                break
            yield json.dumps(event) + "\n"

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


@api.post("/confirm")
async def confirm(body: ConfirmRequest, request: Request):
    rate_limit(chat_limiter, request)
    result = await run_in_threadpool(confirm_action, body.run_id, body.approve, body.session_id)
    return result.to_dict()


@api.get("/sessions/{session_id}/messages")
def session_messages(session_id: str = Path(pattern=SESSION_ID)):
    rows = memory.recent_messages(session_id, limit=100, roles=("user", "assistant", "notification"))
    return {"session_id": session_id, "messages": [memory.message_to_dict(r) for r in rows]}


@api.get("/memory")
def get_memories(q: str | None = Query(default=None, max_length=200)):
    if q:
        return {"memories": memory.search_memories(q, limit=20)}
    return {"memories": memory.list_memories()}


@api.post("/memory", status_code=201)
def create_memory(body: MemoryCreate, request: Request):
    rate_limit(write_limiter, request)
    try:
        return memory.add_memory(body.content)
    except memory.MemoryRejected as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@api.delete("/memory/{memory_id}")
def remove_memory(memory_id: int = Path(ge=1)):
    if not memory.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found.")
    return {"deleted": memory_id}


@api.get("/tasks")
def get_tasks():
    return {"tasks": scheduler.list_tasks()}


@api.post("/tasks", status_code=201)
def create_task(body: TaskCreate, request: Request):
    rate_limit(write_limiter, request)
    try:
        return scheduler.create_task(body.title, body.when, body.action, body.session_id or "default")
    except scheduler.TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@api.delete("/tasks/{task_id}")
def remove_task(task_id: int = Path(ge=1)):
    if not scheduler.delete_task(task_id):
        raise HTTPException(status_code=404, detail="Task not found.")
    return {"deleted": task_id}


@api.get("/notifications")
def notifications(after: int = Query(default=0, ge=0)):
    with get_session() as db:
        stmt = (
            select(Message)
            .where(Message.role == "notification", Message.id > after)
            .order_by(Message.id.desc())
            .limit(50)
        )
        rows = list(db.scalars(stmt))
    rows.reverse()
    return {"notifications": [{**memory.message_to_dict(r), "session_id": r.session_id} for r in rows]}


app.include_router(api)

# The frontend is served last so /api and /health take priority
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
