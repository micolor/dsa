# -*- coding: utf-8 -*-
"""视觉调用的路由与"图真的被转发"守卫。

背景（2026-09-23 实测）：litellm 的 provider 专属路由（`deepseek/`）不会转发
`image_url` 内容块，于是模型"看不见图"却仍返回一个**合法但无用**的回答（例如
`"[]"`）。这类失败没有任何运行期检查能抓住——它不是空响应，也不能靠能力元数据
判断（`litellm.register_model(..., supports_vision=True)` 实测无效）。因此必须
用一条**不 mock 转发层**的测试把"图确实到达了模型"钉住。
"""

from __future__ import annotations

import base64
import io

import pytest

from src.services import image_stock_extractor as ex


def _render_code_image(code: str = "600519", name: str = "Guizhou Moutai") -> tuple[str, str]:
    """渲染一张写着代码与名称的 PNG，返回 (base64, mime)。"""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (600, 220), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=90)
    except TypeError:  # 旧 Pillow
        font = ImageFont.load_default()
    draw.text((30, 40), code, fill="black", font=font)
    draw.text((30, 150), name, fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode(), "image/png"


# ---- 离线：路由映射本身 ------------------------------------------------

def test_wire_model_reroutes_known_broken_provider_through_openai_route():
    """deepseek 必须改走通用 openai/ 路由，否则图会被 provider 适配器剥掉。"""
    assert ex._vision_wire_model("deepseek/deepseek-v4-flash") == "openai/deepseek-v4-flash"


def test_wire_model_keeps_native_routes_that_work():
    """已知能正常传图的原生路由不得被改写。"""
    for model in ("openai/gpt-4o", "gemini/gemini-2.0-flash", "anthropic/claude-sonnet-5"):
        assert ex._vision_wire_model(model) == model


def test_wire_model_is_idempotent_for_openai_route():
    assert ex._vision_wire_model("openai/deepseek-v4-flash") == "openai/deepseek-v4-flash"


def test_wire_model_prefers_deployment_model_over_caller_model():
    """deployment 里显式写的 model 优先（与既有 wire_model 解析一致）。"""
    wire = ex._vision_wire_model("deepseek/whatever", {"model": "deepseek/deepseek-v4-pro"})
    assert wire == "openai/deepseek-v4-pro"


# ---- 在线：图真的到达了模型（承重墙） ---------------------------------

@pytest.mark.network
def test_vision_call_actually_sees_the_image():
    """不 mock litellm：断言模型**读出了图里的代码**。

    这条是 spec §8 那条不变式的唯一守卫。若它红了，说明图没被转发（或所用配置
    没有视觉能力），此时整个贴图功能与截图提取功能都是坏的。
    """
    b64, mime = _render_code_image()
    text = ex._call_litellm_vision_with_prompt(
        "这张图片里写了什么数字和英文单词？直接照抄你看到的内容。", b64, mime
    )
    assert "600519" in text, f"模型没有读出图中的代码，说明图未被转发。模型回话：{text[:200]!r}"
