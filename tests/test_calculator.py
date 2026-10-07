import pytest

from app.tools.basic import safe_calculate
from app.tools.registry import ToolError, run_tool


@pytest.mark.parametrize("expr, expected", [
    ("125 * 42", 5250),
    ("25 * 48 + 100", 1300),
    ("2 + 3 * 4", 14),
    ("(2 + 3) * 4", 20),
    ("2 ^ 10", 1024),
    ("10 / 4", 2.5),
    ("7 // 2", 3),
    ("-5 + 2", -3),
    ("sqrt(16) + abs(-2)", 6),
    ("round(pi, 2)", 3.14),
    ("1,000 * 3", 3000),
    ("15% of 200", 30),
    ("max(3, 9, 4)", 9),
])
def test_valid_expressions(expr, expected):
    assert safe_calculate(expr) == expected


@pytest.mark.parametrize("expr", [
    "__import__('os').system('id')",
    "open('/etc/passwd').read()",
    "().__class__.__bases__",
    "eval('1+1')",
    "x = 5",
    "lambda: 1",
    "[1, 2, 3]",
    "'a' * 10",
    "sqrt(x=4)",
    "os.getcwd()",
])
def test_rejects_code(expr):
    with pytest.raises(ToolError):
        safe_calculate(expr)


def test_huge_numbers_are_refused():
    with pytest.raises(ToolError):
        safe_calculate("9 ** 9 ** 9")
    with pytest.raises(ToolError):
        safe_calculate("factorial(100000)")


def test_division_by_zero():
    with pytest.raises(ToolError, match="zero"):
        safe_calculate("1 / 0")


def test_tool_returns_structured_result():
    result = run_tool("calculator", {"expression": "125 * 42"})
    assert result == {"success": True, "expression": "125 * 42", "result": 5250}

    bad = run_tool("calculator", {"expression": "import os"})
    assert bad["success"] is False and "error" in bad


def test_datetime_tool():
    result = run_tool("get_datetime", {})
    assert result["success"] and result["date"] and result["time"]
    assert run_tool("get_datetime", {"timezone": "Asia/Kolkata"})["success"]
    assert run_tool("get_datetime", {"timezone": "../../etc/passwd"})["success"] is False
