# -*- coding: utf-8 -*-
"""Tools for the chat assistant to read and manage user-defined scheduled tasks."""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

_READ_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=["db_read"],
    execution_mode="none",
    risk_level="read",
    cancellation_safe=True,
)

_DIRECT_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="direct",
    risk_level="low",
    undo_hint="通过 set_scheduled_task_enabled 停用任务，或在定时任务页删除",
    cancellation_safe=False,
)

_PROPOSAL_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="proposal",
    risk_level="high",
    cancellation_safe=False,
)


def _executed(kind: str, summary: str, result: Dict[str, Any], undo_hint: str) -> Dict[str, Any]:
    return {
        "kind": kind,
        "summary": summary,
        "result": result,
        "undo_hint": undo_hint,
    }


_RUN_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["background_task"],
    execution_mode="direct",
    risk_level="low",
    undo_hint="任务在后台异步执行，无法撤销，可在定时任务页查看运行记录",
    cancellation_safe=False,
)


def _service():
    from src.services.scheduled_task_service import ScheduledTaskService

    return ScheduledTaskService()


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _handle_get_scheduled_tasks_view(days: int = 7) -> str:
    service = _service()
    span = max(1, min(int(days or 7), 31))
    today = date.today()
    calendar = service.calendar(today, today + timedelta(days=span - 1))
    return _json(
        {
            "tasks": service.list_tasks(),
            "calendar": calendar,
        }
    )


def _handle_create_scheduled_task(
    name: str,
    task_type: str,
    time_of_day: str,
    weekdays: Optional[List[int]] = None,
    payload: Optional[Dict[str, Any]] = None,
    trading_day_only: bool = True,
    enabled: bool = True,
) -> str:
    try:
        task = _service().create_task(
            {
                "name": name,
                "task_type": task_type,
                "time_of_day": time_of_day,
                "weekdays": weekdays if weekdays is not None else list(range(7)),
                "payload": payload or {},
                "trading_day_only": bool(trading_day_only),
                "enabled": bool(enabled),
            }
        )
    except Exception as exc:
        return {"error": str(exc)}
    return _executed(
        "scheduled_task_create",
        f"已创建定时任务「{task['name']}」",
        {"task": task},
        "删除或停用任务可撤销；删除需用户确认",
    )


def _handle_update_scheduled_task(
    task_id: int,
    name: Optional[str] = None,
    time_of_day: Optional[str] = None,
    weekdays: Optional[List[int]] = None,
    payload: Optional[Dict[str, Any]] = None,
    trading_day_only: Optional[bool] = None,
    enabled: Optional[bool] = None,
) -> str:
    fields: Dict[str, Any] = {}
    if name is not None:
        fields["name"] = name
    if time_of_day is not None:
        fields["time_of_day"] = time_of_day
    if weekdays is not None:
        fields["weekdays"] = weekdays
    if payload is not None:
        fields["payload"] = payload
    if trading_day_only is not None:
        fields["trading_day_only"] = trading_day_only
    if enabled is not None:
        fields["enabled"] = enabled
    if not fields:
        return {"error": "没有需要修改的字段"}
    try:
        task = _service().update_task(int(task_id), fields)
    except KeyError:
        return _json({"ok": False, "error": "定时任务不存在"})
    except Exception as exc:
        return {"error": str(exc)}
    return _executed(
        "scheduled_task_update",
        f"已更新定时任务「{task['name']}」",
        {"task": task},
        "重新调用 update_scheduled_task 改回原配置即可撤销",
    )


def _handle_set_scheduled_task_enabled(task_id: int, enabled: bool) -> str:
    try:
        task = _service().set_enabled(int(task_id), bool(enabled))
    except KeyError:
        return _json({"ok": False, "error": "定时任务不存在"})
    except Exception as exc:
        return _json({"ok": False, "error": str(exc)})
    state = "启用" if task["enabled"] else "停用"
    return _executed(
        "scheduled_task_enable",
        f"已{state}定时任务「{task['name']}」",
        {"task": task},
        "再次调用 set_scheduled_task_enabled 切换回相反状态",
    )


def _handle_run_scheduled_task_now(task_id: int) -> str:
    task = _service().get_task(int(task_id))
    if task is None:
        return {"error": "定时任务不存在"}

    def _run() -> None:
        _service().execute(task, trigger_source="manual")

    threading.Thread(
        target=_run,
        name=f"agent-scheduled-task-{task_id}",
        daemon=True,
    ).start()
    return _executed(
        "scheduled_task_run",
        f"已提交定时任务「{task['name']}」立即执行",
        {"task_id": int(task_id)},
        "任务已在后台异步执行，执行记录可在定时任务页查看",
    )


def _handle_propose_scheduled_task_delete(task_id: int) -> str:
    task = _service().get_task(int(task_id))
    if task is None:
        return {"error": "定时任务不存在"}
    return {
        "kind": "scheduled_task_delete",
        "summary": f"删除定时任务「{task['name']}」（{task['time_of_day']}）及其运行记录",
        "proposal": {"task_id": int(task_id)},
    }


get_scheduled_tasks_view = ToolDefinition(
    name="get_scheduled_tasks_view",
    description="读取用户创建的定时任务及其未来若干天的日历安排（默认 7 天）。",
    parameters=[
        ToolParameter(name="days", type="integer", description="查看未来多少天，1-31，默认 7", required=False),
    ],
    handler=_handle_get_scheduled_tasks_view,
    category="site_read",
    policy=_READ_POLICY,
)

create_scheduled_task = ToolDefinition(
    name="create_scheduled_task",
    description=(
        "创建一个周期性定时任务。task_type: stock_analysis / market_review / screening / "
        "backtest / agent_prompt。weekdays 用 0=周一 ... 6=周日；time_of_day 用 HH:MM（Asia/Shanghai）。"
        "payload：stock_analysis={stocks:[],report_type,mode:'standard|quick',notify}（快速模式只拉行情+基础指标，不跑完整流水线；场外基金用 stocks:[\"fund:005827\"]），market_review={region,notify}，"
        "screening={strategy,market,max_results}，backtest={code,force,limit}，"
        "agent_prompt={prompt}（自定义助手指令，5-2000 字）。执行前必须先征得用户明确同意。"
    ),
    parameters=[
        ToolParameter(name="name", type="string", description="任务名称", required=True),
        ToolParameter(
            name="task_type",
            type="string",
            description="任务类型",
            required=True,
            enum=["stock_analysis", "market_review", "screening", "backtest", "agent_prompt"],
        ),
        ToolParameter(name="time_of_day", type="string", description="执行时间 HH:MM", required=True),
        ToolParameter(name="weekdays", type="array", description="0=周一 ... 6=周日，默认每天", required=False),
        ToolParameter(name="payload", type="object", description="任务类型对应的参数", required=False),
        ToolParameter(name="trading_day_only", type="boolean", description="仅交易日执行，默认 true", required=False),
    ],
    handler=_handle_create_scheduled_task,
    category="site_task",
    policy=_DIRECT_POLICY,
)

update_scheduled_task = ToolDefinition(
    name="update_scheduled_task",
    description=(
        "修改定时任务的名称、时间、重复日或参数；payload 需按任务类型传完整对象。"
        "修改前先确认用户意图。"
    ),
    parameters=[
        ToolParameter(name="task_id", type="integer", description="任务 ID", required=True),
        ToolParameter(name="name", type="string", description="新名称", required=False),
        ToolParameter(name="time_of_day", type="string", description="新时间 HH:MM", required=False),
        ToolParameter(name="weekdays", type="array", description="0=周一 ... 6=周日", required=False),
        ToolParameter(name="payload", type="object", description="新的完整 payload", required=False),
        ToolParameter(name="trading_day_only", type="boolean", description="仅交易日执行", required=False),
        ToolParameter(name="enabled", type="boolean", description="启用状态", required=False),
    ],
    handler=_handle_update_scheduled_task,
    category="site_task",
    policy=_DIRECT_POLICY,
)

set_scheduled_task_enabled = ToolDefinition(
    name="set_scheduled_task_enabled",
    description="启用或停用一个定时任务；删除任务请让用户在定时任务页操作。",
    parameters=[
        ToolParameter(name="task_id", type="integer", description="任务 ID", required=True),
        ToolParameter(name="enabled", type="boolean", description="true 启用，false 停用", required=True),
    ],
    handler=_handle_set_scheduled_task_enabled,
    category="site_task",
    policy=_DIRECT_POLICY,
)

run_scheduled_task_now = ToolDefinition(
    name="run_scheduled_task_now",
    description="立即在后台执行一次指定定时任务（不影响原有周期）。",
    parameters=[
        ToolParameter(name="task_id", type="integer", description="任务 ID", required=True),
    ],
    handler=_handle_run_scheduled_task_now,
    category="site_task",
    policy=_RUN_POLICY,
)

propose_scheduled_task_delete = ToolDefinition(
    name="propose_scheduled_task_delete",
    description="生成删除定时任务的提案（会一并删除运行记录）；工具本身不删除，必须由用户确认。",
    parameters=[
        ToolParameter(name="task_id", type="integer", description="任务 ID", required=True),
    ],
    handler=_handle_propose_scheduled_task_delete,
    category="site_task",
    policy=_PROPOSAL_POLICY,
)

ALL_SCHEDULED_TASK_TOOLS = [
    get_scheduled_tasks_view,
    create_scheduled_task,
    update_scheduled_task,
    set_scheduled_task_enabled,
    run_scheduled_task_now,
    propose_scheduled_task_delete,
]
