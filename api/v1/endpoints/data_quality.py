# -*- coding: utf-8 -*-
"""Cross-source data-quality discrepancies API."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from api.v1.errors import api_error
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.data_quality import (
    DataQualityDiscrepancyItem,
    DataQualityDiscrepancyListResponse,
)
from src.repositories.data_quality_discrepancy_repo import DataQualityDiscrepancyRepository

logger = logging.getLogger(__name__)

router = APIRouter()


def _internal_error(message: str, exc: Exception) -> HTTPException:
    logger.error("%s: %s", message, exc, exc_info=True)
    return api_error(500, "internal_error", message)


@router.get(
    "/discrepancies",
    response_model=DataQualityDiscrepancyListResponse,
    responses={500: {"model": ErrorResponse}},
    summary="List cross-source data-quality discrepancies",
)
def list_discrepancies(
    market: Optional[str] = Query(None, description="Optional market filter"),
    stock_code: Optional[str] = Query(None, description="Optional stock code filter"),
    issue_type: Optional[str] = Query(None, description="Optional issue type filter"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> DataQualityDiscrepancyListResponse:
    try:
        rows, total = DataQualityDiscrepancyRepository().list_discrepancies(
            market=market,
            stock_code=stock_code,
            issue_type=issue_type,
            page=page,
            page_size=page_size,
        )
        return DataQualityDiscrepancyListResponse(
            items=[DataQualityDiscrepancyItem.model_validate(row) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _internal_error("List data quality discrepancies failed", exc)
