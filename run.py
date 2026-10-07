"""Start ClawMind with one command: python run.py

What it does:
  1. checks the Python version
  2. creates .venv and installs requirements.txt (skipped with --no-install)
  3. creates .env from .env.example if it's missing
  4. creates the data/ and workspace/ folders and the database
  5. starts the web server and prints the URL

Other options:
  python run.py --no-install     use the current Python as-is (Docker, CI)
  python run.py --set-password   turn on login and store a password hash in .env
"""

import argparse
import getpass
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV_DIR / ".requirements-hash"
MIN_PYTHON = (3, 10)


def say(message):
    print(f"[clawmind] {message}", flush=True)


def venv_python():
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def running_in_venv():
    return Path(sys.prefix).resolve() == VENV_DIR.resolve()


def requirements_hash():
    return hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()


def ensure_venv():
    python = venv_python()
    if not python.exists():
        say(f"Creating virtual environment in {VENV_DIR.name}/ ...")
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)

    if STAMP.exists() and STAMP.read_text().strip() == requirements_hash():
        return python

    say("Installing dependencies (first run can take a few minutes) ...")
    cmd = [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)]
    if subprocess.call(cmd) != 0:
        say("Dependency installation failed. Check your internet connection and try again.")
        sys.exit(1)
    STAMP.write_text(requirements_hash())
    return python


def ensure_env_file():
    env_file = ROOT / ".env"
    example = ROOT / ".env.example"
    if env_file.exists() or not example.exists():
        return
    try:
        shutil.copyfile(example, env_file)
        say("Created .env from .env.example. Edit it to configure your AI model.")
    except OSError:
        say("Could not create .env (read-only folder?). Using environment variables only.")


def set_env_value(key, value):
    env_file = ROOT / ".env"
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    found = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def set_password():
    from app.auth import hash_password

    password = getpass.getpass("New ClawMind password: ")
    if len(password) < 10:
        say("Please use at least 10 characters.")
        sys.exit(1)
    if getpass.getpass("Repeat password: ") != password:
        say("Passwords did not match.")
        sys.exit(1)
    # A "$" inside the hash would be read as a variable by python-dotenv, so quote it
    set_env_value("AUTH_PASSWORD_HASH", "'" + hash_password(password) + "'")
    set_env_value("AUTH_ENABLED", "true")
    say("Password saved as a hash in .env and authentication is enabled.")


def maybe_install_browser():
    from app.config import settings

    if not settings.enable_browser or settings.browser_executable_path:
        return
    marker = settings.data_dir / ".playwright-installed"
    if marker.exists():
        return
    say("ENABLE_BROWSER=true: installing the Chromium browser for Playwright ...")
    if subprocess.call([sys.executable, "-m", "playwright", "install", "chromium"]) == 0:
        marker.write_text("ok")
    else:
        say("Browser install failed. The browser tool will be unavailable; fetch_webpage still works.")


def serve():
    from app.config import settings, startup_problems

    problems = startup_problems()
    if problems:
        for problem in problems:
            say("ERROR: " + problem)
        sys.exit(1)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)

    from app.db import init_db

    init_db(settings.database_url)
    maybe_install_browser()

    import uvicorn

    host_for_url = "localhost" if settings.host in ("127.0.0.1", "0.0.0.0", "::") else settings.host
    say(f"Workspace: {settings.workspace_dir}")
    if not settings.llm_available:
        say("No AI model configured yet: set LLM_BASE_URL and LLM_MODEL in .env (offline commands still work).")
    say(f"ClawMind is running at http://{host_for_url}:{settings.port}  (Ctrl+C to stop)")

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level="info",
        access_log=False,
        server_header=False,
        proxy_headers=False,
    )


def main():
    parser = argparse.ArgumentParser(description="Run ClawMind")
    parser.add_argument("--no-install", action="store_true", help="don't create a venv or install packages")
    parser.add_argument("--set-password", action="store_true", help="enable login and set the password")
    args = parser.parse_args()

    if sys.version_info < MIN_PYTHON:
        say(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required, you have {platform.python_version()}.")
        sys.exit(1)

    os.chdir(ROOT)
    say(f"Python {platform.python_version()} on {platform.system() or 'unknown OS'}")
    ensure_env_file()

    if not args.no_install and not running_in_venv():
        python = ensure_venv()
        # Re-run this script inside the virtual environment
        cmd = [str(python), str(ROOT / "run.py"), "--no-install"]
        if args.set_password:
            cmd.append("--set-password")
        try:
            sys.exit(subprocess.call(cmd))
        except KeyboardInterrupt:
            sys.exit(0)

    sys.path.insert(0, str(ROOT))
    if args.set_password:
        set_password()
        return
    try:
        serve()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
