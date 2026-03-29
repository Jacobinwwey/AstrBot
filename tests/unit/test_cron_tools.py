"""Tests for cron tool metadata."""

from types import SimpleNamespace

import pytest

from astrbot.core.tools.cron_tools import CreateActiveCronTool


def test_create_future_task_cron_description_prefers_named_weekdays():
    """The cron tool should steer users toward unambiguous named weekdays."""
    tool = CreateActiveCronTool()

    description = tool.parameters["properties"]["cron_expression"]["description"]

    assert "mon-fri" in description
    assert "sat,sun" in description
    assert "1-5" in description
    assert "avoid ambiguity" in description


@pytest.mark.asyncio
async def test_create_future_task_auto_promotes_run_at_to_run_once():
    tool = CreateActiveCronTool()

    class _CronMgr:
        def __init__(self) -> None:
            self.db = object()
            self.calls: list[dict] = []

        async def add_active_job(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                job_id="job-test-1",
                name=kwargs.get("name", "active_agent_task"),
                next_run_time=None,
            )

    cron_mgr = _CronMgr()
    ctx = SimpleNamespace(
        context=SimpleNamespace(
            context=SimpleNamespace(cron_manager=cron_mgr),
            event=SimpleNamespace(
                unified_msg_origin="slack:test",
                get_sender_id=lambda: "U-test",
            ),
        )
    )

    result = await tool.call(
        ctx,
        note="tomorrow reminder",
        run_at="2026-03-15T08:30:00+08:00",
        run_once=False,
    )

    assert "Scheduled future task" in result
    assert cron_mgr.calls
    assert cron_mgr.calls[-1]["run_once"] is True
    assert cron_mgr.calls[-1]["cron_expression"] is None


@pytest.mark.asyncio
async def test_create_future_task_rejects_missing_schedule_with_actionable_message():
    tool = CreateActiveCronTool()

    class _CronMgr:
        def __init__(self) -> None:
            self.db = object()

        async def add_active_job(self, **kwargs):  # pragma: no cover - should not run
            raise AssertionError("add_active_job should not be called")

    ctx = SimpleNamespace(
        context=SimpleNamespace(
            context=SimpleNamespace(cron_manager=_CronMgr()),
            event=SimpleNamespace(
                unified_msg_origin="slack:test",
                get_sender_id=lambda: "U-test",
            ),
        )
    )

    result = await tool.call(ctx, note="just remind me later")

    assert "schedule is required" in result
    assert "cron_expression" in result
    assert "run_at" in result
