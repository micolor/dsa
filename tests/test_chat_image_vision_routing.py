# -*- coding: utf-8 -*-
"""视觉调用的路由与"图真的被转发"守卫。

背景（2026-09-23 实测）：litellm 的 provider 专属路由（`deepseek/`）不会转发
`image_url` 内容块，于是模型"看不见图"却仍返回一个**合法但无用**的回答（例如
`"[]"`）。这类失败没有任何运行期检查能抓住——它不是空响应，也不能靠能力元数据
判断（`litellm.register_model(..., supports_vision=True)` 实测无效）。因此必须
用一条**不 mock 转发层**的测试把"图确实到达了模型"钉住。

与 tests/test_tw_institutional_network.py 的策略一致：**漂移要大声失败，环境/传输
问题要安静跳过**。所以 `-m network` 那条只在"本机根本没有可用的视觉模型"时 skip；
一旦配了视觉模型却读不出图，它必须**失败**——那正是它存在的理由。
"""

from __future__ import annotations

import base64
import io
from unittest.mock import MagicMock, patch

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


def _good_response(text: str = '[{"code":"600519","name":"贵州茅台","confidence":"high"}]'):
    """litellm.completion 的最小可用返回值。"""
    msg = MagicMock()
    msg.content = text
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    return resp


_DS_KEY = "sk-deepseek-testkey-1234"
_DS_API_BASE = "https://api.deepseek.com"


def _deepseek_deployment_cfg(**kwargs) -> ex.Config:
    """一份"只有 DeepSeek 渠道"的配置（复刻本机现状：VISION_MODEL 为空）。"""
    defaults = dict(
        stock_list=["600519"],
        tushare_token=None,
        llm_model_list=[{
            "model_name": "deepseek/deepseek-v4-flash",
            "litellm_params": {
                "model": "deepseek/deepseek-v4-flash",
                "api_key": _DS_KEY,
                "api_base": _DS_API_BASE,
            },
        }],
        llm_channels=[],
        litellm_config_path=None,
        litellm_model="deepseek/deepseek-v4-flash",
        litellm_fallback_models=[],
        vision_model="",
        vision_provider_priority="gemini,anthropic,openai",
        gemini_api_keys=[],
        gemini_model="gemini-3.1-pro-preview",
        anthropic_api_keys=[],
        anthropic_model="claude-sonnet-4-6",
        openai_api_keys=[],
        openai_model="gpt-5.5",
        openai_base_url=None,
        openai_vision_model=None,
        deepseek_api_keys=[],
        config_validate_mode="warn",
    )
    defaults.update(kwargs)
    return ex.Config(**defaults)


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


def test_wire_model_never_invents_a_provider_for_a_bare_name():
    """没有 provider 前缀的裸名不得被硬加前缀（解析不出 provider 就不该发明一个）。"""
    assert ex._vision_wire_model("deepseek-v4-flash") == "deepseek-v4-flash"


def test_wire_model_prefers_deployment_model_over_caller_model():
    """deployment 里显式写的 model 优先（与既有 wire_model 解析一致）。"""
    wire = ex._vision_wire_model("deepseek/whatever", {"model": "deepseek/deepseek-v4-pro"})
    assert wire == "openai/deepseek-v4-pro"


# ---- 离线：真正发出去的调用（承重墙 1/2） -----------------------------

def test_call_actually_sends_the_rerouted_model_and_the_deployment_api_base():
    """钉住**调用点**，不只是纯函数：路由逻辑必须在 `_call_litellm_vision` 里真的接上。

    只测 `_vision_wire_model` 抓不到"逻辑全在、但没接上"——把调用点改回修前的
    `str(deployment_params.get("model") or model).strip()`，那几条纯函数测试依然全绿，
    而唯一的网络测试被 `-m "not network"` 挡在阻断门禁之外。这与原始 bug 是同一类洞。

    第二条断言把"改写路由依赖 deployment 的 api_base 透传"这个隐式耦合变成事实：
    改前缀不能把 endpoint 丢掉，否则会打到 api.openai.com 而不是 DeepSeek。
    """
    cfg = _deepseek_deployment_cfg()
    with patch("src.services.image_stock_extractor.get_config", return_value=cfg), \
         patch("src.services.image_stock_extractor.litellm.completion",
               return_value=_good_response()) as mock_comp:
        ex._call_litellm_vision("base64data", "image/png")

    kwargs = mock_comp.call_args.kwargs
    assert kwargs["model"] == "openai/deepseek-v4-flash"
    assert kwargs["api_base"] == _DS_API_BASE


# ---- 在线：图真的到达了模型（承重墙 2/2） -----------------------------

@pytest.mark.network
def test_vision_call_actually_sees_the_image():
    """不 mock litellm：断言模型**读出了图里的代码**。

    spec §8 那条不变式的唯一守卫。若它红了，先看 `image_stock_extractor.
    _PROVIDER_ROUTES_WITHOUT_IMAGE_FORWARDING`（剥图的路由名单）与设计文档 §1.2 的
    逐层定位过程——图没被转发时整个贴图功能与截图提取功能都是坏的。

    只在"环境根本跑不了"时 skip（CI 的 Network Smoke 没有配置任何视觉模型/密钥）；
    配了视觉模型却读不出图，必须**失败**，不得降级成 skip。
    """
    model = ex._resolve_vision_model()
    if not model or not ex._get_api_keys_for_model(model, ex.get_config()):
        pytest.skip("no vision model/API key configured — cannot exercise a real forwards-image call")

    b64, mime = _render_code_image()
    text = ex._call_litellm_vision_with_prompt(
        "这张图片里写了什么数字和英文单词？直接照抄你看到的内容。", b64, mime
    )
    assert "600519" in text, f"模型没有读出图中的代码，说明图未被转发。模型回话：{text[:200]!r}"
