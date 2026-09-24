# -*- coding: utf-8 -*-
"""把图片转成可注入对话的文本块。"""

from __future__ import annotations

import base64
import logging
from unittest import mock

import pytest

from src.services import chat_image_context as ctx
from src.utils.sanitize import redact_for_log


def test_prompt_includes_the_user_question():
    """用户的问题必须进 prompt，否则摘要会答非所问。"""
    assert "{question}" in ctx.IMAGE_CONTEXT_PROMPT


def test_build_sends_the_user_question_to_the_vision_model():
    """真正传出去的 prompt 里必须是用户的问题本身，而不是未替换的占位符。

    只断言常量里有 `{question}` 抓不到「忘了替换」——模型会对一个字面占位符作答，
    回答格式一切正常，用户看到的却是一个从没读过问题的摘要。
    """
    with mock.patch.object(ctx, "_call_litellm_vision_with_prompt", return_value="图里有 600519") as mocked:
        ctx.build_image_context("b64", "image/png", "这是什么")
    prompt = mocked.call_args.args[0]
    assert "这是什么" in prompt
    assert "{question}" not in prompt


def test_build_strips_whitespace_and_wraps_model_output():
    with mock.patch.object(ctx, "_call_litellm_vision_with_prompt", return_value="  图里有 600519  \n"):
        out = ctx.build_image_context("b64", "image/png", "  这是什么  ")
    assert "600519" in out
    assert out.startswith("【图片内容】")
    # 模型输出的首尾空白必须去掉，否则注入块里会出现空行与缩进
    assert "\n图里有 600519\n" in out
    assert "  图里有" not in out
    # 用户问题再附在注入块末尾，同样不带首尾空白，便于模型把图文对上
    assert "【用户问题】这是什么" in out


def test_build_without_question_omits_that_block():
    with mock.patch.object(ctx, "_call_litellm_vision_with_prompt", return_value="图里有一只猫"):
        out = ctx.build_image_context("b64", "image/png", "")
    assert "【用户问题】" not in out


def test_empty_model_output_raises_not_silently_injects():
    """空内容必须按失败处理——这正是"看起来成功"最容易伪装的边界。"""
    with mock.patch.object(ctx, "_call_litellm_vision_with_prompt", return_value="   "):
        with pytest.raises(ctx.ImageContextError):
            ctx.build_image_context("b64", "image/png", "问题")


def test_underlying_failure_is_wrapped_in_image_context_error():
    with mock.patch.object(ctx, "_call_litellm_vision_with_prompt", side_effect=ValueError("未配置 Vision API")):
        with pytest.raises(ctx.ImageContextError):
            ctx.build_image_context("b64", "image/png", "问题")


def test_image_context_error_is_a_runtime_error():
    """基类是接口声明的一部分：调用方按 RuntimeError 兜底捕获。"""
    assert issubclass(ctx.ImageContextError, RuntimeError)


def test_missing_vision_model_is_mapped_to_its_own_subclass():
    """「没配视觉模型」必须原样传成子类，端点才能换一套可行动的文案。

    这里喂的是 extractor 真实抛出的那个类型。把映射丢掉（或把子类并回基类）时端点只能
    给"稍后重试或换一张图"——而配置缺失时重试与换图**永远**不会成功。
    """
    from src.services.image_stock_extractor import VisionNotConfiguredError as ExtractorNotConfigured

    with mock.patch.object(
        ctx,
        "_call_litellm_vision_with_prompt",
        side_effect=ExtractorNotConfigured("未配置 Vision API。请设置 LITELLM_MODEL 或相关 API Key。"),
    ):
        with pytest.raises(ctx.VisionNotConfiguredError) as exc_info:
            ctx.build_image_context("b64", "image/png", "问题")

    # 子类关系是既有捕获点的契约（agent.py 先接子类、再接基类）。
    assert isinstance(exc_info.value, ctx.ImageContextError)
    # 上游文本不进异常消息：这层不该把 VISION_MODEL 之外的内部配置名往外带。
    assert "LITELLM_MODEL" not in str(exc_info.value)


def test_ordinary_failures_stay_in_the_base_class():
    """只有"没配模型"走子类：别的失败必须留在基类，否则端点会给错建议。"""
    with mock.patch.object(
        ctx, "_call_litellm_vision_with_prompt", side_effect=ValueError("调用超时")
    ):
        with pytest.raises(ctx.ImageContextError) as exc_info:
            ctx.build_image_context("b64", "image/png", "问题")
    assert not isinstance(exc_info.value, ctx.VisionNotConfiguredError)


def test_upstream_text_never_reaches_the_error_message(caplog):
    """上游原文不进异常消息，这条钉在源头，不靠调用方替换文案。

    端点的固定文案只保证"用户看不到"，不保证"消息里没有"：将来多一个直接调
    ``build_image_context`` 的消费者（bot / CLI），消息就会原样带走 api_base、模型名、
    provider 回显的 base64 图片。所以这一层自己就得干净。

    喂进去的两类材料都放在上游消息的**最前面**（`secret.internal` 在第 0 个字符，
    base64 从第 39 个字符开始，都落在 ``redact_for_log`` 的 200 字窗口内），这样"消息里
    没有"就不可能是被裁剪顺手遮掉的——只有这一层真的没把它们写进消息才解释得通。
    """
    b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"y" * 300).decode()
    upstream = f"secret.internal data:image/png;base64,{b64} (api_base=https://internal.example/v1)"

    with mock.patch.object(
        ctx, "_call_litellm_vision_with_prompt", side_effect=ValueError(upstream)
    ):
        with caplog.at_level(logging.WARNING, logger=ctx.__name__):
            with pytest.raises(ctx.ImageContextError) as exc_info:
                ctx.build_image_context(b64, "image/png", "问题")

    message = str(exc_info.value)
    assert "secret.internal" not in message
    assert b64[:40] not in message
    assert message == "图片未能读取"

    # 脱掉的是外泄，不是诊断：原文（脱敏后）仍留在日志里。
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert caplog.records, "失败必须留下一条日志，否则这条测试是空转"
    assert "[REDACTED]" in logged
    assert b64[:40] not in logged
    assert "secret.internal" in logged
    assert "api_base=https://internal.example/v1" in logged


def test_log_sanitizer_redacts_base64_and_truncates():
    exc = ValueError("bad request: " + "A" * 500 + " end")
    out = redact_for_log(exc)
    assert "[REDACTED]" in out
    assert "A" * 40 not in out          # 长串已被抹掉
    assert len(out) < 260               # 已裁剪
    assert out.startswith("ValueError: ")  # 保留类型，便于诊断

    # 裁剪必须单独钉住：上面那个输入会被"抹掉长串"顺带缩短（500 个 A → [REDACTED]），
    # 所以删掉 [:limit] 它照样短、长度断言照样绿。没有 base64 的超长报错（比如一整页
    # HTML 错误）才是 limit 真正要挡的情况。
    plain = redact_for_log(ValueError("word " * 100))
    assert len(plain) < 260
    assert plain.startswith("ValueError: ")


def test_log_sanitizer_keeps_the_diagnostic_part():
    """脱敏不能把诊断价值一起抹掉——"未配置视觉模型 / 401 / 超时"必须还看得出来。"""
    out = redact_for_log(RuntimeError("未配置 Vision API: 401 unauthorized"))
    assert "RuntimeError" in out
    assert "未配置 Vision API" in out
    assert "401" in out


def test_image_failure_log_never_carries_the_image_content(caplog):
    """把"日志里不出现图片内容"钉在行为上，而不是只信那个小函数。"""
    b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 200).decode()
    with mock.patch.object(
        ctx,
        "_call_litellm_vision_with_prompt",
        side_effect=ValueError(f"bad request, body was: {{'image_url': 'data:image/png;base64,{b64}'}}"),
    ):
        with caplog.at_level(logging.WARNING, logger=ctx.__name__):
            with pytest.raises(ctx.ImageContextError):
                ctx.build_image_context(b64, "image/png", "问题")

    assert caplog.records, "失败必须留下一条日志，否则这条测试是空转"
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert b64[:40] not in logged
    assert "[REDACTED]" in logged
