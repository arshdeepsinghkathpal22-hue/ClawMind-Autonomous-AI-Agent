"""A few simple commands that work even when no LLM is configured."""

import re

from app import memory, scheduler
from app.timeparse import split_reminder
from app.tools.registry import run_tool

NO_LLM_MESSAGE = (
    "AI reasoning is unavailable because no language model is configured. "
    "Set LLM_BASE_URL and LLM_MODEL (and LLM_API_KEY if your provider needs one) in the .env file, "
    "then restart ClawMind.\n\n"
    "Until then I can still handle simple commands:\n"
    "- `calculate 125 * 42`\n"
    "- `what time is it?`\n"
    "- `remember that my project is called ClawMind`\n"
    "- `list memories` / `forget memory 3`\n"
    "- `remind me tomorrow at 5 PM to study DSA`\n"
    "- `list files` / `read study.md`"
)

MATH_CHARS = re.compile(r"^[\d\s.+\-*/()%^,×÷a-z]+$")


def _fmt_error(result):
    return f"Sorry, that didn't work: {result.get('error', 'unknown error')}"


def handle(message, session_id):
    """Return (response, tools_used)."""
    text = message.strip()
    low = text.lower().rstrip("?!. ")

    m = re.match(r"^(?:calculate|calc|compute|evaluate|what is|what's|whats)\s+(.+)$", low)
    expr = m.group(1) if m else low
    if MATH_CHARS.match(expr) and re.search(r"\d", expr) and re.search(r"[+\-*/^%×÷]|sqrt|log", expr):
        result = run_tool("calculator", {"expression": expr})
        if result["success"]:
            return f"{result['expression']} = **{result['result']}**", ["calculator"]
        return _fmt_error(result), ["calculator"]

    if re.search(r"\b(what time|current time|the time|what('s| is) the date|today's date|what day is)\b", low):
        result = run_tool("get_datetime", {})
        return f"It's {result['time'][:5]} on {result['date']} ({result['timezone']}).", ["get_datetime"]

    m = re.match(r"^(?:please\s+)?remember(?:\s+that)?\s+(.+)$", text, re.I)
    if m:
        try:
            saved = memory.add_memory(m.group(1))
        except memory.MemoryRejected as exc:
            return str(exc), []
        return f"Got it. I'll remember: “{saved['content']}”", ["remember"]

    if low in ("list memories", "show memories", "memories", "what do you remember", "what do you remember about me"):
        rows = memory.list_memories(limit=50)
        if not rows:
            return "I don't have any memories saved yet.", ["list_memories"]
        lines = "\n".join(f"- #{r['id']}: {r['content']}" for r in rows)
        return f"Here's what I remember:\n{lines}", ["list_memories"]

    m = re.match(r"^forget\s+(?:memory\s+)?#?(\d+)$", low)
    if m:
        if memory.delete_memory(int(m.group(1))):
            return f"Deleted memory #{m.group(1)}.", ["forget"]
        return f"There is no memory #{m.group(1)}.", []

    m = re.match(r"^remind me\s+(.+)$", text, re.I)
    if m:
        try:
            what, when = split_reminder(m.group(1))
            task = scheduler.create_task(what, when, session_id=session_id)
        except (ValueError, scheduler.TaskError) as exc:
            return f"I couldn't schedule that: {exc}", []
        return f"Okay! I'll remind you to **{task['title']}** {task['schedule']}.", ["schedule_task"]

    if low in ("list files", "show files", "ls"):
        result = run_tool("list_files", {"path": "."})
        if not result["success"]:
            return _fmt_error(result), ["list_files"]
        if not result["entries"]:
            return "The workspace is empty.", ["list_files"]
        lines = "\n".join(f"- {e['path']}{'/' if e['type'] == 'folder' else ''}" for e in result["entries"])
        return f"Files in the workspace:\n{lines}", ["list_files"]

    m = re.match(r"^(?:read|open|show|cat)\s+(?:the\s+)?(?:file\s+)?(\S+\.\w{1,6})$", text, re.I)
    if m:
        result = run_tool("read_file", {"path": m.group(1)})
        if not result["success"]:
            return _fmt_error(result), ["read_file"]
        return f"**{result['path']}**\n\n```\n{result['content'][:5000]}\n```", ["read_file"]

    related = memory.search_memories(text, limit=3)
    reply = NO_LLM_MESSAGE
    if related:
        notes = "\n".join(f"- {r['content']}" for r in related)
        reply = f"From your saved memories:\n{notes}\n\n---\n\n" + reply
    return reply, []

