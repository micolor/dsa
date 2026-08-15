# -*- coding: utf-8 -*-
"""
===================================
全局异常处理
===================================

职责：
1. 捕获未处理的异常
2. 统一错误响应格式
3. 记录错误日志
"""

import logging
import traceback

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from api.v1.errors import error_body

logger = logging.getLogger(__name__)


def add_error_handlers(app) -> None:
    """
    添加全局异常处理器

    为 FastAPI 应用添加各类异常的处理器

    Args:
        app: FastAPI 应用实例
    """

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """处理 HTTP 异常。"""
        # 如果 detail 已经是 ErrorResponse 格式的 dict，直接使用
        if isinstance(exc.detail, dict) and "error" in exc.detail and "message" in exc.detail:
            return JSONResponse(
                status_code=exc.status_code,
                content=exc.detail,
            )
        # 否则包装成 ErrorResponse 格式（detail 为空时省略，与 error_body 语义一致）
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(
                "http_error",
                str(exc.detail) if exc.detail else "HTTP Error",
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """处理请求验证异常。"""
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "message": "请求参数验证失败",
                "detail": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception):
        """处理通用异常，返回统一错误体且不向客户端泄露内部细节。"""
        logger.error(
            "未处理的异常: %s\n请求路径: %s\n堆栈: %s",
            exc,
            request.url.path,
            traceback.format_exc(),
        )
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "服务器内部错误"),
        )
