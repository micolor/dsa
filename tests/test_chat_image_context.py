# -*- coding: utf-8 -*-
"""把图片转成可注入对话的文本块。"""

from __future__ import annotations

from unittest import mock

import pytest

from src.services import chat_image_context as ctx


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
