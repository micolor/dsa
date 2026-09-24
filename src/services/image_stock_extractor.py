# -*- coding: utf-8 -*-
"""
===================================
图片股票代码提取 (Vision LLM)
===================================

从截图/图片中提取股票代码，使用 Vision LLM。
优先级：Gemini -> Anthropic -> OpenAI（首个可用）。
"""

from __future__ import annotations

import base64
import json
import logging
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config, channel_allows_empty_api_key, get_config
from src.llm.hermes import route_has_hermes

logger = logging.getLogger(__name__)


class _LiteLLMPlaceholder:
    """Provide a patchable placeholder before litellm is imported."""

    completion = None


# Keep a patchable module attribute while still avoiding a hard import at module load.
litellm = sys.modules.get("litellm") or _LiteLLMPlaceholder()


class VisionNotConfiguredError(ValueError):
    """没有配置可用的视觉模型（VISION_MODEL 为空且推断不出任何带 Key 的模型）。

    单独成型是为了让上层能把「配置缺失」与「这一次调用失败」分开：前者重试、换图、
    检查网络都无济于事，只能去设置页配置 VISION_MODEL。继承 ``ValueError`` 是为了
    不打断既有按 ``ValueError`` 捕获的调用方（本模块多处按此约定抛错）。
    """


EXTRACT_PROMPT = """请分析这张股票市场截图或图片，提取其中所有可见的股票代码及名称。

重要：若图中同时显示股票名称和代码（如自选股列表、ETF 列表），必须同时提取两者，每个元素必须包含 code 和 name 字段。

输出格式：仅返回有效的 JSON 数组，不要 markdown、不要解释。
每个元素为对象：{"code":"股票代码","name":"股票名称","confidence":"high|medium|low"}
- code: 必填，股票代码（A股6位、港股5位、美股1-5字母、ETF 如 159887/512880）
- name: 若图中有名称则必填（如 贵州茅台、银行ETF、证券ETF），与代码一一对应；仅当图中确实无名称时可省略
- confidence: 必填，识别置信度，high=确定、medium=较确定、low=不确定

示例（图中同时有名称和代码时）：
- 个股：600519 贵州茅台、300750 宁德时代
- 港股：00700 腾讯控股、09988 阿里巴巴
- 美股：AAPL 苹果、TSLA 特斯拉
- ETF：159887 银行ETF、512880 证券ETF、512000 券商ETF、512480 半导体ETF、515030 新能源车ETF

输出示例：[{"code":"600519","name":"贵州茅台","confidence":"high"},{"code":"159887","name":"银行ETF","confidence":"high"}]

禁止只返回代码数组如 ["159887","512880"]，必须使用对象格式。若未找到任何股票代码，返回：[]"""

# Valid confidence values; invalid ones normalized to medium
_VALID_CONFIDENCE = frozenset({"high", "medium", "low"})

# LLM sometimes returns JSON field names or markdown labels as "code"; filter these out
_FAKE_CODES = frozenset({
    "CODE", "NAME", "HIGH", "LOW", "MEDIUM", "CONFIDENCE", "JSON",
    # 交易所代号本身不是股票代码：裸的 HK / SH / SZ 会被 1–5 个字母的美股规则接受，
    # 而它们只可能来自 00700.HK / 600519.SH 这类写法被拆开（`stock_scope` 里
    # 的 `_EXCHANGE_TOKEN_CANDIDATES` 出于同一原因拒绝这几个词）。
    "HK", "SH", "SZ", "BJ", "SS",
})

# 兜底扫描只用这两个形态，而不是在整段原文上做 IGNORECASE 的字母扫描：
# - 数字串就是明确的代码形态，命中什么算什么；
# - 字母代码必须是原文里的大写，并且前后不能挨着词字符或点号——前者挡掉
#   「could / not / image」这类小写英文单词，后者挡掉 `600519.SH` / `00700.HK`
#   里被当成独立代码的交易所后缀（SH / SZ / HK）。
_DIGIT_CODE_RE = re.compile(r"\b([0-9]{5,6})\b")
_LETTER_CODE_RE = re.compile(r"(?<![\w.])[A-Z]{1,5}(?:\.[A-Z])?(?![\w.])")
# 一个数字代码都没有、却出现 ≥2 个连续小写字母时，这段响应是自然语言
# （例如「没有在图中找到股票代码」），不是代码清单。
_PROSE_RE = re.compile(r"[a-z]{2,}")

ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp", "image/gif"})
MAX_SIZE_BYTES = 5 * 1024 * 1024  # 5MB
VISION_API_TIMEOUT = 60  # seconds; avoid long blocks on network/API issues

# litellm 的 provider 专属路由不会转发 image_url 内容块（实测：deepseek/ 下模型回
# 「未收到图片」或 "[]"，而同一个 endpoint 用 openai/ 通用路由能正确读出图中代码）。
# 证据与逐层定位过程见 docs/superpowers/specs/2026-09-23-chat-image-paste-design.md §1.2
# （见 CHANGELOG 的 [Unreleased]）。
# 只有**实测过**会剥图的路由才加进来——不要凭猜测往这里添，也不要泛化成「非原生前缀
# 一律改走 openai/」：azure/ 需要 api_version 与自己的认证头，bedrock/、ollama/、
# openrouter/、openai/responses/… 与 Hermes 路由被改写后会**静默**改变 api_base 与
# 认证语义；而 deepseek 作为 provider 本身是仓库文档记载的正常用法，被剥掉的只是
# **多模态载荷**，不是 provider 前缀。
_PROVIDER_ROUTES_WITHOUT_IMAGE_FORWARDING = frozenset({"deepseek"})

# Magic bytes for server-side MIME validation (client Content-Type can be forged)
_IMAGE_SIGNATURES = {
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/png": [b"\x89PNG\r\n\x1a\n"],
    "image/gif": [b"GIF87a", b"GIF89a"],
    "image/webp": [b"RIFF"],  # bytes[8:12] must be WEBP, checked separately
}


def _vision_wire_model(model: str, deployment_params: Optional[Dict[str, Any]] = None) -> str:
    """Return the litellm model string to use for a vision call.

    deployment 里显式写的 model 优先（与既有 wire_model 解析一致）；若该 provider 的
    专属路由实测会剥掉图片，则改走 litellm 的通用 ``openai/`` 兼容路由。改写只动
    provider 前缀：有匹配 deployment 时 ``api_base`` 由该 deployment 传入，否则沿用
    既有的 ``cfg.openai_base_url`` 兜底，两种情况都不会退化成静默空答案。
    """
    wire = str((deployment_params or {}).get("model") or model).strip()
    if "/" not in wire:
        return wire
    prefix, _, rest = wire.partition("/")
    if prefix.lower() in _PROVIDER_ROUTES_WITHOUT_IMAGE_FORWARDING:
        return f"openai/{rest}"
    return wire


def _verify_image_magic_bytes(image_bytes: bytes, mime_type: str) -> None:
    """Verify actual file content matches declared MIME type (rejects forged Content-Type)."""
    if len(image_bytes) < 12:
        raise ValueError("图片文件过小或损坏")
    if mime_type not in _IMAGE_SIGNATURES:
        raise ValueError(f"无法验证类型: {mime_type}")
    if mime_type == "image/webp":
        if image_bytes[:4] != b"RIFF" or image_bytes[8:12] != b"WEBP":
            raise ValueError("文件内容与声明的类型 image/webp 不匹配，可能被篡改")
        return
    for sig in _IMAGE_SIGNATURES[mime_type]:
        if image_bytes.startswith(sig):
            return
    raise ValueError(f"文件内容与声明的类型 {mime_type} 不匹配，可能被篡改")


def _normalize_code(raw: str) -> Optional[str]:
    """Normalize and validate a single stock code. A-shares & HK: 5-6 digits; US: 1-5 letters."""
    s = raw.strip().upper()
    if not s:
        return None
    # A-shares & HK: 5-6 digit codes (600519, 00700, 09988)
    if s.isdigit() and len(s) in (5, 6):
        return s
    # US stocks: 1-5 letters, optionally with . (e.g. BRK.B)
    if re.match(r"^[A-Z]{1,5}(\.[A-Z])?$", s):
        return s
    # 港股：截图里常按原文写成 00700.HK / HK00700，模型也常照抄；统一成 5 位纯数字码
    # （与 5 位数字分支的输出形态一致），否则整条 item 会走到下面的 return None 被静默丢弃。
    if s.endswith(".HK"):
        base = s[: -len(".HK")].strip()
        if base.isdigit() and 1 <= len(base) <= 5:
            return base.zfill(5)
    if s.startswith("HK") and s[2:].isdigit() and 1 <= len(s[2:]) <= 5:
        return s[2:].zfill(5)
    # 尝试去除 SH/SZ 后缀
    for suffix in (".SH", ".SZ", ".SS"):
        if s.endswith(suffix):
            base = s[: -len(suffix)].strip()
            if base.isdigit() and len(base) in (5, 6):
                return base
    return None


def _parse_codes_from_text(text: str) -> List[str]:
    """从 LLM 响应文本解析股票代码（legacy format）。"""
    seen: set[str] = set()
    result: List[str] = []

    # 优先尝试 JSON 数组；只移除开头的 markdown 围栏，避免 find("```") 误删结尾导致清空
    cleaned = text.strip()
    for start in ("```json", "```"):
        if cleaned.startswith(start):
            cleaned = cleaned[len(start) :].strip()
            break
    end_idx = cleaned.rfind("```")
    if end_idx >= 0:
        cleaned = cleaned[:end_idx].strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    c = _normalize_code(item)
                    if c and c not in seen and c not in _FAKE_CODES:
                        seen.add(c)
                        result.append(c)
            return result
    except json.JSONDecodeError:
        pass

    # 兜底：只扫明确的代码形态。数字串先扫；字母代码仅在「这段文本不是自然语言」
    # 时才算数。不这么判的话，模型回答一句「I could not find any stock codes in
    # this image.」会产出 I / COULD / NOT / FIND / ANY / STOCK / CODES / IN /
    # THIS / IMAGE 一整串假美股代码，`{"codes": [...]}` 这类对象响应也会把
    # 字段名 CODES / NAMES 当成代码——这些假代码会被用户勾选后进入分析队列。
    digit_matches = [m.group(1) for m in _DIGIT_CODE_RE.finditer(text)]
    letter_matches: List[str] = []
    if digit_matches or not _PROSE_RE.search(text):
        letter_matches = [m.group(0) for m in _LETTER_CODE_RE.finditer(text)]

    for raw in digit_matches + letter_matches:
        c = _normalize_code(raw)
        if c and c not in seen and c not in _FAKE_CODES:
            seen.add(c)
            result.append(c)

    return result


def _parse_items_from_text(text: str) -> List[Tuple[str, Optional[str], str]]:
    """
    Parse LLM response into items (code, name, confidence).
    Tries new format first, fallback to legacy codes-only format.
    """
    cleaned = text.strip()
    for start in ("```json", "```"):
        if cleaned.startswith(start):
            cleaned = cleaned[len(start) :].strip()
            break
    end_idx = cleaned.rfind("```")
    if end_idx >= 0:
        cleaned = cleaned[:end_idx].strip()

    # Try new format: list of objects
    parsed_data = None
    try:
        parsed_data = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json

            parsed_data = repair_json(cleaned, return_objects=True)
            logger.debug("[ImageExtractor] json.loads failed, repaired malformed JSON response")
        except Exception:
            parsed_data = None

    if isinstance(parsed_data, list):
        seen: set[str] = set()
        result: List[Tuple[str, Optional[str], str]] = []
        for item in parsed_data:
            if not isinstance(item, dict):
                continue
            code_raw = item.get("code") if isinstance(item.get("code"), str) else None
            if not code_raw:
                continue
            code = _normalize_code(code_raw)
            if not code or code in seen or code in _FAKE_CODES:
                # 记一条日志：解析不出来就整条丢弃（含 name/confidence），
                # 不记的话用户只看到「图里没有这只票」，无从判断是模型没认出
                # 还是代码形态不被支持（例如 .HK 后缀）。
                if not code:
                    logger.debug("[ImageExtractor] 丢弃无法归一化的代码: %r", code_raw)
                continue
            seen.add(code)
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                name = name.strip()
            else:
                name = None
            conf = item.get("confidence")
            if isinstance(conf, str) and conf.lower() in _VALID_CONFIDENCE:
                conf = conf.lower()
            else:
                conf = "medium"
            result.append((code, name, conf))
        if result:
            return result

    # Fallback: legacy format (codes only)
    codes = _parse_codes_from_text(text)
    if not codes:
        logger.info("[ImageExtractor] 无法解析为结构化 items，且 legacy code 提取为空")
    return [(c, None, "medium") for c in codes]


def _resolve_vision_model() -> str:
    """Determine the litellm model to use for vision."""
    cfg = get_config()
    # Prefer explicit vision model, then OPENAI_VISION_MODEL alias, then primary litellm model
    model = (cfg.vision_model or cfg.openai_vision_model or cfg.litellm_model or "").strip()
    if not model:
        # Fallback: infer from available keys
        if cfg.gemini_api_keys:
            model_name = cfg.gemini_model or "gemini-3.1-pro-preview"
            model = model_name if "/" in model_name else f"gemini/{model_name}"
        elif cfg.anthropic_api_keys:
            model = f"anthropic/{cfg.anthropic_model or 'claude-sonnet-4-6'}"
        elif cfg.openai_api_keys:
            model = f"openai/{cfg.openai_model or 'gpt-5.5'}"
        else:
            return ""
    return model


def _matching_vision_deployments(model: str, cfg: Config) -> List[Dict[str, Any]]:
    """Return configured LiteLLM deployments for a public vision route."""
    normalized_model = (model or "").strip()
    if not normalized_model:
        return []
    return [
        entry
        for entry in (getattr(cfg, "llm_model_list", []) or [])
        if isinstance(entry, dict)
        and str(entry.get("model_name") or "").strip() == normalized_model
        and isinstance(entry.get("litellm_params"), dict)
    ]


def _get_api_keys_for_model(model: str, cfg: Config) -> List[str]:
    """Return available API keys for the given litellm model."""
    deployment_keys: List[str] = []
    for deployment in _matching_vision_deployments(model, cfg):
        key = str((deployment.get("litellm_params") or {}).get("api_key") or "").strip()
        if key and len(key) >= 8 and key not in deployment_keys:
            deployment_keys.append(key)
    if deployment_keys:
        return deployment_keys
    if model.startswith("gemini/") or model.startswith("vertex_ai/"):
        return [k for k in cfg.gemini_api_keys if k and len(k) >= 8]
    if model.startswith("anthropic/"):
        return [k for k in cfg.anthropic_api_keys if k and len(k) >= 8]
    return [k for k in cfg.openai_api_keys if k and len(k) >= 8]


def _deployment_allows_empty_api_key(deployment: Dict[str, Any]) -> bool:
    """Return whether a configured vision deployment is a supported keyless endpoint."""
    params = deployment.get("litellm_params") or {}
    if str(params.get("api_key") or "").strip():
        return False
    wire_model = str(params.get("model") or "").strip()
    protocol = wire_model.split("/", 1)[0] if "/" in wire_model else None
    return channel_allows_empty_api_key(protocol, params.get("api_base"))


def _call_litellm_vision_with_prompt(
    prompt: str, image_b64: str, mime_type: str, api_key: Optional[str] = None
) -> str:
    """Call a vision model with an arbitrary prompt and one image (OpenAI vision format)."""
    global litellm
    cfg = get_config()
    model = _resolve_vision_model()
    if not model:
        raise VisionNotConfiguredError("未配置 Vision API。请设置 LITELLM_MODEL 或相关 API Key。")
    if route_has_hermes(getattr(cfg, "llm_model_list", []) or [], model):
        raise ValueError("Hermes Vision 未验证：VISION_MODEL 不能选择包含 Hermes deployment 的 route。")

    deployments = _matching_vision_deployments(model, cfg)
    keys = _get_api_keys_for_model(model, cfg)
    key = api_key if api_key and api_key in keys else (random.choice(keys) if keys else None)

    deployment_params: Dict[str, Any] = {}
    if deployments:
        deployment = next(
            (
                item
                for item in deployments
                if str((item.get("litellm_params") or {}).get("api_key") or "").strip() == key
            ),
            None,
        )
        if deployment is None:
            deployment = next(
                (item for item in deployments if _deployment_allows_empty_api_key(item)),
                None,
            )
            if deployment is not None:
                key = None
        if deployment is not None:
            deployment_params = dict(deployment.get("litellm_params") or {})
    if key is None and not deployment_params:
        raise ValueError(f"No API key found for vision model {model}")
    wire_model = _vision_wire_model(model, deployment_params)

    data_url = f"data:{mime_type};base64,{image_b64}"
    call_kwargs: dict = {
        "model": wire_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 1024,
        "timeout": VISION_API_TIMEOUT,
    }
    effective_api_key = str(deployment_params.get("api_key") or key or "").strip()
    if effective_api_key:
        call_kwargs["api_key"] = effective_api_key
    if deployment_params.get("api_base"):
        call_kwargs["api_base"] = deployment_params["api_base"]
    if deployment_params.get("extra_headers"):
        call_kwargs["extra_headers"] = dict(deployment_params["extra_headers"])
    # Add api_base and custom headers for OpenAI-compatible providers
    if not deployment_params and not model.startswith("gemini/") and not model.startswith("anthropic/") and not model.startswith("vertex_ai/"):
        if cfg.openai_base_url:
            call_kwargs["api_base"] = cfg.openai_base_url
        if cfg.openai_base_url and "aihubmix.com" in cfg.openai_base_url:
            call_kwargs["extra_headers"] = {"APP-Code": "GPIJ3886"}

    if getattr(litellm, "completion", None) is None:
        import litellm as litellm_module
        litellm = litellm_module
    response = litellm.completion(**call_kwargs)
    if response and response.choices and response.choices[0].message.content:
        return response.choices[0].message.content
    raise ValueError("LiteLLM vision returned empty response")


def _call_litellm_vision(image_b64: str, mime_type: str, api_key: Optional[str] = None) -> str:
    """Extract stock codes from an image using litellm (all providers via OpenAI vision format)."""
    return _call_litellm_vision_with_prompt(EXTRACT_PROMPT, image_b64, mime_type, api_key)


def extract_stock_codes_from_image(
    image_bytes: bytes,
    mime_type: str,
) -> Tuple[List[Tuple[str, Optional[str], str]], str]:
    """
    从图片中提取股票代码及名称（使用 Vision LLM）。

    优先级：Gemini -> Anthropic -> OpenAI（首个可用）。
    支持多 Key 轮询与重试（最多 3 次，指数退避）。

    Args:
        image_bytes: 原始图片字节
        mime_type: MIME 类型（如 image/jpeg, image/png）

    Returns:
        (items, raw_text) - items 为 [(code, name?, confidence), ...]，raw_text 为原始 LLM 响应。

    Raises:
        ValueError: 图片无效、未配置 Vision API 或提取失败时。
    """
    mime_type = (mime_type or "image/jpeg").strip().lower().split(";")[0].strip()
    if mime_type not in ALLOWED_MIME:
        raise ValueError(f"不支持的图片类型: {mime_type}。允许: {list(ALLOWED_MIME)}")

    if not image_bytes:
        raise ValueError("图片内容为空")

    if len(image_bytes) > MAX_SIZE_BYTES:
        raise ValueError(f"Image too large (max {MAX_SIZE_BYTES // (1024 * 1024)}MB)")

    _verify_image_magic_bytes(image_bytes, mime_type)

    image_b64 = base64.b64encode(image_bytes).decode("ascii")
    model = _resolve_vision_model()
    keys = _get_api_keys_for_model(model, get_config())

    last_error: Optional[Exception] = None
    for attempt in range(3):
        try:
            key = random.choice(keys) if keys else None
            raw = _call_litellm_vision(image_b64, mime_type, api_key=key)
            logger.debug("[ImageExtractor] raw LLM response:\n%s", raw)
            items = _parse_items_from_text(raw)
            logger.info(
                f"[ImageExtractor] {model} 提取 {len(items)} 个: "
                f"{[(i[0], i[1]) for i in items[:5]]}{'...' if len(items) > 5 else ''}"
            )
            return items, raw
        except VisionNotConfiguredError as e:
            # 配置缺失**不可重试**：它不是网络抖动，重试、换图、查代理都永远不会成功。
            # 让它走下面那条通用重试只会白等 1s+2s，最后还给出一句"检查 API Key 与网络"的
            # 错建议——用户照做也修不好。直接抛出可区分的类型与可行动的文案。
            logger.warning(f"[ImageExtractor] 未配置视觉模型，不重试: {e}")
            raise VisionNotConfiguredError(
                "未配置可用的视觉模型，请在设置页配置 VISION_MODEL 后重试"
            ) from e
        except Exception as e:
            last_error = e
            if attempt < 2:
                delay = 2 ** attempt
                logger.warning(f"[ImageExtractor] 尝试 {attempt + 1}/3 失败，{delay}s 后重试: {e}")
                time.sleep(delay)

    raise ValueError(
        f"Vision API 调用失败，请检查 API Key 与网络: {last_error}"
    ) from last_error
