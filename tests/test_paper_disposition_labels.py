# -*- coding: utf-8 -*-
"""保证模拟盘 disposition 的 Web 标签与后端取值集合不漂移。

`paper_signal_records.disposition` 的取值由后端权威维护，Web 需要一份独立映射才能
把它渲染成用户可读标签（`PaperRecordsList` 的 `dispositionMeta`）。后端新增取值而
前端漏补时，徽章会回退成裸英文串（`no_cash`、`lot_too_small`）——这正是引入
`no_fill` 时在 docs/paper-trading-optimization.md 里明确要避免的。

取值集合从 `paper_service` 的三个产出方法里解析，而不是在测试里另抄一份清单：
手抄清单会随后端新增取值而悄悄过期，本测试就失去意义。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVICE_PATH = "src/services/paper_service.py"
LIST_PATH = "apps/dsa-web/src/components/paper/PaperRecordsList.tsx"
FEATURE_TEXT_PATH = "apps/dsa-web/src/locales/featureText.ts"

# 只有这三个方法产出会被持久化的 disposition。
_DISPOSITION_METHODS = ("_handle_signal", "_open_or_add", "_reduce_position")

_MODULE_CONST_RE = re.compile(r'^([A-Z_]+) = "([a-z_]+)"', re.M)
_RETURN_LITERAL_RE = re.compile(r'return "([a-z_]+)"')
_RETURN_CONST_RE = re.compile(r"return ([A-Z_]+)\b")
_ASSIGN_LITERAL_RE = re.compile(r'disposition = "([a-z_]+)"')

# 数据源失败时返回但不落库：`process_signal` 在写入信号记录之前就提前返回，
# 因此它永远不会出现在信号列表里，Web 不需要也不应该有对应标签。
_NOT_PERSISTED = {"data_unavailable"}

# `dispositionMeta` 里每一项形如 `no_cash: { label: text.dispNoCash, variant: 'warning' },`
_DISPOSITION_META_RE = re.compile(
    r"^\s*([a-z_]+):\s*\{\s*label:\s*text\.([A-Za-z0-9_]+)", re.M
)
_FEATURE_TEXT_VALUE_RE = re.compile(r"^\s*([A-Za-z0-9_]+):\s*(['\"])(.*?)\2", re.M)


def _method_body(source: str, name: str) -> str:
    """切出 `    def <name>(` 到下一个同类缩进 `def` 之间的方法体。"""
    start = source.index(f"    def {name}(")
    next_def = source.find("\n    def ", start + 1)
    return source[start : next_def if next_def != -1 else len(source)]


def _emitted_dispositions() -> set[str]:
    source = (ROOT / SERVICE_PATH).read_text(encoding="utf-8")
    consts = dict(_MODULE_CONST_RE.findall(source))

    emitted: set[str] = set()
    for name in _DISPOSITION_METHODS:
        body = _method_body(source, name)
        emitted.update(_RETURN_LITERAL_RE.findall(body))
        emitted.update(_ASSIGN_LITERAL_RE.findall(body))
        for const in _RETURN_CONST_RE.findall(body):
            if const in consts:
                emitted.add(consts[const])
    return emitted - _NOT_PERSISTED


def _web_disposition_map() -> dict[str, str]:
    """返回 disposition 值 -> featureText 里的标签 key（如 dispNoCash）。"""
    source = (ROOT / LIST_PATH).read_text(encoding="utf-8")
    start = source.index("const dispositionMeta")
    end = source.index("};", start)
    return dict(_DISPOSITION_META_RE.findall(source[start:end]))


def _feature_text_blocks() -> tuple[str, str]:
    source = (ROOT / FEATURE_TEXT_PATH).read_text(encoding="utf-8")
    start = source.index("export const PAPER_TRADING_TEXT")
    end = source.index("\n} as const;", start)
    block = source[start:end]
    marker = block.index("\n  en: {")
    return block[:marker], block[marker:]


def test_every_persisted_disposition_has_a_web_label() -> None:
    emitted = _emitted_dispositions()
    assert emitted, "未能从 paper_service 解析出任何 disposition，解析逻辑需要更新"

    web = _web_disposition_map()
    missing = sorted(emitted - set(web))
    assert missing == [], (
        f"以下 disposition 后端可持久化但 Web 没有标签，页面上会露出裸英文串：{missing}"
    )


def test_web_disposition_map_does_not_invent_values() -> None:
    """双向一致性：Web 不得发明后端产不出的 disposition。

    多出来的键说明两边对取值集合的理解已分叉（例如后端删改取值后前端没跟着改），
    此时那些分支是死代码，会掩盖真正的漏补。
    """
    emitted = _emitted_dispositions()
    web = set(_web_disposition_map())
    assert web == emitted, (
        "Web 与后端的 disposition 集合不一致："
        f"仅 Web 有 {sorted(web - emitted)}；仅后端有 {sorted(emitted - web)}"
    )


def test_web_disposition_labels_exist_in_both_languages() -> None:
    """zh / en 文案都要存在：只查 key 存在性发现不了单边漏配。"""
    zh_block, en_block = _feature_text_blocks()
    web = _web_disposition_map()

    def _keys(block: str) -> set[str]:
        return {key for key, _quote, _value in _FEATURE_TEXT_VALUE_RE.findall(block)}

    zh_keys, en_keys = _keys(zh_block), _keys(en_block)
    missing_zh = sorted(value for value, key in web.items() if key not in zh_keys)
    missing_en = sorted(value for value, key in web.items() if key not in en_keys)
    assert missing_zh == [], f"以下 disposition 缺少中文文案：{missing_zh}"
    assert missing_en == [], f"以下 disposition 缺少英文文案：{missing_en}"
