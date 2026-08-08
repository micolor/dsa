# -*- coding: utf-8 -*-
"""Paper-trading endpoints."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.paper import (
    BackfillRequest,
    BackfillResponse,
    EquityPoint,
    PaperAccountResponse,
    PaperPositionItem,
    PaperSignalListResponse,
    PaperSnapshotResponse,
    PaperTradeListResponse,
    PaperValuationResponse,
    RefreshResponse,
)
from src.services.paper_service import PaperService
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


def _service(db_manager: DatabaseManager) -> PaperService:
    return PaperService(db_manager)


@router.get(
    "/account",
    response_model=PaperAccountResponse,
    responses={500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="获取模拟盘账户概览",
)
def get_account(
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> PaperAccountResponse:
    try:
        payload = _service(db_manager).get_or_create_account()
        return PaperAccountResponse(**payload)
    except Exception as exc:
        logger.error(f"获取模拟盘账户失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"获取模拟盘账户失败: {str(exc)}"},
        )


@router.get(
    "/snapshot",
    response_model=PaperSnapshotResponse,
    responses={500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="获取模拟盘最新快照",
)
def get_snapshot(
    account_id: int = Query(0, description="账户ID，0 表示默认账户"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> PaperSnapshotResponse:
    try:
        service = _service(db_manager)
        if account_id <= 0:
            account_id = service.get_or_create_account()["account_id"]
        snapshot = service.get_snapshot(account_id)
        return PaperSnapshotResponse(**snapshot)
    except Exception as exc:
        logger.error(f"获取模拟盘快照失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"获取模拟盘快照失败: {str(exc)}"},
        )


@router.get(
    "/equity-curve",
    response_model=list[EquityPoint],
    responses={500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="获取模拟盘净值曲线",
)
def get_equity_curve(
    account_id: int = Query(0, description="账户ID，0 表示默认账户"),
    start: Optional[date] = Query(None, description="起始日期"),
    end: Optional[date] = Query(None, description="结束日期"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> list[EquityPoint]:
    try:
        service = _service(db_manager)
        if account_id <= 0:
            account_id = service.get_or_create_account()["account_id"]
        points = service.get_equity_curve(account_id, start=start, end=end)
        return [EquityPoint(**p) for p in points]
    except Exception as exc:
        logger.error(f"获取模拟盘净值曲线失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"获取模拟盘净值曲线失败: {str(exc)}"},
        )


@router.get(
    "/positions",
    response_model=list[PaperPositionItem],
    responses={500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="获取模拟盘持仓",
)
def get_positions(
    account_id: int = Query(0, description="账户ID，0 表示默认账户"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> list[PaperPositionItem]:
    try:
        service = _service(db_manager)
        if account_id <= 0:
            account_id = service.get_or_create_account()["account_id"]
        positions = service.get_positions(account_id)
        return [PaperPositionItem(**p) for p in positions]
    except Exception as exc:
        logger.error(f"获取模拟盘持仓失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"获取模拟盘持仓失败: {str(exc)}"},
        )


@router.get(
    "/trades",
    response_model=PaperTradeListResponse,
    responses={500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="获取模拟盘成交流水",
)
def get_trades(
    account_id: int = Query(0, description="账户ID，0 表示默认账户"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> PaperTradeListResponse:
    try:
        service = _service(db_manager)
        if account_id <= 0:
            account_id = service.get_or_create_account()["account_id"]
        data = service.get_trades(account_id, page=page, limit=limit)
        return PaperTradeListResponse(**data)
    except Exception as exc:
        logger.error(f"获取模拟盘成交流水失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"获取模拟盘成交流水失败: {str(exc)}"},
        )


@router.get(
    "/signals",
    response_model=PaperSignalListResponse,
    responses={500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="获取模拟盘信号消费记录",
)
def get_signals(
    account_id: int = Query(0, description="账户ID，0 表示默认账户"),
    page: int = Query(1, ge=1, description="页码"),
    limit: int = Query(50, ge=1, le=200, description="每页数量"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> PaperSignalListResponse:
    try:
        service = _service(db_manager)
        if account_id <= 0:
            account_id = service.get_or_create_account()["account_id"]
        data = service.get_signals(account_id, page=page, limit=limit)
        return PaperSignalListResponse(**data)
    except Exception as exc:
        logger.error(f"获取模拟盘信号记录失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"获取模拟盘信号记录失败: {str(exc)}"},
        )


@router.post(
    "/refresh",
    response_model=PaperValuationResponse,
    responses={500: {"description": "服务器错误", "model": ErrorResponse}},
    summary="触发模拟盘当日估值",
)
def refresh(
    account_id: int = Query(0, description="账户ID，0 表示默认账户"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> PaperValuationResponse:
    try:
        service = _service(db_manager)
        if account_id <= 0:
            account_id = service.get_or_create_account()["account_id"]
        valuation = service.run_daily_valuation(account_id)
        return PaperValuationResponse(**valuation)
    except Exception as exc:
        logger.error(f"模拟盘估值失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"模拟盘估值失败: {str(exc)}"},
        )


@router.post(
    "/backfill",
    response_model=BackfillResponse,
    responses={
        400: {"description": "请求参数错误", "model": ErrorResponse},
        500: {"description": "服务器错误", "model": ErrorResponse},
    },
    summary="历史信号回填",
)
def backfill(
    request: BackfillRequest,
    account_id: int = Query(0, description="账户ID，0 表示默认账户"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> BackfillResponse:
    try:
        service = _service(db_manager)
        if account_id <= 0:
            account_id = service.get_or_create_account()["account_id"]
        result = service.backfill_history(account_id, request.from_date, request.to_date)
        return BackfillResponse(**result)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_params", "message": str(exc)},
        )
    except Exception as exc:
        logger.error(f"模拟盘历史回填失败: {exc}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": f"模拟盘历史回填失败: {str(exc)}"},
        )
