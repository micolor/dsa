# -*- coding: utf-8 -*-
"""Server-side proactive inbox endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from src.services.proactive_inbox_service import ProactiveInboxService

router = APIRouter()


class InboxReadRequest(BaseModel):
    message_ids: List[int] = []


@router.get("/messages")
def list_inbox_messages(
    type: Optional[str] = None,
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 50,
    id: Optional[int] = None,
) -> Dict[str, Any]:
    return {"status": "ok", **ProactiveInboxService().list(
        type=type,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
        message_id=id,
    )}


@router.post("/read")
def mark_inbox_read(request: InboxReadRequest) -> Dict[str, Any]:
    count = ProactiveInboxService().mark_read(request.message_ids)
    return {"status": "ok", "marked": count}


@router.post("/unread")
def mark_inbox_unread(request: InboxReadRequest) -> Dict[str, Any]:
    count = ProactiveInboxService().mark_unread(request.message_ids)
    return {"status": "ok", "marked": count}


@router.post("/read-all")
def mark_all_inbox_read() -> Dict[str, Any]:
    count = ProactiveInboxService().mark_all_read()
    return {"status": "ok", "marked": count}
