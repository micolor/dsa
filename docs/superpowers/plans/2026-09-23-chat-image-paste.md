# 问股支持贴图 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Web 问股输入框能粘贴/拖入一张图，AI 既回答关于图的问题、也提取图中股票代码；用户明确要求时通过既有确认卡片把代码加进自选。图片二进制不落盘。

**Architecture:** 视觉前置 + 文本注入。图片在进入 agent 之前先被视觉模型看一遍，结果拼成文本块注入当轮用户消息（端点内、`prepare_turn` 之前），因此注入后的文本自动进 `conversation_messages`，且 **agent 主模型、`llm_adapter`、`executor`、runner 一行不改**。顺带修复视觉调用在 litellm `deepseek/` 路由下被剥图的缺陷。

**Tech Stack:** Python 3.12 / FastAPI / pytest（仓库根）；React 19 + TypeScript + Vite + Vitest（`apps/dsa-web`）；PIL（测试里渲染图片，已装 12.3.0）。

**Spec:** `docs/superpowers/specs/2026-09-23-chat-image-paste-design.md`

## Global Constraints

- **提交需要用户明确确认。** 上一个计划的"本计划内自动提交"授权**不适用于本计划**；每个 Task 末尾的 commit 步骤先获得用户同意再执行，未获同意则改动留在工作区。
- commit message 用英文，**不得添加 `Co-Authored-By`**（`AGENTS.md` §1）。
- 不新增数据库表/列、不做数据迁移、不新增配置项（复用已有 `VISION_MODEL`）。
- 不改 `EXTRACT_PROMPT`（`src/services/image_stock_extractor.py:37-55`）；`AGENTS.md` 规定改它必须在 PR 里附完整 prompt 全文，说明它被视为调优资产。
- **不改共用 agent 路径**：`src/agent/llm_adapter.py` 的消息转换、`src/agent/executor.py:749-753` 的消息构造、`src/agent/runner.py` 的工具循环都不动。
- 图片二进制**不落盘**：不写附件表、不写目录、不塞进消息历史。
- **任何情况下不得出现"看起来成功但图没被看到"**——这是本计划的承重不变式，由 Task 1 的真转发测试守（见 spec §8 的说明：运行期检查抓不住"瞎着回答"）。
- 本仓库有约 88 条既有平台性失败（Windows/POSIX），**判断回归必须逐用例去基线重跑**，不能用失败数（见记忆 `dsa-local-test-env-quirks`）。
- 注释与文档用中文，与文件语境一致。

## Task Overview

| Task | 内容 | 主要文件 |
| --- | --- | --- |
| 1 | 视觉路由修复 + 真转发测试（前置，独立修好现有静默失效） | `src/services/image_stock_extractor.py`、`tests/test_chat_image_vision_routing.py`（新） |
| 2 | 视觉上下文构造（新 prompt + `build_image_context`） | `src/services/chat_image_context.py`（新） |
| 3 | `ChatRequest` 加字段 + 校验 + 端点注入 | `api/v1/endpoints/agent.py` |
| 4 | 提示词规则 10（窄触发）+ 守卫测试 | `src/agent/executor.py` |
| 5 | 前端 composer 三入口 + 缩略图 + 即时拦截 | `apps/dsa-web/src/pages/ChatPage.tsx` |
| 6 | 前端传参 + 消息内渲染原图 | `ChatPage.tsx`、`api/agent.ts`、`stores/agentChatStore.ts` |
| 7 | 文档同步 | `docs/image-extract-prompt.md`、`full-guide.md`(+EN)、`CHANGELOG.md` |
| 8 | 全量验证 + 端到端 | — |

---

### Task 1: 视觉路由修复 + 真转发测试

现有截图提取功能在本机**静默失效**：同一张写着 `600519` 的图，`deepseek/` 路由下模型回「未收到图片」或 `"[]"`，而 `openai/deepseek-v4-flash` + 同一 `api_base` 能正确读出代码（2026-09-23 实测）。本 Task 修路由并加一条**不 mock 转发层**的测试把它钉死。

**Files:**
- Modify: `src/services/image_stock_extractor.py`（新增 `_vision_wire_model`、`_PROVIDER_ROUTES_WITHOUT_IMAGE_FORWARDING`；把 `_call_litellm_vision` 泛化为可传 prompt）
- Create: `tests/test_chat_image_vision_routing.py`

**Interfaces:**
- Consumes: 既有 `_resolve_vision_model()`（无参）、`_matching_vision_deployments(model, cfg)`、`_get_api_keys_for_model(model, cfg)`
- Produces:
  - `_PROVIDER_ROUTES_WITHOUT_IMAGE_FORWARDING: frozenset[str]`
  - `_vision_wire_model(model: str, deployment_params: Optional[Dict[str, Any]] = None) -> str`
  - `_call_litellm_vision_with_prompt(prompt: str, image_b64: str, mime_type: str, api_key: Optional[str] = None) -> str`
  - `_call_litellm_vision(image_b64, mime_type, api_key=None) -> str` 改为薄包装（传 `EXTRACT_PROMPT`），**行为与签名不变**

- [ ] **Step 1: 写失败测试**

Create `tests/test_chat_image_vision_routing.py`:

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_chat_image_vision_routing.py -q -m "not network"`
Expected: FAIL —— `AttributeError: module ... has no attribute '_vision_wire_model'`

Run（在线那条，可选，需网络与可用视觉模型）: `uv run python -m pytest tests/test_chat_image_vision_routing.py -q -m network`
Expected: FAIL —— 模型回话里没有 `600519`（当前 `deepseek/` 路由剥图）

- [ ] **Step 3: 实现路由映射与泛化调用**

在 `src/services/image_stock_extractor.py` 中，`ALLOWED_MIME`（`:80`）附近新增：

```python
# litellm 的 provider 专属路由不会转发 image_url 内容块（实测：deepseek/ 下模型回
# 「未收到图片」或 "[]"，而同一个 endpoint 用 openai/ 通用路由能正确读出图中代码）。
# 只有**实测过**会剥图的路由才加进来——不要凭猜测往这里添。
_PROVIDER_ROUTES_WITHOUT_IMAGE_FORWARDING = frozenset({"deepseek"})


def _vision_wire_model(model: str, deployment_params: Optional[Dict[str, Any]] = None) -> str:
    """Return the litellm model string to use for a vision call.

    deployment 里显式写的 model 优先（与既有 wire_model 解析一致）；若该 provider 的
    专属路由实测会剥掉图片，则改走 litellm 的通用 ``openai/`` 兼容路由（``api_base``
    由调用方按 deployment 传入，因此仍打同一个 endpoint）。
    """
    wire = str((deployment_params or {}).get("model") or model).strip()
    if "/" not in wire:
        return wire
    prefix, _, rest = wire.partition("/")
    if prefix.lower() in _PROVIDER_ROUTES_WITHOUT_IMAGE_FORWARDING:
        return f"openai/{rest}"
    return wire
```

把 `_call_litellm_vision`（`:309`）改为「泛化函数 + 薄包装」：把函数体里的 `EXTRACT_PROMPT` 换成参数 `prompt`，并把 `"model": wire_model` 改为 `_vision_wire_model(model, deployment_params)`：

```python
def _call_litellm_vision_with_prompt(
    prompt: str, image_b64: str, mime_type: str, api_key: Optional[str] = None
) -> str:
    """Call a vision model with an arbitrary prompt and one image (OpenAI vision format)."""
    # ...（原 _call_litellm_vision 的函数体，两处改动：）
    #   messages 里 {"type": "text", "text": EXTRACT_PROMPT} -> {"type": "text", "text": prompt}
    #   call_kwargs 里 "model": wire_model -> "model": _vision_wire_model(model, deployment_params)
    # 其余（凭据解析、api_base、extra_headers、Hermes 拒绝、空响应抛错）一律不动


def _call_litellm_vision(image_b64: str, mime_type: str, api_key: Optional[str] = None) -> str:
    """Extract stock codes from an image using litellm (all providers via OpenAI vision format)."""
    return _call_litellm_vision_with_prompt(EXTRACT_PROMPT, image_b64, mime_type, api_key)
```

> 保留 `_call_litellm_vision` 的名字与签名，设置页那条提取路径与既有测试（`tests/test_image_stock_extractor_litellm.py`）因此无需改动。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_chat_image_vision_routing.py -q -m "not network"`
Expected: PASS（4 passed）

Run: `uv run python -m pytest tests/test_image_stock_extractor_litellm.py -q`
Expected: PASS（既有 mock 测试不受影响）

Run（在线，需网络）: `uv run python -m pytest tests/test_chat_image_vision_routing.py -q -m network`
Expected: PASS（模型读出 `600519`）。**若失败**：说明该配置仍无法看图，**停下来报告**，不要调低断言。

- [ ] **Step 5: 确认既有提取接口真的好了**

起服务后（`.venv/Scripts/python.exe main.py --serve-only`）用渲染的图打一次提取接口：

```bash
curl -s -m 120 -X POST "http://127.0.0.1:8000/api/v1/stocks/extract-from-image?include_raw=true" \
  -F "file=@<渲染出的图片路径>;type=image/png"
```
Expected: `codes` 里出现 `600519`（此前恒为 `[]`）

- [ ] **Step 6: Commit（需用户确认）**

```bash
git add src/services/image_stock_extractor.py tests/test_chat_image_vision_routing.py
git commit -m "fix: route vision calls through the OpenAI-compatible path so images are forwarded"
```

---

### Task 2: 视觉上下文构造

**Files:**
- Create: `src/services/chat_image_context.py`
- Test: `tests/test_chat_image_context.py`

**Interfaces:**
- Consumes: `_call_litellm_vision_with_prompt`（Task 1）
- Produces:
  - `IMAGE_CONTEXT_PROMPT: str`
  - `build_image_context(image_b64: str, mime_type: str, question: str = "") -> str` —— 失败时抛 `ImageContextError`
  - `class ImageContextError(RuntimeError)`

- [ ] **Step 1: 写失败测试**

Create `tests/test_chat_image_context.py`:

```python
# -*- coding: utf-8 -*-
"""把图片转成可注入对话的文本块。"""

from __future__ import annotations

from unittest import mock

import pytest

from src.services import chat_image_context as ctx


def test_prompt_includes_the_user_question():
    """用户的问题必须进 prompt，否则摘要会答非所问。"""
    assert "{question}" in ctx.IMAGE_CONTEXT_PROMPT


def test_build_strips_and_wraps_model_output():
    with mock.patch.object(ctx, "_call_litellm_vision_with_prompt", return_value="  图里有 600519  \n"):
        out = ctx.build_image_context("b64", "image/png", "这是什么")
    assert "600519" in out
    assert out.startswith("【图片内容】")
    # 用户问题再附在注入块末尾，便于模型把图文对上
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_chat_image_context.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.services.chat_image_context'`

- [ ] **Step 3: 实现**

Create `src/services/chat_image_context.py`:

```python
# -*- coding: utf-8 -*-
"""把用户贴的图片转成一段可注入对话的文本块。

问股采用「视觉前置 + 文本注入」：图片在进入 agent 之前先被视觉模型看一遍，结果作为文本
注入当轮用户消息。这样做的好处是 agent 主模型、工具循环与消息构造**全都不用改**，注入后的
文本还会自然进入会话历史（因此后续轮次 AI 仍"记得"它看到了什么，但看不到原图——图片二进制
不落盘是有意的取舍，见设计文档 §2）。
"""

from __future__ import annotations

import logging
from typing import Optional

from src.services.image_stock_extractor import _call_litellm_vision_with_prompt

logger = logging.getLogger(__name__)


class ImageContextError(RuntimeError):
    """图片无法被读取（未配置视觉模型、调用失败、或返回内容为空）。"""


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
        logger.warning("chat image context failed: %s", exc)
        raise ImageContextError(f"图片未能读取：{exc}") from exc

    text = (raw or "").strip()
    if not text:
        logger.warning("chat image context returned empty content")
        raise ImageContextError("图片未能读取：视觉模型没有返回可用内容")
    return _render_block(text, question)
```

> 注意 `{question}` 用 `str.replace` 而非 `.format`：prompt 里若将来出现其它花括号，`.format` 会炸。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_chat_image_context.py -q`
Expected: PASS（5 passed）

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/services/chat_image_context.py tests/test_chat_image_context.py
git commit -m "feat: build an injectable text block from a pasted chat image"
```

---

### Task 3: `ChatRequest` 加字段 + 校验 + 端点注入

**Files:**
- Modify: `api/v1/endpoints/agent.py`（`ChatRequest` `:56-71`；`/chat` `:203-260`；`/chat/stream` `:519-640`）
- Test: `tests/test_agent_chat_image_request.py`

> **⚠️ 落点必须改（初版计划写错了三处，实现时已修正，此处记录以免日后照抄）。**
>
> 1. **图片处理必须在端点体内、`StreamingResponse` 之前**，不能在生成器里。`/chat/stream` 的
>    `event_generator()` 定义在 `:615`、`return StreamingResponse(...)` 在 `:709`，而既有的
>    `prepare_turn` 在生成器内部——SSE 一旦开始，抛 `HTTPException` 就**无法**变成 400/503，
>    只会退化成一条通用流错误。
> 2. **`/chat` 的调用点在一个 `try: … except Exception: raise HTTPException(500)` 里面**，
>    而 `HTTPException` 本身就是 `Exception`——照初版把 `message=` 那一行改成解析调用，会把
>    400/503 **改写成 500**。必须把解析调用提到 `try:` **之上**。另：`/chat` 是 `async def`，
>    要用 `await asyncio.to_thread(...)`。
> 3. **`/chat/stream` 的解析调用要放在 codex 冲突登记块之前**。那个登记有副作用
>    （`_ACTIVE_CODEX_STREAMS[request_id] = cancel_event`），清理在生成器的 `finally` 里；
>    抛 400 时生成器根本不跑，于是带坏图的 codex 请求会**永久占用该 request_id**、之后每次重试
>    都 409。仓库已有同类不变式的测试（`test_codex_stream_skill_resolution_raises_before_registration`）。
>
> 另外 `base64` 在该模块尚未导入，需要补 `import base64`。
>
> **4. 视觉失败的状态码用 503，不是 502**（质量审查后改）。理由：本仓库既有的"所需依赖不可用"
> 惯例是 **503**（`api/v1/endpoints/history.py` 的 `share_image_unavailable`、
> `screening_service.py` 里同类），502 在本仓库没有先例；前端对两者处理相同。**并且 body 只能放固定
> 中文文案**（如「图片未能读取，请稍后重试或换一张图」），**上游异常原文只进日志**——因为前端的错误
> 分类器会先按关键词匹配（`timeout` 等），而视觉调用最常见的失败恰是超时，会把"图没读到"显示成
> 「请检查当前网络与代理设置」；且 litellm 的异常文本可能带 `api_base`、模型名，某些 provider 还会
> 回显请求体（含 base64 图片）。详见设计文档 §8 下的说明。

**Interfaces:**
- Consumes: `build_image_context` / `ImageContextError`（Task 2）；`ALLOWED_MIME`、`MAX_IMAGE_BYTES`、`_verify_image_magic_bytes`（`image_stock_extractor`）
- Produces:
  - `ChatRequest.image_base64` / `ChatRequest.image_mime`（可选，成对）
  - `_resolve_image_message(request: ChatRequest) -> str` —— 返回（可能已注入的）消息文本；校验失败抛 `HTTPException(400)`
  - `CHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024`

- [ ] **Step 1: 写失败测试**

Create `tests/test_agent_chat_image_request.py`:

```python
# -*- coding: utf-8 -*-
"""ChatRequest 的图片字段校验与注入。"""

from __future__ import annotations

import base64
import io
from unittest import mock

import pytest
from fastapi import HTTPException

from api.v1.endpoints.agent import CHAT_IMAGE_MAX_BYTES, ChatRequest, _resolve_image_message


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


def test_oversized_image_is_rejected_with_clear_message():
    big = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * (CHAT_IMAGE_MAX_BYTES + 1)).decode()
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64=big, image_mime="image/png"))
    assert e.value.status_code == 400
    assert "2MB" in str(e.value.detail)


def test_bad_magic_bytes_are_rejected():
    b64 = base64.b64encode(b"not an image at all").decode()
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64=b64, image_mime="image/png"))
    assert e.value.status_code == 400


def test_vision_failure_surfaces_as_503_not_a_blind_answer():
    """视觉失败必须让请求失败——不许降级成"看不到图也照样回答"。"""
    b64 = base64.b64encode(_png_bytes()).decode()
    with mock.patch("api.v1.endpoints.agent.build_image_context",
                    side_effect=ImageContextError("图片未能读取：未配置视觉模型")):
        with pytest.raises(HTTPException) as e:
            _resolve_image_message(_req(message="x", image_base64=b64, image_mime="image/png"))
    assert e.value.status_code == 503
    assert "图片未能读取" in str(e.value.detail)


def test_invalid_base64_is_rejected():
    with pytest.raises(HTTPException) as e:
        _resolve_image_message(_req(message="x", image_base64="!!!not-base64!!!", image_mime="image/png"))
    assert e.value.status_code == 400
```

补 `from src.services.chat_image_context import ImageContextError` 到测试文件顶部。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_agent_chat_image_request.py -q`
Expected: FAIL —— `ImportError: cannot import name 'CHAT_IMAGE_MAX_BYTES'`

- [ ] **Step 3: 实现**

`api/v1/endpoints/agent.py` 顶部 import 区补：

```python
from src.services.chat_image_context import ImageContextError, build_image_context
from src.services.image_stock_extractor import ALLOWED_MIME, _verify_image_magic_bytes
```

`ChatRequest`（`:56-71`）内新增字段：

```python
    # 贴图：base64 + mime 成对出现。二进制不落盘，只在当轮转成文本注入（见设计文档 §2/§5）。
    # 成对校验刻意放在 _resolve_image_message 里而不是 model_validator：那样所有图片问题
    # 都是同一套 400 + 明确 message，而不是"成对错误回 422、其余回 400"。
    image_base64: Optional[str] = None
    image_mime: Optional[str] = None
```

文件内新增（放在 `ChatRequest` 之后）：

```python
# 聊天请求体是 JSON 且还带会话上下文，base64 会放大 ~1.33 倍，因此上限比提取接口的 5MB 窄。
CHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024


def _image_error(message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "invalid_image", "message": message})


def _resolve_image_message(request: ChatRequest) -> str:
    """Return the message to hand to the agent, with image context injected when present.

    校验失败 → 400（参数问题）；视觉失败 → 503（外部依赖问题，与本仓库既有的"依赖不可用"惯例一致）。**不降级成"看不到图也照样
    回答"**：那种回答既无用又会伪装成成功（见设计文档 §8）。
    """
    if not request.image_base64 and not request.image_mime:
        return request.message
    if bool(request.image_base64) != bool(request.image_mime):
        raise _image_error("image_base64 与 image_mime 必须同时提供")

    mime = str(request.image_mime or "").strip().lower()
    if mime not in ALLOWED_MIME:
        raise _image_error(f"不支持的图片类型：{mime or '(空)'}；支持 {', '.join(sorted(ALLOWED_MIME))}")

    try:
        raw = base64.b64decode(request.image_base64, validate=True)
    except Exception as exc:
        raise _image_error(f"图片数据不是合法 base64：{exc}") from exc

    if len(raw) > CHAT_IMAGE_MAX_BYTES:
        raise _image_error(f"图片过大（{len(raw) // 1024}KB），上限 2MB")

    try:
        _verify_image_magic_bytes(raw, mime)
    except Exception as exc:
        raise _image_error(f"图片内容与声明的类型不符：{exc}") from exc

    try:
        block = build_image_context(request.image_base64, mime, request.message)
    except ImageContextError as exc:
        # 只放固定文案：上游原文可能含 api_base/模型名，某些 provider 还会回显请求体（含 base64 图片），
        # 而前端会先按关键词（timeout 等）分类——超时会被误报成"请检查网络与代理"。异常只进脱敏日志。
        raise api_error(503, "image_unreadable", "图片未能读取，请稍后重试或换一张图") from exc

    # 注入块在前、用户原话在后：模型先读图，再对着问题回答。
    return f"{block}\n{request.message}"
```

`base64` 需在文件顶部 import（若尚未导入）。

两个端点各改一行，把 `message=request.message` 换成注入后的文本：

- `/chat`（约 `:244-245`）：`message=_resolve_image_message(request)`
- `/chat/stream`（约 `:621-627` 的 `executor.prepare_turn` 调用）：同样改为 `message=_resolve_image_message(request)`

> **为什么在这里注入**：`prepare_turn` 会把 `message` 原样持久化（`src/agent/chat_executor.py:98`），所以在它之前注入，注入后的文本自动进 `conversation_messages`，且 agent 拿到的就是它——共用路径一行不动。
>
> **阻塞问题（二选一，别混用）**：`build_image_context` 是同步网络调用（最长 60s 超时），而 `prepare_turn` 已经用 `asyncio.to_thread` 包着。做法：在 `/chat/stream` 里**先**单独 `message_text = await asyncio.to_thread(_resolve_image_message, request)`，再把这个结果传给既有的 `asyncio.to_thread(executor.prepare_turn, message=message_text, ...)`。`/chat`（非流式）本身就是同步函数，直接调用即可。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_agent_chat_image_request.py -q`
Expected: PASS（8 passed）

- [ ] **Step 5: 跑既有 chat 回归**

Run: `uv run python -m pytest tests/test_agent_chat_api.py -q`
Expected: PASS（无图片的请求行为不变）

- [ ] **Step 6: Commit（需用户确认）**

```bash
git add api/v1/endpoints/agent.py tests/test_agent_chat_image_request.py
git commit -m "feat: accept and inject a pasted image in the agent chat endpoints"
```

---

### Task 4: 提示词规则 10（窄触发）

> **⚠️ 本 Task 之前先做两条 Part 0 修复**（都是本次审查查出来的、与本功能直接相关的问题）：
>
> **P1. 修收集期的 `sys.modules["litellm"]` 注入（范围比初版以为的大）。**
> 初版只点了 `tests/test_image_stock_extractor_litellm.py`，实际范围经穷举后是 **6 个危险形态的注入点**
> （"不在 `sys.modules` 就注入"，即 litellm 可 import 时也注入）：
> `test_fetcher_source_optimization.py`、`test_hk_realtime_routing.py`、`test_stock_code_bse.py`、
> `test_market_analyzer_generate_text.py`、`tests/agent/test_runtime_facts.py`（初版 grep 只扫了
> `tests/*.py`，漏了子目录），以及 **`tests/litellm_stub.py` 的 `ensure_litellm_stub`（被 40 个文件在 import 期调用）**。
> 机制：`image_stock_extractor.py` 在 import 时把 `sys.modules.get("litellm")` **绑成模块全局**，所以
> 只要有任何一处在收集期注入 MagicMock，**Task 1 那条承重网络守卫在全量跑里就拿到 MagicMock 并失败**
> ——看起来像"图不再被转发"，而它本该守住的正是这件事。修法统一为"先真 import，只有
> `ModuleNotFoundError`/`ImportError` 才注入"，保留各文件"没有 litellm 的环境也能跑"的原意。
>
> **连带发现（必须一起处理，否则会多出 20 条红）**：禁用 stub 后真实 litellm 会被 import，而它在
> import 期会执行 `dotenv.load_dotenv()`（`litellm/__init__.py`：`if os.getenv("LITELLM_MODE","DEV")=="DEV"`），
> 于是**把仓库 `.env` 读进测试进程的 `os.environ`**；`.env` 里的 `LITELLM_FALLBACK_MODELS` 会让
> `test_system_config_service`（17）/`test_system_config_api`（2）/`test_provider_cache`（1）共 20 条
> 对"未声明的 fallback"报错——这 20 条是**既有**的测试隔离缺陷，此前被 stub 掩盖着。
> **正确终点是两个都要**：守卫工作**且**测试进程不读开发机 `.env`。做法是在 `tests/conftest.py` 模块级
> `os.environ.setdefault("LITELLM_MODE", "PROD")`（早于任何 litellm import，`setdefault` 尊重外部显式设置），
> 从根上让那行 `load_dotenv` 不执行。**不要**改那 20 条的期望值，也**不要**用事后清理 `os.environ` 的
> fixture 遮盖（挡不住收集期已发生的 `load_dotenv`）。
>
> **这条属于既有的测试隔离缺陷，但它使我们的守卫在全量跑里失效，所以在本计划内修。**
>
> **P2. 会话标题不要被注入块污染。** `src/storage.py:3770` 用「第一条持久化的用户消息前 60 字」当会话标题，
> 而带图那一轮持久化的正是注入后的文本 → 侧边栏标题会变成 `【图片内容】` + 模型对图片描述的开头，
> 而不是用户问的那句话。修法：派生标题时剥掉注入脚手架（若文本里出现 `【用户问题】`，取其后的部分；
> 否则取原文），并补一条测试覆盖"带图首轮"与"纯文本轮"两种情形。改动要小，不要动持久化本身。



**Files:**
- Modify: `src/agent/executor.py`（两套 chat 提示词的规则 9 之后）
- Test: `tests/test_chat_prompt_image_rule.py`

**Interfaces:**
- Consumes: 既有 `CHAT_SYSTEM_PROMPT`、`LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT`、`CODEX_CHAT_SYSTEM_PROMPT`
- Produces: 两套 chat 提示词新增规则 10（逐字相同）

- [ ] **Step 1: 写失败测试**

Create `tests/test_chat_prompt_image_rule.py`:

```python
# -*- coding: utf-8 -*-
"""图中股票的加自选提案规则：触发条件必须窄。

上一个计划踩过"触发条件过宽 → 模型在没人要求时开卡片"的坑，因此这里同时钉住
"必须提案"与"不得仅因图中出现代码而提案"两半。
"""

from __future__ import annotations

import unittest

from src.agent.executor import CHAT_SYSTEM_PROMPT, LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT, CODEX_CHAT_SYSTEM_PROMPT

CHAT_PROMPTS = (LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT, CHAT_SYSTEM_PROMPT)


class TestChatPromptImageRule(unittest.TestCase):
    def test_both_chat_prompts_carry_the_image_rule(self):
        for prompt in CHAT_PROMPTS:
            self.assertIn("图中股票的加自选提案", prompt)
            self.assertIn("propose_watchlist_change", prompt)
            # 窄触发的两半都要在
            self.assertIn("明确表达", prompt)
            self.assertIn("不得主动提案", prompt)

    def test_rule_blocks_are_identical_across_variants(self):
        blocks = []
        for prompt in CHAT_PROMPTS:
            block = [ln for ln in prompt.splitlines() if ln.startswith("10. ")]
            self.assertEqual(len(block), 1, msg=f"规则 10 的行数不是 1：{block}")
            blocks.append(block[0])
        self.assertEqual(blocks[0], blocks[1], msg="两套 chat 提示词的规则 10 必须逐字相同")

    def test_codex_prompt_does_not_get_the_rule(self):
        """Codex 是只读面，提案工具不在该面上（见 tool_surface 的 cancellation_safe 过滤）。"""
        self.assertNotIn("图中股票的加自选提案", CODEX_CHAT_SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_chat_prompt_image_rule.py -q`
Expected: FAIL —— `AssertionError: '图中股票的加自选提案' not found in ...`

- [ ] **Step 3: 加规则**

在**两套** chat 提示词的规则 9 之后各插入（`replace_all` 一次改两处，保持逐字相同）：

```
10. **图中股票的加自选提案** — 当用户提供了图片、且消息里已列出图中股票代码时：**只有**当用户明确表达了「把图里这些加入自选/关注」的意图，才用 `propose_watchlist_change` 逐个生成提案交由用户确认；**仅因为图中出现代码不得主动提案**，此时只在回答中列出代码与名称即可。
```

**不要**动 `CODEX_CHAT_SYSTEM_PROMPT` 与两份分析提示词。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_chat_prompt_image_rule.py tests/test_chat_prompt_proposal_rules.py -q`
Expected: PASS（后者是既有守卫，确认没改坏规则 7-9）

- [ ] **Step 5: 变异验证**

把规则 10 里的「明确表达」删掉（两处一起）→ `test_both_chat_prompts_carry_the_image_rule` 必须转红；把规则 10 加进 Codex 提示词 → `test_codex_prompt_does_not_get_the_rule` 必须转红。改完还原。

- [ ] **Step 6: Commit（需用户确认）**

```bash
git add src/agent/executor.py tests/test_chat_prompt_image_rule.py
git commit -m "feat(agent): tell chat prompts when to propose watchlist adds from an image"
```

---

### Task 5: 前端 composer 三入口 + 缩略图 + 即时拦截

**Files:**
- Modify: `apps/dsa-web/src/pages/ChatPage.tsx`（composer 区 `:1844-1879`）
- Test: `apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx`（追加）
- 复用范式: `apps/dsa-web/src/components/settings/IntelligentImport.tsx:203-209`（拖放）、`:320-326`（隐藏 file input + accept）

**Interfaces:**
- Produces（ChatPage 内）：
  - `const [pendingImage, setPendingImage] = useState<{ dataUrl: string; base64: string; mime: string } | null>(null)`
  - `handleImageFile(file: File)` —— 校验并读入；失败用既有 toast/提示反馈
  - `CHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024`（与后端常量一致，放在文件顶部并加注释说明必须同步）

> **⚠️ 必须给"正在读取图片"的反馈。** 视觉前置意味着带图那一轮，**SSE 流要等图片读完才开始**
> （后端 `VISION_API_TIMEOUT` 上限 60 秒；实测通常 1–2 秒）。
>
> 现状核实（**初版计划在这点上写错了，已更正**）：该文件在 `loading` 期间的实际反馈是"textarea 禁用 +
> 发送按钮 `isLoading` 转圈"（`disabled={loading || !agentAvailable}`、`isLoading={loading}`）；而
> `t('chat.thinking')` 的值是 **'思考过程'**——那是**可折叠思考区块的标题**，不是加载提示。所以读图期间
> 用户看到的是"转圈但不知道在干什么"，最长 60 秒。
>
> 具体做法（用**既有**钩子，不要新造状态机）：发送时若带图，置一个 `readingImage` 本地状态；清除它的时机用
> **既有的** `StreamMeta.onAccepted` 回调（后端是在流开始**之前**完成图片读取的，所以 `accepted` 到达
> 就意味着读图阶段结束——`startStream(payload, { onAccepted })` 这个钩子 ChatPage 已在用）。渲染时
> `readingImage` 为真就在 composer 附近显示一行"正在读取图片…"（不动既有转圈）。
>
> i18n 锚点（已核实）：新键加在 `chat.actionProposal*` 那一组旁边——`uiText.ts` zh `:1056-1061`、
> en `:2203-2208`，两段都要加（`en` 是 `Record<UiTextKey, string>`，漏一段 `tsc -b` 会红）：
> `'chat.readingImage': '正在读取图片…'` / `'Reading image…'`。
>
> 测试形态（该文件的 mock 已支持）：既有用例在 `:235` 用
> `mockStartStream.mockImplementation(async (_payload, meta) => {...})` 拿到过 `meta`，所以你可以在测试里
> 捕获它、再手动触发确认读图阶段结束：
>
> ```tsx
>     let capturedMeta: { onAccepted?: () => void } | undefined;
>     mockStartStream.mockImplementation(async (_payload, meta) => { capturedMeta = meta; });
>     // 贴图 + 发送 ...
>     expect(await screen.findByText('正在读取图片…')).toBeInTheDocument();
>     act(() => { capturedMeta?.onAccepted?.(); });
>     expect(screen.queryByText('正在读取图片…')).not.toBeInTheDocument();
> ```
>
> 文案 key 走 i18n（新增 `chat.readingImage`，zh/en 两段都要补，否则 `tsc -b` 会红）。

- [ ] **Step 1: 写失败测试**

在 `ChatPage.test.tsx` 追加（沿用该文件既有的 `mockStoreState` 与 `MemoryRouter` 渲染方式）：

```tsx
  it('accepts a pasted image and shows a removable thumbnail', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', { type: 'image/png' });
    const textarea = screen.getByRole('textbox');
    fireEvent.paste(textarea, {
      clipboardData: { files: [file], items: [], types: ['Files'] },
    });

    // 缩略图出现，并且在发送前可以删掉
    expect(await screen.findByAltText('待发送的图片')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '移除图片' }));
    expect(screen.queryByAltText('待发送的图片')).not.toBeInTheDocument();
  });

  it('rejects an oversized image before sending', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const big = new File([new Uint8Array(2 * 1024 * 1024 + 1)], 'big.png', { type: 'image/png' });
    const textarea = screen.getByRole('textbox');
    fireEvent.paste(textarea, { clipboardData: { files: [big], items: [], types: ['Files'] } });

    expect(await screen.findByText(/图片过大/)).toBeInTheDocument();
    expect(screen.queryByAltText('待发送的图片')).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/dsa-web && npm run test -- src/pages/__tests__/ChatPage.test.tsx`
Expected: FAIL —— 找不到 `待发送的图片`

- [ ] **Step 3: 实现 composer 交互**

`ChatPage.tsx` 顶部新增常量（与后端 `CHAT_IMAGE_MAX_BYTES` 同步，注释写明）：

```tsx
// 与后端 api/v1/endpoints/agent.py 的 CHAT_IMAGE_MAX_BYTES 必须一致；改一处要同时改另一处。
const CHAT_IMAGE_MAX_BYTES = 2 * 1024 * 1024;
const CHAT_IMAGE_MIME = ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
```

状态与处理函数（放在既有的 `input` 状态附近）：

```tsx
  const [pendingImage, setPendingImage] = useState<{ dataUrl: string; base64: string; mime: string } | null>(null);

  const handleImageFile = useCallback((file: File) => {
    if (!CHAT_IMAGE_MIME.includes(file.type)) {
      showSendFeedback({ type: 'error', message: `不支持的图片类型：${file.type || '(未知)'}；支持 jpg/png/webp/gif` }, 4000);
      return;
    }
    if (file.size > CHAT_IMAGE_MAX_BYTES) {
      showSendFeedback({ type: 'error', message: '图片过大（上限 2MB），请压缩后再试' }, 4000);
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || '');
      const base64 = dataUrl.split(',', 2)[1] || '';
      setPendingImage({ dataUrl, base64, mime: file.type });
    };
    reader.readAsDataURL(file);
  }, [showSendFeedback]);
```

> `showSendFeedback` 是本文件既有的提示机制（`:896-900`，配 `sendToast` + `<AutoDismissToast>`）：签名是 `(nextToast: { type: 'success' | 'error'; message: string }, durationMs: number) => void`。复用它，不要新造一套提示。


composer 区（既有的 `flex items-end gap-3` 容器）加上。**该容器已核实结构**：它是「textarea + （Stop 或 Send 按钮）」两栏；因此**附件按钮作为该行的第一个子元素**（textarea 之前），而**缩略图 chip 放在该行所在的外层容器里、该行之上**（不要塞进 `items-end` 那一行，否则会和 textarea 底对齐错位）。**拖放与隐藏 input 的机制照抄 `IntelligentImport.tsx` 的既有写法**（已核实原文）：

```tsx
  // 拖放：机制与 IntelligentImport.tsx:198-213 一致（preventDefault + 取 files[0]），
  // 但这里按 MIME 校验（后端也是按 MIME + magic byte 校验，扩展名可伪造）。
  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    const f = e.dataTransfer?.files?.[0];
    if (f) handleImageFile(f);
  }, [handleImageFile]);
```

- textarea 上：`onPaste={(e) => { const f = e.clipboardData?.files?.[0]; if (f) { e.preventDefault(); handleImageFile(f); } }}`（**粘贴是主入口**）
- 外层容器加 `onDrop={handleDrop}` 与 `onDragOver={(e) => e.preventDefault()}`
- 缩略图 chip：`pendingImage && (<span className="..."><img src={pendingImage.dataUrl} alt="待发送的图片" className="h-16 w-16 rounded object-cover" /><button type="button" aria-label="移除图片" onClick={() => setPendingImage(null)}>×</button></span>)`
- 隐藏 file input + 触发按钮（照 `IntelligentImport.tsx:320-326` 的 `ref` + `type="file"` + `accept=".jpg,.jpeg,.png,.webp,.gif"` + `className="hidden"` 写法）
- **发送按钮的启用条件必须一起改**：现在是 `disabled={!input.trim() || loading || !agentAvailable}`，而"只贴图不打字"是受支持的一轮（后端为此把 `message` 改成可选并给了明确 400，见 Task 3/4 的说明），所以改为
  `disabled={(!input.trim() && !pendingImage) || loading || !agentAvailable}` —— 否则贴了图但没打字时按钮是灰的，用户无从发送。补一条断言：仅有 `pendingImage`、输入为空时，发送按钮**可点**。

> 报错/提示请复用本文件**既有**的提示机制（该文件里已有 toast / `createParsedApiError` 等用法，先搜再动手），不要为了这个功能新造一套提示组件。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd apps/dsa-web && npm run test -- src/pages/__tests__/ChatPage.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add apps/dsa-web/src/pages/ChatPage.tsx apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx
git commit -m "feat(web): accept pasted or dropped images in the chat composer"
```

---

### Task 6: 前端传参 + 消息内渲染

> **⚠️ 本 Task 之前先做三条 Part 0（都来自 Task 5 的实现与审查）：**
>
> **P1. 发送成功后清掉待发送缩略图。** Task 5 刻意没清（留给本 Task 决定）：chip 是**输入**的载体，不是"已发送"的指示；发送后应当清空 `pendingImage`，图片改由消息里的 `imageDataUrl` 渲染（来自 `meta`）。
>
> **P2. 给 chip 的三个标签补 i18n。** Task 5 里它们是硬编码中文（`待发送的图片` / `移除图片` / `添加图片`），与相邻 composer 的既有风格一致，但在英文界面下是可见缺口。补 `chat.pendingImageAlt` / `chat.removeImage` / `chat.addImage` 三个键（zh + en 两段），并把测试里的断言改为断言 **zh 渲染结果**（测试环境是中文，字符串不变，所以断言不必改字面量——但要确认确实如此）。
>
> **P3. 不要"修"快速提问 + 待发送图并存的情况。** Task 5 报告：若 chip 未发送就点快速提问，那次发送会带上图片并因此显示"正在读取图片…"。**这是正确行为**（那个图确实被附上了、确实在读），不是缺陷；不要为它加守卫或特判。若审查者在本 Task 里提这条，引用本行说明。
>
> **P4. Task 5 那条 reading-image 测试的 mock 不具代表性（记录用，本 Task 别照抄）。**
> 它用的 mock **立即 resolve 且从不触发 `accepted`**——而真实 store 在这种情况下会抛
> `Agent stream ended before accepted`，即那个流在现实中不存在。后果是"清除读图状态"只能选满足这条假流的
> 机制（`chatError` effect），而它在 **abort 路径**上会永久卡住横幅（store 对 abort 不设 `chatError`，
> 横幅又没有 `loading` 守卫）。Task 5 已补 `useEffect(() => { if (!loading) setReadingImage(false) }, [loading])`
> 覆盖该路径。**若将来重写这条测试，应让它模拟"先真发 `accepted` 再失败"的真实形状。**



**Files:**
- Modify: `apps/dsa-web/src/api/agent.ts`（`ChatRequest` `:18-21`）
- Modify: `apps/dsa-web/src/pages/ChatPage.tsx`（发送体构造 `:856-863`；用户消息渲染处）
- Modify: `apps/dsa-web/src/stores/agentChatStore.ts`（`Message` 加可选字段；`startStream` 把图片挂到用户消息上）
- Test: `apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx`、`apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts`

**Interfaces:**
- Produces:
  - `ChatRequest.image_base64?` / `ChatRequest.image_mime?`
  - `Message.imageDataUrl?: string`（**仅会话内存在**：刷新即失，与"不存二进制"一致）

- [ ] **Step 1: 写失败测试**

`ChatPage.test.tsx` 追加：

```tsx
  it('sends the image with the message and renders it in the user bubble', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], 'shot.png', { type: 'image/png' });
    fireEvent.paste(screen.getByRole('textbox'), {
      clipboardData: { files: [file], items: [], types: ['Files'] },
    });
    await screen.findByAltText('待发送的图片');

    fireEvent.change(screen.getByRole('textbox'), { target: { value: '这张图怎么看？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));  // 可访问名来自 t('chat.send')，zh 段为「发送」

    await waitFor(() => expect(mockStartStream).toHaveBeenCalled());
    // 用 .at(-1) 取最后一次调用：该文件既有用例就是这么写的（`:892`），
    // 用 calls[0][0] 在"已发过消息"的用例里会取到旧调用。
    const payload = mockStartStream.mock.calls.at(-1)?.[0];
    expect(payload.message).toBe('这张图怎么看？');
    expect(payload.image_mime).toBe('image/png');
    expect(typeof payload.image_base64).toBe('string');
    // 图片走 meta（前端渲染用元数据，不上后端）
    expect(mockStartStream.mock.calls.at(-1)?.[1]).toMatchObject({ imageDataUrl: expect.stringContaining('data:image') });

    // 用户那条消息里渲染原图
    expect(await screen.findByAltText('已发送的图片')).toBeInTheDocument();
  });
```

`agentChatStore.test.ts` 追加：`startStream({ message, imageDataUrl })` 后，用户消息带 `imageDataUrl`。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/dsa-web && npm run test -- src/pages/__tests__/ChatPage.test.tsx src/stores/__tests__/agentChatStore.test.ts`
Expected: FAIL —— 请求体里没有 `image_mime`

- [ ] **Step 3: 实现**

`api/agent.ts` 的 `ChatRequest`：

```ts
export interface ChatRequest {
  message: string;
  skills?: string[];
  /** 贴图：base64 与原图 mime，成对出现（后端校验）。二进制不落盘。 */
  image_base64?: string;
  image_mime?: string;
}
```

`ChatPage.tsx` 发送体（`:856-863`）：加上两个字段（**仅当有图时**）：

```tsx
      const payload = {
        message: msgText,
        session_id: sessionId,
        ...(requestedSkillIds !== null ? { skills: normalizeSelectedSkillIds(requestedSkillIds) } : {}),
        context: contextForSend ?? undefined,
        // 有图才带这两个字段（后端要求成对）
        ...(pendingImage ? { image_base64: pendingImage.base64, image_mime: pendingImage.mime } : {}),
      };
```

> **⚠️ base64 必须干净**：后端用 `base64.b64decode(..., validate=True)`，它会**拒绝含换行或空白的
> base64**。`FileReader.readAsDataURL` 的结果是干净的（`data:...;base64,XXXX`，逗号后无换行），
> `split(',', 2)[1]` 取到的就是合法输入——**不要**改成带换行的格式化输出（例如某些库的
> `base64.encodebytes`），也不要先做美化再拼。

发送后清空 `setPendingImage(null)`，并把 `imageDataUrl` 通过**第二个参数（`meta`）**传给 `startStream` —— **不要塞进 `payload`**：后端 `ChatRequest` 没有这个字段（Pydantic 会静默忽略，属于误导性代码）；`meta` 本来就是"前端渲染用、不上后端"的元数据：

```tsx
      await startStream(payload, {
        skillNames: usedSkillNames,
        skillName: usedSkillNames.join('、'),
        imageDataUrl: pendingImage?.dataUrl,   // 新增
        onAccepted: () => { /* 既有回调不动 */ },
      });
      setPendingImage(null);
```

`stores/agentChatStore.ts`：
- `StreamMeta`（`:69-73`）加：
  ```ts
  /** 用户贴的图（仅用于会话内渲染；不上后端、不持久化）。 */
  imageDataUrl?: string;
  ```
- `Message` 加：
  ```ts
  /** 用户贴的图（仅会话内存在；刷新即失，与"不存二进制"一致）。 */
  imageDataUrl?: string;
  ```
- **建用户消息处已核实为 `:404-412`** 的 `const userMessage: Message = { id, role: 'user', content: payload.message, skills, skill, skillNames, skillName }` —— 在其中加上 `imageDataUrl: meta?.imageDataUrl`（`meta` 在该作用域内已可用）。

用户消息渲染处加：

```tsx
        {msg.role === 'user' && msg.imageDataUrl && (
          <img src={msg.imageDataUrl} alt="已发送的图片" className="mb-2 max-h-40 rounded-lg border border-subtle" />
        )}
```

> **⚠️ 必须同时处理"刷新后"的渲染与解析**（设计文档 §9.1；这是**在注入时就固定下来**的约定，
> 事后无法给既有数据补标记）：
>
> 1. **折叠注入块**：会话在刷新/切换后是从库里还原的，而带图那一轮持久化的**就是**注入后的文本
>    （`block + "\n" + 用户原话`），所以不处理的话，用户气泡里会出现整段
>    `【图片内容】…【图片中股票】…【用户问题】…`，看起来像用户自己敲的。约定：**用户消息若以
>    `【图片内容】` 开头**，就渲染成一行「[图片] 已由 AI 读取」+ 可展开原文，不要把整段当用户的话倒出来。
>    （`imageDataUrl` 只覆盖"本次会话内"的图片，还原后它是空的——所以这一条不能靠它。）
> 2. **活跃标的上文解析只看用户那部分**：`restoreActiveStockContextFromMessages`（`ChatPage.tsx:165-218`
>    一带）会 `extractStockCodesFromMessage` + `isCompareStockMessage` 扫原始文本；块里列出 ≥2 个代码时，
>    刷新后会话会静默切进"对比模式"。改成只用 `【用户问题】` 之后的文本做解析，否则同一会话刷新前后
>    行为不一致。
>
> 两条都要有测试：一条"以 `【图片内容】` 开头的用户消息被折叠"，一条"含 ≥2 个代码的注入块不会让
> 还原后的会话进入对比模式"。

> 两个 alt 文案刻意不同：`待发送的图片`（chip，发送前）与 `已发送的图片`（消息内），测试与用户可访问性都因此能区分。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd apps/dsa-web && npm run test -- src/pages/__tests__/ChatPage.test.tsx src/stores/__tests__/agentChatStore.test.ts`
Expected: PASS

Run: `cd apps/dsa-web && npx tsc -b && npm run lint`
Expected: 干净

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add apps/dsa-web/src/api/agent.ts apps/dsa-web/src/pages/ChatPage.tsx apps/dsa-web/src/stores/agentChatStore.ts apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts
git commit -m "feat(web): send pasted images with the message and show them in the thread"
```

---

### Task 7: 文档同步

**Files:**
- Modify: `docs/image-extract-prompt.md`（路由修复带来的行为说明）
- Modify: `docs/full-guide.md`（本地 WebUI 管理界面一节）+ `docs/full-guide_EN.md`
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）

- [ ] **Step 1: 补 CHANGELOG**

在 `[Unreleased]` 末尾追加（扁平格式，一条一行，不加类目标题）：

```markdown
- [新功能] 问股 Web 输入框支持贴图（粘贴/拖放/选文件）：图片先由视觉模型读取，结果作为文本注入当轮对话，AI 据此回答并结合行情工具分析；图中出现股票代码时会列出，用户明确要求加自选时通过既有确认卡片落库。图片二进制不落盘，刷新后不可回看
- [修复] 截图提取股票代码此前静默失效：litellm 的 `deepseek/` provider 路由不会转发 `image_url` 内容块，模型"看不见图"却仍返回合法但无用的 `[]`，接口回 200 让人误以为图里没有代码。现对实测会剥图的路由改走通用 `openai/` 兼容路由，并新增一条不 mock 转发层的测试守住"图确实到达模型"
```

- [ ] **Step 2: 文档与代码一致性核对**

```bash
grep -n "deepseek\|VISION_MODEL" docs/image-extract-prompt.md | head
```
若该文档描述了模型选择或路由，补一句说明（**不改 `EXTRACT_PROMPT` 本身**，因此无需附 prompt 全文）。

`docs/full-guide.md` 的「本地 WebUI 管理界面」一节补一句用户可见能力：

```markdown
问股输入框支持直接粘贴（Ctrl+V）或拖入一张图（jpg/png/webp/gif，上限 2MB）：AI 会先读取这张图再回答，
图中出现的股票代码会一并列出；明确要求时可以一键加入自选。图片不会被保存，刷新后无法回看。
```

`docs/full-guide_EN.md` 同步同一句英文；若英文版结构差异较大无法直接对应，**在交付说明里写明未同步的原因**（`AGENTS.md` 要求）。

- [ ] **Step 3: 同步 API 契约快照**

审查发现 `docs/architecture/api_spec.json` 里的 `ChatRequest` 没有重新生成，会继续把请求文档写成"没有图片字段"。查仓库是否有生成脚本（`scripts/` 下找类似 `generate_index_from_csv.py` 之外的 spec 生成入口，或看该文件头部注释里的生成命令）；有就跑一次并提交产物，没有就在 Step 4 的交付说明里写明"该快照未重新生成，原因：无生成入口"，**不要手改这个自动生成的 JSON**。

- [ ] **Step 4: Commit（需用户确认）**

```bash
git add docs/CHANGELOG.md docs/image-extract-prompt.md docs/full-guide.md docs/full-guide_EN.md
git commit -m "docs: document chat image paste and the vision routing fix"
```

---

### Task 8: 全量验证 + 端到端

- [ ] **Step 1: 后端回归（逐用例对基线，不看失败数）**

```bash
# 显式用 OS 临时目录：bash 的 /tmp 与 Write 工具的 /tmp 解析到不同位置（本计划里已有多名实现者踩过），别用裸 /tmp。
TMPD="$LOCALAPPDATA/Temp/dsa_img_verify"; mkdir -p "$TMPD"
uv run python -m pytest -m "not network" -q 2>&1 | grep -E "^FAILED" | sort > "$TMPD/after.txt"
```
对 `after.txt` 里**每一条**失败用例，去本计划的基线提交上单跑（`git archive <base> | tar -x -C "$TMPD/base"`），确认"基线也失败"。只有"HEAD 失败而基线通过"才是回归。
**特别要确认**：`tests/test_chat_image_vision_routing.py::test_vision_call_actually_sees_the_image` 在全量跑里**不再失败**（它此前被收集期 `sys.modules["litellm"]` 注入打回 MagicMock，是 Part 0 修掉的；这条验证的是那个修复真的生效）。

- [ ] **Step 2: 网络类测试**

```bash
uv run python -m pytest -m network -q -k "vision or routing"
```
Expected: PASS（含 Task 1 的真转发测试）。**无网络时明确记录为未验证**，不要跳过不提。

- [ ] **Step 3: 前端**

```bash
cd apps/dsa-web && npx tsc -b && npm run lint && npm run build && npm run test
```
Expected: 构建干净；测试失败集与基线一致（`HomePage.test.tsx` 有一条**确定性**失败、`DecisionSignalsPage.test.tsx` 是负载敏感抖动——两者都在基线存在）。

- [ ] **Step 4: 端到端（手工，需真实 LLM）**

1. 重启服务（`.venv/Scripts/python.exe main.py --serve-only`，会重建前端产物约 1 分钟）
2. 浏览器打开问股，**粘贴一张 K 线或持仓截图**（也可用测试渲染的图）
3. **观察"正在读取图片…"**：粘贴带图发送后、回答开始前，界面应显示这一句（而不是一直显示"思考中"，也不应毫无变化）——视觉前置最多 60 秒，没有这句体感就是卡死
4. 发一句与图相关的问题 → 回答里应体现图的内容
5. 若图中含代码 → 回答里列出代码；说「把这几只加入自选」→ **出现确认卡片** → 确认 → 自选页出现
6. **刷新页面，验证两件事**（这两条只在浏览器里能看到）：
   - 用户那条消息里的注入块应**折叠**成一行（如「[图片] 已由 AI 读取」+ 可展开），**不是**把 `【图片内容】…【用户问题】…` 整段倒出来当用户的话
   - 左侧会话标题应是**你问的那句话**，不是 `【图片内容】…` 或模型对图的描述
7. **只贴图不打字**再来一轮：贴一张图直接发送 → 回答仍应基于图；刷新后标题也应干净（不是 `【图片内容】…`）
8. **清理**：把加进去的代码移出自选，核对自选恢复原状（自选是用户数据）；删除这一轮产生的临时会话（如有）
9. 视觉失败路径：临时把 `VISION_MODEL` 指到一个不存在的模型 → 贴图应变 **503** 并给出「图片未能读取」类提示，**不是**给出一个看不到图的回答；验完还原。（注意本机存在一条会被 401 拒绝的 ModelScope 旁路渠道，报错文案可能是鉴权而非路由——仍是 503 + 明确文案，不影响本条验收意图。）

- [ ] **Step 5: 交付说明**

按 `AGENTS.md` §9 六段结构输出。未验证项须写明：真转发测试在无网络环境下的状态、bot 与桌面端未做图片、视觉提取准确率未量化评估、以及英文文档同步情况。
