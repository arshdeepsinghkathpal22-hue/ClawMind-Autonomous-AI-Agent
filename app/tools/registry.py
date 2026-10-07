"""Central list of tools the agent can call."""

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from app.db import ToolLog, get_session
from app.redact import redact

log = logging.getLogger("clawmind.tools")


class Permission(str, Enum):
    SAFE = "SAFE"            # pure computation, no side effects
    READ = "READ"            # reads workspace files or public web data
    WRITE = "WRITE"          # creates or changes data inside ClawMind / the workspace
    EXTERNAL = "EXTERNAL"    # drives an external program such as a browser
    DANGEROUS = "DANGEROUS"  # destructive; always needs the user's confirmation


class ToolError(Exception):
    """Expected failure. The message is shown to the agent and user."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable
    permission: Permission
    activity: str = "Working..."
    enabled: Callable[[], bool] = lambda: True
    # For DANGEROUS tools: turns the arguments into "delete the file report.pdf"
    describe: Callable[[dict], str] | None = None
    required: list = field(default_factory=list)

    def schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def confirmation_text(self, args):
        if self.describe:
            try:
                return self.describe(args)
            except Exception:
                pass
        return f"run {self.name} with {json.dumps(args)[:200]}"


TOOLS: dict[str, Tool] = {}


def tool(name, description, parameters, permission, activity="Working...", enabled=None, describe=None):
    """Decorator that registers a function as a tool."""

    def wrap(func):
        TOOLS[name] = Tool(
            name=name,
            description=description,
            parameters=parameters,
            func=func,
            permission=permission,
            activity=activity,
            enabled=enabled or (lambda: True),
            describe=describe,
            required=parameters.get("required", []),
        )
        return func

    return wrap


def get_tool(name):
    return TOOLS.get(name)


def enabled_tools(allow_dangerous=True):
    result = []
    for t in TOOLS.values():
        if not t.enabled():
            continue
        if t.permission == Permission.DANGEROUS and not allow_dangerous:
            continue
        result.append(t)
    return result


def openai_schemas(allow_dangerous=True):
    return [t.schema() for t in enabled_tools(allow_dangerous)]


TYPE_CHECKS = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def _clean_args(t, args):
    """Check arguments against the tool's schema. Unknown keys are dropped."""
    if not isinstance(args, dict):
        raise ToolError("Tool arguments must be a JSON object.")
    if "_invalid_json" in args:
        raise ToolError("The tool arguments were not valid JSON.")

    props = t.parameters.get("properties", {})
    cleaned = {}
    for key, value in args.items():
        if key not in props:
            continue
        expected = props[key].get("type")
        # Small models sometimes send numbers as strings
        if expected == "integer" and isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value)
        if expected == "boolean" and isinstance(value, str) and value.lower() in ("true", "false"):
            value = value.lower() == "true"
        check = TYPE_CHECKS.get(expected)
        if check and not check(value):
            raise ToolError(f"Argument '{key}' should be of type {expected}.")
        cleaned[key] = value

    missing = [key for key in t.required if key not in cleaned]
    if missing:
        raise ToolError(f"Missing required argument(s): {', '.join(missing)}.")
    return cleaned


def _log(run_id, name, args, success, error, duration_ms):
    try:
        with get_session() as db:
            db.add(ToolLog(
                run_id=run_id,
                tool=name[:64],
                arguments=redact(json.dumps(args, default=str))[:2000],
                success=success,
                error=redact(error or "")[:1000],
                duration_ms=duration_ms,
            ))
            db.commit()
    except Exception:
        log.exception("Could not write tool log")


def run_tool(name, args, run_id=None, confirmed=False):
    """Run a tool and always return a dict with a 'success' key. Never raises."""
    started = time.monotonic()
    t = TOOLS.get(name)
    result = None
    error = ""

    try:
        if not t:
            raise ToolError(f"Unknown tool '{name}'.")
        if not t.enabled():
            raise ToolError(f"The {name} tool is disabled in the settings.")
        if t.permission == Permission.DANGEROUS and not confirmed:
            raise ToolError(f"{name} needs the user's confirmation before it can run.")
        cleaned = _clean_args(t, args)
        value = t.func(**cleaned)
        if isinstance(value, dict) and "success" in value:
            result = value
        else:
            result = {"success": True, "result": value}
    except ToolError as exc:
        error = str(exc)
        result = {"success": False, "error": error}
    except Exception as exc:
        log.exception("Tool %s crashed", name)
        error = f"{type(exc).__name__}: {exc}"
        result = {"success": False, "error": f"The {name} tool failed unexpectedly."}

    duration_ms = int((time.monotonic() - started) * 1000)
    success = bool(result.get("success"))
    if not error and not success:
        error = str(result.get("error", ""))
    _log(run_id, name, args if isinstance(args, dict) else {}, success, error, duration_ms)
    log.info("tool=%s success=%s duration_ms=%s", name, success, duration_ms)
    return result
