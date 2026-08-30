# -*- coding: utf-8 -*-
"""Watchlist API regressions for stock-code variant matching."""

from api.v1.endpoints.stocks import (
    _list_named_watchlists,
    add_to_watchlist,
    get_watchlist,
    get_watchlist_lists,
    remove_from_watchlist,
)
from api.v1.schemas.history import WatchlistRequest


class FakeSystemConfigService:
    def __init__(self, stock_list: str) -> None:
        self.stock_list = stock_list
        self.config_version = "cfg-v1"
        self.update_calls: list[str] = []
        # named lists: key -> comma-joined value
        self.named: dict[str, str] = {}

    def get_config(self, include_schema: bool = False) -> dict:
        items = [{"key": "STOCK_LIST", "value": self.stock_list}]
        for key, value in self.named.items():
            items.append({"key": key, "value": value})
        return {
            "config_version": self.config_version,
            "items": items,
        }

    def update(self, **kwargs) -> None:
        items = kwargs["items"]
        key = items[0]["key"]
        value = items[0]["value"]
        if key == "STOCK_LIST":
            self.stock_list = value
        else:
            self.named[key] = value
        self.update_calls.append(value)


def test_watchlist_add_deduplicates_raw_hk_code_against_prefixed_variant() -> None:
    service = FakeSystemConfigService("00700")

    response = add_to_watchlist(
        WatchlistRequest(stock_code="HK00700"),
        service=service,
    )

    assert response.stock_codes == ["00700"]
    assert service.stock_list == "00700"
    assert service.update_calls == []


def test_watchlist_remove_deletes_raw_hk_code_from_prefixed_variant_request() -> None:
    service = FakeSystemConfigService("00700")

    response = remove_from_watchlist(
        WatchlistRequest(stock_code="HK00700"),
        service=service,
    )

    assert response.stock_codes == []
    assert service.stock_list == ""
    assert service.update_calls == [""]


def test_watchlist_matching_is_case_insensitive_for_us_tickers() -> None:
    service = FakeSystemConfigService("aapl")

    add_response = add_to_watchlist(
        WatchlistRequest(stock_code="AAPL"),
        service=service,
    )
    remove_response = remove_from_watchlist(
        WatchlistRequest(stock_code="AAPL"),
        service=service,
    )

    assert add_response.stock_codes == ["aapl"]
    assert remove_response.stock_codes == []
    assert service.update_calls == [""]


def test_watchlist_reads_common_copy_paste_separators() -> None:
    service = FakeSystemConfigService("600519，300750  AAPL")

    response = get_watchlist(service=service)

    assert response.stock_codes == ["600519", "300750", "AAPL"]


def test_watchlist_add_normalizes_existing_mixed_separators_on_write() -> None:
    service = FakeSystemConfigService("600519，300750")

    response = add_to_watchlist(
        WatchlistRequest(stock_code="AAPL"),
        service=service,
    )

    assert response.stock_codes == ["600519", "300750", "AAPL"]
    assert service.stock_list == "600519,300750,AAPL"
    assert service.update_calls == ["600519,300750,AAPL"]


def test_default_watchlist_ignores_named_list() -> None:
    """无 list_name 时读写默认 STOCK_LIST，不受命名列表影响。"""
    service = FakeSystemConfigService("600519")
    service.named["WATCHLIST_SHORT"] = "300750"

    get_response = get_watchlist(service=service)
    add_response = add_to_watchlist(WatchlistRequest(stock_code="AAPL"), service=service)

    assert get_response.stock_codes == ["600519"]
    assert add_response.stock_codes == ["600519", "AAPL"]
    assert service.stock_list == "600519,AAPL"
    assert service.named["WATCHLIST_SHORT"] == "300750"


def test_named_list_add_reads_and_writes_separate_key() -> None:
    service = FakeSystemConfigService("600519")

    add_response = add_to_watchlist(
        WatchlistRequest(stock_code="300750", list_name="短线池"),
        service=service,
    )
    get_response = get_watchlist(list_name="短线池", service=service)

    assert add_response.stock_codes == ["300750"]
    assert add_response.list_name == "短线池"
    assert service.named["WATCHLIST_短线池"] == "300750"
    assert get_response.stock_codes == ["300750"]
    # 默认列表不被污染
    assert service.stock_list == "600519"


def test_named_list_remove_updates_named_key() -> None:
    service = FakeSystemConfigService("600519")
    service.named["WATCHLIST_LONG"] = "600519,300750"

    response = remove_from_watchlist(
        WatchlistRequest(stock_code="300750", list_name="LONG"),
        service=service,
    )

    assert response.stock_codes == ["600519"]
    assert service.named["WATCHLIST_LONG"] == "600519"


def test_named_list_key_resolution_normalizes_name() -> None:
    """列表名会转大写并清洗为非字母数字字符。"""
    service = FakeSystemConfigService("")
    mixed = add_to_watchlist(WatchlistRequest(stock_code="AAPL", list_name="  short pool "), service=service)

    assert service.named["WATCHLIST_SHORT_POOL"] == "AAPL"
    assert mixed.list_name == "  short pool "


def test_list_named_watchlists_enumerates_and_counts() -> None:
    service = FakeSystemConfigService("600519")
    service.named["WATCHLIST_SHORT"] = "300750,AAPL"
    service.named["WATCHLIST_LONG"] = "600519"
    # 非 WATCHLIST_ 前缀的键不算命名列表
    service.named["STOCK_LIST"] = "600519"

    named = _list_named_watchlists(service)
    lookup = {n["name"]: n["count"] for n in named}

    assert lookup == {"short": 2, "long": 1}
    assert all(key.startswith("WATCHLIST_") for key in service.named if key in ("WATCHLIST_SHORT", "WATCHLIST_LONG"))


def test_get_watchlist_lists_endpoint_returns_summary() -> None:
    service = FakeSystemConfigService("600519")
    service.named["WATCHLIST_SHORT"] = "300750,AAPL"

    response = get_watchlist_lists(service=service)

    assert response.lists[0].name == "short"
    assert response.lists[0].count == 2
    assert response.lists[0].key == "WATCHLIST_SHORT"
