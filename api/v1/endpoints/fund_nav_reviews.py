# -*- coding: utf-8 -*-
"""Fund NAV review API endpoints."""

from __future__ import annotations

import logging
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Query, Security
from fastapi.security import APIKeyCookie

from api.v1.errors import api_error
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.fund_nav_reviews import (
    FundNavRestoreBackfillRequest,
    FundNavRestoreBackfillResponse,
    FundNavReviewBackfillRequest,
    FundNavReviewBackfillResponse,
    FundNavRangeResponse,
    FundNavReviewHorizon,
    FundNavReviewItem,
    FundNavReviewListResponse,
    FundNavReviewRunRequest,
    FundNavReviewRunResponse,
    FundNavReviewStatsResponse,
)
from src.auth import COOKIE_NAME
from src.services.fund_nav_review_service import FundNavReviewService

logger = logging.getLogger(__name__)

admin_session_cookie = APIKeyCookie(
    name=COOKIE_NAME,
    scheme_name="AdminSessionCookie",
    auto_error=False,
)
router = APIRouter(dependencies=[Security(admin_session_cookie)])

AUTH_RESPONSE = {
    401: {
        "model": ErrorResponse,
        "description": "未登录或管理员会话无效（ADMIN_AUTH_ENABLED=true 时）",
    },
}


@router.get(
    "",
    response_model=FundNavReviewListResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "查询参数非法"},
        422: {"model": ErrorResponse, "description": "查询参数校验失败"},
        500: {"model": ErrorResponse, "description": "查询失败"},
    },
    summary="查询基金净值复盘台账",
    description="分页查询基金体检锚点及其后续净值表现；与 decision_signals 完全独立。",
    operation_id="listFundNavReviews",
)
def list_fund_nav_reviews(
    fund_code: Optional[str] = Query(None, max_length=16),
    horizon: Optional[FundNavReviewHorizon] = Query(None),
    eval_status: Optional[str] = Query(None),
    anchor_from: Optional[date] = Query(None, description="Anchor date lower bound (inclusive)"),
    anchor_to: Optional[date] = Query(None, description="Anchor date upper bound (inclusive)"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> FundNavReviewListResponse:
    try:
        return FundNavReviewListResponse(
            **FundNavReviewService().list_reviews(
                fund_code=fund_code,
                horizon=horizon,
                eval_status=eval_status,
                anchor_from=anchor_from,
                anchor_to=anchor_to,
                page=page,
                page_size=page_size,
            )
        )
    except ValueError as exc:
        raise api_error(400, "validation_error", str(exc))
    except Exception as exc:
        logger.error("List fund nav reviews failed: %s", exc, exc_info=True)
        raise api_error(500, "internal_error", "List fund nav reviews failed")


@router.get(
    "/stats",
    response_model=FundNavReviewStatsResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "查询参数非法"},
        500: {"model": ErrorResponse, "description": "统计失败"},
    },
    summary="查询基金净值复盘统计",
    description="返回全部复盘样本及 horizon / 月份聚合；比率在无已评估样本时为 null。",
    operation_id="getFundNavReviewStats",
)
def get_fund_nav_review_stats(
    horizons: Optional[List[FundNavReviewHorizon]] = Query(None),
) -> FundNavReviewStatsResponse:
    try:
        return FundNavReviewStatsResponse(
            **FundNavReviewService().get_stats(horizons=horizons)
        )
    except ValueError as exc:
        raise api_error(400, "validation_error", str(exc))
    except Exception as exc:
        logger.error("Get fund nav review stats failed: %s", exc, exc_info=True)
        raise api_error(500, "internal_error", "Get fund nav review stats failed")


@router.get(
    "/nav-range",
    response_model=FundNavRangeResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "查询参数非法"},
        422: {"model": ErrorResponse, "description": "查询参数校验失败"},
        500: {"model": ErrorResponse, "description": "查询失败"},
    },
    summary="查询基金区间净值走势",
    description=(
        "只读返回指定区间的单位净值、累计净值与分红再投资复权净值；"
        "区间最长 1100 天，超过 1000 条记录时截断。"
    ),
    operation_id="getFundNavRange",
)
def get_fund_nav_range(
    fund_code: str = Query(..., max_length=16),
    start_date: date = Query(..., description="Start date (inclusive)"),
    end_date: date = Query(..., description="End date (inclusive)"),
) -> FundNavRangeResponse:
    try:
        return FundNavRangeResponse(
            **FundNavReviewService().get_nav_range(
                fund_code=fund_code,
                start_date=start_date,
                end_date=end_date,
            )
        )
    except ValueError as exc:
        raise api_error(400, "validation_error", str(exc))
    except Exception as exc:
        logger.error("Get fund nav range failed: %s", exc, exc_info=True)
        raise api_error(500, "internal_error", "Get fund nav range failed")


@router.post(
    "/run",
    response_model=FundNavReviewRunResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "请求字段非法"},
        422: {"model": ErrorResponse, "description": "请求体校验失败"},
        500: {"model": ErrorResponse, "description": "评估失败"},
    },
    summary="手动评估基金净值复盘",
    description="按到期锚点抓取区间净值并计算表现；该接口会访问外部数据源。",
    operation_id="runFundNavReviews",
)
def run_fund_nav_reviews(payload: FundNavReviewRunRequest) -> FundNavReviewRunResponse:
    try:
        result = FundNavReviewService().run_reviews(
            fund_code=payload.fund_code,
            horizons=payload.horizons,
            force=payload.force,
            limit=payload.limit,
        )
        return FundNavReviewRunResponse(
            items=[FundNavReviewItem(**item) for item in result.get("items", [])],
            evaluated=int(result.get("evaluated", 0)),
            pending=int(result.get("pending", 0)),
            unable=int(result.get("unable", 0)),
            skipped=int(result.get("skipped", 0)),
            engine_version=str(result.get("engine_version", "")),
        )
    except ValueError as exc:
        raise api_error(400, "validation_error", str(exc))
    except Exception as exc:
        logger.error("Run fund nav reviews failed: %s", exc, exc_info=True)
        raise api_error(500, "internal_error", "Run fund nav reviews failed")


@router.post(
    "/backfill/run",
    response_model=FundNavReviewBackfillResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "请求字段非法"},
        422: {"model": ErrorResponse, "description": "请求体校验失败"},
        500: {"model": ErrorResponse, "description": "补录失败"},
    },
    summary="从历史基金报告补录净值锚点",
    description="默认 dry_run=true 且不联网；旧报告缺少净值日期时需显式 resolve_missing=true。",
    operation_id="backfillFundNavReviews",
)
def backfill_fund_nav_reviews(
    payload: FundNavReviewBackfillRequest,
) -> FundNavReviewBackfillResponse:
    try:
        result = FundNavReviewService().backfill_from_history(
            fund_code=payload.fund_code,
            since=payload.since,
            until=payload.until,
            dry_run=payload.dry_run,
            resolve_missing=payload.resolve_missing,
            limit=payload.limit,
        )
        return FundNavReviewBackfillResponse(**result)
    except ValueError as exc:
        raise api_error(400, "validation_error", str(exc))
    except Exception as exc:
        logger.error("Backfill fund nav reviews failed: %s", exc, exc_info=True)
        raise api_error(500, "internal_error", "Backfill fund nav reviews failed")


@router.post(
    "/backfill/restore-metrics",
    response_model=FundNavRestoreBackfillResponse,
    responses={
        **AUTH_RESPONSE,
        400: {"model": ErrorResponse, "description": "请求字段非法"},
        422: {"model": ErrorResponse, "description": "请求体校验失败"},
        500: {"model": ErrorResponse, "description": "回填失败"},
    },
    summary="回填基金净值复权指标",
    description=(
        "为已评估且尚未计算复权指标的复盘行补算累计净值与分红再投资口径；"
        "默认 dry_run=true，不写库。"
    ),
    operation_id="backfillFundNavRestoreMetrics",
)
def backfill_fund_nav_restore_metrics(
    payload: FundNavRestoreBackfillRequest,
) -> FundNavRestoreBackfillResponse:
    try:
        result = FundNavReviewService().backfill_restored_metrics(
            fund_code=payload.fund_code,
            anchor_from=payload.anchor_from,
            anchor_to=payload.anchor_to,
            limit=payload.limit,
            dry_run=payload.dry_run,
            force=payload.force,
        )
        return FundNavRestoreBackfillResponse(**result)
    except ValueError as exc:
        raise api_error(400, "validation_error", str(exc))
    except Exception as exc:
        logger.error("Backfill fund nav restore metrics failed: %s", exc, exc_info=True)
        raise api_error(500, "internal_error", "Backfill fund nav restore metrics failed")
