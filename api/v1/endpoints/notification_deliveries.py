# -*- coding: utf-8 -*-
"""Notification delivery receipts API (generic / non-alert routes)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.errors import api_error
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.notifications import (
    NotificationDeliveryItem,
    NotificationDeliveryListResponse,
)
from src.repositories.notification_delivery_repo import NotificationDeliveryRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return api_error(500, "internal_error", message)


@router.get(
    "/deliveries",
    response_model=NotificationDeliveryListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List notification delivery receipts",
)
def list_deliveries(
    route_type: Optional[str] = Query(None, description="Optional route type filter"),
    channel: Optional[str] = Query(None, description="Optional channel filter"),
    success: Optional[bool] = Query(None, description="Optional success filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> NotificationDeliveryListResponse:
    try:
        rows, total = NotificationDeliveryRepository().list_deliveries(
            route_type=route_type,
            channel=channel,
            success=success,
            page=page,
            page_size=page_size,
        )
        return NotificationDeliveryListResponse(
            items=[NotificationDeliveryItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _internal_error("List notification deliveries failed", exc)
