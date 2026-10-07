import sys

import pytest

from app.config import BASE_DIR
from app.tools import python_exec
from app.tools.registry import run_tool


def run(code, files=None):
    return run_tool("run_python", {"code": code, "files": files or []})


def test_simple_calculation():
    result = run("total = sum(x * x for x in range(10))\nprint(total)")
    assert result["success"], result
    assert result["stdout"].strip() == "285"


def test_last_expression_is_printed():
    result = run("import statistics\nstatistics.mean([2, 4, 6])")
    assert result["success"] and result["stdout"].strip() == "4"


def test_pandas_analysis_with_workspace_file(workspace):
    (workspace / "sales.csv").write_text("region,revenue\nNorth,10\nSouth,30\nNorth,5\n")
    code = (
        "import pandas as pd\n"
        "df = pd.read_csv('sales.csv')\n"
        "summary = df.groupby('region')['revenue'].sum()\n"
        "summary.to_csv('summary.csv')\n"
        "print(summary.to_dict())"
    )
    result = run(code, ["sales.csv"])
    assert result["success"], result
    assert "'North': 15" in result["stdout"]
    assert result["saved_files"] == ["outputs/summary.csv"]
    assert (workspace / "outputs" / "summary.csv").exists()


@pytest.mark.parametrize("code", [
    "import os\nos.system('echo hacked')",
    "import subprocess\nsubprocess.run(['id'])",
    "import socket\nsocket.socket()",
    "import shutil\nshutil.rmtree('/tmp')",
    "import ctypes",
    "import sys\nsys.exit(0)",
    "from os import system",
    "import importlib\nimportlib.import_module('os')",
    "__import__('os').system('id')",
    "eval('1+1')",
    "exec('import os')",
    "compile('1', 'x', 'eval')",
    "getattr(print, '__self__')",
    "().__class__.__base__.__subclasses__()",
    "print.__self__",
    "import pandas as pd\npd.io.common.os.system('id')",
    "globals()['__builtins__']",
])
def test_dangerous_code_is_rejected_before_running(code):
    result = run(code)
    assert result["success"] is False
    assert "rejected" in result["error"] or "not allowed" in result["error"]


@pytest.mark.parametrize("code, message", [
    ("open('/etc/passwd').read()", "Blocked"),
    (f"open({str(BASE_DIR / '.env.example')!r}).read()", "Blocked"),
    (f"open({str(BASE_DIR / 'app' / 'config.py')!r}).read()", "Blocked"),
    ("open('/tmp/clawmind_escape.txt', 'w').write('x')", "Blocked"),
    ("import pandas as pd\npd.read_csv('/etc/hostname')", "Blocked"),
])
def test_runtime_guard_blocks_file_access_outside_sandbox(code, message):
    result = run(code)
    assert result["success"] is False
    assert message in result["error"]


@pytest.mark.parametrize("code", [
    "import os\nos.system('echo hacked > /tmp/clawmind_pwned')",
    "import subprocess\nsubprocess.run(['echo', 'hi'])",
    pytest.param("import _posixsubprocess\n_posixsubprocess.fork_exec()",
                 marks=pytest.mark.skipif(sys.platform == "win32", reason="POSIX only")),
    "import socket\nsocket.create_connection(('1.1.1.1', 80), timeout=2)",
    "import os\nprint(os.environ.get('LLM_API_KEY'))\nos.listdir('/')",
    "import urllib.request\nurllib.request.urlopen('http://1.1.1.1', timeout=2)",
])
def test_runtime_guard_works_even_if_static_check_is_bypassed(monkeypatch, code):
    # Simulate a bypass of the AST check to make sure the second layer holds
    monkeypatch.setattr(python_exec, "check_code", lambda c: [])
    result = run(code)
    assert result["success"] is False, result
    assert "Blocked" in result["error"] or "not available" in result["error"] or "PermissionError" in result["error"]


def test_child_gets_no_secrets(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-test-should-not-leak-1234567890")
    monkeypatch.setattr(python_exec, "check_code", lambda c: [])
    result = run("import os\nprint(sorted(os.environ))")
    assert "LLM_API_KEY" not in result["stdout"]
    assert "sk-test" not in result["stdout"]


def test_timeout(monkeypatch):
    monkeypatch.setattr(python_exec.settings, "python_timeout", 2)
    result = run("while True:\n    pass")
    assert result["success"] is False
    assert "stopped after 2 seconds" in result["error"]


def test_python_tool_can_be_disabled(monkeypatch):
    monkeypatch.setattr(python_exec.settings, "enable_python", False)
    result = run("print(1)")
    assert result["success"] is False and "disabled" in result["error"]
