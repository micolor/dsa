# -*- coding: utf-8 -*-
"""User-defined recurring task endpoints and calendar view."""

from __future__ import annotations

import threading
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from api.v1.errors import api_error
from src.services.scheduled_task_service import (
    ScheduledTaskService,
    ScheduledTaskValidationError,
)

router = APIRouter()


class ScheduledTaskCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    task_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    weekdays: List[int] = Field(default_factory=lambda: list(range(7)))
    time_of_day: str
    enabled: bool = True
    trading_day_only: bool = True


class ScheduledTaskUpdateRequest(BaseModel):
    name: Optional[str] = None
    task_type: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None
    weekdays: Optional[List[int]] = None
    time_of_day: Optional[str] = None
    enabled: Optional[bool] = None
    trading_day_only: Optional[bool] = None


class ScheduledTaskEnabledRequest(BaseModel):
    enabled: bool


def _service() -> ScheduledTaskService:
    return ScheduledTaskService()


def _bad_request(exc: Exception) -> HTTPException:
    return api_error(400, "invalid_scheduled_task", str(exc))


@router.get("")
def list_scheduled_tasks(enabled_only: bool = False) -> Dict[str, Any]:
    return {"status": "ok", "items": _service().list_tasks(enabled_only=enabled_only)}


@router.post("")
def create_scheduled_task(request: ScheduledTaskCreateRequest) -> Dict[str, Any]:
    try:
        task = _service().create_task(request.model_dump())
    except ScheduledTaskValidationError as exc:
        raise _bad_request(exc)
    return {"status": "ok", "task": task}


@router.patch("/{task_id}")
def update_scheduled_task(task_id: int, request: ScheduledTaskUpdateRequest) -> Dict[str, Any]:
    try:
        task = _service().update_task(task_id, request.model_dump(exclude_unset=True))
    except KeyError:
        raise api_error(404, "scheduled_task_not_found", "定时任务不存在")
    except ScheduledTaskValidationError as exc:
        raise _bad_request(exc)
    return {"status": "ok", "task": task}


@router.delete("/{task_id}")
def delete_scheduled_task(task_id: int) -> Dict[str, Any]:
    if not _service().delete_task(task_id):
        raise api_error(404, "scheduled_task_not_found", "定时任务不存在")
    return {"status": "ok", "deleted": 1}


@router.post("/{task_id}/enabled")
def set_scheduled_task_enabled(task_id: int, request: ScheduledTaskEnabledRequest) -> Dict[str, Any]:
    try:
        task = _service().set_enabled(task_id, request.enabled)
    except KeyError:
        raise api_error(404, "scheduled_task_not_found", "定时任务不存在")
    except ScheduledTaskValidationError as exc:
        raise _bad_request(exc)
    return {"status": "ok", "task": task}


@router.post("/{task_id}/run")
def run_scheduled_task_now(task_id: int) -> Dict[str, Any]:
    """Run asynchronously; the calendar/run history shows the final status."""
    service = _service()
    task = service.get_task(task_id)
    if task is None:
        raise api_error(404, "scheduled_task_not_found", "定时任务不存在")

    def _run() -> None:
        service.execute(task, trigger_source="manual")

    threading.Thread(target=_run, name=f"scheduled-task-{task_id}", daemon=True).start()
    return {"status": "accepted", "task_id": task_id}


@router.get("/calendar")
def scheduled_task_calendar(
    start: date = Query(..., description="Start date YYYY-MM-DD"),
    end: date = Query(..., description="End date YYYY-MM-DD"),
) -> Dict[str, Any]:
    try:
        return {"status": "ok", **_service().calendar(start, end)}
    except ScheduledTaskValidationError as exc:
        raise _bad_request(exc)


@router.get("/{task_id}/runs")
def list_scheduled_task_runs(task_id: int, limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    service = _service()
    if service.get_task(task_id) is None:
        raise api_error(404, "scheduled_task_not_found", "定时任务不存在")
    return {
        "status": "ok",
        "items": service.db.list_scheduled_task_runs(task_id=task_id, limit=limit),
    }
