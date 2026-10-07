from sqlalchemy import inspect, text

from app import db


def test_all_tables_exist():
    tables = set(inspect(db.engine).get_table_names())
    assert {"messages", "memories", "scheduled_tasks", "tool_logs", "agent_runs"} <= tables


def test_tool_calls_are_logged_without_secrets():
    from app.tools.registry import run_tool

    run_tool("calculator", {"expression": "1 + 1", "note": "sk-abcdefghijklmnopqrstuvwxyz"})
    with db.get_session() as session:
        row = session.query(db.ToolLog).order_by(db.ToolLog.id.desc()).first()
    assert row.tool == "calculator" and row.success
    assert "sk-abcdef" not in row.arguments
    assert row.duration_ms >= 0


def test_user_text_is_stored_as_data_not_sql():
    from app.memory import save_message

    evil = "x'); DROP TABLE messages; --"
    save_message("sql-test", "user", evil)
    with db.engine.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM messages WHERE content = :c"), {"c": evil}).scalar()
    assert count == 1
    assert "messages" in inspect(db.engine).get_table_names()


def test_timestamps_round_trip():
    stamp = db.utcnow()
    assert db.iso(stamp).endswith("Z")
    assert db.as_utc(stamp).tzinfo is not None
