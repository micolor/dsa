# -*- coding: utf-8 -*-
"""Fund NAV review API schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


FundNavReviewHorizon = Literal["1m", "3m", "6m", "1y"]
FundNavReviewStatus = Literal["pending", "evaluated", "unable"]


class FundNavReviewItem(BaseModel):
    id: int
    fund_code: str
    fund_name: str
    horizon: str
    engine_version: str
    anchor_date: date
    anchor_nav: float
    due_date: date
    eval_status: str
    unable_reason: Optional[str] = None
    eval_date: Optional[date] = None
    eval_nav: Optional[float] = None
    nav_return_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    eval_window_records: Optional[int] = None
    analysis_history_id: Optional[int] = None
    anchor_source: str
    attempts: int
    last_error: Optional[str] = None
    checked_at: Optional[datetime] = None
    evaluated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FundNavReviewListResponse(BaseModel):
    items: List[FundNavReviewItem] = Field(default_factory=list)
    total: int
    page: int
    page_size: int


class FundNavReviewRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_code: Optional[str] = Field(None, max_length=16)
    horizons: Optional[List[FundNavReviewHorizon]] = None
    force: bool = False
    limit: int = Field(100, ge=1, le=400)


class FundNavReviewRunResponse(BaseModel):
    items: List[FundNavReviewItem] = Field(default_factory=list)
    evaluated: int
    pending: int
    unable: int
    skipped: int
    engine_version: str


class FundNavReviewBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fund_code: Optional[str] = Field(None, max_length=16)
    since: Optional[date] = None
    until: Optional[date] = None
    dry_run: bool = True
    resolve_missing: bool = False
    limit: int = Field(200, ge=1, le=500)


class FundNavReviewBackfillResponse(BaseModel):
    scanned: int
    created: int
    skipped: int
    unmatched: int
    funds: int
    dry_run: bool
    engine_version: str


class FundNavReviewStatsBucket(BaseModel):
    dimension: str
    value: str
    total: int
    anchor_count: int
    pending: int
    evaluated: int
    unable: int
    positive_rate_pct: Optional[float] = None
    avg_nav_return_pct: Optional[float] = None
    avg_max_drawdown_pct: Optional[float] = None
    unable_reasons: Dict[str, int] = Field(default_factory=dict)


class FundNavReviewStatsResponse(BaseModel):
    engine_version: str
    horizons: List[str] = Field(default_factory=list)
    total: int
    anchor_count: int
    pending: int
    evaluated: int
    unable: int
    positive_rate_pct: Optional[float] = None
    avg_nav_return_pct: Optional[float] = None
    avg_max_drawdown_pct: Optional[float] = None
    unable_reasons: Dict[str, int] = Field(default_factory=dict)
    breakdowns: Dict[str, List[FundNavReviewStatsBucket]] = Field(default_factory=dict)
