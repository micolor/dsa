# -*- coding: utf-8 -*-
"""ChatRequest 的图片字段校验与注入。"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from types import SimpleNamespace
from unittest import mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.app import create_app
from api.v1.endpoints import agent as agent_endpoint
from api.v1.endpoints.agent import CHAT_IMAGE_MAX_BYTES, ChatRequest, _resolve_image_message
from src.config import Config
from src.services.agent_chat_session_service import AgentChatSessionService
from src.services.chat_image_context import ImageContextError
from src.storage import DatabaseManager


def setup_function() -> None:
    DatabaseManager.reset_instance()
    Config.reset_instance()


def teardown_function() -> None:
    DatabaseManager.reset_instance()
    Config.reset_instance()


def _png_bytes(w: int = 2, h: int = 2) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def _req(**kw) -> ChatRequest:
    return ChatRequest(**kw)


def test_no_image_returns_message_unchanged():
    assert _resolve_image_message(_req(message="你好")) == "你好"


def test_image_is_injected_before_the_user_text():
    b64 = base64.b64encode(_png_bytes()).decode()
    with mock.patch("api.v1.endpoints.agent.build_image_context",
                    return_value="【图片内容】\n一张图\n【用户问题】这是什么"):
        out = _resolve_image_message(_req(message="这是什么", image_base64=b64, image_mime="image/png"))
    assert out.startswith("【图片内容】")
    assert out.endswith("这是什么")


def test_mime_and_base64_must_appear_together():
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64="abc"))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_mime="image/png"))
    assert e.value.status_code == 400


def test_unsupported_mime_is_rejected():
    b64 = base64.b64encode(_png_bytes()).decode()
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64=b64, image_mime="image/bmp"))
    assert e.value.status_code == 400
    # 钉住是这个守卫拒的：magic-byte 守卫也会以"无法验证类型"拦下 image/bmp，
    # 只断言 400 的话删掉 MIME 守卫这条测试仍然绿。
    assert "不支持的图片类型" in str(e.value.detail)


def test_oversized_image_is_rejected_with_clear_message():
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (CHAT_IMAGE_MAX_BYTES + 1)).decode()
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64=big, image_mime="image/png"))
    assert e.value.status_code == 400
    detail = str(e.value.detail)
    assert "上限 2MB" in detail
    # 边界：这张图只比上限多 9 字节。截断成 KB 会写成「2048KB，上限 2MB」——用户看到的
    # 数字正好等于那个他"超过"的上限。必须向上取整到 0.1MB。
    assert "2.1MB" in detail
    assert "2048KB" not in detail


def test_bad_magic_bytes_are_rejected():
    b64 = base64.b64encode(b"not an image at all").decode()
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64=b64, image_mime="image/png"))
    assert e.value.status_code == 400


def test_vision_failure_surfaces_as_503_not_a_blind_answer():
    """视觉失败必须让请求失败——不许降级成"看不到图也照样回答"。"""
    b64 = base64.b64encode(_png_bytes()).decode()
    with mock.patch("api.v1.endpoints.agent.build_image_context",
                    side_effect=ImageContextError("未配置视觉模型")):
        with pytest.raises(HTTPException) as e:
            _resolve_image_message(_req(message="x", image_base64=b64, image_mime="image/png"))
    assert e.value.status_code == 503
    assert "图片未能读取" in str(e.value.detail)


def test_vision_failure_does_not_echo_the_upstream_error(caplog):
    """上游异常原文只进日志：它可能带 api_base / 模型名，甚至回显 base64 图片本身；
    超时类文本还会被前端 error.ts 的关键词分类器判成"连接上游服务超时"，
    把用户引向网络与代理设置——而他的图根本没被读到。"""
    b64 = base64.b64encode(_png_bytes()).decode()
    echoed = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 200).decode()
    upstream = (
        "litellm.Timeout: api_base=https://secret.internal/v1 model=deepseek-vl timeout "
        f"body={{'image_url': 'data:image/png;base64,{echoed}'}}"
    )
    with mock.patch("api.v1.endpoints.agent.build_image_context",
                    side_effect=ImageContextError(upstream)):
        with caplog.at_level(logging.WARNING, logger="api.v1.endpoints.agent"):
            with pytest.raises(HTTPException) as e:
                _resolve_image_message(_req(message="x", image_base64=b64, image_mime="image/png"))
    body = str(e.value.detail)
    assert e.value.status_code == 503
    for leak in ("secret.internal", "deepseek-vl", "litellm", "timeout"):
        assert leak not in body
    assert e.value.detail["message"] == "图片未能读取，请稍后重试或换一张图"

    # 日志这一侧同样不许落盘图片内容（设计 §2 的"不落盘"覆盖日志）。
    assert caplog.records, "视觉失败必须留下一条诊断日志"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert echoed[:40] not in logged
    assert "<redacted>" in logged


def test_invalid_base64_is_rejected():
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64="!!!not-base64!!!", image_mime="image/png"))
    assert e.value.status_code == 400


# --- 端点接线 ---------------------------------------------------------------
# 上面 8 个测试只覆盖 _resolve_image_message 本身。下面两个钉住"它被放在哪里"：
# 位置错了(放进生成器 / 放进 /chat 的兜底 try)上面 8 个测试会全绿，但用户拿到的是
# 通用流错误或 500，而不是 400。

def _litellm_config() -> SimpleNamespace:
    return SimpleNamespace(
        agent_backend="auto",
        is_agent_available=lambda: True,
        report_language="zh",
    )


def _codex_config() -> SimpleNamespace:
    return SimpleNamespace(
        agent_backend="codex_app_server",
        agent_arch="single",
        agent_orchestrator_timeout_s=600,
        report_language="zh",
    )


def _session_service() -> mock.MagicMock:
    service = mock.MagicMock(spec=AgentChatSessionService)
    service.resolve_skill_selection.return_value = SimpleNamespace(
        effective_skill_ids=None,
        selected_skill_ids_update=None,
    )
    return service


def test_stream_endpoint_rejects_a_bad_image_before_the_stream_starts():
    """必须在端点体里抛：进了生成器就只能是一条 SSE error 事件，不再是 400。"""
    request_id = "image-rejected"
    # 只有 base64、没有 mime：成对校验失败，且（用 codex 配置）能顺带验证
    # 校验发生在 codex 注册之前，失败不会把 request_id 永久占住。
    request = ChatRequest(message="x", image_base64="abc", request_id=request_id)
    try:
        with mock.patch("api.v1.endpoints.agent.get_config", return_value=_codex_config()), \
             pytest.raises(HTTPException) as e:
            asyncio.run(
                agent_endpoint.agent_chat_stream(request, session_service=_session_service())
            )
        assert e.value.status_code == 400
        assert e.value.detail["error"] == "invalid_image"
        with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
            assert request_id not in agent_endpoint._ACTIVE_CODEX_STREAMS
    finally:
        with agent_endpoint._ACTIVE_CODEX_STREAMS_LOCK:
            agent_endpoint._ACTIVE_CODEX_STREAMS.pop(request_id, None)


def test_chat_endpoint_image_error_is_not_swallowed_into_a_500():
    """/chat 的兜底 except Exception 会吃掉 HTTPException，解析必须在 try 之外。"""
    request = ChatRequest(message="x", image_base64="abc")
    with mock.patch("api.v1.endpoints.agent.get_config", return_value=_litellm_config()), \
         pytest.raises(HTTPException) as e:
        asyncio.run(
            agent_endpoint.agent_chat(request, session_service=_session_service())
        )
    assert e.value.status_code == 400
    assert e.value.detail["error"] == "invalid_image"


def test_http_bodies_are_what_the_frontend_actually_reads(tmp_path):
    """断言真正发出去的 body，而不只是 exc.detail。

    api/middlewares/error_handler.py:39 只在 detail 含 error + message 时原样透传，
    前端 extractErrorCode 读的是 detail.error——所以光断言 exc.detail 的话，改个键名
    这些测试仍然全绿，而前端的错误分类已经坏了。
    """
    b64 = base64.b64encode(_png_bytes()).decode()
    client = TestClient(create_app(static_dir=tmp_path / "static"))
    upstream = "litellm.Timeout: api_base=https://secret.internal/v1 timeout"

    with mock.patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         mock.patch("api.v1.endpoints.agent.get_config", return_value=_litellm_config()):
        bad = client.post(
            "/api/v1/agent/chat",
            json={"message": "x", "image_base64": "abc"},  # 缺 image_mime
        )
        with mock.patch("api.v1.endpoints.agent.build_image_context",
                        side_effect=ImageContextError(upstream)):
            unreadable = client.post(
                "/api/v1/agent/chat",
                json={"message": "x", "image_base64": b64, "image_mime": "image/png"},
            )

    assert bad.status_code == 400
    assert bad.json()["error"] == "invalid_image"
    assert bad.json()["message"]

    assert unreadable.status_code == 503
    assert unreadable.json()["error"] == "image_unreadable"
    assert unreadable.json()["message"] == "图片未能读取，请稍后重试或换一张图"
    assert "secret.internal" not in unreadable.text
    assert "timeout" not in unreadable.text
