from datetime import datetime
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import FunctionTool, ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext


def _extract_job_session(job: Any) -> str | None:
    payload = getattr(job, "payload", None)
    if not isinstance(payload, dict):
        return None
    session = payload.get("session")
    return str(session) if session is not None else None


def _parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on"}:
            return True
        if lowered in {"0", "false", "no", "n", "off", ""}:
            return False
    return bool(value)


@dataclass
class CreateActiveCronTool(FunctionTool[AstrAgentContext]):
    name: str = "create_future_task"
    description: str = (
        "Create a future task for your future. Supports recurring cron expressions or one-time run_at datetime. "
        "Use this when you or the user want scheduled follow-up or proactive actions."
    )
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "cron_expression": {
                    "type": "string",
                    "description": "Cron expression defining recurring schedule (e.g., '0 8 * * *' or '0 23 * * mon-fri'). Prefer named weekdays like 'mon-fri' or 'sat,sun' instead of numeric day-of-week ranges such as '1-5' to avoid ambiguity across cron implementations.",
                },
                "run_at": {
                    "type": "string",
                    "description": "ISO datetime for one-time execution, e.g., 2026-02-02T08:00:00+08:00. Use with run_once=true.",
                },
                "note": {
                    "type": "string",
                    "description": "Detailed instructions for your future agent to execute when it wakes.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional label to recognize this future task.",
                },
                "run_once": {
                    "type": "boolean",
                    "description": "If true, the task will run only once and then be deleted. Use run_at to specify the time. If run_at is provided and cron_expression is omitted, the tool may auto-treat this as run_once=true.",
                },
            },
            "required": ["note"],
            "anyOf": [
                {"required": ["cron_expression"]},
                {"required": ["run_at"]},
            ],
            "examples": [
                {
                    "note": "Every workday 09:00 check CI status and report.",
                    "cron_expression": "0 9 * * mon-fri",
                    "run_once": False,
                },
                {
                    "note": "Tomorrow 08:30 remind me to review outline.",
                    "run_once": True,
                    "run_at": "2026-03-15T08:30:00+08:00",
                },
            ],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        cron_mgr = context.context.context.cron_manager
        if cron_mgr is None:
            return "error: cron manager is not available."

        cron_expression_raw = kwargs.get("cron_expression")
        run_at_raw = kwargs.get("run_at")
        cron_expression = (
            str(cron_expression_raw).strip() if cron_expression_raw is not None else ""
        )
        run_at = str(run_at_raw).strip() if run_at_raw is not None else ""
        cron_expression = cron_expression or None
        run_at = run_at or None
        run_once = _parse_bool(
            kwargs.get("run_once"),
            default=bool(run_at and not cron_expression),
        )
        note = str(kwargs.get("note", "")).strip()
        name = str(kwargs.get("name") or "").strip() or "active_agent_task"

        # Common LLM/tool-schema mismatch guard:
        # if run_at is provided without cron_expression, treat as one-time task.
        if run_at and not cron_expression and not run_once:
            run_once = True

        if not note:
            return "error: note is required."
        if not run_at and not cron_expression:
            return (
                "error: schedule is required. Provide either "
                "`cron_expression` (recurring) or `run_at` (one-time ISO datetime)."
            )
        if run_once and not run_at:
            return "error: run_at is required when run_once=true."
        if (not run_once) and not cron_expression:
            return (
                "error: cron_expression is required when run_once=false. "
                "For one-time tasks, set run_once=true and provide run_at."
            )
        if run_once and cron_expression:
            cron_expression = None
        run_at_dt = None
        if run_at:
            try:
                run_at_dt = datetime.fromisoformat(str(run_at))
            except Exception:
                return "error: run_at must be ISO datetime, e.g., 2026-02-02T08:00:00+08:00"

        payload = {
            "session": context.context.event.unified_msg_origin,
            "sender_id": context.context.event.get_sender_id(),
            "note": note,
            "origin": "tool",
        }

        job = await cron_mgr.add_active_job(
            name=name,
            cron_expression=str(cron_expression) if cron_expression else None,
            payload=payload,
            description=note,
            run_once=run_once,
            run_at=run_at_dt,
        )
        next_run = job.next_run_time or run_at_dt
        suffix = (
            f"one-time at {next_run}"
            if run_once
            else f"expression '{cron_expression}' (next {next_run})"
        )
        return f"Scheduled future task {job.job_id} ({job.name}) {suffix}."


@dataclass
class DeleteCronJobTool(FunctionTool[AstrAgentContext]):
    name: str = "delete_future_task"
    description: str = "Delete a future task (cron job) by its job_id."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "The job_id returned when the job was created.",
                }
            },
            "required": ["job_id"],
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        cron_mgr = context.context.context.cron_manager
        if cron_mgr is None:
            return "error: cron manager is not available."
        current_umo = context.context.event.unified_msg_origin
        job_id = kwargs.get("job_id")
        if not job_id:
            return "error: job_id is required."
        job = await cron_mgr.db.get_cron_job(str(job_id))
        if not job:
            return f"error: cron job {job_id} not found."
        if _extract_job_session(job) != current_umo:
            return "error: you can only delete future tasks in the current umo."
        await cron_mgr.delete_job(str(job_id))
        return f"Deleted cron job {job_id}."


@dataclass
class ListCronJobsTool(FunctionTool[AstrAgentContext]):
    name: str = "list_future_tasks"
    description: str = "List existing future tasks (cron jobs) for inspection."
    parameters: dict = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {
                "job_type": {
                    "type": "string",
                    "description": "Optional filter: basic or active_agent.",
                }
            },
        }
    )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        cron_mgr = context.context.context.cron_manager
        if cron_mgr is None:
            return "error: cron manager is not available."
        current_umo = context.context.event.unified_msg_origin
        job_type = kwargs.get("job_type")
        jobs = [
            job
            for job in await cron_mgr.list_jobs(job_type)
            if _extract_job_session(job) == current_umo
        ]
        if not jobs:
            return "No cron jobs found."
        lines = []
        for j in jobs:
            lines.append(
                f"{j.job_id} | {j.name} | {j.job_type} | run_once={getattr(j, 'run_once', False)} | enabled={j.enabled} | next={j.next_run_time}"
            )
        return "\n".join(lines)


CREATE_CRON_JOB_TOOL = CreateActiveCronTool()
DELETE_CRON_JOB_TOOL = DeleteCronJobTool()
LIST_CRON_JOBS_TOOL = ListCronJobsTool()

__all__ = [
    "CREATE_CRON_JOB_TOOL",
    "DELETE_CRON_JOB_TOOL",
    "LIST_CRON_JOBS_TOOL",
    "CreateActiveCronTool",
    "DeleteCronJobTool",
    "ListCronJobsTool",
]
