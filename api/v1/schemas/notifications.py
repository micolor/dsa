# -*- coding: utf-8 -*-
"""Schemas for generic (non-alert) notification delivery receipts."""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class NotificationDeliveryItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    route_type: str
    channel: str
    attempt: int
    success: bool
    error_code: Optional[str] = None
    retryable: bool
    latency_ms: Optional[int] = None
    diagnostics: Optional[str] = None
    created_at: datetime


class NotificationDeliveryListResponse(BaseModel):
    items: List[NotificationDeliveryItem]
    total: int
    page: int
    page_size: int
