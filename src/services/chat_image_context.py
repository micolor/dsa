# -*- coding: utf-8 -*-
"""把用户贴的图片转成一段可注入对话的文本块。

问股采用「视觉前置 + 文本注入」：图片在进入 agent 之前先被视觉模型看一遍，结果作为文本
注入当轮用户消息。这样做的好处是 agent 主模型、工具循环与消息构造**全都不用改**，注入后的
文本还会自然进入会话历史（因此后续轮次 AI 仍"记得"它看到了什么，但看不到原图——图片二进制
不落盘是有意的取舍，见设计文档 §2）。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from src.services.image_stock_extractor import (
    VisionNotConfiguredError as _VisionNotConfiguredError,
    _call_litellm_vision_with_prompt,
)

logger = logging.getLogger(__name__)

# 设计 §2 承诺"图片二进制不落盘"，这个承诺**覆盖日志**：某些 provider 会在错误体里
# 回显请求内容（含 base64 图片），而日志会被轮转、打包、上传，比数据库更难回收。
_BASE64_RUN_RE = re.compile(r"[A-Za-z0-9+/=]{40,}")


class ImageContextError(RuntimeError):
    """图片无法被读取（未配置视觉模型、调用失败、或返回内容为空）。"""


class VisionNotConfiguredError(ImageContextError):
    """没有配置可用的视觉模型。

    与其它 ``ImageContextError`` 分开，是因为对用户的建议完全不同：调用失败时"稍后重试
    或换一张图"是对的，而模型压根没配时重试与换图**永远**不会成功，只能去设置页配置。
    端点为这两类映射到各自固定的文案（见设计 §8 的失败矩阵）。
    """


IMAGE_CONTEXT_PROMPT = """你在帮一个股票分析助手读取用户贴的图片。

【用户的问题】{question}

请输出两部分，用中文，不要客套话：
1. 与用户问题相关的图片内容：只写与问题有关的、你在图上真实看到的信息（图表形态、数值、
   文字、指标等）。看不清或图上没有就直说，不要推测、不要补全。
2. 若图中出现了股票代码或股票名称，另起一行以 `【图中股票】` 开头，逐个列出「代码 名称」，
   用「；」分隔。图中没有股票就省略这一行。

只输出上述内容，不要解释你的任务。"""


def _render_block(model_text: str, question: str) -> str:
    lines = ["【图片内容】", model_text.strip()]
    if question.strip():
        lines += ["【用户问题】" + question.strip()]
    return "\n".join(lines)


def _sanitize_for_log(exc: BaseException, *, limit: int = 200) -> str:
    """把上游异常文本裁剪并抹掉疑似 base64 长串，避免图片内容落盘。

    provider 可能在错误体里回显请求内容（含 base64 图片），日志会被轮转/打包/上传，
    因此这里只保留诊断需要的部分：异常类型 + 裁剪后的文本 + 抹掉的长串。
    """
    text = _BASE64_RUN_RE.sub("<redacted>", str(exc))[:limit]
    return f"{type(exc).__name__}: {text}"


def build_image_context(image_b64: str, mime_type: str, question: str = "") -> str:
    """Ask the vision model about the image and return a text block for injection.

    Raises ``ImageContextError`` on any failure: no vision model, call failure, or an
    empty/whitespace reply. Partial success is deliberately not accepted — an empty
    injection would look like success while the model never saw the image.
    """
    prompt = IMAGE_CONTEXT_PROMPT.replace("{question}", question.strip() or "（用户没有提具体问题，请总结这张图里与股票分析相关的信息）")
    try:
        raw: Optional[str] = _call_litellm_vision_with_prompt(prompt, image_b64, mime_type)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误给用户
        # 这一层同样要脱敏：异常原文里可能回显着 base64 图片，而日志是要落盘的。
        logger.warning("chat image context failed: %s", _sanitize_for_log(exc))
        if isinstance(exc, _VisionNotConfiguredError):
            # "没配视觉模型"是可行动的原因，必须原样传成子类让端点换一套文案；
            # 这里用固定措辞，不把上游文本带进异常消息。
            raise VisionNotConfiguredError("图片未能读取：未配置可用的视觉模型") from exc
        raise ImageContextError(f"图片未能读取：{exc}") from exc

    text = (raw or "").strip()
    if not text:
        logger.warning("chat image context returned empty content")
        raise ImageContextError("图片未能读取：视觉模型没有返回可用内容")
    return _render_block(text, question)
