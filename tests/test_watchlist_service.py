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

    def _replace(self, key, value):
        for item in self._items:
            if item.get("key") == key:
                item["value"] = value
                return
        self._items.append({"key": key, "value": value})


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
