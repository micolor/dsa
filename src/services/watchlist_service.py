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

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注；见下方说明
    # 不在运行时导入 SystemConfigService 是为了控制导入重量，而非避免循环依赖
    # （该模块并不反向 import 本模块）。实测：只 import 本模块会拉起约 1200 个模块，
    # 而 import system_config_service 要拉起约 14000 个。本模块的调用方包含
    # 工具/Agent 侧的轻量入口，不应为此付出这个代价。
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
