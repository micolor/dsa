from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class FundNavRow(BaseModel):
    date: date
    unit_nav: Optional[float] = None
    daily_growth: Optional[float] = None


class FundProfileRow(BaseModel):
    code: str
    name: Optional[str] = None
    fund_type: Optional[str] = None
    inception_date: Optional[str] = None
    manager: Optional[str] = None
    scale: Optional[str] = None
    fee: Optional[str] = None


class FundHoldingRow(BaseModel):
    code: Optional[str] = None
    name: Optional[str] = None
    ratio: Optional[float] = None
    market_value: Optional[float] = None
    quarter: Optional[str] = None


class FundReportSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    fund_name: Optional[str] = None
    fund_type: Optional[str] = None
    manager: Optional[str] = None
    scale: Optional[str] = None
    inception_date: Optional[str] = None
    interval_return: Optional[float] = None
    max_drawdown: Optional[float] = None
    current_drawdown: Optional[float] = None
    holdings_concentration: Optional[str] = None
    top_holdings: Optional[List[FundHoldingRow]] = None
    analysis_summary: Optional[str] = None
    operation_advice: Optional[str] = None
    risk_warning: Optional[str] = None
    sentiment_score: Optional[int] = Field(default=None, ge=0, le=100)
