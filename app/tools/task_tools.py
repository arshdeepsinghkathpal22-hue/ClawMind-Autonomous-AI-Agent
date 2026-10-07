import threading

from app import scheduler
from app.tools.registry import Permission, ToolError, tool

# The agent sets this so tasks created in a chat report back to that chat
current_session = threading.local()


def _describe_cancel(args):
    task = scheduler.get_task(args.get("task_id", 0))
    if task:
        return f"cancel the scheduled task “{task['title']}” ({task['schedule']})"
    return f"cancel scheduled task #{args.get('task_id')}"


@tool(
    name="schedule_task",
    description="Schedule a reminder or a recurring agent task. 'when' accepts phrases like "
                "'tomorrow at 5 PM', 'in 30 minutes', 'every day at 9am', 'every monday at 10:00', "
                "'every 2 hours' or an ISO date-time. action='reminder' shows the text at that time; "
                "action='agent' runs the text as a prompt for ClawMind at that time.",
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "What to remind about, e.g. 'Study DSA'"},
            "when": {"type": "string", "description": "When to run"},
            "action": {"type": "string", "enum": ["reminder", "agent"]},
        },
        "required": ["title", "when"],
    },
    permission=Permission.WRITE,
    activity="Scheduling task...",
)
def schedule_task(title, when, action="reminder"):
    session_id = getattr(current_session, "id", None) or "default"
    try:
        task = scheduler.create_task(title, when, action=action, session_id=session_id)
    except scheduler.TaskError as exc:
        raise ToolError(str(exc)) from None
    return {"success": True, "task": task}


@tool(
    name="list_tasks",
    description="List scheduled reminders and tasks with their ids and next run time (UTC).",
    parameters={"type": "object", "properties": {}},
    permission=Permission.READ,
    activity="Checking scheduled tasks...",
)
def list_tasks():
    return {"success": True, "tasks": scheduler.list_tasks()}


@tool(
    name="cancel_task",
    description="Cancel (delete) a scheduled task by id. The user is asked to confirm.",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "integer"}},
        "required": ["task_id"],
    },
    permission=Permission.DANGEROUS,
    activity="Cancelling task...",
    describe=_describe_cancel,
)
def cancel_task(task_id):
    if not scheduler.delete_task(task_id):
        raise ToolError(f"There is no task with id {task_id}.")
    return {"success": True, "cancelled": task_id}
