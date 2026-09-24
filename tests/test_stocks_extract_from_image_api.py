# -*- coding: utf-8 -*-
"""`/stocks/extract-from-image` 的失败日志必须脱敏。

设计 §2 承诺"图片二进制不落盘"，这个承诺**覆盖日志**：provider 可能在错误体里回显请求内容
（含 base64 图片），而日志会被轮转、打包、上传。这里把该端点的最终日志钉在行为上——诊断信息
（异常类型、状态码、主机名）要留着，长串要抹掉。

之所以单列一个文件：该端点的失败分支此前没有任何测试，而"日志里不出现图片内容"是设计承诺，
不是实现细节。
"""

import base64
import io
import logging
from unittest import mock

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from api.v1.endpoints import stocks


def _png_upload() -> UploadFile:
    data = b"\x89PNG\r\n\x1a\n" + b"x" * 200
    return UploadFile(
        file=io.BytesIO(data),
        size=len(data),
        filename="shot.png",
        headers=Headers({"content-type": "image/png"}),
    )


def test_extract_failure_log_keeps_diagnostics_but_not_the_image(caplog):
    """上游回显的 base64 不得进日志；异常类型、状态码、主机名要留下。"""
    b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 200).decode()
    assert len(b64) >= 40, "长串要跨过脱敏阈值，否则测的是别的东西"
    upstream = RuntimeError(
        f"500 upstream error from https://secret.internal/v1/chat: "
        f"body={{'image_url': 'data:image/png;base64,{b64}'}}"
    )

    with caplog.at_level(logging.ERROR, logger=stocks.__name__), \
         mock.patch.object(stocks, "extract_stock_codes_from_image", side_effect=upstream):
        with pytest.raises(HTTPException) as exc_info:
            stocks.extract_from_image(file=_png_upload(), include_raw=False)

    # 非 ValueError 的失败对客户端是固定的 500/「图片提取失败」，不负责回吐上游文本。
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["message"] == "图片提取失败"

    assert caplog.records, "失败必须留下一条日志，否则这条测试是空转"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "RuntimeError" in logged, "异常类型要留着，便于定位"
    assert "secret.internal" in logged, "上游主机名是诊断信息，要留着"
    # 只断言"完整长串不在日志里"是空转：`redact_for_log` 还会把文本截到 200 字，光靠截断就能
    # 让 280 字的长串不出现（实测把脱敏换成 str(exc)[:limit] 这条断言照样绿）。所以要钉
    # "被替换成了 [REDACTED]"，并钉一个挤得进 200 字窗口的**前缀**也不出现。
    assert "[REDACTED]" in logged, "长串必须被脱敏替换，而不是碰巧被 200 字的截断切掉"
    assert b64[:60] not in logged, "前缀也不得出现（截断挡不住 60 字）"
    assert all(record.exc_info is None for record in caplog.records), (
        "exc_info 会把原始异常链原样再打一遍，把刚抹掉的长串从后门带回来"
    )
