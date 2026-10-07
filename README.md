
# ClawMind

ClawMind is a personal AI agent that runs on your own computer. You chat with it in the browser, and it can decide to use tools: search the web, read pages, work with files in a sandboxed `workspace/` folder, run small Python analyses, remember things you tell it to, and set reminders or recurring tasks.

It works with any OpenAI-compatible API, including a local model through Ollama. Everything (database, memory, files) stays on your machine.

```
python run.py
```

That's the whole setup. Then open http://localhost:8000.

---

## Features

- **Chat UI** with live progress ("Searching the web...", "Reading file...") instead of hidden reasoning
- **Agent loop** with tool calling and hard limits (12 steps, 20 tool calls per request)
- **Planner** that writes a short step-by-step plan for bigger tasks like "analyze this CSV and write a report"
- **Tools**: calculator, date/time, web search, webpage reader, file tools, Python analysis, headless browser, memory, scheduler
- **Memory**: recent conversation (short-term) plus facts you explicitly ask it to remember (long-term). Secrets are refused.
- **Semantic memory search** when an embedding model is configured, keyword search otherwise
- **Scheduler**: one-time, daily, weekly and interval tasks, stored in SQLite and reloaded on restart
- **Human confirmation** for destructive actions (deleting files, memories or tasks)
- **Security**: workspace-only file access, SSRF protection, sandboxed Python, prompt-injection wrapping, local-only by default
- **Offline mode**: with no model configured the app still starts and handles simple commands
- Works on Windows, macOS and Linux. Docker setup included.

## Architecture

```
Browser (frontend/)
   │  fetch + streamed progress (NDJSON)
   ▼
FastAPI (app/main.py) ── auth, rate limits, size limits, host/origin checks
   │
   ▼
Agent (app/agent.py)
   ├── memory lookup (app/memory.py)
   ├── planner for bigger tasks (app/planner.py)
   ├── LLM client (app/llm.py, any OpenAI-compatible API)
   └── tool registry (app/tools/registry.py)
          ├── calculator, get_datetime        (basic.py)
          ├── web_search, fetch_webpage       (web.py)
          ├── list/read/write/delete files    (filesystem.py)
          ├── run_python                      (python_exec.py)
          ├── browser_open, browser_screenshot(browser.py)
          ├── remember, forget, search...     (memory_tools.py)
          └── schedule_task, list/cancel      (task_tools.py)

SQLite (data/clawmind.db): messages, memories, scheduled_tasks, tool_logs, agent_runs
APScheduler runs inside the same process (app/scheduler.py)
```

One request goes like this:

1. The user message is saved and recent messages for the session are loaded.
2. Relevant memories are looked up and added to the system prompt.
3. If the task looks complex, the model is asked for a JSON plan (validated with Pydantic).
4. The model is called with the tool list. If it asks for tools, they run and the results go back to it wrapped as untrusted data. This repeats until it gives a final answer or a limit is hit.
5. If the model asks for a DANGEROUS tool, the run stops in the `PENDING_CONFIRMATION` state and the user is asked first.

## Installation

You need **Python 3.10 or newer**. Nothing else has to be installed by hand.

```bash
git clone <your fork> ClawMind
cd ClawMind
python run.py          # use "py run.py" or "python3 run.py" if that's how Python is called on your system
```

On the first run `run.py`:

1. checks your Python version and OS
2. creates `.venv/` and installs `requirements.txt` into it (takes a few minutes the first time)
3. copies `.env.example` to `.env`
4. creates `data/` and `workspace/` and the SQLite database
5. starts the server on http://localhost:8000

Later runs skip steps that are already done. If `requirements.txt` changes, the dependencies are reinstalled automatically.

Options:

| Command | What it does |
|---|---|
| `python run.py` | Normal start |
| `python run.py --no-install` | Use the current Python environment as-is (Docker, CI, your own venv) |
| `python run.py --set-password` | Turn on login and save a password hash to `.env` |

## Configuring the AI model

Edit `.env` and restart. Any OpenAI-compatible chat API works.

**OpenAI**

```env
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
EMBEDDING_MODEL=text-embedding-3-small
```

**Ollama (local, free)**

1. Install Ollama from https://ollama.com and start it.
2. Pull a model that supports tool calling, plus an embedding model if you want semantic memory:
   ```bash
   ollama pull llama3.2
   ollama pull nomic-embed-text
   ```
3. Configure:
   ```env
   LLM_BASE_URL=http://localhost:11434/v1
   LLM_API_KEY=ollama
   LLM_MODEL=llama3.2
   EMBEDDING_MODEL=nomic-embed-text
   ```

Small local models (3B) follow instructions less reliably than large hosted models. If tools are being ignored, try a bigger model such as `qwen2.5:7b` or `llama3.1:8b`.

**Other providers** (Groq, OpenRouter, Together, LM Studio, vLLM, ...) work the same way: set their OpenAI-compatible base URL, key and model name.

**No model at all**: ClawMind still starts. The chat tells you AI reasoning is unavailable and handles a few direct commands:

```
calculate 125 * 42
what time is it?
remember that my project is called ClawMind
list memories / forget memory 3
remind me tomorrow at 5 PM to study DSA
list files / read study.md
```

## Web search

| `SEARCH_PROVIDER` | Needs | Notes |
|---|---|---|
| `duckduckgo` (default) | nothing | Uses DuckDuckGo's HTML page. Fine for personal use but can be rate limited. |
| `brave` | `BRAVE_API_KEY` | Brave Search API, free tier available. Most reliable. |
| `searxng` | `SEARXNG_URL` | Your own SearXNG instance with JSON output enabled. |

If search fails the tool returns an error like "Web search is unavailable: ..." and the agent tells you instead of making something up.

## Browser setup

The browser tool uses Playwright with headless Chromium. It is **off by default**.

```env
ENABLE_BROWSER=true
```

On the next start `run.py` downloads Chromium once (`python -m playwright install chromium`). If you already have Chrome or Chromium installed you can point to it instead:

```env
BROWSER_EXECUTABLE_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
```

Tools: `browser_open` (title + visible text of a JavaScript-rendered page) and `browser_screenshot` (PNG saved to `workspace/screenshots/`). If the browser can't start, `fetch_webpage` still works.

`open_page()` in `app/tools/browser.py` is the building block for future tools like clicking or typing.

## Running locally

```bash
python run.py
```

- UI: http://localhost:8000
- Health check: http://localhost:8000/health
- Logs: printed to the console and written to `data/clawmind.log`
- Your files: `workspace/` (the agent can't see anything else)

Set `DEBUG=true` to get the interactive API docs at `/docs`. Don't use it on a shared network.

## Docker

```bash
docker compose up --build
```

Then open http://localhost:8000. The compose file:

- publishes the port on **127.0.0.1 only**
- runs as your user id (`UID`/`GID`, default 1000), never as root
- mounts only `./workspace` and `./data`
- uses a read-only root filesystem, drops all Linux capabilities and sets `no-new-privileges`
- reads your `.env` if it exists

To use Ollama running on the host, set `LLM_BASE_URL=http://host.docker.internal:11434/v1`.

To include Chromium for the browser tool, build with `INSTALL_BROWSER: "true"` in `docker-compose.yml` and set `ENABLE_BROWSER=true`.

Inside the container the server listens on `0.0.0.0` so the port can be published. ClawMind refuses to do that without a password, unless `DOCKER_LOCAL_ONLY=true` is set, which the compose file does because it only publishes on 127.0.0.1. If you change the port mapping to `0.0.0.0`, remove `DOCKER_LOCAL_ONLY` and set a password.

Docker is also the recommended way to run ClawMind if you want stronger isolation for the Python tool.

## Security model

ClawMind is a **single-user, local-first** app. The defaults assume you're the only person using it, on your own machine.

**Network exposure**
- Binds to `127.0.0.1` by default. Any other `HOST` requires `AUTH_ENABLED=true`, otherwise the app refuses to start.
- Login uses a PBKDF2-SHA256 password hash (600k iterations) stored in `.env`. The plaintext password is never stored. Session tokens are random, kept only as SHA-256 hashes in memory, sent as `HttpOnly; SameSite=Strict` cookies (add `COOKIE_SECURE=true` behind HTTPS).
- The `Host` header must be localhost or one of `ALLOWED_HOSTS`. This blocks DNS-rebinding attacks from malicious websites against the local server.
- Write requests must be `application/json` and come from the same origin (or `CORS_ORIGINS`). A random website you visit can't make your browser drive the agent.
- Request bodies are limited to 64 KB (including chunked uploads), chat is rate limited per client (30/min by default), login to 5 attempts per minute.
- Error responses never contain stack traces. Real errors go to the server log.
- Security headers: strict CSP (no inline scripts), `X-Frame-Options: DENY`, `nosniff`, `no-referrer`.

**Files**
- Every path is resolved and must stay inside `workspace/`. Absolute paths, `..`, Windows drive/UNC paths and `~` are rejected.
- Symlinks pointing outside the workspace are rejected (the resolved path is checked).
- Hidden files (`.env`, `.ssh`, ...), keys (`*.pem`, `*.key`, `id_rsa`) and databases (`*.db`, `*.sqlite`) are blocked even inside the workspace.
- The app refuses to start if the workspace is the project folder or contains the data folder.

**Web (SSRF protection)**
- Only `http`/`https` on ports 80, 443, 8080, 8443. No credentials in URLs.
- Hostnames like `localhost`, `*.local`, `*.internal`, single-word names (`redis`, `db`) and cloud metadata hosts are blocked.
- The hostname is resolved and **every** returned IP must be public. Loopback, private, link-local (`169.254.169.254`), CGNAT, multicast, reserved, IPv4-mapped IPv6, NAT64, 6to4 and Teredo addresses are rejected.
- The request is sent to the IP that was checked (with the right `Host` header and TLS SNI), so the DNS answer can't change between the check and the request.
- Redirects are followed manually (max 5) and every hop is checked again.
- Responses must be a text content type and are capped at 2 MB, with a 15 s timeout.

**Python tool** (controlled execution, **not** a perfect sandbox)
- Static check: imports must come from an allowlist (math, statistics, json, csv, re, datetime, collections, numpy, pandas, matplotlib, ...). `eval`, `exec`, `__import__`, `getattr`, dunder attributes and names like `.os`, `.system`, `.environ` are rejected.
- Runs in a separate process (`python -I`) in a fresh temp folder with an almost empty environment, so `LLM_API_KEY` and other secrets aren't inherited.
- A PEP 578 audit hook inside that process blocks process creation, sockets, ctypes, and reading or writing files outside the temp folder, even if the static check is bypassed.
- Timeout (20 s), plus memory (1 GB), CPU-time and file-size limits on Linux (and on macOS where the OS honours them).
- Only the files you pass in are copied into the sandbox; files it creates are copied to `workspace/outputs/`.
- For truly untrusted code, run ClawMind in Docker.

**Prompt injection**
- The system prompt tells the model that tool output is untrusted data and must never be followed as instructions, and that it can't change permissions or what the user authorised.
- Every tool result is wrapped in `<untrusted_data source="...">` blocks. Fake closing tags inside the content are neutralised.
- Destructive tools need a click from the user, so even a successful injection can't delete anything silently.
- Memories are only saved when the user asks, and are labelled as notes, not instructions.

**Secrets**
- Secrets live in `.env`, which is in `.gitignore` and `.dockerignore`. `.env.example` has empty values.
- No tool can read `.env` or environment variables. `/api/status` reports only whether a model is configured, never the key.
- Logs pass through a redaction filter that masks API keys, tokens, card numbers and `password=...` pairs. Tool arguments are redacted before they are stored in `tool_logs`.
- Memory refuses anything that looks like a password, API key, token, private key, card number (Luhn check), OTP or security answer.
- All database access goes through SQLAlchemy with bound parameters.

## Threat model

These are covered by the test suite (`tests/`):

| Area | What is tested |
|---|---|
| Filesystem | `../`, `../../secret.txt`, `/etc/passwd`, `C:\Windows\System32`, `..\..\` , UNC paths, `~`, symlinks to files and folders outside the workspace, `.env`, keys, `.db` files, Windows reserved names |
| SSRF | `localhost`, `127.0.0.1`, `127.1`, `0.0.0.0`, `[::1]`, `[::ffff:127.0.0.1]`, `169.254.169.254`, `metadata.google.internal`, 10/8, 172.16/12, 192.168/16, 100.64/10, IPv6 ULA and link-local, decimal/hex IPs, single-label hosts, `.local`/`.internal`, `file://`, `ftp://`, `gopher://`, credentials in URL, non-web ports, DNS answers with private IPs, mixed public/private answers, redirect to the metadata endpoint |
| Command injection | File names containing `;`, `&&`, `\|\|`, `\|`, `` ` ``, `$()` are treated as plain names; nothing ever goes through a shell |
| Python escape | `os`, `subprocess`, `socket`, `shutil`, `ctypes`, `sys`, `importlib`, `__import__`, `eval`, `exec`, `compile`, `getattr`, dunder chains, `pandas.io.common.os`, reading `/etc/passwd` or project files, writing outside the sandbox, inherited secrets, infinite loops. The runtime guard is also tested with the static check switched off. |
| Prompt injection | A file containing "ignore previous instructions", a fake `</untrusted_data>` tag and a request to delete files: content stays wrapped, the delete waits for confirmation |
| API abuse | Huge bodies (also chunked), invalid JSON, wrong types, unknown fields, bad session ids, wrong content type, cross-origin POSTs, foreign Host headers, rate limits, login brute force, no stack traces or secrets in responses |

Known gaps are listed under [Limitations](#limitations).

## Tool permissions

| Tool | Permission | Notes |
|---|---|---|
| `calculator` | SAFE | AST-based, no `eval` |
| `get_datetime` | SAFE | |
| `web_search` | READ | |
| `fetch_webpage` | READ | SSRF-protected |
| `list_files`, `read_file` | READ | workspace only |
| `search_memories`, `list_memories` | READ | |
| `list_tasks` | READ | |
| `write_file`, `create_directory` | WRITE | workspace only, no silent overwrite |
| `run_python` | WRITE | sandboxed, `ENABLE_PYTHON_TOOL` |
| `remember` | WRITE | only when asked, secrets refused |
| `schedule_task` | WRITE | |
| `browser_open`, `browser_screenshot` | EXTERNAL | off unless `ENABLE_BROWSER=true` |
| `delete_file` | DANGEROUS | needs confirmation |
| `forget` | DANGEROUS | needs confirmation |
| `cancel_task` | DANGEROUS | needs confirmation |

DANGEROUS tools can't run without the user's confirmation: the registry itself refuses them unless the call is marked as confirmed. They are also not offered to the model in scheduled agent tasks, since nobody is there to confirm.

To add a tool, write a function in `app/tools/` and decorate it:

```python
@tool(
    name="word_count",
    description="Count words in a text.",
    parameters={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
    permission=Permission.SAFE,
    activity="Counting words...",
)
def word_count(text):
    return {"success": True, "words": len(text.split())}
```

and import the module in `app/tools/load.py`.

## API endpoints

All `/api` routes except `/api/auth` and `/api/login` require login when `AUTH_ENABLED=true` (cookie or `Authorization: Bearer <token>`). POST bodies must be JSON.

| Method | Path | Description |
|---|---|---|
| GET | `/health` | `{"status": "ok"}` |
| POST | `/api/chat` | `{"message", "session_id"?}` → `{"response", "session_id", "tools_used", "plan", "status", "pending_confirmation", ...}` |
| POST | `/api/chat/stream` | Same, streamed as newline-delimited JSON (`activity` events, then `result`) |
| POST | `/api/confirm` | `{"run_id", "approve": true/false, "session_id"}` for a pending action |
| GET | `/api/sessions/{id}/messages` | Chat history for a session |
| GET | `/api/memory` | List memories, `?q=` to search |
| POST | `/api/memory` | `{"content": "..."}` |
| DELETE | `/api/memory/{id}` | Delete a memory |
| GET | `/api/tasks` | List scheduled tasks |
| POST | `/api/tasks` | `{"title", "when", "action": "reminder"\|"agent", "session_id"?}` |
| DELETE | `/api/tasks/{id}` | Delete a task |
| GET | `/api/notifications?after=<id>` | Reminders and task results that fired |
| GET | `/api/status` | Configuration summary and tool list (no secrets) |
| GET | `/api/auth`, POST `/api/login`, POST `/api/logout` | Authentication |

Example:

```bash
curl -s http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Calculate 25 * 48 + 100", "session_id": "abc"}'
```

## Examples

| You say | What happens |
|---|---|
| Calculate 125 * 42. | `calculator` → 5250 |
| What time is it? | `get_datetime` |
| Create a file called study.md containing DSA, OS, Computer Networks | `write_file` creates `workspace/study.md` |
| Read study.md and summarize it. | `read_file`, then a summary |
| Search the web for the latest Python release. | `web_search` (maybe `fetch_webpage`), then an answer with links |
| Remember that my project is called ClawMind. | `remember` |
| What is my project called? | answered from memory |
| Remind me tomorrow at 5 PM to study DSA. | `schedule_task`; the reminder appears in the chat at 17:00 |
| Analyze sample_sales.csv and create a report | plan → `read_file` → `run_python` (pandas, charts saved to `outputs/`) → `write_file` report |
| Delete report.md | asks "I am ready to delete the file report.md... Do you want me to continue?" |

`workspace/sample_sales.csv` is included so you can try the analysis example right away.

Schedule phrases that work: `in 30 minutes`, `in 2 hours`, `today at 18:00`, `tomorrow at 5 PM`, `at 9am`, `on friday at 3pm`, `next monday at 9:00`, `every day at 9am`, `every monday at 10:00`, `every 2 hours`, `hourly`, or an ISO date like `2026-12-25T08:00`. Times use your computer's timezone.

## Running tests

```bash
.venv/bin/pip install -r requirements-dev.txt      # Windows: .venv\Scripts\pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check .
```

The agent tests use a scripted fake model, so no API key is needed. The real-browser test is skipped when Chromium isn't installed (set `CLAWMIND_TEST_BROWSER=/path/to/chrome` to use an existing binary).

## Troubleshooting

**"AI reasoning is unavailable"**: `LLM_BASE_URL` or `LLM_MODEL` is empty in `.env`. Fill them in and restart.

**"Could not connect to the AI model"**: the server at `LLM_BASE_URL` isn't reachable. For Ollama, check `ollama serve` is running and the URL ends in `/v1`.

**"The AI provider rejected the request"**: wrong or missing `LLM_API_KEY`.

**The model never uses tools**: the model may not support tool calling. Use a tool-capable model (gpt-4o-mini, llama3.1/3.2, qwen2.5, mistral-nemo, ...).

**Web search says it's unavailable**: DuckDuckGo may be rate limiting you. Wait a bit, or switch to `SEARCH_PROVIDER=brave` with a free API key.

**Browser tool errors**: run `python -m playwright install chromium` inside the venv (`.venv/bin/python -m playwright install chromium`), or set `BROWSER_EXECUTABLE_PATH`. On Linux, Chromium may also need system libraries: `python -m playwright install-deps chromium`.

**ClawMind refused to start: HOST is set to ...**: you tried to listen on the network without a password. Run `python run.py --set-password` or set `HOST=127.0.0.1`.

**Port 8000 already in use**: set `PORT=8001` in `.env`.

**Reminders don't show up**: they appear in the chat of the session that created them and as a toast in any open ClawMind tab (polled every 20 s). Enable desktop notifications in Settings to get them outside the tab. The server must be running at that time; one-time tasks missed by less than 12 hours run right after the next start.

**Reset everything**: stop the app and delete the `data/` folder. Your `workspace/` files are not touched.

## Project structure

```
ClawMind/
├── app/
│   ├── main.py          FastAPI app, middleware, routes
│   ├── config.py        settings from .env, startup safety checks
│   ├── db.py            SQLAlchemy models and session
│   ├── llm.py           OpenAI-compatible client
│   ├── agent.py         agent loop, confirmations
│   ├── planner.py       JSON plans for bigger tasks
│   ├── memory.py        long-term memory + chat history
│   ├── scheduler.py     APScheduler + scheduled_tasks table
│   ├── timeparse.py     "tomorrow at 5 PM" → datetime
│   ├── offline.py       commands that work without a model
│   ├── auth.py          password hashing, sessions, rate limiter
│   ├── redact.py        secret detection for memory and logs
│   └── tools/
│       ├── registry.py      Tool, permissions, run_tool()
│       ├── basic.py         calculator, get_datetime
│       ├── filesystem.py    workspace file tools
│       ├── web.py           search + SSRF-safe fetching
│       ├── python_exec.py   sandboxed Python
│       ├── browser.py       Playwright tools
│       ├── memory_tools.py  remember / forget / search
│       ├── task_tools.py    schedule / list / cancel
│       └── load.py          imports all tool modules
├── frontend/            index.html, style.css, app.js (no build step, no CDN)
├── workspace/           the only folder the agent can touch
├── data/                database, logs (created on first run, git-ignored)
├── tests/               pytest suite
├── run.py               one-command launcher
├── requirements.txt / requirements-dev.txt
├── Dockerfile / docker-compose.yml
└── .env.example
```

## Limitations

- **Python sandbox**: the audit-hook approach blocks the common escape routes and is tested against them, but CPython was never designed as a security boundary. Someone determined, with code execution, may find a way around it. Use Docker for real isolation. On Windows there are no memory/CPU limits, only the timeout.
- **Browser and DNS rebinding**: `fetch_webpage` pins the checked IP, but Chromium does its own DNS lookups, so the browser tool checks each request's host right before Chromium fetches it. A fast DNS-rebinding attack could slip through that gap. WebSocket connections opened by a page aren't filtered. That's one reason the browser is off by default.
- **Prompt injection** can't be fully solved by prompting. The wrapping and confirmation step limit the damage, but a model could still be tricked into, for example, summarising a page misleadingly or writing a file in the workspace.
- **Confirmations** run only the confirmed action. The agent doesn't continue the rest of the original task afterwards; ask again if there's more to do.
- **Single user**: one password, no user accounts. Login sessions are in memory, so restarting logs you out.
- **Scheduler** runs inside the web process. If ClawMind isn't running, nothing fires (missed one-time tasks within 12 h run at the next start; missed recurring runs are skipped).
- **DuckDuckGo search** scrapes the HTML endpoint and can break or get rate limited. Brave or SearXNG are more reliable.
- **Memory search** without embeddings is simple keyword overlap.
- Only text files can be read directly. PDFs, Excel files and images need `run_python` (pandas can read Excel if `openpyxl` is installed).

## Future improvements

- Persistent browser sessions with click/type/fill tools built on `open_page()`
- Email and messaging tools (the confirmation flow is ready for them)
- Resume the original task after a confirmation
- PDF and DOCX reading tools
- Running the Python tool in a throwaway container when Docker is available
- Multiple users with separate workspaces
- A proper vector store (ChromaDB/sqlite-vec) for large memory collections

# ClawMind-Autonomous-AI-Agent
An intelligent AI-powered autonomous assistant designed to simplify tasks through tool integration, persistent memory, scheduled automation, file analysis, and local LLM support. Built to make AI more practical, extensible, secure, and useful for everyday workflows, productivity, and development tasks.