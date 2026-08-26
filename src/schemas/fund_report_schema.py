from datetime import date
from typing import List, Optional
from pydantic import BaseModel


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
