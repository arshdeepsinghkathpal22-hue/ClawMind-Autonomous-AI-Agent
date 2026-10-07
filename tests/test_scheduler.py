from datetime import datetime, timedelta, timezone

import pytest

from app import memory, scheduler
from app.db import ScheduledTask, get_session
from app.timeparse import parse_when, split_reminder

# Monday 5 Oct 2026, 10:30 local (fixed offset so the tests are deterministic)
NOW = datetime(2026, 10, 5, 10, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))


@pytest.mark.parametrize("phrase, expected", [
    ("tomorrow at 5 PM", datetime(2026, 10, 6, 17, 0)),
    ("tomorrow at 5pm", datetime(2026, 10, 6, 17, 0)),
    ("at 5:30 pm tomorrow", datetime(2026, 10, 6, 17, 30)),
    ("today at 18:00", datetime(2026, 10, 5, 18, 0)),
    ("at 9am", datetime(2026, 10, 6, 9, 0)),
    ("in 30 minutes", datetime(2026, 10, 5, 11, 0)),
    ("in 2 hours", datetime(2026, 10, 5, 12, 30)),
    ("in half an hour", datetime(2026, 10, 5, 11, 0)),
    ("on friday at 3pm", datetime(2026, 10, 9, 15, 0)),
    ("next monday at 9:00", datetime(2026, 10, 12, 9, 0)),
    ("tomorrow", datetime(2026, 10, 6, 9, 0)),
    ("tonight", datetime(2026, 10, 5, 20, 0)),
    ("2026-12-25T08:00", datetime(2026, 12, 25, 8, 0)),
])
def test_one_time_phrases(phrase, expected):
    schedule = parse_when(phrase, now=NOW)
    assert schedule.type == "once"
    assert schedule.run_at.replace(tzinfo=None) == expected


def test_recurring_phrases():
    daily = parse_when("every day at 9am", now=NOW)
    assert (daily.type, daily.hour, daily.minute) == ("daily", 9, 0)

    weekly = parse_when("every monday at 10:15", now=NOW)
    assert (weekly.type, weekly.day_of_week, weekly.hour, weekly.minute) == ("weekly", 0, 10, 15)

    interval = parse_when("every 2 hours", now=NOW)
    assert (interval.type, interval.interval_minutes) == ("interval", 120)

    assert parse_when("every 15 minutes", now=NOW).interval_minutes == 15
    assert parse_when("hourly", now=NOW).interval_minutes == 60


@pytest.mark.parametrize("phrase", ["yesterday at 5", "today at 9am", "at 25:00", "whenever", "", "in 5 lightyears"])
def test_bad_phrases(phrase):
    with pytest.raises(ValueError):
        parse_when(phrase, now=NOW)


def test_split_reminder():
    assert split_reminder("tomorrow at 5 PM to study DSA", now=NOW) == ("study DSA", "tomorrow at 5 PM")
    assert split_reminder("to call mom in 2 hours", now=NOW) == ("call mom", "in 2 hours")
    assert split_reminder("every day at 9am to drink water", now=NOW) == ("drink water", "every day at 9am")


@pytest.fixture
def running_scheduler():
    with get_session() as db:
        db.query(ScheduledTask).delete()
        db.commit()
    scheduler.start()
    yield scheduler
    scheduler.shutdown()


def test_create_list_delete_task(running_scheduler):
    task = scheduler.create_task("Study DSA", "tomorrow at 5 PM", session_id="sched-test")
    assert task["schedule_type"] == "once" and task["status"] == "active"
    assert task["next_run"] is not None
    assert scheduler.scheduler.get_job(f"task-{task['id']}") is not None

    assert any(t["id"] == task["id"] for t in scheduler.list_tasks())
    assert scheduler.delete_task(task["id"])
    assert scheduler.scheduler.get_job(f"task-{task['id']}") is None
    assert not scheduler.delete_task(task["id"])


def test_recurring_task_types(running_scheduler):
    for when in ("every day at 9am", "every monday at 10:00", "every 30 minutes"):
        task = scheduler.create_task("Check email", when)
        assert task["next_run"], when


def test_task_validation(running_scheduler):
    with pytest.raises(scheduler.TaskError):
        scheduler.create_task("", "tomorrow")
    with pytest.raises(scheduler.TaskError):
        scheduler.create_task("x", "whenever")
    with pytest.raises(scheduler.TaskError):
        scheduler.create_task("my password is hunter2", "tomorrow")
    with pytest.raises(scheduler.TaskError):
        scheduler.create_task("check prices", "every 2 minutes", action="agent")


def test_reminder_fires_and_posts_notification(running_scheduler):
    task = scheduler.create_task("Study DSA", "in 10 minutes", session_id="fire-test")
    scheduler.fire_task(task["id"])

    rows = memory.recent_messages("fire-test", roles=("notification",))
    assert rows[-1].content == "⏰ Reminder: Study DSA"
    assert scheduler.get_task(task["id"])["status"] == "done"


def test_tasks_survive_restart(running_scheduler):
    task = scheduler.create_task("Weekly review", "every friday at 6pm")
    scheduler.shutdown()
    assert scheduler.scheduler is None

    scheduler.start()
    assert scheduler.scheduler.get_job(f"task-{task['id']}") is not None


def test_missed_one_time_task_runs_after_restart(running_scheduler):
    task = scheduler.create_task("Missed one", "in 5 minutes", session_id="late-test")
    scheduler.shutdown()
    with get_session() as db:
        row = db.get(ScheduledTask, task["id"])
        row.run_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
        db.commit()

    scheduler.start()
    job = scheduler.scheduler.get_job(f"task-{task['id']}")
    assert job is not None and job.next_run_time - datetime.now(timezone.utc) < timedelta(seconds=10)
