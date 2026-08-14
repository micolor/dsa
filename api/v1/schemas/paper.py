# -*- coding: utf-8 -*-
"""Paper-trading API schemas."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class PaperSnapshotResponse(BaseModel):
    account_id: int
    cash: float = 0.0
    market_value: float = 0.0
    net_value: float = 0.0
    return_pct: float = 0.0
    initial_capital: float = 0.0
    open_position_count: int = 0


class PaperAccountResponse(BaseModel):
    account_id: int
    name: str
    initial_capital: float = 0.0
    cash: float = 0.0
    status: str
    snapshot: PaperSnapshotResponse


class EquityPoint(BaseModel):
    trade_date: str
    net_value: float = 0.0
    return_pct: Optional[float] = None


class PaperPositionItem(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    market: Optional[str] = None
    quantity: float = 0.0
    avg_cost: Optional[float] = None
    current_price: Optional[float] = None
    market_value: Optional[float] = None
    entry_date: Optional[str] = None
    stop_loss: Optional[float] = None
    target_price: Optional[float] = None
    status: Optional[str] = None
    status: str = "open"


class PaperTradeItem(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    side: str
    quantity: float = 0.0
    price: Optional[float] = None
    amount: Optional[float] = None
    trade_date: str
    reason: Optional[str] = None


class PaperTradeListResponse(BaseModel):
    items: List[PaperTradeItem] = Field(default_factory=list)
    total: int = 0


class PaperSignalItem(BaseModel):
    signal_id: int
    action: str
    disposition: str
    processed_at: str
    stock_code: Optional[str] = None
    stock_name: Optional[str] = None


class PaperSignalListResponse(BaseModel):
    items: List[PaperSignalItem] = Field(default_factory=list)
    total: int = 0


class PaperValuationResponse(BaseModel):
    account_id: int
    trade_date: str
    cash: float = 0.0
    market_value: float = 0.0
    net_value: float = 0.0
    return_pct: float = 0.0


class BackfillRequest(BaseModel):
    from_date: date = Field(..., description="回填起始日期（含）")
    to_date: Optional[date] = Field(None, description="回填结束日期（含），默认今天")


class BackfillResponse(BaseModel):
    account_id: int
    from_date: str
    to_date: str
    signals_replayed: int = 0
    snapshot: PaperSnapshotResponse


class RefreshResponse(BaseModel):
    snapshot: PaperSnapshotResponse
    valuation: Optional[Dict[str, Any]] = None
