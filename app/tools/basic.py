import ast
import math
import operator
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.timeparse import local_now, local_tz
from app.tools.registry import Permission, ToolError, tool

BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

FUNCTIONS = {
    "sqrt": math.sqrt, "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "min": min, "max": max,
    "factorial": math.factorial,
}
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

MAX_INT_BITS = 4096


def _check_size(value):
    if isinstance(value, int) and value.bit_length() > MAX_INT_BITS:
        raise ToolError("The result is too large.")
    return value


def _eval(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value

    if isinstance(node, ast.BinOp) and type(node.op) in BIN_OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 10000:
            raise ToolError("Exponent is too large.")
        too_big = isinstance(left, int) and abs(left) > 1 and abs(right) * math.log2(abs(left)) > MAX_INT_BITS
        if isinstance(node.op, ast.Pow) and too_big:
            raise ToolError("The result is too large.")
        try:
            return _check_size(BIN_OPS[type(node.op)](left, right))
        except ZeroDivisionError:
            raise ToolError("Division by zero.") from None
        except OverflowError:
            raise ToolError("The result is too large.") from None

    if isinstance(node, ast.UnaryOp) and type(node.op) in UNARY_OPS:
        return UNARY_OPS[type(node.op)](_eval(node.operand))

    if isinstance(node, ast.Name) and node.id in CONSTANTS:
        return CONSTANTS[node.id]

    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
        if node.keywords:
            raise ToolError("Keyword arguments are not supported.")
        args = [_eval(arg) for arg in node.args]
        if node.func.id == "factorial" and (not args or args[0] > 500):
            raise ToolError("factorial() is limited to numbers up to 500.")
        try:
            return _check_size(FUNCTIONS[node.func.id](*args))
        except (ValueError, TypeError) as exc:
            raise ToolError(f"Math error: {exc}") from None

    raise ToolError("Only numbers, + - * / // % ** and basic math functions are allowed.")


def safe_calculate(expression):
    expr = str(expression).strip()
    if not expr:
        raise ToolError("Empty expression.")
    if len(expr) > 300:
        raise ToolError("Expression is too long.")

    expr = expr.replace("×", "*").replace("÷", "/").replace("^", "**")
    expr = re.sub(r"(?<=\d),(?=\d{3}\b)", "", expr)  # 1,000 -> 1000
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%\s*of\s*", r"\1/100*", expr)  # 15% of 200

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise ToolError("That doesn't look like a valid math expression.") from None

    result = _eval(tree.body)
    if isinstance(result, float):
        if math.isnan(result) or math.isinf(result):
            raise ToolError("The result is not a finite number.")
        if result.is_integer() and abs(result) < 1e15:
            result = int(result)
        else:
            result = round(result, 10)
    return result


@tool(
    name="calculator",
    description="Evaluate a math expression exactly. Supports + - * / // % ** (or ^), parentheses, "
                "sqrt, log, sin, cos, tan, round, abs, min, max, factorial, pi and e. Use this for any arithmetic.",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "e.g. 25 * 48 + 100"}},
        "required": ["expression"],
    },
    permission=Permission.SAFE,
    activity="Calculating...",
)
def calculator(expression):
    return {"success": True, "expression": expression, "result": safe_calculate(expression)}


@tool(
    name="get_datetime",
    description="Get the current date and time. Optionally pass an IANA timezone like 'Asia/Kolkata' or 'Europe/London'.",
    parameters={
        "type": "object",
        "properties": {"timezone": {"type": "string", "description": "IANA timezone name (optional)"}},
    },
    permission=Permission.SAFE,
    activity="Checking the time...",
)
def get_datetime(timezone=None):
    if timezone:
        try:
            now = datetime.now(ZoneInfo(timezone))
        except (ZoneInfoNotFoundError, ValueError):
            raise ToolError(f"Unknown timezone '{timezone}'.") from None
    else:
        now = local_now()
    return {
        "success": True,
        "datetime": now.isoformat(timespec="seconds"),
        "date": now.strftime("%A, %d %B %Y"),
        "time": now.strftime("%H:%M:%S"),
        "timezone": timezone or str(local_tz()),
    }
