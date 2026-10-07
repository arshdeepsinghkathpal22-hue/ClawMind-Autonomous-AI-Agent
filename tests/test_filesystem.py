import os
import sys

import pytest

from app.config import BASE_DIR
from app.tools.filesystem import resolve_path
from app.tools.registry import ToolError, run_tool


def test_write_read_list(workspace):
    result = run_tool("write_file", {"path": "study.md", "content": "DSA\nOS\nComputer Networks\n"})
    assert result["success"], result
    assert (workspace / "study.md").read_text() == "DSA\nOS\nComputer Networks\n"

    read = run_tool("read_file", {"path": "study.md"})
    assert read["success"] and "Computer Networks" in read["content"]

    listing = run_tool("list_files", {})
    assert listing["success"]
    assert [e["path"] for e in listing["entries"]] == ["study.md"]


def test_nested_folders_and_overwrite(workspace):
    assert run_tool("create_directory", {"path": "notes/2026"})["success"]
    assert run_tool("write_file", {"path": "notes/2026/a.txt", "content": "one"})["success"]

    again = run_tool("write_file", {"path": "notes/2026/a.txt", "content": "two"})
    assert again["success"] is False and "already exists" in again["error"]

    assert run_tool("write_file", {"path": "notes/2026/a.txt", "content": "two", "overwrite": True})["success"]
    assert run_tool("write_file", {"path": "notes/2026/a.txt", "content": "+", "append": True})["success"]
    assert (workspace / "notes/2026/a.txt").read_text() == "two+"


@pytest.mark.parametrize("path", [
    "../secret.txt",
    "../../secret.txt",
    "notes/../../secret.txt",
    "/etc/passwd",
    "../../etc/passwd",
    "C:\\Windows\\System32",
    "C:/Windows/System32/drivers/etc/hosts",
    "..\\..\\Windows\\win.ini",
    "\\\\server\\share\\file.txt",
    "~/.ssh/id_rsa",
    ".env",
    "config/.env",
    ".ssh/id_rsa",
    "id_rsa",
    "keys/server.pem",
    "private.key",
    "clawmind.db",
    "data.sqlite3",
    "bad\x00name.txt",
    "con.txt",
    str(BASE_DIR / "app" / "config.py"),
])
def test_dangerous_paths_are_rejected(workspace, path):
    with pytest.raises(ToolError):
        resolve_path(path)
    for tool in ("read_file", "list_files"):
        result = run_tool(tool, {"path": path})
        assert result["success"] is False


def test_write_outside_workspace_is_rejected(workspace, tmp_path):
    target = tmp_path / "escape.txt"
    result = run_tool("write_file", {"path": str(target), "content": "x"})
    assert result["success"] is False
    assert not target.exists()

    result = run_tool("write_file", {"path": "../escape.txt", "content": "x"})
    assert result["success"] is False
    assert not (workspace.parent / "escape.txt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin rights on Windows")
def test_symlink_escape_is_blocked(workspace, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    os.symlink(secret, workspace / "link.txt")
    os.symlink(tmp_path, workspace / "linkdir")

    assert run_tool("read_file", {"path": "link.txt"})["success"] is False
    assert run_tool("read_file", {"path": "linkdir/secret.txt"})["success"] is False
    assert run_tool("write_file", {"path": "linkdir/new.txt", "content": "x"})["success"] is False
    assert not (tmp_path / "new.txt").exists()
    entries = run_tool("list_files", {})["entries"]
    assert all(e["path"] not in ("link.txt", "linkdir") for e in entries)


@pytest.mark.parametrize("name", [
    "notes; rm -rf ~.txt",
    "a && echo hacked > pwned.txt",
    "x || touch pwned.txt",
    "$(touch pwned.txt).txt",
    "`touch pwned.txt`.txt",
])
def test_shell_characters_are_never_executed(workspace, name):
    run_tool("write_file", {"path": name, "content": "hi"})
    run_tool("read_file", {"path": name})
    assert not (workspace / "pwned.txt").exists()
    assert not os.path.exists("pwned.txt")


def test_delete_requires_confirmation(workspace):
    (workspace / "report.pdf").write_text("x")
    result = run_tool("delete_file", {"path": "report.pdf"})
    assert result["success"] is False and "confirmation" in result["error"]
    assert (workspace / "report.pdf").exists()

    result = run_tool("delete_file", {"path": "report.pdf"}, confirmed=True)
    assert result["success"]
    assert not (workspace / "report.pdf").exists()


def test_read_missing_and_binary_files(workspace):
    assert "not found" in run_tool("read_file", {"path": "nope.txt"})["error"]
    (workspace / "image.bin").write_bytes(b"\x89PNG\x00\x00\x01")
    assert "binary" in run_tool("read_file", {"path": "image.bin"})["error"]
