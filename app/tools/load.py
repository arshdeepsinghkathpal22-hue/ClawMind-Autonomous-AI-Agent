"""Import every tool module so its tools get registered."""

from app.tools import (  # noqa: F401
    basic,
    browser,
    filesystem,
    memory_tools,
    python_exec,
    task_tools,
    web,
)
from app.tools.registry import TOOLS


def load_tools():
    return sorted(TOOLS)
