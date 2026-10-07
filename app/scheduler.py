"""Scheduled reminders and agent tasks.

The scheduled_tasks table is the source of truth. On startup every active task
is registered with APScheduler again, so tasks survive restarts.
"""

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.db import ScheduledTask, as_utc, get_session, iso, utcnow
from app.memory import save_message
from app.redact import find_secret
from app.timeparse import Schedule, local_tz, parse_when

log = logging.getLogger("clawmind.scheduler")

scheduler = None

# Tasks that were due while the app was off are still run if they are at most this late
LATE_GRACE = timedelta(hours=12)


class TaskError(ValueError):
    pass


def start():
    global scheduler
    if scheduler and scheduler.running:
        return
    scheduler = BackgroundScheduler(
        timezone=local_tz(),
        job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600},
    )
    scheduler.start()
    load_tasks()
    log.info("Scheduler started")


def shutdown():
    global scheduler
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
    scheduler = None


def _job_id(task_id):
    return f"task-{task_id}"


def _trigger(task):
    tz = local_tz()
    if task.schedule_type == "once":
        return DateTrigger(run_date=as_utc(task.run_at))
    if task.schedule_type == "daily":
        return CronTrigger(hour=task.hour, minute=task.minute, timezone=tz)
    if task.schedule_type == "weekly":
        return CronTrigger(day_of_week=task.day_of_week, hour=task.hour, minute=task.minute, timezone=tz)
    # Anchor to the creation time so restarts don't shift the schedule
    first_run = as_utc(task.created_at) + timedelta(minutes=task.interval_minutes)
    return IntervalTrigger(minutes=task.interval_minutes, start_date=first_run, timezone=tz)


def _register(task, trigger=None):
    if not scheduler:
        return
    scheduler.add_job(
        fire_task,
        trigger or _trigger(task),
        args=[task.id],
        id=_job_id(task.id),
        replace_existing=True,
    )


def _next_run(task_id):
    if not scheduler:
        return None
    job = scheduler.get_job(_job_id(task_id))
    if job and job.next_run_time:
        return job.next_run_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return None


def task_to_dict(task):
    schedule = Schedule(
        type=task.schedule_type,
        run_at=as_utc(task.run_at).astimezone(local_tz()) if task.run_at else None,
        hour=task.hour,
        minute=task.minute,
        day_of_week=task.day_of_week,
        interval_minutes=task.interval_minutes,
    )
    return {
        "id": task.id,
        "title": task.title,
        "action": task.action,
        "schedule_type": task.schedule_type,
        "schedule": schedule.describe(),
        "when": task.when_text,
        "status": task.status,
        "next_run": _next_run(task.id) if task.status == "active" else None,
        "last_run": iso(task.last_run_at),
        "session_id": task.session_id,
        "created_at": iso(task.created_at),
    }


def create_task(title, when, action="reminder", session_id="default"):
    title = " ".join(str(title or "").split())
    if not title:
        raise TaskError("The task needs a description.")
    if len(title) > 500:
        raise TaskError("Task descriptions are limited to 500 characters.")
    if action not in ("reminder", "agent"):
        raise TaskError("action must be 'reminder' or 'agent'.")
    if find_secret(title):
        raise TaskError("That task text looks like it contains a password or token. Leave secrets out of tasks.")

    try:
        schedule = parse_when(when)
    except ValueError as exc:
        raise TaskError(str(exc)) from None

    if action == "agent" and schedule.type == "interval" and schedule.interval_minutes < 5:
        raise TaskError("Agent tasks can run at most every 5 minutes.")

    task = ScheduledTask(
        title=title,
        action=action,
        schedule_type=schedule.type,
        when_text=str(when)[:200],
        run_at=schedule.run_at.astimezone(timezone.utc).replace(tzinfo=None) if schedule.run_at else None,
        hour=schedule.hour,
        minute=schedule.minute,
        day_of_week=schedule.day_of_week,
        interval_minutes=schedule.interval_minutes,
        session_id=session_id or "default",
    )
    with get_session() as db:
        db.add(task)
        db.commit()
        _register(task)
        log.info("Created task #%s (%s, %s)", task.id, task.action, schedule.describe())
        return task_to_dict(task)


def list_tasks():
    with get_session() as db:
        rows = db.scalars(select(ScheduledTask).order_by(ScheduledTask.id.desc()))
        return [task_to_dict(row) for row in rows]


def get_task(task_id):
    with get_session() as db:
        row = db.get(ScheduledTask, int(task_id))
        return task_to_dict(row) if row else None


def delete_task(task_id):
    with get_session() as db:
        task = db.get(ScheduledTask, int(task_id))
        if not task:
            return False
        db.delete(task)
        db.commit()
    if scheduler:
        try:
            scheduler.remove_job(_job_id(task_id))
        except JobLookupError:
            pass
    log.info("Deleted task #%s", task_id)
    return True


def load_tasks():
    now = utcnow()
    with get_session() as db:
        tasks = list(db.scalars(select(ScheduledTask).where(ScheduledTask.status == "active")))
        for task in tasks:
            if task.schedule_type == "once" and task.run_at <= now:
                if now - task.run_at <= LATE_GRACE:
                    # Missed while the app was off: run it shortly after startup
                    soon = datetime.now(timezone.utc) + timedelta(seconds=3)
                    _register(task, DateTrigger(run_date=soon))
                else:
                    task.status = "missed"
                continue
            _register(task)
        db.commit()
    log.info("Loaded %s active task(s)", len(tasks))


def fire_task(task_id):
    with get_session() as db:
        task = db.get(ScheduledTask, task_id)
        if not task or task.status != "active":
            return
        title, action, session_id = task.title, task.action, task.session_id
        late = task.schedule_type == "once" and utcnow() - task.run_at > timedelta(minutes=5)

    if action == "reminder":
        text = f"⏰ Reminder: {title}"
        if late:
            text += " (this was due while ClawMind was not running)"
    else:
        text = _run_agent_task(title, session_id)

    save_message(session_id, "notification", text)

    with get_session() as db:
        task = db.get(ScheduledTask, task_id)
        if task:
            task.last_run_at = utcnow()
            if task.schedule_type == "once":
                task.status = "done"
            db.commit()
    log.info("Fired task #%s", task_id)


def _run_agent_task(prompt, session_id):
    from app.agent import run_agent

    try:
        result = run_agent(prompt, session_id, save_messages=False, allow_dangerous=False)
        return f"🗓️ Scheduled task “{prompt}”:\n\n{result.response}"
    except Exception:
        log.exception("Scheduled agent task failed")
        return f"🗓️ Scheduled task “{prompt}” failed. Check the server log for details."
