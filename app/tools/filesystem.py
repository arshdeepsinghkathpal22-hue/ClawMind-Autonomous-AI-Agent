"""File tools. Everything is locked to the workspace folder."""

import re
from pathlib import Path, PurePosixPath, PureWindowsPath

from app.config import settings
from app.tools.registry import Permission, ToolError, tool

MAX_READ_CHARS = 100_000
MAX_WRITE_BYTES = 1_000_000
MAX_LIST_ENTRIES = 300

BLOCKED_NAMES = {
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "authorized_keys", "known_hosts",
    "credentials", "secrets", "shadow", "passwd",
}
BLOCKED_SUFFIXES = (
    ".env", ".pem", ".key", ".p12", ".pfx", ".crt", ".jks", ".kdbx", ".keystore",
    ".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm",
)
WINDOWS_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


def workspace_root():
    root = settings.workspace_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _check_name(part):
    lower = part.lower()
    if lower.startswith("."):
        raise ToolError("Hidden files and folders (starting with '.') are not accessible.")
    if lower in BLOCKED_NAMES or lower.endswith(BLOCKED_SUFFIXES):
        raise ToolError(f"Access to '{part}' is blocked because it may contain secrets.")
    if any(ch in '<>:"|?*' for ch in part):
        raise ToolError(f"'{part}' contains characters that are not allowed in file names.")
    if lower.split(".")[0] in WINDOWS_RESERVED:
        raise ToolError(f"'{part}' is a reserved file name.")
    if part.endswith((" ", ".")):
        raise ToolError("File names cannot end with a space or a dot.")


def resolve_path(user_path):
    """Map a user-supplied relative path to an absolute path inside the workspace.

    Raises ToolError for absolute paths, '..', hidden/secret files and anything
    that resolves (for example through a symlink) outside the workspace.
    """
    if not isinstance(user_path, str):
        raise ToolError("Path must be a string.")
    raw = user_path.strip()
    if len(raw) > 400:
        raise ToolError("Path is too long.")
    if any(ord(ch) < 32 for ch in raw):
        raise ToolError("Path contains invalid characters.")

    root = workspace_root()
    if raw in ("", ".", "./", "/workspace", "workspace", "workspace/"):
        return root
    if raw.startswith(("workspace/", "workspace\\")):
        raw = raw[len("workspace/"):]

    windows = PureWindowsPath(raw)
    normalized = raw.replace("\\", "/")
    if normalized.startswith("/") or windows.drive or windows.root or re.match(r"^[A-Za-z]:", raw) or raw.startswith("~"):
        raise ToolError("Absolute paths are not allowed. Use a path relative to the workspace, like 'notes/todo.md'.")

    parts = PurePosixPath(normalized).parts
    if ".." in parts:
        raise ToolError("Path traversal ('..') is not allowed.")
    for part in parts:
        _check_name(part)

    full = (root / Path(*parts)).resolve()
    if full != root and not full.is_relative_to(root):
        raise ToolError("That path points outside the workspace.")

    # A symlink inside the workspace could point at a secret file inside it too
    for part in full.relative_to(root).parts:
        _check_name(part)
    return full


def relative(path):
    rel = path.relative_to(workspace_root()).as_posix()
    return rel or "."


@tool(
    name="list_files",
    description="List files and folders in the workspace. Paths are relative to the workspace.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Folder to list, default '.'"},
            "recursive": {"type": "boolean", "description": "Include subfolders"},
        },
    },
    permission=Permission.READ,
    activity="Listing files...",
)
def list_files(path=".", recursive=False):
    folder = resolve_path(path)
    if not folder.exists():
        raise ToolError(f"Folder '{path}' does not exist.")
    if not folder.is_dir():
        raise ToolError(f"'{path}' is a file, not a folder.")

    root = workspace_root()
    items = folder.rglob("*") if recursive else folder.iterdir()
    entries = []
    for item in sorted(items):
        rel_parts = item.relative_to(root).parts
        if any(p.startswith(".") or p.lower().endswith(BLOCKED_SUFFIXES) for p in rel_parts):
            continue
        if item.is_symlink() and not item.resolve().is_relative_to(root):
            continue
        entries.append({
            "path": item.relative_to(root).as_posix(),
            "type": "folder" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
        if len(entries) >= MAX_LIST_ENTRIES:
            break
    return {"success": True, "folder": relative(folder), "entries": entries, "count": len(entries)}


@tool(
    name="read_file",
    description="Read a text file from the workspace (txt, md, csv, json, py, html, ...).",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path relative to the workspace"}},
        "required": ["path"],
    },
    permission=Permission.READ,
    activity="Reading file...",
)
def read_file(path):
    file = resolve_path(path)
    if not file.exists():
        raise ToolError(f"File '{path}' was not found in the workspace.")
    if not file.is_file():
        raise ToolError(f"'{path}' is a folder, not a file.")

    with open(file, "rb") as fh:
        data = fh.read(MAX_READ_CHARS * 4 + 1)
    if b"\x00" in data[:4096]:
        raise ToolError(f"'{path}' looks like a binary file. Use run_python to inspect it.")

    text = data.decode("utf-8", errors="replace")
    truncated = len(text) > MAX_READ_CHARS
    return {
        "success": True,
        "path": relative(file),
        "content": text[:MAX_READ_CHARS],
        "truncated": truncated,
        "size": file.stat().st_size,
    }


@tool(
    name="write_file",
    description="Create a text file in the workspace. Set overwrite=true to replace an existing file, "
                "or append=true to add to the end of it. Parent folders are created automatically.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to the workspace"},
            "content": {"type": "string", "description": "Text to write"},
            "overwrite": {"type": "boolean", "description": "Replace the file if it exists"},
            "append": {"type": "boolean", "description": "Append to the file instead of replacing it"},
        },
        "required": ["path", "content"],
    },
    permission=Permission.WRITE,
    activity="Writing file...",
)
def write_file(path, content, overwrite=False, append=False):
    file = resolve_path(path)
    if file == workspace_root():
        raise ToolError("Please give a file name.")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_WRITE_BYTES:
        raise ToolError("Content is too large (limit is 1 MB).")
    if file.exists() and file.is_dir():
        raise ToolError(f"'{path}' is a folder.")
    if file.exists() and not (overwrite or append):
        raise ToolError(f"'{relative(file)}' already exists. Ask to overwrite or append to it.")

    file.parent.mkdir(parents=True, exist_ok=True)
    with open(file, "ab" if append else "wb") as fh:
        fh.write(encoded)
    return {"success": True, "path": relative(file), "bytes_written": len(encoded), "appended": append}


@tool(
    name="create_directory",
    description="Create a folder inside the workspace.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Folder path relative to the workspace"}},
        "required": ["path"],
    },
    permission=Permission.WRITE,
    activity="Creating folder...",
)
def create_directory(path):
    folder = resolve_path(path)
    if folder.exists() and not folder.is_dir():
        raise ToolError(f"'{path}' already exists as a file.")
    folder.mkdir(parents=True, exist_ok=True)
    return {"success": True, "path": relative(folder)}


@tool(
    name="delete_file",
    description="Delete a file from the workspace. The user is always asked to confirm first.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path relative to the workspace"}},
        "required": ["path"],
    },
    permission=Permission.DANGEROUS,
    activity="Deleting file...",
    describe=lambda args: f"delete the file {args.get('path', '?')} from the workspace",
)
def delete_file(path):
    file = resolve_path(path)
    if file == workspace_root():
        raise ToolError("Refusing to delete the workspace itself.")
    if not file.exists():
        raise ToolError(f"File '{path}' was not found.")
    if file.is_dir():
        raise ToolError("Deleting folders is not supported. Delete the files inside it instead.")
    file.unlink()
    return {"success": True, "deleted": relative(file)}
