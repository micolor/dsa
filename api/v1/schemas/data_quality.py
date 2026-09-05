# -*- coding: utf-8 -*-
"""Schemas for cross-source data-quality discrepancies.

Response models serialise snake_case ORM/attribute names to camelCase keys
(stockCode, issueType, primarySource, ...) per the reconciliation design
spec, mirroring the camelCase output pattern used by auth schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class DataQualityDiscrepancyItem(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: int
    market: str = Field(alias="market")
    stock_code: str = Field(alias="stockCode")
    issue_type: str = Field(alias="issueType")
    primary_source: Optional[str] = Field(None, alias="primarySource")
    secondary_source: Optional[str] = Field(None, alias="secondarySource")
    primary_price: Optional[float] = Field(None, alias="primaryPrice")
    secondary_price: Optional[float] = Field(None, alias="secondaryPrice")
    price_diff_pct: Optional[float] = Field(None, alias="priceDiffPct")
    primary_ts: Optional[str] = Field(None, alias="primaryTs")
    secondary_ts: Optional[str] = Field(None, alias="secondaryTs")
    detail: Optional[str] = Field(None, alias="detail")
    created_at: datetime = Field(alias="createdAt")


class DataQualityDiscrepancyListResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    items: List[DataQualityDiscrepancyItem] = Field(alias="items")
    total: int = Field(alias="total")
    page: int = Field(alias="page")
    page_size: int = Field(alias="pageSize")
