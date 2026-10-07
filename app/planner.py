"""Creates a short step-by-step plan for bigger tasks before the agent starts."""

import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from app.llm import LLMError

log = logging.getLogger("clawmind.planner")

PLAN_HINTS = (
    "analy", "report", "compare", "research", "investigate", "step by step", "chart",
    "graph", "plot", "summary of", "spreadsheet", "dataset", "breakdown", "then ",
)


class PlanStep(BaseModel):
    step: int
    description: str = Field(max_length=200)
    tool: str | None = None


class Plan(BaseModel):
    goal: str = Field(max_length=300)
    steps: list[PlanStep] = Field(min_length=1, max_length=8)

    def as_text(self):
        return "\n".join(f"{s.step}. {s.description}" for s in self.steps)


def needs_plan(message):
    text = message.lower()
    if len(text) > 400:
        return True
    return len(text.split()) >= 5 and any(hint in text for hint in PLAN_HINTS)


def _extract_json(text):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def make_plan(llm, message, tool_names):
    prompt = (
        "Break the user's task into 2 to 7 short, concrete steps. "
        f"Available tools: {', '.join(tool_names)}. "
        'Reply with JSON only, in this exact shape: '
        '{"goal": "...", "steps": [{"step": 1, "description": "...", "tool": "read_file"}]}. '
        "Use null for tool when no tool is needed. Do not include any other text."
    )
    try:
        reply = llm.chat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": message}],
            temperature=0,
        )
    except LLMError as exc:
        log.info("Planning skipped: %s", exc)
        return None

    data = _extract_json(reply["content"])
    if not data:
        return None
    try:
        plan = Plan.model_validate(data)
    except ValidationError:
        log.info("Planner returned an invalid plan")
        return None

    valid = set(tool_names)
    for step in plan.steps:
        if step.tool not in valid:
            step.tool = None
    return plan
