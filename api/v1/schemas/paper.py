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
    status: str = "open"


class PaperTradeItem(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    side: str
    quantity: float = 0.0
    price: Optional[float] = None
    amount: Optional[float] = None
    fee: float = 0.0
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
    # ``data_unavailable`` means at least one held symbol had no usable bar for
    # the selected session, so no snapshot was written and the caller should
    # retry later. ``ok`` with ``snapshot_written=false`` means the day already
    # had a snapshot and the existing values were returned unchanged.
    status: str = "ok"
    missing_symbols: List[str] = Field(default_factory=list)
    snapshot_written: bool = True


class PaperResetRequest(BaseModel):
    initial_capital: Optional[float] = Field(
        None,
        gt=0,
        description="新账户初始资金，省略则用 PAPER_INITIAL_CAPITAL 配置值",
    )


class BackfillRequest(BaseModel):
    from_date: date = Field(..., description="回填起始日期（含）")
    to_date: Optional[date] = Field(None, description="回填结束日期（含），默认今天")


class PaperAutoTradeResponse(BaseModel):
    status: str = "ok"
    account_id: int
    lookback_days: int = 7
    signals_seen: int = 0
    stale_skipped: int = 0
    processed: int = 0
    skipped: int = 0
    unavailable: int = 0
    errors: int = 0
    results: List[Dict[str, Any]] = Field(default_factory=list)


class BackfillResponse(BaseModel):
    account_id: int
    from_date: str
    to_date: str
    signals_replayed: int = 0
    signals_unavailable: int = 0
    snapshot: PaperSnapshotResponse
