# 问股「AI 提案 → 用户确认 → 系统执行」统一通道 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让问股 AI 能发起「记一笔买入/卖出」与「自选股增删」两类写操作提案，用户在聊天里点确认后才落库；同时把现有告警提案的硬编码约定收敛为统一的 `action_proposal` 通道。

**Architecture:** 提案工具一律只读，只返回 `{kind, summary, proposal}` 信封（`proposal` 即写接口请求体）；runner 按工具名集合拦截信封、发 `action_proposal` SSE 事件并把工具结果改写成摘要；前端 store 把提案挂在助手消息上，ChatPage 渲染一张通用卡片，确认时由**浏览器**在用户会话下调既有 REST 接口。AI 始终不持有写权限。

**Tech Stack:** Python 3.12 / FastAPI / pytest（仓库根）；React 19 + TypeScript + Vite + Vitest（`apps/dsa-web`）。

**Spec:** `docs/superpowers/specs/2026-09-22-ai-chat-confirmable-write-actions-design.md`

## Global Constraints

- **提交需要用户明确确认。** `AGENTS.md` §1 硬规则：未经明确确认不执行 `git commit` / `git tag` / `git push`。本计划每个 Task 末尾的 commit 步骤**必须先获得用户确认**；用户未确认就跳过 commit，改动留在工作区。
- commit message 用英文，**不得添加 `Co-Authored-By`**（`AGENTS.md` §1）。
- 不新增数据库表、不做数据迁移、不新增配置项、不改 `.env.example`。
- 不新增后端 endpoint：写入复用 `POST /api/v1/portfolio/trades`、`POST /api/v1/stocks/watchlist/add`、`POST /api/v1/stocks/watchlist/remove`、`POST /api/v1/alerts/rules`。
- 提案工具必须声明 `ToolPolicy.declared(read_only=True, side_effects=[], permissions=[], scope_dimensions=[], cancellation_safe=False)`——`cancellation_safe=False` 是它被挡在 Codex 只读面外的依据（`src/agent/tool_surface.py:177`）。
- 提案工具**绝不**调用任何写接口、绝不持久化任何东西。
- 卡片文案由**工具处理器**生成，不得由模型自由生成。
- 注释与文档用中文，与文件语境一致。
- 每个 Task 结束跑该 Task 指定的测试命令；Task 12 执行完整验证矩阵。
- 新增工具必须同时补 `runner._THINKING_TOOL_LABELS` 与 `api/v1/endpoints/agent.py` 的 `TOOL_DISPLAY_NAMES`，否则 `tests/test_agent_tool_label_parity.py` 转红。
- 新增 i18n key 必须同时补 `uiText.ts` 的 zh 与 en 两段，否则 `tsc -b` 失败。

## Task Overview

| Task | 内容 | 主要文件 |
| --- | --- | --- |
| 1 | 提取 `watchlist_service`（纯搬迁） | `src/services/watchlist_service.py`（新）、`api/v1/endpoints/stocks.py` |
| 2 | `propose_portfolio_trade` 工具 | `src/agent/tools/action_tools.py`（新） |
| 3 | `propose_watchlist_change` 工具 | `src/agent/tools/action_tools.py` |
| 4 | 注册两工具 + 两处标签表 | `src/agent/factory.py`、`src/agent/runner.py`、`api/v1/endpoints/agent.py` |
| 5 | 告警工具改信封 + runner 泛化为 `action_proposal` | `src/agent/tools/alert_tools.py`、`src/agent/runner.py` |
| 6 | 前端类型 + apply 分发 + 单测 | `apps/dsa-web/src/types/actionProposal.ts`（新）、`src/utils/actionProposal.ts`（新） |
| 7 | 前端 store 改造 + 单测 | `apps/dsa-web/src/stores/agentChatStore.ts` |
| 8 | ChatPage 卡片 + i18n + 页面测试 | `apps/dsa-web/src/pages/ChatPage.tsx`、`src/i18n/uiText.ts` |
| 9 | ~~bot 提示行按 kind 收尾~~——**已并入 Task 5 修复轮，作废** | `bot/commands/ask.py` |
| 10 | 系统提示词规则 7/8 | `src/agent/executor.py` |
| 11 | 文档同步 | `docs/agent-stream-events.md`、`docs/CHANGELOG.md` |
| 12 | 全量验证 + 手工 E2E | — |

---

### Task 1: 提取 `watchlist_service`（纯搬迁，行为不变）

自选列表的读写与股票代码校验目前是 `api/v1/endpoints/stocks.py` 的私有函数，`src/agent` 无法复用（反向 import API 层是分层倒置，还会把 FastAPI 拖进工具模块）。本 Task 把逻辑搬到 service，端点保留同名私有函数作为 HTTP 语义包装——5 个调用点一行都不用改。

**Files:**
- Create: `src/services/watchlist_service.py`
- Modify: `api/v1/endpoints/stocks.py:59-154`（`_watchlist_env_key`、`_read_watchlist_codes`、`_write_watchlist_codes`、`_list_named_watchlists`、`_STOCK_CODE_RE`、`_validate_and_normalize_stock_code`）
- Test: `tests/test_watchlist_service.py`

**Interfaces:**
- Consumes: `src.services.stock_list_parser.split_stock_list`；`data_provider.base.normalize_stock_code`（与端点当前用法一致，`api/v1/endpoints/stocks.py:48,50`）
- Produces:
  - `resolve_watchlist_key(list_name: Optional[str]) -> str`
  - `read_watchlist_codes(service: SystemConfigService, list_name: Optional[str] = None) -> List[str]`
  - `write_watchlist_codes(service: SystemConfigService, codes: List[str], list_name: Optional[str] = None) -> None`
  - `list_named_watchlists(service: SystemConfigService) -> List[Dict[str, Any]]`
  - `validate_and_normalize_stock_code(code: str) -> str` —— 不合法时抛 `ValueError`
  - `STOCK_CODE_RE: re.Pattern`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_watchlist_service.py`：

```python
"""Tests for the shared watchlist config helpers."""

import pytest

from src.services.watchlist_service import (
    list_named_watchlists,
    read_watchlist_codes,
    resolve_watchlist_key,
    validate_and_normalize_stock_code,
    write_watchlist_codes,
)


class _FakeConfigService:
    """只实现 service 真正用到的两个方法：get_config / update。"""

    def __init__(self, items=None, config_version="v1"):
        self._items = list(items or [])
        self._config_version = config_version
        self.updates = []

    def get_config(self, include_schema=True, mask_token="******"):
        return {"config_version": self._config_version, "items": list(self._items)}

    def update(self, config_version, items, mask_token="******", reload_now=False):
        self.updates.append(
            {"config_version": config_version, "items": items, "reload_now": reload_now}
        )
        for item in items:
            self._replace(item["key"], item["value"])


def test_resolve_watchlist_key_defaults_to_stock_list():
    assert resolve_watchlist_key(None) == "STOCK_LIST"
    assert resolve_watchlist_key("   ") == "STOCK_LIST"


def test_resolve_watchlist_key_keeps_cjk_and_upper_cases():
    assert resolve_watchlist_key("短线池") == "WATCHLIST_短线池"
    assert resolve_watchlist_key("my list") == "WATCHLIST_MY_LIST"


def test_read_watchlist_codes_splits_values():
    service = _FakeConfigService(items=[{"key": "STOCK_LIST", "value": "600519,AAPL"}])
    assert read_watchlist_codes(service) == ["600519", "AAPL"]


def test_read_watchlist_codes_returns_empty_for_unknown_named_list():
    service = _FakeConfigService(items=[{"key": "STOCK_LIST", "value": "600519"}])
    assert read_watchlist_codes(service, "短线池") == []


def test_write_watchlist_codes_persists_joined_value():
    service = _FakeConfigService(items=[{"key": "STOCK_LIST", "value": ""}])
    write_watchlist_codes(service, ["600519", "AAPL"])
    assert service.updates == [
        {
            "config_version": "v1",
            "items": [{"key": "STOCK_LIST", "value": "600519,AAPL"}],
            "reload_now": True,
        }
    ]


def test_list_named_watchlists_excludes_default_and_sorts():
    service = _FakeConfigService(
        items=[
            {"key": "STOCK_LIST", "value": "600519"},
            {"key": "WATCHLIST_B", "value": "600519,000001"},
            {"key": "WATCHLIST_A", "value": "AAPL"},
        ]
    )
    assert list_named_watchlists(service) == [
        {"name": "a", "key": "WATCHLIST_A", "count": 1},
        {"name": "b", "key": "WATCHLIST_B", "count": 2},
    ]


def test_validate_and_normalize_stock_code_accepts_supported_formats():
    assert validate_and_normalize_stock_code(" 600519 ") == "600519"
    assert validate_and_normalize_stock_code("hk00700").upper() == "HK00700"


def test_validate_and_normalize_stock_code_raises_on_empty():
    with pytest.raises(ValueError, match="不能为空"):
        validate_and_normalize_stock_code("   ")


def test_validate_and_normalize_stock_code_raises_on_bad_format():
    with pytest.raises(ValueError, match="不是合法的股票代码格式"):
        validate_and_normalize_stock_code("600519;;;")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_watchlist_service.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.services.watchlist_service'`

- [ ] **Step 3: 创建 `src/services/watchlist_service.py`**

逻辑逐行照搬 `api/v1/endpoints/stocks.py:59-154`，只把 `HTTPException` 换成 `ValueError`：

```python
# -*- coding: utf-8 -*-
"""自选队列（STOCK_LIST / WATCHLIST_<NAME>）的读写与股票代码校验。

这些逻辑原先只以私有函数形式存在于 ``api/v1/endpoints/stocks.py``，Agent 侧无法复用。
问股的 ``propose_watchlist_change`` 工具需要与写接口**同一份**校验与枚举能力，否则
「提案通过 ⇒ 接口接受」的契约不成立，故提取到这里；端点保留同名私有函数作为 HTTP
语义包装（把 ``ValueError`` 转成 400），调用点无需改动。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from data_provider.base import normalize_stock_code
from src.services.stock_list_parser import split_stock_list

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免运行时循环依赖
    from src.services.system_config_service import SystemConfigService

# Stock code validation patterns (aligned with frontend validateStockCode)
STOCK_CODE_RE = re.compile(
    r"^(?:\d{6}"                              # A-share 6-digit
    r"|(?:SH|SZ|BJ)\d{6}"                     # exchange-prefixed A-share
    r"|\d{6}\.(?:SH|SZ|SS|BJ)"                # exchange-suffixed A-share
    r"|\d{1,5}\.HK"                           # HK suffix format
    r"|HK\d{1,5}"                             # HK prefix format
    r"|\d{5}"                                 # bare 5-digit HK code
    r"|[A-Z]{1,5}(?:\.(?:US|[A-Z]))?"         # US ticker
    r")$",
    re.IGNORECASE,
)


def resolve_watchlist_key(list_name: Optional[str]) -> str:
    """Resolve the config-item key for a watchlist.

    Named lists map to ``WATCHLIST_<UPPER_NAME>``; the default (no name) maps to
    the legacy ``STOCK_LIST`` so existing clients keep their behavior unchanged.

    The key keeps CJK/字母/数字/下划线 (Unicode word chars), collapses any other
    character run into a single underscore, so Chinese list names like 「短线池」
    remain human-readable rather than being stripped to underscores.
    """
    if not list_name or not str(list_name).strip():
        return "STOCK_LIST"
    name = str(list_name).strip().upper()
    name = re.sub(r"[\W]+", "_", name)
    return f"WATCHLIST_{name}"


def read_watchlist_codes(service: "SystemConfigService", list_name: Optional[str] = None) -> List[str]:
    """Read watchlist codes as-is (no normalization).

    Reads ``WATCHLIST_<NAME>`` when ``list_name`` is given, else the legacy
    ``STOCK_LIST``. Named lists that do not exist yet resolve to an empty list.
    """
    key = resolve_watchlist_key(list_name)
    config_data = service.get_config(include_schema=False)
    stock_list_str = ""
    for item in config_data.get("items", []):
        if item.get("key") == key:
            stock_list_str = str(item.get("value", ""))
            break
    return split_stock_list(stock_list_str)


def write_watchlist_codes(
    service: "SystemConfigService", codes: List[str], list_name: Optional[str] = None
) -> None:
    """Persist watchlist codes as-is (no normalization)."""
    key = resolve_watchlist_key(list_name)
    config_data = service.get_config(include_schema=False)
    config_version = config_data.get("config_version", "")
    service.update(
        config_version=config_version,
        items=[{"key": key, "value": ",".join(codes)}],
        mask_token="******",
        reload_now=True,
    )


def list_named_watchlists(service: "SystemConfigService") -> List[Dict[str, Any]]:
    """Enumerate configured named watchlists (``WATCHLIST_<NAME>`` keys)."""
    config_data = service.get_config(include_schema=False)
    named = []
    for item in config_data.get("items", []):
        key = str(item.get("key", ""))
        if not key.startswith("WATCHLIST_"):
            continue
        name = key[len("WATCHLIST_"):].lower()
        codes = split_stock_list(str(item.get("value", "")))
        named.append({"name": name, "key": key, "count": len(codes)})
    named.sort(key=lambda x: x["name"])
    return named


def validate_and_normalize_stock_code(code: str) -> str:
    """Validate stock code format and return canonical form.

    Raises ``ValueError`` if the code does not match supported formats.
    """
    stripped = code.strip()
    if not stripped:
        raise ValueError("股票代码不能为空")
    if not STOCK_CODE_RE.match(stripped):
        raise ValueError(f"'{stripped}' 不是合法的股票代码格式")
    return normalize_stock_code(stripped)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_watchlist_service.py -q`
Expected: PASS（9 passed —— 本 Task 的测试文件含 9 个测试函数）

- [ ] **Step 5: 把端点的私有函数改成委托**

在 `api/v1/endpoints/stocks.py` 中，把 `_read_watchlist_codes` / `_write_watchlist_codes` / `_list_named_watchlists` / `_validate_and_normalize_stock_code` 的**函数体**改为委托，并删除 `_watchlist_env_key` 与 `_STOCK_CODE_RE`。函数名、签名、`HTTPException(400)` 语义全部保持不变，所有既有调用点无需改动。

**`_watchlist_env_key` 要整个删掉，不要保留。** 它原本的两个调用点都在你改成委托的那两个函数体内，因此这次改动会把它变成零调用点的死代码；仓库规则要求移除"由本次改动造成的孤儿"，留着只会让它读起来像"还有人在用"。删完顺带检查 `resolve_watchlist_key` 的 import 是否也随之失去用途。

```python
def _read_watchlist_codes(service: SystemConfigService, list_name: Optional[str] = None) -> list:
    """Read watchlist codes as-is (no normalization)."""
    return read_watchlist_codes(service, list_name)


def _write_watchlist_codes(service: SystemConfigService, codes: list, list_name: Optional[str] = None) -> None:
    """Persist watchlist codes as-is (no normalization)."""
    return write_watchlist_codes(service, codes, list_name)


def _list_named_watchlists(service: SystemConfigService) -> list:
    """Enumerate configured named watchlists (``WATCHLIST_<NAME>`` keys)."""
    return list_named_watchlists(service)


def _validate_and_normalize_stock_code(code: str) -> str:
    """Validate stock code format and return canonical form.

    Raises HTTPException(400) if the code does not match supported formats.
    """
    try:
        return validate_and_normalize_stock_code(code)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_stock_code", "message": str(exc)},
        )


def _watchlist_match_key(code: str) -> str:
    """Return the equivalence key used for watchlist add/remove matching."""
    normalized = normalize_stock_code(code.strip())
    if re.fullmatch(r"\d{5}", normalized):
        return f"HK{normalized}"
    return normalized.upper()
```

同时把这 5 个新函数加进 import 块（紧邻 `from src.services.stock_list_parser import split_stock_list`）：

```python
from src.services.watchlist_service import (
    list_named_watchlists,
    read_watchlist_codes,
    resolve_watchlist_key,
    validate_and_normalize_stock_code,
    write_watchlist_codes,
)
```

> 注意两点：一是 `_STOCK_CODE_RE` 已从端点删除，若删除后 `re` 仍被 `_watchlist_match_key` 使用，`import re` 保留不动；二是上面 import 块里的 `resolve_watchlist_key` 只服务于被删掉的 `_watchlist_env_key`，删完它对端点就失去用途——此时应从 import 块移除，只留另外三个函数。

- [ ] **Step 6: 跑端点与相关回归测试**

Run: `uv run python -m pytest tests/test_stocks_parse_import_api.py tests/test_watchlist_service.py -q`
Expected: PASS。若仓库存在自选相关测试（`uv run python -m pytest tests -q -k "watchlist"`），一并确认 PASS。

- [ ] **Step 7: 语法与静态检查**

Run: `uv run python -m py_compile src/services/watchlist_service.py api/v1/endpoints/stocks.py`
Expected: 无输出（成功）

- [ ] **Step 8: Commit（需用户确认）**

```bash
git add src/services/watchlist_service.py api/v1/endpoints/stocks.py tests/test_watchlist_service.py
git commit -m "refactor: extract shared watchlist service for agent reuse"
```

---

### Task 2: `propose_portfolio_trade` 工具

**Files:**
- Create: `src/agent/tools/action_tools.py`
- Test: `tests/test_action_tools.py`

**Interfaces:**
- Consumes: `src.services.portfolio_service.PortfolioService.list_accounts(include_inactive: bool = False) -> List[Dict[str, Any]]`（每项含 `id` / `name` / `is_active`，见 `src/services/portfolio_service.py:1769-1780`）
- Produces:
  - `_handle_propose_portfolio_trade(account_id, symbol, side, quantity, price, trade_date="", fee=0, tax=0, market="", note="", reason="") -> Dict[str, Any]`
  - `propose_portfolio_trade_tool: ToolDefinition`（`name="propose_portfolio_trade"`、`category="action"`）
  - `ALL_ACTION_TOOLS: List[ToolDefinition]`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_action_tools.py`：

```python
"""Tests for the confirmable write-action proposal tools."""

import json
from datetime import date
from types import SimpleNamespace
from unittest import mock

from src.agent.tools.action_tools import (
    _handle_propose_portfolio_trade,
    propose_portfolio_trade_tool,
)

_ACCOUNTS = [
    {"id": 1, "name": "A股主账户", "is_active": True},
    {"id": 2, "name": "港美股", "is_active": True},
    {"id": 3, "name": "已停用账户", "is_active": False},
]


def _patch_accounts(accounts=None):
    service = mock.MagicMock()
    service.list_accounts.return_value = _ACCOUNTS if accounts is None else accounts
    return mock.patch("src.services.portfolio_service.PortfolioService", return_value=service)


def test_tool_declares_proposal_policy():
    assert propose_portfolio_trade_tool.name == "propose_portfolio_trade"
    assert propose_portfolio_trade_tool.category == "action"
    assert propose_portfolio_trade_tool.policy.read_only is True
    # 不进 Codex 只读面：cancellation_safe 必须为 False
    assert propose_portfolio_trade_tool.policy.cancellation_safe is False


def test_propose_trade_builds_api_shaped_payload():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1,
            symbol="005827",
            side="buy",
            quantity=25900,
            price=1.6706,
            trade_date="2026-09-22",
        )
    assert "error" not in result
    assert result["kind"] == "portfolio_trade"
    assert result["proposal"] == {
        "account_id": 1,
        "symbol": "005827",
        "trade_date": "2026-09-22",
        "side": "buy",
        "quantity": 25900.0,
        "price": 1.6706,
        "fee": 0.0,
        "tax": 0.0,
    }
    assert result["summary"].startswith("在「A股主账户」记一笔买入 005827 25900 @ ¥1.6706")
    assert "2026-09-22" in result["summary"]


def test_propose_trade_defaults_trade_date_to_today():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert result["proposal"]["trade_date"] == date.today().isoformat()


def test_propose_trade_rejects_unknown_account_and_lists_choices():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=99, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert "不存在" in result["error"]
    assert "A股主账户" in result["error"]


def test_propose_trade_rejects_inactive_account():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=3, symbol="600519", side="buy", quantity=100, price=1800
        )
    assert "已停用" in result["error"]


def test_propose_trade_rejects_bad_side_quantity_price():
    with _patch_accounts():
        assert "side" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="hold", quantity=100, price=1800
        )["error"]
        assert "quantity" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=0, price=1800
        )["error"]
        assert "price" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=100, price=-1
        )["error"]


def test_propose_trade_rejects_bad_date_and_empty_symbol():
    with _patch_accounts():
        assert "trade_date" in _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="buy", quantity=1, price=1, trade_date="2026/09/22"
        )["error"]
        assert "symbol" in _handle_propose_portfolio_trade(
            account_id=1, symbol="  ", side="buy", quantity=1, price=1
        )["error"]


def test_propose_trade_includes_amount_and_reason():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1,
            symbol="005827",
            side="buy",
            quantity=25900,
            price=1.6706,
            trade_date="2026-09-22",
            fee=5,
            reason="定投补仓",
        )
    # 25900 × 1.6706 = 43268.54，加 5 元手续费 = 43273.54
    assert "约 ¥43273.54" in result["summary"]
    assert result["summary"].endswith("（定投补仓）")


def test_propose_trade_envelope_is_json_serializable():
    with _patch_accounts():
        result = _handle_propose_portfolio_trade(
            account_id=1, symbol="600519", side="sell", quantity=100, price=1800
        )
    json.dumps(result, ensure_ascii=False)
    assert result["summary"].startswith("在「A股主账户」记一笔卖出")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_action_tools.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'src.agent.tools.action_tools'`

- [ ] **Step 3: 创建 `src/agent/tools/action_tools.py`**

```python
# -*- coding: utf-8 -*-
"""Agent 工具：把需要用户确认的写操作包装成「提案」。

与 ``alert_tools.propose_alert`` 同一套约定：工具本身**只读**，只做校验并返回
``{"kind", "summary", "proposal"}`` 信封，其中 ``proposal`` 就是对应写接口的请求体。
runner 读到该信封后发出 ``action_proposal`` SSE 事件，真正的写入由用户在 Web 端确认后、
用浏览器会话调用既有 REST 接口完成。工具从不持久化任何东西、从不调用写接口，因此 AI
始终不持有写权限。

``cancellation_safe=False`` 让本模块的工具被 ``tool_surface`` 挡在 Codex 只读面外
（与 ``propose_alert`` 一致）。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy

logger = logging.getLogger(__name__)

_PROPOSAL_POLICY = ToolPolicy.declared(
    read_only=True,
    side_effects=[],
    permissions=[],
    scope_dimensions=[],
    # 与 propose_alert 一致：交互式确认流工具，不适用于 Codex 只读面。
    cancellation_safe=False,
)

SUPPORTED_SIDES = ("buy", "sell")
SUPPORTED_MARKETS = ("cn", "hk", "us", "jp", "kr", "tw")


def _fmt_num(value: Any) -> str:
    """Format a number without a trailing ``.0`` for whole values."""
    number = float(value)
    return str(int(number)) if number.is_integer() else str(number)


def _fmt_amount(value: float) -> str:
    """Format a cash amount with two decimals."""
    return f"{value:.2f}"


def _check_number(
    value: Any, field: str, *, allow_zero: bool
) -> Tuple[Optional[float], Optional[str]]:
    """Validate a numeric field; returns (value, error)."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, f"{field} 必须是数字，收到 {value!r}"
    if allow_zero and number < 0:
        return None, f"{field} 必须 >= 0，收到 {number}"
    if not allow_zero and number <= 0:
        return None, f"{field} 必须 > 0，收到 {number}"
    return number, None


# ============================================================
# propose_portfolio_trade
# ============================================================

def _format_account_choices(accounts: List[Dict[str, Any]]) -> str:
    """Render the account list as a one-line hint so the model can self-correct."""
    active = [a for a in accounts if a.get("is_active")]
    if not active:
        return "当前没有已激活的账户，请先在持仓页创建账户"
    return "；".join(f"{int(a['id'])}: {a.get('name') or '未命名'}" for a in active)


def _resolve_account(account_id: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Resolve ``account_id`` to an active account; returns (account, error)."""
    try:
        parsed_id = int(account_id)
    except (TypeError, ValueError):
        return None, f"account_id 必须是整数，收到 {account_id!r}"

    try:
        from src.services.portfolio_service import PortfolioService
    except Exception as exc:
        logger.warning("propose_portfolio_trade unavailable: %s", exc)
        return None, f"持仓模块不可用: {exc}"

    try:
        accounts = PortfolioService().list_accounts(include_inactive=True)
    except Exception as exc:
        logger.warning("propose_portfolio_trade list_accounts failed: %s", exc)
        return None, f"读取账户失败: {exc}"

    account = next((a for a in accounts if int(a.get("id", -1)) == parsed_id), None)
    if account is None:
        return None, (
            f"account_id {parsed_id} 不存在；可用账户：{_format_account_choices(accounts)}"
        )
    if not account.get("is_active"):
        return None, (
            f"账户「{account.get('name') or parsed_id}」({parsed_id}) 已停用，不能录入交易；"
            f"可用账户：{_format_account_choices(accounts)}"
        )
    return account, None


def _handle_propose_portfolio_trade(
    account_id: Any,
    symbol: str,
    side: str,
    quantity: Any,
    price: Any,
    trade_date: str = "",
    fee: Any = 0,
    tax: Any = 0,
    market: str = "",
    note: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """Validate one trade entry and return a proposal; persists nothing.

    校验逐条对齐 ``PortfolioTradeCreateRequest``（``api/v1/schemas/portfolio.py:45-57``），
    因此「提案通过」意味着确认时的 ``POST /api/v1/portfolio/trades`` 不会因参数被拒。
    """
    account, err = _resolve_account(account_id)
    if err:
        return {"error": err}

    norm_side = str(side or "").strip().lower()
    if norm_side not in SUPPORTED_SIDES:
        return {"error": f"side 必须是 {'/'.join(SUPPORTED_SIDES)}，收到 {side!r}"}

    norm_symbol = str(symbol or "").strip()
    if not norm_symbol:
        return {"error": "symbol 不能为空"}
    if len(norm_symbol) > 16:
        return {"error": f"symbol 过长（最多 16 字符）: {norm_symbol!r}"}

    norm_quantity, err = _check_number(quantity, "quantity", allow_zero=False)
    if err:
        return {"error": err}
    norm_price, err = _check_number(price, "price", allow_zero=False)
    if err:
        return {"error": err}
    norm_fee, err = _check_number(fee, "fee", allow_zero=True)
    if err:
        return {"error": err}
    norm_tax, err = _check_number(tax, "tax", allow_zero=True)
    if err:
        return {"error": err}

    raw_date = str(trade_date or "").strip()
    if raw_date:
        try:
            norm_date = date.fromisoformat(raw_date)
        except ValueError:
            return {"error": f"trade_date 必须是 YYYY-MM-DD，收到 {raw_date!r}"}
    else:
        # 记账默认今天；用户要记历史交易必须显式给出日期，且日期会显示在卡片上。
        norm_date = date.today()

    norm_market = str(market or "").strip().lower() or None
    if norm_market and norm_market not in SUPPORTED_MARKETS:
        return {"error": f"market 必须是 {list(SUPPORTED_MARKETS)} 之一，收到 {market!r}"}

    proposal: Dict[str, Any] = {
        "account_id": int(account["id"]),
        "symbol": norm_symbol,
        "trade_date": norm_date.isoformat(),
        "side": norm_side,
        "quantity": norm_quantity,
        "price": norm_price,
        "fee": norm_fee,
        "tax": norm_tax,
    }
    if norm_market:
        proposal["market"] = norm_market
    norm_note = str(note or "").strip()
    if norm_note:
        proposal["note"] = norm_note[:255]

    action_label = "买入" if norm_side == "buy" else "卖出"
    amount = norm_quantity * norm_price + norm_fee + norm_tax
    summary = (
        f"在「{account.get('name') or '未命名账户'}」记一笔{action_label} "
        f"{norm_symbol} {_fmt_num(norm_quantity)} @ ¥{_fmt_num(norm_price)}"
        f"（{norm_date.isoformat()}，约 ¥{_fmt_amount(amount)}）"
    )
    norm_reason = str(reason or "").strip()
    if norm_reason:
        summary = f"{summary}（{norm_reason}）"

    return {"kind": "portfolio_trade", "summary": summary, "proposal": proposal}


propose_portfolio_trade_tool = ToolDefinition(
    name="propose_portfolio_trade",
    description=(
        "当用户要求把某笔买入/卖出记入持仓账户，或对话中已确认份额、成本、账户时，"
        "用本工具生成一条持仓录入提案交给用户确认。只生成提案，不写入任何数据。\n"
        "account_id 必填：先用 get_portfolio_snapshot 读取账户的 account_id / account_name；"
        "多个账户而用户未指明时必须先追问用户，不要猜。\n"
        "trade_date 省略时按今天记账；用户要补记历史交易必须显式给出 YYYY-MM-DD。"
    ),
    parameters=[
        ToolParameter(
            name="account_id",
            type="integer",
            description="目标账户 id，取自 get_portfolio_snapshot 返回的 accounts[].account_id",
            required=True,
        ),
        ToolParameter(name="symbol", type="string", description="股票/基金代码，如 600519 / 005827 / HK00700 / AAPL", required=True),
        ToolParameter(
            name="side",
            type="string",
            description="交易方向",
            required=True,
            enum=list(SUPPORTED_SIDES),
        ),
        ToolParameter(name="quantity", type="number", description="成交数量（股或份），必须大于 0", required=True),
        ToolParameter(name="price", type="number", description="成交单价/净值，必须大于 0", required=True),
        ToolParameter(
            name="trade_date",
            type="string",
            description="成交日期 YYYY-MM-DD；省略则按今天记账",
            required=False,
        ),
        ToolParameter(name="fee", type="number", description="手续费，默认 0", required=False, default=0),
        ToolParameter(name="tax", type="number", description="税费，默认 0", required=False, default=0),
        ToolParameter(
            name="market",
            type="string",
            description="市场；省略时由后端按账户与代码推断",
            required=False,
            enum=list(SUPPORTED_MARKETS),
        ),
        ToolParameter(name="note", type="string", description="可选备注，写入交易记录", required=False),
        ToolParameter(name="reason", type="string", description="可选提案理由，用于在卡片上向用户说明", required=False),
    ],
    handler=_handle_propose_portfolio_trade,
    category="action",
    policy=_PROPOSAL_POLICY,
)


ALL_ACTION_TOOLS: List[ToolDefinition] = [propose_portfolio_trade_tool]
```

> `Callable` 在本 Task 用不到时不要写进 import（下面的 Task 3 也不会用到）；上面 import 行按实际使用保留 `Any, Dict, List, Optional, Tuple`，不写 `Callable`。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_action_tools.py -q`
Expected: PASS（9 passed）

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/agent/tools/action_tools.py tests/test_action_tools.py
git commit -m "feat(agent): add propose_portfolio_trade confirmation tool"
```

---

> **Task 2 收尾修正记录（审查后追加，勿删）。**
> 1. `SUPPORTED_SIDES` / `SUPPORTED_MARKETS` 是**复制品**，不是"service 层没有对应集合"——
>    `src/services/portfolio_service.py:35,38` 已有 `VALID_MARKETS` / `VALID_SIDES`。复制它们
>    违反了 AGENTS.md「不新增平行实现」，但**不要**改成模块级导入：`enum=` 在 import 时求值，
>    而 `portfolio_service` 会拖进 yfinance/config/repos，破坏本模块刻意保持的懒导入
>    （`src/agent/tools/data_tools.py:567` 的既有模式）。改用一条三方一致性测试
>    （工具元组 == Pydantic `Literal` == service 集合）拿同样的保护，零导入代价。
> 2. `_check_number` 必须拒绝非有限浮点（`math.isfinite`）。`json.loads('{"price": 1e999}')`
>    得到 `inf`，而 `inf <= 0` / `nan <= 0` 都是 `False`，守卫对二者完全失效；`inf` 会被工具
>    **和** Pydantic 双双接受，最后 `json.dumps` 输出非标准 JSON `Infinity`，前端 `JSON.parse` 失败。
> 3. `list_accounts` 的 `include_inactive=True` 是 `已停用` 分支的**唯一**可达性来源
>    （`src/repositories/portfolio_repo.py:84-92` 在 `False` 时过滤掉停用账户），必须断言该调用参数。
> 4. summary 的金额不能硬编码 `¥`：成交价是**标的**的计价币种，可能不等于账户本位币
>    （美元账户买 A 股，价是 CNY）。优先级为 `proposal.currency` → `account.base_currency` →
>    仅数字，且一律用币种**代码**（`¥` 在 CNY/JPY 间本身有歧义，而 `market` 枚举里确有 `jp`）。
>    卖出金额是 `qty*price - fee - tax`（费用是**扣掉**而非加上），文案用 `约支出` / `约收入` 区分方向。
> 5. **新增了一个工具参数 `currency`（可选，string）**，初版计划的参数列表里没有。理由：`currency`
>    本来就是 `PortfolioTradeCreateRequest` 的字段与 DB 的 `String(8)` 列，这个参数暴露的是既有契约；
>    而上面第 4 点的币种优先级里"模型显式给的 `currency`"只能通过参数表达，没有它那条分支不可达，
>    模型也就无法纠正计价币种不匹配的情况。必须按 schema 校验长度 3–8，且只在非空时写入 `proposal`。
>    因此本 Task 的 handler 签名是
>    `_handle_propose_portfolio_trade(account_id, symbol, side, quantity, price, trade_date="", fee=0, tax=0, market="", currency="", note="", reason="")`。
>
> 复查后遗留的**非阻断**项（刻意不改）：金额在 `quantity=price=1e308` 这类荒谬但有限的输入下会溢出成
> `约支出 inf`（`proposal` 本身仍有限且合法，且该串落在 JSON 字符串里，不会产生非标准 `Infinity` token）；
> 手续费超过成交额时卖出会显示 `约收入 -4.00`；`currency` 只校验长度不校验字符集（与端点同样宽松，
> 刻意不比端点更严）。

### Task 3: `propose_watchlist_change` 工具

**Files:**
- Modify: `src/agent/tools/action_tools.py`（追加）
- Test: `tests/test_action_tools.py`（追加）

**Interfaces:**
- Consumes: `src.services.watchlist_service.validate_and_normalize_stock_code`、`src.services.watchlist_service.list_named_watchlists`（Task 1）
- Produces:
  - `_handle_propose_watchlist_change(action, symbol, list_name="", reason="") -> Dict[str, Any]`
  - `propose_watchlist_change_tool: ToolDefinition`（`name="propose_watchlist_change"`）
  - `ALL_ACTION_TOOLS` 变为两个元素

> **⚠️ 工具参数名必须是 `symbol`，不能叫 `stock_code`（改自初版计划）。**
> `src/agent/tools/execution.py:189-193` 的 `_is_stock_scoped_tool` 只按**参数名**判断：
> `any(param.name == "stock_code" ...)`。一旦工具声明名为 `stock_code` 的参数，
> `_guard_tool_stock_scope`（`:210-240`）就会在单股会话里对不在该会话范围内的代码**硬阻断**——
> 返回 `{"error": "stock_scope_violation", ..., "retriable": False}`，模型连重试都不行。
> 于是用户在 600519 的会话里说「把宁德时代加入自选」会被静默拒绝，而那是用户明确点名的标的。
> 既有两个提案工具（`propose_alert` 用 `target`、`propose_portfolio_trade` 用 `symbol`）都不受该守卫
> 约束，本工具应与之一致。
>
> 实现上：handler 参数名与 `ToolParameter(name=...)` 都用 `symbol`；**`proposal` 里仍写 `stock_code`**，
> 以匹配 `WatchlistRequest`（`api/v1/schemas/history.py:389-393`）的请求体。参数名与请求体字段名
> 不同是刻意的。
>
> **因此本 Task 下面的代码块与测试里出现的每一次 `stock_code=` 都要改成 `symbol=`**（handler 定义
> 与所有调用处），而 `proposal` 断言里的 `{"stock_code": ...}` 保持不变。

- [ ] **Step 1: 写失败测试**

在 `tests/test_action_tools.py` 末尾追加：

```python
from src.agent.tools.action_tools import (
    ALL_ACTION_TOOLS,
    _handle_propose_watchlist_change,
    propose_watchlist_change_tool,
)


def _patch_named_lists(names=("短线池",)):
    service = mock.MagicMock()

    def _get_config(include_schema=True, mask_token="******"):
        # 记录 include_schema：真实 get_config(include_schema=True) 返回的是带 schema/掩码的
        # 结构，不是裸 items；若实现里漏传 False，只有断言过这个参数才抓得到。
        service.get_config_calls.append(include_schema)
        return {
            "config_version": "v1",
            "items": [{"key": f"WATCHLIST_{n.upper()}", "value": ""} for n in names],
        }

    service.get_config_calls = []
    service.get_config.side_effect = _get_config
    return mock.patch("src.services.system_config_service.SystemConfigService", return_value=service)


def test_watchlist_tool_parameter_is_named_symbol_not_stock_code():
    """参数名必须是 symbol。

    叫 stock_code 会被 `_is_stock_scoped_tool`（`src/agent/tools/execution.py:189-193`）视为受
    股票范围约束的工具，`_guard_tool_stock_scope` 会在单股会话里对跨标的请求硬阻断
    （`retriable: False`，模型无法绕过），从而拒绝用户明确点名的标的。这条断言把该决定钉住，
    防止将来有人「为了和请求体字段名对齐」把它改回去。
    """
    declared = {param.name for param in propose_watchlist_change_tool.parameters}
    assert "symbol" in declared
    assert "stock_code" not in declared


def test_watchlist_tool_registered_in_all_action_tools():
    names = [t.name for t in ALL_ACTION_TOOLS]
    assert names == ["propose_portfolio_trade", "propose_watchlist_change"]
    assert propose_watchlist_change_tool.category == "action"
    assert propose_watchlist_change_tool.policy.cancellation_safe is False


def test_watchlist_add_proposal_shape():
    result = _handle_propose_watchlist_change(action="add", stock_code=" 600519 ")
    assert "error" not in result
    assert result["kind"] == "watchlist_add"
    assert result["proposal"] == {"stock_code": "600519", "list_name": None}
    assert result["summary"] == "把「600519」加入自选"


def test_watchlist_remove_proposal_shape():
    result = _handle_propose_watchlist_change(action="remove", stock_code="AAPL")
    assert result["kind"] == "watchlist_remove"
    assert result["summary"] == "把「AAPL」移出自选"


def test_watchlist_named_list_is_echoed_in_summary():
    with _patch_named_lists():
        result = _handle_propose_watchlist_change(
            action="add", stock_code="600519", list_name="短线池"
        )
    assert "error" not in result
    assert result["proposal"] == {"stock_code": "600519", "list_name": "短线池"}
    assert result["summary"] == "把「600519」加入自选列表「短线池」"


def test_watchlist_rejects_unknown_named_list_and_lists_valid_names():
    with _patch_named_lists(names=("短线池", "长线池")):
        result = _handle_propose_watchlist_change(
            action="add", stock_code="600519", list_name="编的池子"
        )
    assert "不存在" in result["error"]
    assert "短线池" in result["error"]
    assert "长线池" in result["error"]


def test_watchlist_rejects_bad_action_and_bad_code():
    assert "action" in _handle_propose_watchlist_change(action="toggle", stock_code="600519")["error"]
    assert "不是合法的股票代码格式" in _handle_propose_watchlist_change(
        action="add", stock_code="600519;;;"
    )["error"]


def test_watchlist_appends_reason():
    result = _handle_propose_watchlist_change(
        action="add", stock_code="600519", reason="突破前高"
    )
    assert result["summary"].endswith("（突破前高）")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_action_tools.py -q -k watchlist`
Expected: FAIL —— `ImportError: cannot import name 'propose_watchlist_change_tool'`

- [ ] **Step 3: 实现工具**

在 `src/agent/tools/action_tools.py` 中，把 `ALL_ACTION_TOOLS` 那一行**替换**为下面的内容（`_PROPOSAL_POLICY` / `_fmt_num` 等已存在，直接复用）：

```python
SUPPORTED_WATCHLIST_ACTIONS = ("add", "remove")


def _resolve_named_list(list_name: str) -> Tuple[Optional[str], Optional[str]]:
    """Validate a named watchlist; returns (raw_name, error)."""
    raw = str(list_name or "").strip()
    if not raw:
        return None, None
    try:
        from src.services.system_config_service import SystemConfigService
    except Exception as exc:
        logger.warning("propose_watchlist_change unavailable: %s", exc)
        return None, f"配置模块不可用: {exc}"

    try:
        named = list_named_watchlists(SystemConfigService())
    except Exception as exc:
        logger.warning("propose_watchlist_change list_named_watchlists failed: %s", exc)
        return None, f"读取自选列表失败: {exc}"

    # 用与写接口相同的 key 解析规则比对：否则「My List」这类名字会因大小写/分隔符差异被误判为不存在。
    wanted_key = resolve_watchlist_key(raw)
    if any(n["key"] == wanted_key for n in named):
        return raw, None

    available = [n["name"] for n in named]
    if not available:
        return None, f"自选列表「{raw}」不存在；当前没有配置任何命名自选列表"
    return None, f"自选列表「{raw}」不存在；可用列表：{'、'.join(available)}"


def _handle_propose_watchlist_change(
    action: str,
    symbol: str,
    list_name: str = "",
    reason: str = "",
) -> Dict[str, Any]:
    """Validate one watchlist add/remove and return a proposal; persists nothing."""
    norm_action = str(action or "").strip().lower()
    if norm_action not in SUPPORTED_WATCHLIST_ACTIONS:
        return {
            "error": f"action 必须是 {'/'.join(SUPPORTED_WATCHLIST_ACTIONS)}，收到 {action!r}"
        }

    try:
        norm_code = validate_and_normalize_stock_code(str(symbol or ""))
    except ValueError as exc:
        return {"error": str(exc)}

    norm_list_name, err = _resolve_named_list(list_name)
    if err:
        return {"error": err}

    target = f"自选列表「{norm_list_name}」" if norm_list_name else "自选"
    verb = "加入" if norm_action == "add" else "移出"
    summary = f"把「{norm_code}」{verb}{target}"
    norm_reason = str(reason or "").strip()
    if norm_reason:
        summary = f"{summary}（{norm_reason}）"

    return {
        "kind": "watchlist_add" if norm_action == "add" else "watchlist_remove",
        "summary": summary,
        "proposal": {"stock_code": norm_code, "list_name": norm_list_name},
    }


propose_watchlist_change_tool = ToolDefinition(
    name="propose_watchlist_change",
    description=(
        "当用户要求把某只股票加入自选或从自选移除时，用本工具生成提案交给用户确认。"
        "只生成提案，不写入任何配置。\n"
        "list_name 省略时作用于默认自选（STOCK_LIST）；指定命名列表时必须命中已存在的列表，"
        "否则会报错并列出可用列表名。"
    ),
    parameters=[
        ToolParameter(
            name="action",
            type="string",
            description="add 加入自选 / remove 移出自选",
            required=True,
            enum=list(SUPPORTED_WATCHLIST_ACTIONS),
        ),
        ToolParameter(
            # 参数名必须是 symbol 而不是 stock_code：叫 stock_code 会让
            # _is_stock_scoped_tool 把它当成受股票范围约束的工具并硬阻断跨标的请求。
            name="symbol",
            type="string",
            description="股票代码，支持 600519 / HK00700 / AAPL 等格式",
            required=True,
        ),
        ToolParameter(
            name="list_name",
            type="string",
            description="可选命名自选列表名；省略作用于默认 STOCK_LIST",
            required=False,
        ),
        ToolParameter(name="reason", type="string", description="可选提案理由，用于在卡片上向用户说明", required=False),
    ],
    handler=_handle_propose_watchlist_change,
    category="action",
    policy=_PROPOSAL_POLICY,
)


ALL_ACTION_TOOLS: List[ToolDefinition] = [
    propose_portfolio_trade_tool,
    propose_watchlist_change_tool,
]
```

并在文件顶部的 import 中补上 watchlist_service 的三个函数：

```python
from src.services.watchlist_service import (
    list_named_watchlists,
    resolve_watchlist_key,
    validate_and_normalize_stock_code,
)
```

> 说明：`_resolve_named_list` 内部自行构造 `SystemConfigService()`。**注意这与端点的用法并不相同**——端点是通过 `Depends(get_system_config_service)`（`api/deps.py:72-78`）注入应用级缓存实例，而工具每次提案新建一个实例、只做一次 `get_config` 读。成本同量级（端点也是每请求读一次），所以实现没问题，但别把它当成"复用了端点的做法"。测试通过 `mock.patch("src.services.system_config_service.SystemConfigService")` 注入假 service——函数体内的 import 发生在调用时，因此 patch 生效。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_action_tools.py -q`
Expected: PASS（16 passed）

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/agent/tools/action_tools.py tests/test_action_tools.py
git commit -m "feat(agent): add propose_watchlist_change confirmation tool"
```

---

### Task 4: 注册两工具 + 两处标签表

**Files:**
- Modify: `src/agent/factory.py:193-220`
- Modify: `src/agent/runner.py:70-91`（`_THINKING_TOOL_LABELS`）
- Modify: `api/v1/endpoints/agent.py:25-44`（`TOOL_DISPLAY_NAMES`）
- Test: `tests/test_action_tools.py`（追加注册断言）

**Interfaces:**
- Consumes: `src.agent.tools.action_tools.ALL_ACTION_TOOLS`（Task 2/3）
- Produces: 注册表从 19 个工具增至 **21** 个；两处标签表各多 2 条

> **注意工具基数：改动前注册表是 19 个工具，不是 21 个。** 初版计划写"23"是错的——它把一份"21 个工具"的说法
> 直接加了 2，而那份清单本身相加只有 19（7 data + 4 analysis + 2 search + 2 market + 3 backtest + 1 alert）。
> 实施时以 `len(get_tool_registry().list_names())` 的**实测值**为准（改动前 19 → 改动后 21），不要为了凑数字去加工具。

- [ ] **Step 1: 写失败测试**

在 `tests/test_action_tools.py` 末尾追加：

```python
def test_action_tools_are_registered_with_chinese_labels():
    from api.v1.endpoints.agent import TOOL_DISPLAY_NAMES
    from src.agent.factory import get_tool_registry
    from src.agent.runner import _THINKING_TOOL_LABELS

    registry = get_tool_registry()
    for name in ("propose_portfolio_trade", "propose_watchlist_change"):
        tool = registry.resolve(name)
        assert tool is not None, f"{name} 未注册"
        assert _THINKING_TOOL_LABELS.get(name), f"{name} 缺少思考过程标签"
        assert TOOL_DISPLAY_NAMES.get(name), f"{name} 缺少中文展示名"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_action_tools.py -q -k registered_with_chinese`
Expected: FAIL —— `assert tool is not None`（`propose_portfolio_trade 未注册`）

- [ ] **Step 3: 在 `factory.py` 注册**

`src/agent/factory.py` 的 `get_tool_registry()` 中，import 块加一行、拼接元组加一项：

```python
    from src.agent.tools.alert_tools import ALL_ALERT_TOOLS
    from src.agent.tools.action_tools import ALL_ACTION_TOOLS
```

```python
    for tool_fn in (
        ALL_DATA_TOOLS
        + ALL_ANALYSIS_TOOLS
        + ALL_SEARCH_TOOLS
        + ALL_MARKET_TOOLS
        + ALL_BACKTEST_TOOLS
        + ALL_ALERT_TOOLS
        + ALL_ACTION_TOOLS
    ):
        registry.register(tool_fn)
```

- [ ] **Step 4: 补两处标签表**

`src/agent/runner.py` 的 `_THINKING_TOOL_LABELS`（`:70-91`）末尾追加**两行**：

```python
    "propose_portfolio_trade": "持仓录入提案生成",
    "propose_watchlist_change": "自选变更提案生成",
```

`api/v1/endpoints/agent.py` 的 `TOOL_DISPLAY_NAMES`（`:25-44`）末尾追加**两行**：

```python
    "propose_portfolio_trade": "生成持仓录入提案",
    "propose_watchlist_change": "生成自选变更提案",
```

> **不要重复添加 `propose_alert`。** 它已经存在于两张表里（`src/agent/runner.py:85` = `"告警提案生成"`、
> `api/v1/endpoints/agent.py:44` = `"生成告警提案"`）。初版计划把三行一起写了，照抄会写出重复的字典键
> （Python 静默覆盖，值相同所以无害，但读起来像是漏改），也会让"各多 2 条"的验收标准对不上。

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_action_tools.py tests/test_agent_tool_label_parity.py -q`
Expected: PASS（含标签一致性 4 条）

- [ ] **Step 6: 确认工具总数没有意外漂移**

Run: `uv run python -c "from src.agent.factory import get_tool_registry; r=get_tool_registry(); print(len(r.list_names())); print(sorted(r.list_names()))"`
Expected: `21`（改动前实测 19）且列表含 `propose_alert`、`propose_portfolio_trade`、`propose_watchlist_change`

- [ ] **Step 7: Commit（需用户确认）**

```bash
git add src/agent/factory.py src/agent/runner.py api/v1/endpoints/agent.py tests/test_action_tools.py
git commit -m "feat(agent): register proposal tools and their Chinese labels"
```

---

> **Task 4 → Task 5 → Task 8 之间的中间态（审查结论，勿删）。**
> Task 4 之后工具已注册但 runner 还不拦截它们，所以它们的提案不会变成 `action_proposal` 事件、
> 也就不会有卡片。审查确认这个窗口**安全**：工具没有任何写权限（唯一写者是浏览器，而那条路要到
> Task 6/8 才存在），`tool_done` 事件只带 `{tool, success, duration}`（`runner.py:759,805`）不泄原始
> JSON 给客户端，`_maybe_emit_alert_proposal` 对新名字是 `tc.name != "propose_alert"` 早退、不会半处理。
> 唯一诚实的隐患是模型会把未被改写的信封当成已完成的结果，从而对用户声称"已记录"——但工具描述明写
> `只生成提案，不写入任何数据`，且即便如此也不改任何数据。
>
> **关键结论：这个中间态的闸门是 Task 8（ChatPage 卡片），不是 Task 5。** 把 Task 5 提前不会改善任何
> 用户可见行为——事件发出来但 store（Task 7）与 UI（Task 6/8）仍会忽略它，用户看到的还是同一个死路。
> 因此**不要在 Task 8 完成前把这条分支交付出去**；未确认的提案没有卡片就没有落库入口。

### Task 5: 告警工具改信封 + runner 泛化为 `action_proposal`

这是本次唯一的机制改动点，也是唯一动了已上线代码的一步。

**Files:**
- Modify: `src/agent/tools/alert_tools.py:133-146`（`_handle_propose_alert` 返回值）
- Modify: `src/agent/runner.py:613-643`（常量与发射器）、`:758`、`:804`（两个调用点）
- Modify: `bot/commands/ask.py:268`（**事件名必须与 runner 同一次改完**）
- Test: `tests/test_alert_tools.py`（改写发射器用例）、`tests/test_action_tools.py`（追加）

> **为什么 bot 这一行必须并在本 Task：** `bot/commands/ask.py:268` 也在按事件名判断
> （`if event.get("type") != "alert_proposal"`）。只改 runner 不改它，bot 的"检测到可创建的预警"
> 提示会**静默消失**——那就成了改契约却留下消费者。事件名是跨进程契约，重命名必须原子完成。
> 本 Task 只改**名字**这一处判断（提示文案维持原样），kind-aware 的文案与变量改名留给 Task 9。

**Interfaces:**
- Consumes: 三工具返回 `{kind, summary, proposal}` 信封
- Produces:
  - `runner._PROPOSAL_TOOL_NAMES: frozenset[str]`
  - `runner._ALLOWED_PROPOSAL_KINDS: frozenset[str]`
  - `runner._maybe_emit_action_proposal(tc, result_str, progress_callback, step) -> str`
  - 事件 `{"type": "action_proposal", "kind": ..., "summary": ..., "proposal": {...}}`

- [ ] **Step 1: 写失败测试**

改写 `tests/test_alert_tools.py` 的第 6 行 import 与第 69-97 行三个用例：

```python
from src.agent.runner import _maybe_emit_action_proposal
```

```python
def test_maybe_emit_action_proposal_emits_event_and_rewrites_result():
    events = []
    result = _handle_propose_alert(target="600519", alert_type="price_cross", parameters={"price": 1800})
    tc = SimpleNamespace(name="propose_alert")
    out = _maybe_emit_action_proposal(tc, json.dumps(result, ensure_ascii=False), events.append, step=3)

    assert len(events) == 1
    event = events[0]
    assert event["type"] == "action_proposal"
    assert event["kind"] == "alert"
    assert event["summary"] == result["summary"]
    assert event["proposal"] == result["proposal"]
    # LLM-facing result is rewritten to a short note, not the raw proposal JSON.
    assert json.loads(out) == {"message": result["summary"]}


def test_maybe_emit_action_proposal_ignores_non_propose_tool():
    events = []
    tc = SimpleNamespace(name="get_realtime_quote")
    out = _maybe_emit_action_proposal(tc, '{"quote": 1}', events.append, step=3)
    assert out == '{"quote": 1}'
    assert events == []


def test_maybe_emit_action_proposal_ignores_error_result():
    events = []
    tc = SimpleNamespace(name="propose_alert")
    out = _maybe_emit_action_proposal(tc, json.dumps({"error": "x"}), events.append, step=3)
    assert json.loads(out) == {"error": "x"}
    assert events == []
```

并在 `tests/test_action_tools.py` 末尾追加信封防御的用例：

```python
from src.agent.runner import _maybe_emit_action_proposal


def test_emitter_rejects_unknown_kind():
    events = []
    tc = SimpleNamespace(name="propose_portfolio_trade")
    raw = json.dumps({"kind": "drop_table", "summary": "x", "proposal": {"a": 1}})
    out = _maybe_emit_action_proposal(tc, raw, events.append, step=1)
    assert out == raw
    assert events == []


def test_emitter_rejects_malformed_summary_and_proposal():
    events = []
    tc = SimpleNamespace(name="propose_portfolio_trade")
    for payload in (
        {"kind": "portfolio_trade", "summary": 1, "proposal": {"a": 1}},
        {"kind": "portfolio_trade", "summary": "x", "proposal": "nope"},
        {"kind": ["portfolio_trade"], "summary": "x", "proposal": {"a": 1}},
    ):
        raw = json.dumps(payload)
        assert _maybe_emit_action_proposal(tc, raw, events.append, step=1) == raw
    assert events == []


def test_emitter_accepts_trade_and_watchlist_envelopes():
    events = []
    tc = SimpleNamespace(name="propose_watchlist_change")
    raw = json.dumps(
        {
            "kind": "watchlist_add",
            "summary": "把「600519」加入自选",
            "proposal": {"stock_code": "600519", "list_name": None},
        },
        ensure_ascii=False,
    )
    out = _maybe_emit_action_proposal(tc, raw, events.append, step=2)
    assert events[0]["type"] == "action_proposal"
    assert events[0]["kind"] == "watchlist_add"
    assert json.loads(out) == {"message": "把「600519」加入自选"}


def test_every_proposable_kind_is_accepted_by_the_runner():
    """工具能产出的 kind 必须恰好在 runner 白名单内。

    这条把 runner 侧的白名单与工具侧的实际产出一致性钉住：漏一个，该动作的提案会被
    `_maybe_emit_action_proposal` 静默丢弃（工具结果按原样放行），前端永远等不到卡片，
    而且没有任何测试会红。白名单里多一个死值同样会被这条抓出来。
    """
    from src.agent.runner import _ALLOWED_PROPOSAL_KINDS
    from src.agent.tools.action_tools import _ACTION_KINDS

    producible = {"alert", "portfolio_trade", *set(_ACTION_KINDS.values())}
    assert _ALLOWED_PROPOSAL_KINDS == producible
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -m pytest tests/test_alert_tools.py tests/test_action_tools.py -q`
Expected: FAIL —— `ImportError: cannot import name '_maybe_emit_action_proposal'`

- [ ] **Step 3: 改告警工具返回值**

`src/agent/tools/alert_tools.py` 的 `_handle_propose_alert` 末尾，把 `return {"proposal": proposal, "summary": summary}` 改为：

```python
    return {"kind": "alert", "summary": summary, "proposal": proposal}
```

同时更新模块 docstring 第 3-8 行，把 `alert_proposal` 事件名改为 `action_proposal`：

```python
"""Agent tool for proposing alert rules for user confirmation.

The ``propose_alert`` tool is **read-only**: it validates a well-formed alert
proposal (matching the backend ``AlertRuleCreateRequest`` contract) and returns a
``{"kind": "alert", "summary": ..., "proposal": ...}`` envelope so the runner can
surface it as an ``action_proposal`` SSE event. The actual rule is created only after
the user confirms, via the existing ``POST /api/v1/alerts/rules`` endpoint (triggered
from the web client). This keeps the "confirm before create" semantics and reuses the
existing validation, permission and notification paths.
"""
```

- [ ] **Step 4: 泛化 runner**

把 `src/agent/runner.py:613` 的常量与 `:616-643` 的发射器整体替换为：

```python
# 提案工具：只读、返回 {kind, summary, proposal} 信封，由 runner 转成 action_proposal 事件。
_PROPOSAL_TOOL_NAMES = frozenset({
    "propose_alert",
    "propose_portfolio_trade",
    "propose_watchlist_change",
})

# 前端能分发的动作类型；越界的 kind 一律不发射，避免出现前端无法处理的卡片。
_ALLOWED_PROPOSAL_KINDS = frozenset({
    "alert",
    "portfolio_trade",
    "watchlist_add",
    "watchlist_remove",
})


def _maybe_emit_action_proposal(
    tc,
    result_str: str,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]],
    step: int,
) -> str:
    """Surface a proposal tool result to the client and simplify the LLM view.

    If the executed tool is one of ``_PROPOSAL_TOOL_NAMES`` and its result is a
    ``{"kind": ..., "summary": ..., "proposal": ...}`` envelope, emit an
    ``action_proposal`` SSE event via ``progress_callback`` and rewrite the
    LLM-facing ``result_str`` to a short confirmation note (so the raw proposal
    JSON is not leaked into the conversation). Returns the (possibly rewritten)
    ``result_str``.
    """
    if tc.name not in _PROPOSAL_TOOL_NAMES or not progress_callback:
        return result_str
    try:
        payload = json.loads(result_str)
    except (TypeError, ValueError):
        return result_str
    if not isinstance(payload, dict):
        return result_str
    kind = payload.get("kind")
    proposal = payload.get("proposal")
    summary = payload.get("summary")
    # kind 必须是字符串且在白名单内（字符串判断同时挡住 list/dict 这类不可哈希取值）。
    if not isinstance(kind, str) or kind not in _ALLOWED_PROPOSAL_KINDS:
        return result_str
    if not isinstance(proposal, dict) or not isinstance(summary, str):
        return result_str
    progress_callback(
        stream_event("action_proposal", kind=kind, proposal=proposal, summary=summary)
    )
    return json.dumps({"message": summary}, ensure_ascii=False)
```

把两个调用点（`:758`、`:804`）改为：

```python
        result_str = _maybe_emit_action_proposal(tc, result_str, progress_callback, step)
```

```python
            result_str = _maybe_emit_action_proposal(tc_item, result_str, progress_callback, step)
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run python -m pytest tests/test_alert_tools.py tests/test_action_tools.py -q`
Expected: PASS

- [ ] **Step 6: 全量后端回归**

Run: `uv run python -m pytest -m "not network" -q`
Expected: PASS（无 `alert_proposal` 相关失败）

- [ ] **Step 7: Commit（需用户确认）**

```bash
git add src/agent/tools/alert_tools.py src/agent/runner.py tests/test_alert_tools.py tests/test_action_tools.py
git commit -m "refactor(agent): unify proposal tools behind one action_proposal event"
```

---

### Task 6: 前端类型 + apply 分发 + 单测

**Files:**
- Create: `apps/dsa-web/src/types/actionProposal.ts`
- Create: `apps/dsa-web/src/utils/actionProposal.ts`
- Create: `apps/dsa-web/src/utils/__tests__/actionProposal.test.ts`
- Create: `tests/test_action_proposal_kinds_parity.py`（跨语言一致性钉：Python 白名单 ↔ TS 数组）
- **不要**改 `apps/dsa-web/src/types/alerts.ts`（`AlertProposal` 的删除移到 Task 8，见 Step 4）

**Interfaces:**
- Consumes: `alertsApi.createRule`（`src/api/alerts.ts:97`）、`portfolioApi.createTrade`（`src/api/portfolio.ts:179`）、`systemConfigApi.addToWatchlist` / `removeFromWatchlist`（`src/api/systemConfig.ts:358,371`）、`toCamelCase`（`src/api/utils.ts`）
- Produces:
  - `ActionProposalKind`、`ActionProposal`
  - `applyActionProposal(proposal: ActionProposal): Promise<void>`

- [ ] **Step 1: 写失败测试**

创建 `apps/dsa-web/src/utils/__tests__/actionProposal.test.ts`：

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { applyActionProposal } from '../actionProposal';
import type { ActionProposal } from '../../types/actionProposal';

vi.mock('../../api/alerts', () => ({ alertsApi: { createRule: vi.fn(async () => ({})) } }));
vi.mock('../../api/portfolio', () => ({ portfolioApi: { createTrade: vi.fn(async () => ({})) } }));
vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    addToWatchlist: vi.fn(async () => []),
    removeFromWatchlist: vi.fn(async () => []),
  },
}));

const { alertsApi } = await import('../../api/alerts');
const { portfolioApi } = await import('../../api/portfolio');
const { systemConfigApi } = await import('../../api/systemConfig');

beforeEach(() => {
  vi.clearAllMocks();
});

describe('applyActionProposal', () => {
  it('creates an alert rule from a snake_case proposal', async () => {
    const proposal: ActionProposal = {
      kind: 'alert',
      summary: '「600519」价格上穿 ¥1800',
      proposal: {
        name: '600519 price above 1800',
        target_scope: 'single_symbol',
        target: '600519',
        alert_type: 'price_cross',
        parameters: { direction: 'above', price: 1800 },
        severity: 'info',
      },
    };

    await applyActionProposal(proposal);

    expect(alertsApi.createRule).toHaveBeenCalledWith({
      name: '600519 price above 1800',
      targetScope: 'single_symbol',
      target: '600519',
      alertType: 'price_cross',
      parameters: { direction: 'above', price: 1800 },
      severity: 'info',
    });
  });

  it('records a portfolio trade from a snake_case proposal', async () => {
    const proposal: ActionProposal = {
      kind: 'portfolio_trade',
      summary: '在「A股主账户」记一笔买入 005827 25900 @ ¥1.6706（2026-09-22，约 ¥43268.54）',
      proposal: {
        account_id: 1,
        symbol: '005827',
        trade_date: '2026-09-22',
        side: 'buy',
        quantity: 25900,
        price: 1.6706,
        fee: 0,
        tax: 0,
      },
    };

    await applyActionProposal(proposal);

    expect(portfolioApi.createTrade).toHaveBeenCalledWith({
      accountId: 1,
      symbol: '005827',
      tradeDate: '2026-09-22',
      side: 'buy',
      quantity: 25900,
      price: 1.6706,
      fee: 0,
      tax: 0,
    });
  });

  it('adds a symbol to the default watchlist with positional args', async () => {
    await applyActionProposal({
      kind: 'watchlist_add',
      summary: '把「600519」加入自选',
      proposal: { stock_code: '600519', list_name: null },
    });

    expect(systemConfigApi.addToWatchlist).toHaveBeenCalledWith('600519', undefined);
    expect(systemConfigApi.removeFromWatchlist).not.toHaveBeenCalled();
  });

  it('removes a symbol from a named watchlist', async () => {
    await applyActionProposal({
      kind: 'watchlist_remove',
      summary: '把「600519」移出自选列表「短线池」',
      proposal: { stock_code: '600519', list_name: '短线池' },
    });

    expect(systemConfigApi.removeFromWatchlist).toHaveBeenCalledWith('600519', '短线池');
    expect(systemConfigApi.addToWatchlist).not.toHaveBeenCalled();
  });

  it('rejects an unknown kind instead of silently doing nothing', async () => {
    await expect(
      applyActionProposal({
        kind: 'drop_table' as never,
        summary: 'x',
        proposal: {},
      }),
    ).rejects.toThrow(/drop_table/);
  });
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/dsa-web && npm run test -- src/utils/__tests__/actionProposal.test.ts`
Expected: FAIL —— 无法解析 `../actionProposal`

- [ ] **Step 3: 创建类型文件**

`apps/dsa-web/src/types/actionProposal.ts`：

```ts
/**
 * 问股助手提出、等待用户确认的写操作提案。
 *
 * 这是跨域类型（告警 / 持仓 / 自选），因此不放进任何一个域文件。
 * `proposal` 保持后端原样的 snake_case，且刻意标为 `unknown`：四个 kind 的请求体形状
 * 不同，硬塞泛型只会堆断言，转换与断言统一放在 `utils/actionProposal.ts` 的 apply 分支里。
 *
 * `ACTION_PROPOSAL_KINDS` 是 kind 的**单一源**（运行时数组在前，类型从中派生）。刻意不写成
 * 「联合类型 + 另在 store 里放一份运行时数组」：那样两份可以漂移，而真正决定事件是否被接受
 * 的是运行时数组（union 只在编译期存在）。漏一个 kind 意味着该类提案的卡片永远不渲染，且
 * `tsc` 不会报错——`readonly ActionProposalKind[]` 只约束成员合法，不要求覆盖全部成员。
 */
export const ACTION_PROPOSAL_KINDS = [
  'alert',
  'portfolio_trade',
  'watchlist_add',
  'watchlist_remove',
] as const;

/** kind 类型从运行时数组派生：单一源，二者不可能不一致（详见上方注释）。 */
export type ActionProposalKind = (typeof ACTION_PROPOSAL_KINDS)[number];

export interface ActionProposal {
  kind: ActionProposalKind;
  /** 卡片展示的中文摘要，由后端工具处理器生成，不是模型自由生成 */
  summary: string;
  /** 对应写接口的请求体（snake_case） */
  proposal: unknown;
}
```

- [ ] **Step 4: 本 Task 不要删 `AlertProposal`**

`types/alerts.ts:94-100` 的 `AlertProposal` **留到 Task 8 再删**。本 Task 若删掉它，`agentChatStore.ts` 与 `ChatPage.tsx` 仍会引用它，`tsc -b` 会一直红到 Task 8——那样 Task 6 与 Task 7 两个提交都是**编译不过的状态**，既妨碍 bisect，也让"每个提交可用"这条基本要求失效。它要到 Task 8 之后才真正无人引用。

- [ ] **Step 5: 实现 `applyActionProposal`**

`apps/dsa-web/src/utils/actionProposal.ts`：

```ts
import { alertsApi } from '../api/alerts';
import { portfolioApi } from '../api/portfolio';
import { systemConfigApi } from '../api/systemConfig';
import { toCamelCase } from '../api/utils';
import type { AlertRuleCreateRequest } from '../types/alerts';
import type { PortfolioTradeCreateRequest } from '../types/portfolio';
import type { ActionProposal } from '../types/actionProposal';

/** watchlist 接口的请求体形状（对齐 WatchlistRequest，`api/v1/schemas/history.py:389`）。 */
interface WatchlistProposalPayload {
  stockCode: string;
  listName?: string;
}

/**
 * 执行一条经用户确认的提案。
 *
 * 写入发生在浏览器会话下、走既有 REST 接口，AI 从不持有写权限；这里只负责把后端
 * 原样的 snake_case 请求体转成各客户端的入参形状并调用。
 */
export async function applyActionProposal(proposal: ActionProposal): Promise<void> {
  switch (proposal.kind) {
    case 'alert':
      await alertsApi.createRule(toCamelCase<AlertRuleCreateRequest>(proposal.proposal));
      return;
    case 'portfolio_trade':
      await portfolioApi.createTrade(
        toCamelCase<PortfolioTradeCreateRequest>(proposal.proposal),
      );
      return;
    case 'watchlist_add': {
      const payload = toCamelCase<WatchlistProposalPayload>(proposal.proposal);
      // list_name 后端可能给 null；客户端的可选第二参数用 undefined 表达"默认列表"。
      await systemConfigApi.addToWatchlist(payload.stockCode, payload.listName ?? undefined);
      return;
    }
    case 'watchlist_remove': {
      const payload = toCamelCase<WatchlistProposalPayload>(proposal.proposal);
      await systemConfigApi.removeFromWatchlist(payload.stockCode, payload.listName ?? undefined);
      return;
    }
    default:
      // kind 已在服务端白名单校验过，这里只是纵深防御：宁可直接报错，也不静默什么都不做。
      throw new Error(`unsupported action proposal kind: ${String((proposal as { kind?: unknown }).kind)}`);
  }
}
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd apps/dsa-web && npm run test -- src/utils/__tests__/actionProposal.test.ts`
Expected: PASS（5 passed）

- [ ] **Step 7: 类型检查必须通过**

Run: `cd apps/dsa-web && npx tsc -b`
Expected: **PASS**。本 Task 只新增文件（`AlertProposal` 按 Step 4 保留不动），因此不引入任何编译错误；若这里报错，说明你动了不该动的东西（最可能是删了 `AlertProposal`）。

- [ ] **Step 8: Commit（需用户确认）**

```bash
git add apps/dsa-web/src/types/actionProposal.ts apps/dsa-web/src/utils/actionProposal.ts apps/dsa-web/src/utils/__tests__/actionProposal.test.ts tests/test_action_proposal_kinds_parity.py
git commit -m "feat(web): add action proposal type and apply dispatcher"
```

---

### Task 7: 前端 store 改造 + 单测

**Files:**
- Modify: `apps/dsa-web/src/stores/agentChatStore.ts`（`:13` import、`:45-57` `Message`、`:424-426`、`:563-573`、`:642`、`:662`）
- Test: `apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts:348-380`

**Interfaces:**
- Consumes: `ActionProposal`（Task 6）
- Produces: `Message.actionProposal?: ActionProposal`；SSE `action_proposal` 事件被写入该字段（`proposal` 保持 snake_case 原样）

- [ ] **Step 1: 改测试**

把 `agentChatStore.test.ts` 的 `it('attaches an alert_proposal event to the committed assistant message', ...)` 整段替换为：

```ts
  it('attaches an action_proposal event to the committed assistant message', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        accepted('request-alert-proposal'),
        'data: {"type":"action_proposal","kind":"alert","proposal":{"name":"600519 price above 1800","target_scope":"single_symbol","target":"600519","alert_type":"price_cross","parameters":{"direction":"above","price":1800},"severity":"info"},"summary":"「600519」价格上穿 ¥1800"}',
        'data: {"type":"done","success":true,"content":"建议关注该价格位","backend":"litellm"}',
      ]),
    );

    await useAgentChatStore.getState().startStream({
      message: '茅台价格会突破吗',
      session_id: 'session-test',
      request_id: 'request-alert-proposal',
    });

    const state = useAgentChatStore.getState();
    expect(state.messages).toHaveLength(2);
    const assistant = state.messages[1];
    // proposal 保持后端原样的 snake_case，转换交给 utils/actionProposal 的 apply 分支
    expect(assistant.actionProposal).toMatchObject({
      kind: 'alert',
      summary: '「600519」价格上穿 ¥1800',
      proposal: {
        name: '600519 price above 1800',
        target_scope: 'single_symbol',
        target: '600519',
        alert_type: 'price_cross',
        parameters: { direction: 'above', price: 1800 },
        severity: 'info',
      },
    });
  });

  it('ignores an action_proposal event whose kind is missing', async () => {
    vi.mocked(agentApi.chatStream).mockResolvedValue(
      createStreamResponse([
        accepted('request-bad-proposal'),
        'data: {"type":"action_proposal","summary":"缺 kind","proposal":{"a":1}}',
        'data: {"type":"done","success":true,"content":"ok","backend":"litellm"}',
      ]),
    );

    await useAgentChatStore.getState().startStream({
      message: '问股',
      session_id: 'session-test',
      request_id: 'request-bad-proposal',
    });

    expect(useAgentChatStore.getState().messages[1].actionProposal).toBeUndefined();
  });
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/dsa-web && npm run test -- src/stores/__tests__/agentChatStore.test.ts`
Expected: FAIL —— `assistant.actionProposal` 为 `undefined`（store 仍在处理 `alert_proposal`）

- [ ] **Step 3: 改 store**

`apps/dsa-web/src/stores/agentChatStore.ts`：

import 行（`:13`）替换：

```ts
import {
  ACTION_PROPOSAL_KINDS,
  type ActionProposal,
  type ActionProposalKind,
} from '../types/actionProposal';
```

`Message` 接口中的字段（`:55-56`）替换：

```ts
  /** 写操作提案（告警/持仓录入/自选增删），等待用户在卡片上确认。 */
  actionProposal?: ActionProposal;
```

流式局部变量（`:424-426`）替换：

```ts
      // 提案事件里的待确认动作；在 done 时挂到已提交的助手消息上，避免流中途改动消息。
      let pendingActionProposal: ActionProposal | undefined;
```

事件分支（`:563-573`）替换：

```ts
        if (event.type === 'action_proposal') {
          const kind = (event as { kind?: unknown }).kind;
          const proposal = (event as { proposal?: unknown }).proposal;
          const summary = (event as { summary?: unknown }).summary;
          if (isActionProposalKind(kind) && proposal && typeof summary === 'string') {
            // proposal 保持后端原样（snake_case），snake→camel 与断言在 utils/actionProposal 里做。
            pendingActionProposal = { kind, summary, proposal };
          }
          return;
        }
```

两处 attach（`:642`、`:662`）把 `alertProposal: pendingAlertProposal,` 替换为：

```ts
                actionProposal: pendingActionProposal,
```

```ts
                actionProposal: pendingActionProposal,
```

并在文件模块作用域（`ProgressStep` 接口定义之前）新增 kind 守卫。**数组从 `types/actionProposal.ts` import，不要在 store 里另声明一份**——那份数组是唯一权威（`readonly ActionProposalKind[]` 的注解只约束成员合法、不要求覆盖全部成员，所以另抄一份漏了 kind 时 `tsc` 不会报错）：

```ts
import { ACTION_PROPOSAL_KINDS, type ActionProposal, type ActionProposalKind } from '../types/actionProposal';

/** 未知 kind 一律丢弃，避免把前端无法分发的卡片挂到消息上。 */
function isActionProposalKind(value: unknown): value is ActionProposalKind {
  return typeof value === 'string' && (ACTION_PROPOSAL_KINDS as readonly string[]).includes(value);
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd apps/dsa-web && npm run test -- src/stores/__tests__/agentChatStore.test.ts`
Expected: PASS

> **⚠️ 本 Task 会打破 `ChatPage.tsx` 的类型，必须留一个过渡字段（初版计划漏了这点）。**
> `Message.alertProposal` 改名后，`src/pages/ChatPage.tsx:1056` 读 `msg.alertProposal` 会报
> `TS2339`，`ChatPage.test.tsx` 的两处夹具会报 `TS2353`——而 ChatPage 在本 Task 的禁改清单里
> （Task 8 才重写它）。所以「`tsc -b` 应当通过」在不加过渡字段时**不可能成立**。
>
> 处理：在 `Message` 上**保留**一个标注废弃的过渡字段，store 永不写入它：
>
> ```ts
>   /** 写操作提案（告警/持仓录入/自选增删），等待用户在卡片上确认。 */
>   actionProposal?: ActionProposal;
>   /**
>    * @deprecated 过渡期兼容：ChatPage 的旧告警卡片仍读这个字段（Task 8 重写卡片时删除）。
>    * store 已不再写入它——后端不再发 alert_proposal，旧卡片在改动前后都不会渲染。
>    */
>   alertProposal?: AlertProposal;
> ```
>
> 这样三个提交（Task 7 / Part 0 / Task 8）都能编译，而运行期行为与改动前逐字节相同（store 不写
> 这个字段，旧卡片本来就是死的）。**Task 8 必须连同卡片一起删掉这个字段、`AlertProposal` 的
> import 以及 `types/alerts.ts` 里的类型定义。**

- [ ] **Step 5: 类型检查必须通过**

Run: `cd apps/dsa-web && npx tsc -b`
Expected: **PASS**（在按上面保留过渡字段的前提下）。若报 `ChatPage.tsx` / `ChatPage.test.tsx` 的
`alertProposal` 相关错误，说明你没保留过渡字段——不要为了让它变绿去改 ChatPage（那超出本 Task），
按上面的形态补回过渡字段。

- [ ] **Step 6: Commit（需用户确认）**

```bash
git add apps/dsa-web/src/stores/agentChatStore.ts apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts
git commit -m "refactor(web): store action proposals generically in chat store"
```

---

### Task 8: ChatPage 卡片 + i18n + 页面测试

> **⚠️ 范围调整（Task 7 质量审查后确定，先做 Part 0 再做本 Task）。**
>
> **1. 一条消息可以带多个提案，必须累积而不是只留最后一个。**
> `_maybe_emit_action_proposal` 是**每次工具调用**发一次（`src/agent/runner.py:663`，顺序路径 `:780`
> 与并行路径 `:826` 都调它），而一轮对话里可以有多次提案工具调用——例如「把这三只加入自选」
> 会让模型对每只各调一次 `propose_watchlist_change`。现在的 `pendingActionProposal` 后写覆盖先写，
> 于是只有最后一个提案出卡片，其余**静默消失**；并行路径下发射顺序还是**完成顺序**，丢哪个不确定。
> 设计文档第 363 行正是用这个理由要求 bot 累积，Task 5 已经照做了——web 路径不能留着同一个缺陷。
> 因此 `Message.actionProposal?: ActionProposal` 改为 **`actionProposals?: ActionProposal[]`**。
>
> 连带后果（必须一起处理，否则状态会串）：卡片状态表不能只按 `msg.id` 记，
> `actionProposalStatus` 的键改为 **`` `${msg.id}#${index}` ``**（同一消息内 index 稳定）。
>
> **2. 抽一个 `parseActionProposalEvent` 到 `types/actionProposal.ts`。**
> 现在 store 里对同一个 event 连做三次 `as { x?: unknown }` 转换（同文件其他分支都是转成一个具名局部类型，
> 如 `StreamFailureEvent`）。抽成纯函数后：转换从三次降为一次、store 不再做形状解析、可被表驱动测试直接覆盖；
> 更重要的是那两条**变异测试杀不死**的守卫（`typeof summary === 'string'`、`proposal &&`）可以用
> 一张 3 行表驱动测试直接钉住，比走流式 harness 便宜得多。
>
> ```ts
> /** 从 SSE 事件里解析提案；形状不合规一律返回 undefined（不抛错、不部分接受）。 */
> export function parseActionProposalEvent(event: unknown): ActionProposal | undefined
> ```
>
> 放在 `types/actionProposal.ts`（该文件已有运行时代码：数组与 `isActionProposalKind`），
> **不要**放 `utils/actionProposal.ts`——那个模块 import 三个 API 客户端，是 apply 边界而非 ingest 边界。
>
> **3. 补上第二个 attach 点的覆盖。** 现有测试只走 fresh-append；streaming-placeholder 那条
> （`agentChatStore.ts:648` 一带）总是在 `pendingActionProposals` 为空时执行，所以删掉那一行
> 测试仍全绿。在既有 `action_proposal` 测试里，于提案事件前插一行
> `'data: {"type":"content_delta","delta":"先导"}',`，一条测试即可同时钉住两个 attach 点。
>
> **4. 小改**：`Message.actionProposals` 的注释要写明「`proposal` 是后端 snake_case 原样请求体，
> 转换在 `utils/actionProposal` 里做」（读者最可能停在 `Message` 字段这里）；测试对 `proposal`
> 子对象用 `toEqual` 而非 `toMatchObject`（钉住"恰好是后端请求体，没有多加东西"）。
>
> **已知且刻意不改**：流式出错或被用户取消时不会走 `done` 成功路径，已经发射的提案会被丢弃，
> 而流式正文已留在屏幕上——用户读到一段关于某动作的话却没有卡片。这与"不做提案持久化"同源，
> 属设计取舍；**Task 12 的手工验证不要把"有提案文字但无卡片"当成 bug 上报**。

**Files:**
- Modify: `apps/dsa-web/src/pages/ChatPage.tsx`（`:13`、`:41`、`:260`、`:370-389`、`:1055-1105`、`:1500`）
- Modify: `apps/dsa-web/src/i18n/uiText.ts`（zh `:1056-1061`、en `:2203-2208`）
- Modify: `apps/dsa-web/src/stores/agentChatStore.ts`（**删除 Task 7 留下的 `@deprecated alertProposal` 过渡字段及其 `AlertProposal` import**）
- Modify: `apps/dsa-web/src/types/alerts.ts`（**删除至此才无人引用的 `AlertProposal` 接口**）
- Test: `apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx`、`apps/dsa-web/src/stores/__tests__/agentChatStore.test.ts`（删掉过渡字段的断言）

**Interfaces:**
- Consumes: `applyActionProposal`（Task 6）、`ActionProposal`（Task 6）、`Message.actionProposal`（Task 7）
- Produces: 通用确认卡片；i18n key `chat.actionProposal*`

- [ ] **Step 1: 改测试**

> **⚠️ 本 Task 下方 Step 1 / Step 4 的代码块写于 Part 0 之前，用的是单数形状**（`actionProposal:` /
> `handleApplyActionProposal(msgId, …)` / `actionProposalStatus[msg.id]`）。以上方 ⚠️ 说明为准：
> 字段是 **`actionProposals`（数组）**、handler 收 **cardKey**、状态表键是 `` `${msg.id}#${index}` ``。
> 逐字照抄下面的代码块会让 ChatPage 的卡片测试全红且看不出原因（mock 的 messages 里写 `actionProposal`，
> 而 ChatPage 读 `actionProposals`）。实施时已按 ⚠️ 说明落地。

`apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx` 已有的两个告警卡片用例（`:2397-2478`）**不 mock `utils/actionProposal`**——该文件已经 mock 了 `../../api/alerts`、`../../api/systemConfig`（`:105-121`），而 `applyActionProposal` 正是调这两个模块，所以现有 mock 天然覆盖真实的 dispatch 链路。

把这两个用例改写为下面三段（第一个是改写后的告警用例，后两个是新增）：

```tsx
  it('renders an action proposal card and creates the rule on confirm', async () => {
    mockCreateAlertRule.mockResolvedValue({
      id: 1,
      name: '600519 price above 1800',
      targetScope: 'single_symbol',
      target: '600519',
      alertType: 'price_cross',
      parameters: { direction: 'above', price: 1800 },
      severity: 'info',
      enabled: true,
      source: 'api',
    });
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '分析 600519' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '建议关注该价格位',
        actionProposal: {
          kind: 'alert',
          summary: '「600519」价格上穿 ¥1800',
          // proposal 是后端原样的 snake_case 请求体
          proposal: {
            name: '600519 price above 1800',
            target_scope: 'single_symbol',
            target: '600519',
            alert_type: 'price_cross',
            parameters: { direction: 'above', price: 1800 },
            severity: 'info',
          },
        },
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('「600519」价格上穿 ¥1800')).toBeInTheDocument();
    expect(screen.getByText('待确认操作')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    await waitFor(() => expect(mockCreateAlertRule).toHaveBeenCalledTimes(1));
    // apply 分支把 snake_case 转成客户端入参形状后再调 createRule
    expect(mockCreateAlertRule).toHaveBeenCalledWith(
      expect.objectContaining({ target: '600519', alertType: 'price_cross' }),
    );
    expect(await screen.findByText('已提交')).toBeInTheDocument();
  });

  it('applies a watchlist proposal through the systemConfig client', async () => {
    mockAddToWatchlist.mockResolvedValue(['600519']);
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '把 600519 加入自选' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '已生成提案',
        actionProposal: {
          kind: 'watchlist_add',
          summary: '把「600519」加入自选',
          proposal: { stock_code: '600519', list_name: null },
        },
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('把「600519」加入自选')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    await waitFor(() => expect(mockAddToWatchlist).toHaveBeenCalledWith('600519', undefined));
    expect(mockCreateAlertRule).not.toHaveBeenCalled();
    expect(await screen.findByText('已提交')).toBeInTheDocument();
  });

  it('dismisses an action proposal card on cancel', async () => {
    mockStoreState.messages = [
      { id: 'user-1', role: 'user', content: '分析 600519' },
      {
        id: 'assistant-1',
        role: 'assistant',
        content: '建议关注该价格位',
        actionProposal: {
          kind: 'alert',
          summary: '「600519」价格上穿 ¥1800',
          proposal: {
            name: '600519 price above 1800',
            target_scope: 'single_symbol',
            target: '600519',
            alert_type: 'price_cross',
            parameters: { direction: 'above', price: 1800 },
            severity: 'info',
          },
        },
      },
    ];

    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ChatPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('「600519」价格上穿 ¥1800')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(screen.queryByText('「600519」价格上穿 ¥1800')).not.toBeInTheDocument();
    expect(mockCreateAlertRule).not.toHaveBeenCalled();
  });
```

> `mockAddToWatchlist` 已在本文件的 `vi.hoisted` 块与 systemConfig 模块 mock 中定义（`:43`、`:106`），无需新增。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd apps/dsa-web && npm run test -- src/pages/__tests__/ChatPage.test.tsx`
Expected: FAIL —— 找不到「待确认操作」（i18n key 尚不存在）

- [ ] **Step 3: 改 i18n**

`apps/dsa-web/src/i18n/uiText.ts` zh 段，把 `:1056-1061` 六行替换为：

```ts
  'chat.actionProposalTitle': '待确认操作',
  'chat.actionProposalConfirm': '确认',
  'chat.actionProposalCancel': '取消',
  'chat.actionProposalApplying': '正在提交…',
  'chat.actionProposalApplied': '已提交',
  'chat.actionProposalFailed': '提交失败，请重试',
```

en 段，把 `:2203-2208` 六行替换为：

```ts
  'chat.actionProposalTitle': 'Action to confirm',
  'chat.actionProposalConfirm': 'Confirm',
  'chat.actionProposalCancel': 'Cancel',
  'chat.actionProposalApplying': 'Submitting…',
  'chat.actionProposalApplied': 'Submitted',
  'chat.actionProposalFailed': 'Failed to submit, please retry',
```

> 刻意不做 per-kind 文案：卡片信息量全在工具生成的 `summary` 上，按钮按 kind 分动词只是装饰，却要 4 倍 i18n 键并让 zh/en 两段更容易漂移。

- [ ] **Step 4: 改 ChatPage**

`apps/dsa-web/src/pages/ChatPage.tsx`：

import（`:13`）替换：

```tsx
import type { ActionProposal } from '../types/actionProposal';
import { applyActionProposal } from '../utils/actionProposal';
```

状态类型（`:41`）替换：

```tsx
type ActionProposalStatus = 'pending' | 'applying' | 'applied' | 'error' | 'cancelled';
```

状态容器（`:260`）替换（同时保留 `alertsApi` 的既有 import——它仍被其他逻辑使用；若 `alertsApi` 在本文件已无其他用途，则一并删除其 import）：

```tsx
  const [actionProposalStatus, setActionProposalStatus] = useState<Record<string, ActionProposalStatus>>({});
```

两个 handler（`:370-389`）替换：

```tsx
  const handleApplyActionProposal = useCallback(
    async (msgId: string, proposal: ActionProposal) => {
      setActionProposalStatus((s) => ({ ...s, [msgId]: 'applying' }));
      try {
        await applyActionProposal(proposal);
        if (isMountedRef.current) {
          setActionProposalStatus((s) => ({ ...s, [msgId]: 'applied' }));
        }
      } catch {
        if (isMountedRef.current) {
          setActionProposalStatus((s) => ({ ...s, [msgId]: 'error' }));
        }
      }
    },
    [],
  );

  const handleCancelActionProposal = useCallback((msgId: string) => {
    setActionProposalStatus((s) => ({ ...s, [msgId]: 'cancelled' }));
  }, []);
```

卡片渲染（`:1055-1105`）替换：

```tsx
  const renderActionProposalCard = (msg: Message) => {
    const proposal = msg.actionProposal;
    if (!proposal) return null;
    const status = actionProposalStatus[msg.id] || 'pending';
    if (status === 'cancelled') return null;

    const applied = status === 'applied';
    const applying = status === 'applying';
    const failed = status === 'error';

    return (
      <div className="mb-3 mt-2">
        <InlineAlert
          variant={applied ? 'success' : failed ? 'danger' : 'info'}
          title={applied ? t('chat.actionProposalApplied') : t('chat.actionProposalTitle')}
          message={(
            <span className="flex flex-col gap-3">
              <span className="font-medium">{proposal.summary}</span>
              {!applied && !failed && (
                <span className="flex gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="primary"
                    isLoading={applying}
                    disabled={applying}
                    loadingText={t('chat.actionProposalApplying')}
                    onClick={() => void handleApplyActionProposal(msg.id, proposal)}
                  >
                    {t('chat.actionProposalConfirm')}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="danger-subtle"
                    disabled={applying}
                    onClick={() => handleCancelActionProposal(msg.id)}
                  >
                    {t('chat.actionProposalCancel')}
                  </Button>
                </span>
              )}
              {failed && (
                <span className="text-xs">{t('chat.actionProposalFailed')}</span>
              )}
            </span>
          )}
        />
      </div>
    );
  };
```

挂载点（`:1500`）替换：

```tsx
                    {msg.role === 'assistant' && renderActionProposalCard(msg)}
```

- [ ] **Step 5: 跑页面测试确认通过**

Run: `cd apps/dsa-web && npm run test -- src/pages/__tests__/ChatPage.test.tsx`
Expected: PASS

- [ ] **Step 6: 删除至此才真正无人引用的 `AlertProposal`**

到这一步 store（Task 7）与 ChatPage（本 Task）都已迁到 `ActionProposal`，`types/alerts.ts` 里的 `AlertProposal` 接口（`:94-100`，含其上方注释）成了本次改动产生的孤儿。先确认它确实无引用再删：

Run: `cd apps/dsa-web && grep -rn "AlertProposal" src --include=*.ts --include=*.tsx | grep -v "actionProposal\|ActionProposal"`

若只剩 `types/alerts.ts` 里那处定义，就删掉它；若还有别的引用，先修干净再删。（`grep` 里排除 `ActionProposal` 是必要的：它是本功能的新类型，与这个孤儿只差首字母大小写。）

- [ ] **Step 7: 全量前端测试与构建**

Run: `cd apps/dsa-web && npm run test && npm run lint && npm run build`
Expected: 全部通过；`tsc -b` 干净（本 Task 之后不再有 `AlertProposal` 残留）

- [ ] **Step 8: Commit（需用户确认）**

```bash
git add apps/dsa-web/src/pages/ChatPage.tsx apps/dsa-web/src/i18n/uiText.ts apps/dsa-web/src/types/alerts.ts apps/dsa-web/src/pages/__tests__/ChatPage.test.tsx
git commit -m "feat(web): render a generic confirmation card for action proposals"
```

---

### Task 9: bot 提示行按 kind 收尾 —— **已并入 Task 5 的修复轮，本 Task 作废**

> **不要执行本 Task。** 事件名在 Task 5 改掉后，bot 的 `_on_progress` 会开始**接受**持仓/自选提案
> （它只按事件类型过滤），而提示文案写死为「检测到可创建的预警：…（可用 /alert 或 Web 端落库）」——
> 于是「把 600519 加入自选」会被说成"可创建的**预警**"并给出无效指引。这不是"提示不出现"，而是
> **对用户说了错话**，且是 Task 5 的契约变更新引入的，所以当时就把本 Task 的核心（kind 分支 +
> `action_hint` 改名 + 过期注释）并进去修了。
>
> **保留本节仅为记录范围变更。** 若你在后续轮次看到 `alert_proposal` 残留在 bot 里，说明 Task 5
> 没做干净，应回头修正 Task 5，而不是在这里补。

- [ ] **Step 1: 改代码**

`bot/commands/ask.py:260-270` 的注释与收事件逻辑替换为：

```python
            # Fold the most useful progress signal (an action_proposal outcome) into the
            # final reply. BotResponse has no streaming surface, so we capture the
            # action_proposal event and append a one-line hint instead.
            action_hint: Dict[str, str] = {}

            def _on_progress(event: Any) -> None:
                if not isinstance(event, dict):
                    return
                if event.get("type") != "action_proposal":
                    return
                summary = event.get("summary")
                if isinstance(summary, str) and summary.strip():
                    action_hint["summary"] = summary.strip()
                    kind = event.get("kind")
                    action_hint["kind"] = kind if isinstance(kind, str) else ""
```

`bot/commands/ask.py:276-282` 的提示行替换为：

```python
                if action_hint.get("summary"):
                    # 只有告警在 bot 侧有对应的落库命令（/alert），其余动作需要去 Web 端确认。
                    if action_hint.get("kind") == "alert":
                        follow_up = "（可用 /alert 或 Web 端落库）"
                    else:
                        follow_up = "（请在 Web 端「问股」中确认）"
                    content += (
                        f"\n\n🤖 检测到待确认操作：{action_hint['summary']}\n"
                        f"{follow_up}"
                    )
```

- [ ] **Step 2: 语法检查**

Run: `uv run python -m py_compile bot/commands/ask.py`
Expected: 无输出（成功）

- [ ] **Step 3: 跑 bot 相关测试**

Run: `uv run python -m pytest -m "not network" -q -k "ask or bot"`
Expected: PASS（若无匹配用例则命令退出码为 5，属正常，记录实际输出）

- [ ] **Step 4: Commit（需用户确认）**

```bash
git add bot/commands/ask.py
git commit -m "feat(bot): surface confirmable action proposals in ask replies"
```

---

### Task 10: 系统提示词规则 7/8

> **⚠️ 下方 Step 2 的三条规则文本是初版，已过审后修订，以本节末尾为准。**
> 定稿版（两处提示词逐字相同，且由测试钉住）：
>
> ```
> 7. **持仓录入提案** — 当用户要求把某笔买入/卖出记入账户，**或你正为该笔录入补齐信息、用户已给出份额与成本时**，**必须**调用 `propose_portfolio_trade` 生成提案交由用户确认，不得只用文字回应而跳过提案；**绝不在用户确认前调用任何写入接口**，也**不得**在该笔录入有对应提案工具时声称自己只能查询、无法写入。账户未指明时先调用 `get_portfolio_snapshot` 读取 `account_id` / `account_name`；仍不唯一就先追问用户，不许猜；份额、成本或方向缺失时同样先追问，不得用行情价或估算值顶替。
> 8. **自选增删提案** — 当用户要求把某只股票加入自选或从自选移除时，**必须**调用 `propose_watchlist_change` 生成提案交由用户确认；**绝不在用户确认前修改任何配置**。用户点名命名自选列表时才传 `list_name`。
> 9. **写操作与配置改动一律走提案** — 你永远不直接执行写入：改动账户、持仓或自选只能通过上述提案工具发起，由用户在 Web 端卡片上确认后落库；修改/删除已有记录等没有对应提案工具的操作，如实说明需在对应页面处理，不要用其他提案工具顶替。对能通过提案工具完成的写操作，不得声称自己只能查询、无法写入。
> ```
>
> 五处修订的理由：**触发条件必须锚定在"录入意图"上**（初版「或对话中已确认份额与成本时」是对话状态条件，假设句/复盘闲聊都会误触发，会在没人要求时开卡片）；**「不许猜」要覆盖金额**（初版只管账户，缺价格时模型会拿行情价当成本，落出错的财务记录）；**规则 9 要给没有对应工具的写操作留诚实出路**（初版说"任何写操作只能走提案工具"，而改/删交易与删告警都没有工具，会把模型逼向产出一张与用户要求矛盾的卡片）；**规则 7 的禁止子句加"在该笔录入有对应提案工具时"限定语**，避免模型把规则 9 的"如实说明"泛化到有工具的请求上、退回旧拒答；**规则 9 末尾补一句把禁止面扩到全部提案场景**（规则 7 的禁令只覆盖持仓录入，而用户拒答的根因是模型的一个全局信念，自选也需要同一句禁止），并把规则 9 的加粗标题与正文对齐（正文枚举账户/持仓/自选，告警仍由规则 6 管）。

**Files:**
- Modify: `src/agent/executor.py:415`（`LEGACY_DEFAULT_CHAT_SYSTEM_PROMPT`）
- Modify: `src/agent/executor.py:453`（`CHAT_SYSTEM_PROMPT`）

**Interfaces:**
- Consumes: 三个提案工具已注册（Task 4）
- Produces: 模型被明确告知可以提出写操作提案，且不得自称只读

- [ ] **Step 1: 确认两处锚点**

Run: `grep -n "告警提案" src/agent/executor.py`
Expected: 恰好两行（`:415`、`:453`），均属 chat 提示词；`CODEX_CHAT_SYSTEM_PROMPT`（`:459` 起）与两份分析提示词不含该规则

- [ ] **Step 2: 两块提示词各追加三条规则**

在**两处**规则 6 的下一行各插入（两处内容完全相同）：

```
7. **持仓录入提案** — 当用户要求把某笔买入/卖出记入账户，或对话中已确认份额与成本时，**必须**调用 `propose_portfolio_trade` 生成提案交由用户确认，不得只用文字回应而跳过提案；**绝不在用户确认前调用任何写入接口**，也**不得**声称自己只能查询、无法写入。账户未指明时先调用 `get_portfolio_snapshot` 读取 `account_id` / `account_name`；仍不唯一就先追问用户，不许猜。
8. **自选增删提案** — 当用户要求把某只股票加入自选或从自选移除时，**必须**调用 `propose_watchlist_change` 生成提案交由用户确认；**绝不在用户确认前修改任何配置**。用户点名命名自选列表时才传 `list_name`。
9. **写操作一律走提案** — 任何会改动账户、持仓或配置的动作，都只能通过上述提案工具发起，由用户在 Web 端确认后落库；你永远不直接执行写入。
```

- [ ] **Step 3: 确认没有误改只读面**

Run: `grep -c "propose_portfolio_trade" src/agent/executor.py`
Expected: `2`（只出现在两套 chat 提示词里）

Run: `uv run python -c "from src.agent.executor import CODEX_CHAT_SYSTEM_PROMPT as p; assert 'propose_portfolio_trade' not in p; print('codex read-only prompt untouched')"`
Expected: `codex read-only prompt untouched`

- [ ] **Step 4: 跑相关后端测试**

Run: `uv run python -m pytest -m "not network" -q -k "prompt or executor"`
Expected: PASS

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add src/agent/executor.py
git commit -m "feat(agent): instruct chat prompt to propose portfolio and watchlist writes"
```

---

### Task 11: 文档同步

**Files:**
- Modify: `docs/agent-stream-events.md:41-58`（事件表）
- Modify: `docs/CHANGELOG.md`（`[Unreleased]` 扁平条目）

- [ ] **Step 1: 补事件表**

在 `docs/agent-stream-events.md` 的事件表（`:43-56`）中，`generating` 行之后插入：

```markdown
| `action_proposal` | single-agent loop (proposal tools) | The agent proposed a write action that requires explicit user confirmation. Nothing is persisted; the web client applies it via the existing REST endpoint after the user confirms. | `kind`, `summary`, `proposal` |
```

并在表后补一段说明（放在 `## Web Behavior` 之前）：

```markdown
`action_proposal` carries one of four `kind` values — `alert` / `portfolio_trade` / `watchlist_add` /
`watchlist_remove` (authoritative source: `runner._ALLOWED_PROPOSAL_KINDS`). `proposal` is exactly the
request body of the corresponding write endpoint (snake_case), and `summary` is generated by the backend
tool for direct display on the confirmation card. A client receiving an unknown `kind` must ignore the
event and must not issue any request from it.
```

> **这段必须用英文**：该文件其余部分（含既有的规范句 "Unknown event types should be ignored…"）全是英文，
> 而 `AGENTS.md` 要求文档语言**与文件语境一致**。初版计划给的是中文，已更正。
> 另外刻意写出 `runner._ALLOWED_PROPOSAL_KINDS` 作为**权威源指针**，把"文档里又一份 kind 清单"这个
> 漂移风险降成一个指针，而不是再加一个解析 markdown 的测试（那类测试同样脆弱）。

- [ ] **Step 2: 补 CHANGELOG**

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 段末尾（**扁平格式，每条一行，不加类目标题**）追加：

```markdown
- [新功能] 问股支持持仓录入与自选增删的「AI 提案 → 用户确认 → 系统执行」：新增 `propose_portfolio_trade` 与 `propose_watchlist_change` 两个只读提案工具，AI 不再只能回答「我无法写入」；用户确认后由浏览器调用既有 `POST /api/v1/portfolio/trades`、`POST /api/v1/stocks/watchlist/add|remove` 落库，AI 始终不持有写权限
- [改进] 问股提案通道统一为单一 `action_proposal` SSE 事件（携带 `kind` / `summary` / `proposal`），告警提案一并迁入，取代此前的 `alert_proposal`；前端确认卡片与 bot 提示行随之泛化
- [改进] 提取 `src/services/watchlist_service.py`：自选队列读写与股票代码校验从 `api/v1/endpoints/stocks.py` 私有函数搬入 service，供 Agent 侧复用同一份校验，API 契约不变
```

- [ ] **Step 3: 核对文档与代码一致**

Run: `grep -n "action_proposal" docs/agent-stream-events.md docs/CHANGELOG.md`
Expected: `agent-stream-events.md` 命中表格行与说明段；`CHANGELOG.md` 命中新增条目

Run: `grep -rn "alert_proposal" --include=*.md docs/ | grep -v CHANGELOG`
Expected: 无输出（历史 CHANGELOG 条目保留，属历史记录，不回改）

- [ ] **Step 4: 中英同步核查**

Run: `ls docs/agent-stream-events_EN.md docs/CHANGELOG_EN.md 2>&1`
Expected: 两个文件都不存在 → 两份目标文档均无英文版，无需同步；把该结论写进交付说明

- [ ] **Step 5: Commit（需用户确认）**

```bash
git add docs/agent-stream-events.md docs/CHANGELOG.md
git commit -m "docs: document the unified action_proposal stream event"
```

---

### Task 12: 全量验证 + 手工 E2E

**Files:** 无改动（纯验证）

- [ ] **Step 1: 后端门禁**

Run: `./scripts/ci_gate.sh`
Expected: 通过（flake8 / py_compile / 相关单测）

- [ ] **Step 2: 离线全量测试**

Run: `uv run python -m pytest -m "not network" -q`
Expected: PASS

- [ ] **Step 3: 前端门禁**

Run: `cd apps/dsa-web && npm run lint && npm run build && npm run test`
Expected: 三者全部通过

- [ ] **Step 4: 起服务**

Run: `uv run python main.py --serve-only`
Expected: 日志出现 `Uvicorn running on http://127.0.0.1:8000`；随后

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/`
Expected: `200`

- [ ] **Step 5: 手工 E2E —— 持仓录入**

在浏览器打开 `http://127.0.0.1:8000` → 问股，输入：

> 帮我把 25900 份 005827、成本净值 1.6706 记到账户里

逐项确认：
1. AI 不再回答「我无法写入」；它应调用 `get_portfolio_snapshot` 读账户，再调用 `propose_portfolio_trade`
2. 聊天里出现确认卡片，文案形如 `在「A股主账户」记一笔买入 005827 25900 @ ¥1.6706（2026-09-22，约 ¥43268.54）`
3. 点「确认」→ 卡片变「已提交」
4. 打开持仓页 → 该笔交易存在，数量/成本/日期与卡片一致
5. 刷新页面 → 卡片消失（提案不持久化，符合设计）

- [ ] **Step 6: 手工 E2E —— 自选增删**

在问股输入 `把 600519 加入自选`，确认卡片 → 确认 → 自选页出现 600519；再输入 `把 600519 移出自选`，确认后自选页不再有该代码。若配置了命名自选列表（`WATCHLIST_<NAME>`），额外验证 `加入自选列表「短线池」` 的卡片文案与落库位置。

- [ ] **Step 7: 手工回归 —— 告警提案未被破坏**

在问股输入 `如果茅台跌破 1500 提醒我`，确认仍出现告警确认卡片、点确认后告警页出现该规则（这是本次唯一动过的在产链路，必须回归）。

- [ ] **Step 8: 截图取证**

对 Step 5 / Step 6 / Step 7 的卡片与落库结果截图。**截图不得作为仓库文件合入**（`AGENTS.md` §1），仅用于交付说明。

- [ ] **Step 9: 交付说明**

按 `AGENTS.md` §9 结构输出：改了什么 / 为什么这么改 / 验证情况（含上述命令实际输出）/ 未验证项（`apps/dsa-desktop` 未单独验证，复用 web 产物；Codex 只读面未端到端验证，仅由 `cancellation_safe=False` 与 `tool_surface.py:177` 保证）/ 风险点 / 回滚方式（整体 revert，无迁移、无新表、无新配置）。
