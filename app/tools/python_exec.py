"""Controlled Python execution for calculations and data analysis.

This is NOT a perfect sandbox. Layers of protection:
  1. A static check (AST) that only allows imports from an allowlist and blocks
     eval/exec/__import__/dunder attribute tricks.
  2. A separate Python process in a fresh temporary folder, started in isolated
     mode (-I) with an almost empty environment (no API keys).
  3. A PEP 578 audit hook inside that process that blocks process creation,
     sockets, ctypes, and file access outside the temp folder.
  4. A timeout, plus CPU/memory/file-size limits on Linux and macOS.
For truly untrusted code, run ClawMind inside Docker (see README).
"""

import ast
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.config import settings
from app.tools.filesystem import resolve_path, workspace_root
from app.tools.registry import Permission, ToolError, tool

MAX_CODE_CHARS = 20_000
MAX_OUTPUT_CHARS = 20_000
MAX_INPUT_BYTES = 50_000_000
MAX_SAVED_FILES = 20
MAX_SAVED_FILE_BYTES = 10_000_000

ALLOWED_MODULES = {
    "math", "cmath", "statistics", "random", "decimal", "fractions", "numbers",
    "datetime", "time", "calendar", "zoneinfo",
    "json", "csv", "re", "string", "textwrap", "difflib", "unicodedata", "pprint",
    "collections", "itertools", "functools", "heapq", "bisect", "array", "copy",
    "dataclasses", "enum", "typing", "uuid", "hashlib", "base64",
    "numpy", "pandas", "matplotlib", "scipy", "seaborn",
}

BLOCKED_NAMES = {
    "__import__", "eval", "exec", "compile", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "breakpoint", "input", "help", "exit", "quit", "memoryview",
    "__builtins__", "__loader__", "__spec__",
}

# Runs inside the child process. It imports the libraries the script needs,
# then installs the audit hook, then runs the user's code.
RUNNER = r"""
import sys


def _install_guard():
    import os

    work = os.path.realpath(os.getcwd())
    sep = os.sep
    read_roots = [work]
    for p in [sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix] + list(sys.path):
        if p and os.path.isdir(p):
            read_roots.append(os.path.realpath(p))
    devices = {"/dev/null", "/dev/urandom", "/dev/random", "nul"}

    # ImportError (not PermissionError) so libraries that try-import these fall back cleanly
    blocked_imports = {"ctypes", "_ctypes", "cffi", "pty", "webbrowser", "multiprocessing", "_multiprocessing"}
    blocked_events = (
        "subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork",
        "os.forkpty", "os.startfile", "os.kill", "os.killpg", "os.putenv", "os.unsetenv",
        "os.setuid", "os.setgid", "pty.", "socket.", "ctypes.", "winreg.", "_winapi.",
        "msvcrt.", "webbrowser.", "sys.remote_exec", "os.symlink", "os.link",
        "cpython.remote_debugger", "urllib.Request", "sys.addaudithook",
    )
    write_events = {
        "os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.truncate",
        "os.utime", "os.chflags", "os.lchflags", "os.lchmod", "shutil.rmtree", "shutil.move",
        "shutil.copyfile", "shutil.copytree", "shutil.chown", "os.chdir",
    }
    list_events = {"os.listdir", "os.scandir", "glob.glob"}

    def inside(path, roots):
        try:
            path = os.path.realpath(os.fsdecode(path))
        except Exception:
            return False
        return any(path == r or path.startswith(r.rstrip(sep) + sep) for r in roots)

    def deny(what):
        raise PermissionError("Blocked by ClawMind sandbox: " + what)

    def is_path(value):
        return isinstance(value, (str, bytes)) or hasattr(value, "__fspath__")

    def hook(event, args):
        if event == "import":
            top = (args[0] or "").split(".")[0]
            if top in blocked_imports:
                raise ImportError("Module '" + top + "' is not available in the ClawMind sandbox")
            return
        if event.startswith(blocked_events):
            deny(event)
        if event == "open":
            path, mode, flags = (list(args) + [None, None, None])[:3]
            if not is_path(path):
                return
            writing = False
            if isinstance(mode, str):
                writing = any(c in mode for c in "wax+")
            elif isinstance(flags, int):
                writing = bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC))
            if writing:
                if not inside(path, [work]):
                    deny("writing outside the sandbox folder")
            elif not inside(path, read_roots) and os.fsdecode(path) not in devices:
                deny("reading " + os.fsdecode(path))
            return
        if event in write_events:
            for arg in args:
                if is_path(arg) and not inside(arg, [work]):
                    deny(event + " outside the sandbox folder")
            return
        if event in list_events and args and is_path(args[0]):
            if not inside(args[0], read_roots):
                deny(event + " outside the sandbox folder")

    try:
        import resource
        limit_mb = int(os.environ.get("CLAWMIND_MEMORY_MB", "1024"))
        cpu = int(os.environ.get("CLAWMIND_CPU_SECONDS", "30"))
        resource.setrlimit(resource.RLIMIT_AS, (limit_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu))
        resource.setrlimit(resource.RLIMIT_FSIZE, (20 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass  # resource limits are not available on Windows

    sys.addaudithook(hook)


def _preload(names):
    import importlib
    import subprocess

    # fork_exec starts processes without raising an audit event, so remove it
    def blocked(*args, **kwargs):
        raise PermissionError("Blocked by ClawMind sandbox: process creation")

    try:
        import _posixsubprocess
        _posixsubprocess.fork_exec = blocked
    except ImportError:
        pass
    if hasattr(subprocess, "_fork_exec"):
        subprocess._fork_exec = blocked

    for name in names:
        try:
            importlib.import_module(name)
        except Exception:
            pass  # the script's own import will report the error


def _main():
    import ast
    import traceback

    with open(sys.argv[1], encoding="utf-8") as fh:
        source = fh.read()
    _preload([n for n in sys.argv[2].split(",") if n])
    _install_guard()

    namespace = {"__name__": "__main__"}
    try:
        tree = ast.parse(source, "<code>")
        last = None
        if tree.body and isinstance(tree.body[-1], ast.Expr):
            last = ast.Expression(tree.body.pop().value)
        exec(compile(tree, "<code>", "exec"), namespace)
        if last is not None:
            value = eval(compile(last, "<code>", "eval"), namespace)
            if value is not None:
                print(value.to_string() if hasattr(value, "to_string") else repr(value))
    except SystemExit:
        pass
    except BaseException as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<code>"]
        where = f" (line {frames[-1].lineno})" if frames else ""
        print(f"{type(exc).__name__}{where}: {exc}", file=sys.stderr)
        sys.exit(1)


_main()
"""


# Attribute names that lead to dangerous modules, e.g. pandas.io.common.os
BLOCKED_ATTRIBUTES = {
    "os", "sys", "subprocess", "_posixsubprocess", "socket", "ctypes", "ctypeslib", "importlib",
    "builtins", "posix", "nt", "_winapi", "system", "popen", "spawn", "fork", "fork_exec",
    "environ", "getenv", "putenv", "pythonapi", "_os", "_sys", "_subprocess", "_socket",
}

SAFE_DUNDERS = {"__version__", "__name__", "__doc__"}

# Heavy libraries are imported before the sandbox hook goes on, because their
# import code uses ctypes/subprocess internally.
PRELOAD = {
    "numpy": ["numpy"],
    "pandas": ["numpy", "pandas"],
    "matplotlib": ["numpy", "matplotlib", "matplotlib.pyplot"],
    "scipy": ["numpy", "scipy"],
    "seaborn": ["numpy", "pandas", "matplotlib", "matplotlib.pyplot", "seaborn"],
}


class CodeCheck(ast.NodeVisitor):
    def __init__(self):
        self.problems = []
        self.modules = []

    def _use_module(self, name):
        name = name or ""
        if name.split(".")[0] not in ALLOWED_MODULES:
            return False
        self.modules.append(name)
        return True

    def visit_Import(self, node):
        for alias in node.names:
            if not self._use_module(alias.name):
                self.problems.append(f"import of '{alias.name}' is not allowed")
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        if node.level or not self._use_module(node.module):
            self.problems.append(f"import from '{node.module or '.'}' is not allowed")
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in BLOCKED_NAMES:
            self.problems.append(f"'{node.id}' is not allowed")
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in SAFE_DUNDERS:
            self.problems.append(f"access to '{node.attr}' is not allowed")
        elif node.attr in BLOCKED_ATTRIBUTES:
            self.problems.append(f"'.{node.attr}' is not allowed")
        self.generic_visit(node)


def check_code(code):
    """Reject obviously dangerous code. Returns the modules to preload."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ToolError(f"Syntax error on line {exc.lineno}: {exc.msg}") from None
    checker = CodeCheck()
    checker.visit(tree)
    if checker.problems:
        unique = list(dict.fromkeys(checker.problems))
        raise ToolError(
            "Code rejected: " + "; ".join(unique[:5])
            + ". Allowed modules: " + ", ".join(sorted(ALLOWED_MODULES))
        )

    preload = []
    for name in checker.modules:
        preload += PRELOAD.get(name.split(".")[0], [])
        if name.split(".")[0] in PRELOAD:
            preload.append(name)
    return list(dict.fromkeys(preload))


def _child_env(tmp):
    mpl_cache = settings.data_dir / "mpl-cache"
    mpl_cache.mkdir(parents=True, exist_ok=True)
    env = {
        "PYTHONIOENCODING": "utf-8",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(mpl_cache),
        "HOME": str(tmp),
        "TMPDIR": str(tmp / "work"),
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "CLAWMIND_MEMORY_MB": "1024",
        "CLAWMIND_CPU_SECONDS": str(settings.python_timeout + 5),
    }
    if sys.platform == "win32":
        import os
        # Windows needs SYSTEMROOT for basic things like random numbers
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        env["TEMP"] = env["TMP"] = env["TMPDIR"]
    return env


def _read_limited(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read(MAX_OUTPUT_CHARS + 1)
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + "\n... (output truncated)"
    return text


def _save_outputs(work, input_names):
    saved = []
    outputs = resolve_path("outputs")
    for item in sorted(work.iterdir()):
        if item.name in input_names or item.is_symlink() or not item.is_file():
            continue
        if len(saved) >= MAX_SAVED_FILES or item.stat().st_size > MAX_SAVED_FILE_BYTES:
            continue
        try:
            target = resolve_path(f"outputs/{item.name}")
        except ToolError:
            continue
        outputs.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        saved.append(target.relative_to(workspace_root()).as_posix())
    return saved


@tool(
    name="run_python",
    description="Run a short Python script for calculations or data analysis in a restricted sandbox. "
                "Use print() to show results; the value of the last line is printed automatically. "
                "Pass workspace files in 'files' and open them by file name (e.g. pd.read_csv('sales.csv')). "
                "Files the script creates (charts, reports) are saved to workspace/outputs/. "
                "Allowed imports: math, statistics, json, csv, re, datetime, collections, numpy, pandas, matplotlib and similar. "
                "No network, no shell, no os/sys.",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source code"},
            "files": {"type": "array", "items": {"type": "string"}, "description": "Workspace files to copy in"},
        },
        "required": ["code"],
    },
    permission=Permission.WRITE,
    activity="Running Python analysis...",
    enabled=lambda: settings.enable_python,
)
def run_python(code, files=None):
    if len(code) > MAX_CODE_CHARS:
        raise ToolError("Code is too long (limit 20,000 characters).")
    preload = check_code(code)

    files = files or []
    if len(files) > 10:
        raise ToolError("At most 10 input files can be used.")

    tmp = Path(tempfile.mkdtemp(prefix="clawmind_py_"))
    try:
        work = tmp / "work"
        work.mkdir()

        input_names = set()
        total = 0
        for name in files:
            source = resolve_path(name)
            if not source.is_file():
                raise ToolError(f"Input file '{name}' was not found in the workspace.")
            total += source.stat().st_size
            if total > MAX_INPUT_BYTES:
                raise ToolError("Input files are too large (limit 50 MB in total).")
            shutil.copyfile(source, work / source.name)
            input_names.add(source.name)

        (tmp / "runner.py").write_text(RUNNER, encoding="utf-8")
        (tmp / "code.py").write_text(code, encoding="utf-8")
        stdout_path, stderr_path = tmp / "stdout.txt", tmp / "stderr.txt"

        started = time.monotonic()
        timed_out = False
        with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
            try:
                proc = subprocess.run(
                    [sys.executable, "-I", "-X", "utf8", str(tmp / "runner.py"), str(tmp / "code.py"), ",".join(preload)],
                    cwd=work,
                    env=_child_env(tmp),
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=err,
                    timeout=settings.python_timeout,
                )
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                exit_code = -1

        stdout = _read_limited(stdout_path)
        stderr = _read_limited(stderr_path)
        saved = _save_outputs(work, input_names)

        if timed_out:
            error = f"The script was stopped after {settings.python_timeout} seconds."
        elif exit_code != 0:
            error = stderr.strip().splitlines()[-1] if stderr.strip() else f"The script exited with code {exit_code}."
        else:
            error = None

        result = {
            "success": error is None,
            "stdout": stdout,
            "stderr": stderr[-3000:],
            "saved_files": saved,
            "duration_seconds": round(time.monotonic() - started, 2),
        }
        if error:
            result["error"] = error
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
