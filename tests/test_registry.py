import pytest

from app.tools.registry import TOOLS, Permission, openai_schemas, run_tool

EXPECTED = {
    "calculator": Permission.SAFE,
    "get_datetime": Permission.SAFE,
    "web_search": Permission.READ,
    "fetch_webpage": Permission.READ,
    "list_files": Permission.READ,
    "read_file": Permission.READ,
    "write_file": Permission.WRITE,
    "create_directory": Permission.WRITE,
    "delete_file": Permission.DANGEROUS,
    "run_python": Permission.WRITE,
    "browser_open": Permission.EXTERNAL,
    "browser_screenshot": Permission.EXTERNAL,
    "remember": Permission.WRITE,
    "forget": Permission.DANGEROUS,
    "search_memories": Permission.READ,
    "list_memories": Permission.READ,
    "schedule_task": Permission.WRITE,
    "list_tasks": Permission.READ,
    "cancel_task": Permission.DANGEROUS,
}


def test_all_tools_registered_with_permissions():
    assert {name: t.permission for name, t in TOOLS.items()} == EXPECTED


def test_every_tool_has_a_valid_schema():
    for tool in TOOLS.values():
        schema = tool.schema()
        assert schema["type"] == "function"
        assert schema["function"]["description"]
        assert schema["function"]["parameters"]["type"] == "object"
        for required in tool.required:
            assert required in schema["function"]["parameters"]["properties"]


def test_disabled_tools_are_hidden():
    names = {s["function"]["name"] for s in openai_schemas()}
    assert "browser_open" not in names  # ENABLE_BROWSER=false in tests
    assert "calculator" in names
    assert "browser_open" in TOOLS
    assert "disabled" in run_tool("browser_open", {"url": "https://example.com"})["error"]


def test_dangerous_tools_can_be_excluded():
    names = {s["function"]["name"] for s in openai_schemas(allow_dangerous=False)}
    assert not names & {"delete_file", "forget", "cancel_task"}


@pytest.mark.parametrize("name, args, message", [
    ("nope", {}, "Unknown tool"),
    ("calculator", {}, "Missing required"),
    ("calculator", {"expression": 5}, "should be of type string"),
    ("calculator", "1+1", "JSON object"),
    ("calculator", {"_invalid_json": "{"}, "not valid JSON"),
    ("delete_file", {"path": "x"}, "confirmation"),
])
def test_bad_calls_return_structured_errors(name, args, message):
    result = run_tool(name, args)
    assert result["success"] is False
    assert message in result["error"]


def test_unexpected_crash_is_hidden(monkeypatch):
    def broken(expression):
        raise RuntimeError("internal detail /home/user/secret")

    monkeypatch.setattr(TOOLS["calculator"], "func", broken)
    result = run_tool("calculator", {"expression": "1"})
    assert result == {"success": False, "error": "The calculator tool failed unexpectedly."}


def test_string_numbers_are_coerced():
    from app.tools.registry import _clean_args

    assert _clean_args(TOOLS["forget"], {"memory_id": "3"}) == {"memory_id": 3}
