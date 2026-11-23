# -*- coding: utf-8 -*-
"""风险面板 sector_concentration 的板块查询：失败冷却与单次请求预算。

背景：``/api/v1/portfolio/risk`` 的 sector_concentration 依赖板块归属查询，而该查询
目前只有 efinance 的 ``get_belong_board`` 一个来源，对部分代码稳定失败，且 data
provider 每次真正调用前都要付限速休眠（1.5~3s）。修复前风险面板每次刷新都会重新
付这笔成本，多只持仓时首屏可以拖到几十秒。这里锁定两条护栏：

1. 失败/空结果后同一进程内进入冷却，冷却期内不再发起查询（``skipped_count`` 计数）；
2. 单次请求的板块查询有时间预算，超出后剩余持仓按 UNCLASSIFIED 处理。
"""

from __future__ import annotations

import time
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.services.portfolio_risk_service import PortfolioRiskService


class _StubPortfolioService:
    def convert_amount(self, *, amount, from_currency, to_currency, as_of_date):
        return float(amount), 1.0, False


class _FakeManager:
    """模拟 DataFetcherManager.get_belong_boards。"""

    def __init__(self, *, boards=None, delay=0.0, error=None, results=None):
        self.boards = boards if boards is not None else []
        self.delay = delay
        self.error = error
        self.results = list(results) if results is not None else None
        self.calls = []

    def get_belong_boards(self, stock_code):
        self.calls.append(stock_code)
        if self.delay:
            time.sleep(self.delay)
        if self.results is not None:
            if self.results:
                return self.results.pop(0)
            return self.boards
        if self.error is not None:
            raise self.error
        return self.boards


def _service(manager, *, cooldown=600.0, budget=3.0) -> PortfolioRiskService:
    service = PortfolioRiskService(
        repo=MagicMock(),
        portfolio_service=_StubPortfolioService(),
        decision_signal_service=MagicMock(),
        config=SimpleNamespace(),
    )
    service._data_manager = manager
    service.sector_lookup_failure_cooldown_seconds = cooldown
    service.sector_lookup_budget_seconds = budget
    return service


def _snapshot(symbols, *, market="cn", market_value=1000.0) -> dict:
    return {
        "total_market_value": market_value * len(symbols),
        "accounts": [
            {
                "market": market,
                "positions": [
                    {"symbol": symbol, "market": market, "market_value_base": market_value}
                    for symbol in symbols
                ],
            }
        ],
    }


class PortfolioRiskSectorLookupTest(unittest.TestCase):
    def setUp(self) -> None:
        PortfolioRiskService.clear_sector_lookup_cooldown_for_tests()

    def tearDown(self) -> None:
        PortfolioRiskService.clear_sector_lookup_cooldown_for_tests()

    def _sector(self, service, snapshot) -> dict:
        return service._build_sector_concentration(snapshot, 30.0, as_of_date=date(2026, 11, 22))

    def test_empty_result_enters_cooldown_so_refresh_does_not_retry(self) -> None:
        manager = _FakeManager(boards=[])

        first = self._sector(_service(manager), _snapshot(["003096"]))
        self.assertEqual(manager.calls, ["003096"])
        self.assertEqual(first["coverage"]["unclassified_count"], 1)
        self.assertEqual(first["coverage"]["skipped_count"], 0)

        # 新实例模拟下一次 /risk 请求：冷却命中，不再付一次失败查询（含限速休眠）。
        second = self._sector(_service(manager), _snapshot(["003096"]))
        self.assertEqual(manager.calls, ["003096"])
        self.assertEqual(second["coverage"]["unclassified_count"], 1)
        self.assertEqual(second["coverage"]["skipped_count"], 1)

    def test_raised_error_also_enters_cooldown(self) -> None:
        manager = _FakeManager(error=RuntimeError("board source down"))

        first = self._sector(_service(manager), _snapshot(["600519"]))
        self.assertEqual(first["coverage"]["failed_count"], 1)
        self.assertEqual(manager.calls, ["600519"])

        self._sector(_service(manager), _snapshot(["600519"]))
        self.assertEqual(manager.calls, ["600519"])

    def test_cooldown_expiry_retries_the_source(self) -> None:
        manager = _FakeManager(boards=[])
        service = _service(manager, cooldown=0.05)

        self._sector(service, _snapshot(["600519"]))
        self.assertEqual(len(manager.calls), 1)

        time.sleep(0.1)
        self._sector(_service(manager, cooldown=0.05), _snapshot(["600519"]))
        self.assertEqual(len(manager.calls), 2)

    def test_successful_lookup_classifies_and_clears_cooldown(self) -> None:
        manager = _FakeManager(results=[[], [{"name": "白酒", "type": "行业"}]])
        service = _service(manager)

        first = self._sector(service, _snapshot(["600519"]))
        self.assertEqual(first["coverage"]["classified_count"], 0)

        # 冷却过期后再次查询，命中板块应计入 classified 并清掉冷却标记。
        with PortfolioRiskService._sector_lookup_cooldown_lock:
            PortfolioRiskService._sector_lookup_cooldown_until["600519"] = time.monotonic() - 1.0

        second = self._sector(_service(manager), _snapshot(["600519"]))
        self.assertEqual(second["coverage"]["classified_count"], 1)
        self.assertEqual(second["coverage"]["skipped_count"], 0)
        self.assertEqual(second["top_sectors"][0]["sector"], "白酒")
        self.assertNotIn("600519", PortfolioRiskService._sector_lookup_cooldown_until)

    def test_budget_stops_querying_remaining_positions(self) -> None:
        # 每次都成功但很慢（0.25s/次），预算 0.3s：只允许发起有限次查询，
        # 剩下的持仓跳过并计数，保证单次请求有界延迟。
        manager = _FakeManager(boards=[{"name": "银行", "type": "行业"}], delay=0.25)
        symbols = ["600519", "600036", "601318", "000001", "600000"]

        result = self._sector(_service(manager, budget=0.3), _snapshot(symbols))

        attempts = len(manager.calls)
        self.assertLessEqual(attempts, 2)
        self.assertEqual(result["coverage"]["classified_count"], attempts)
        self.assertEqual(result["coverage"]["skipped_count"], len(symbols) - attempts)

    def test_cooldown_store_prunes_expired_entries_when_it_grows(self) -> None:
        # 冷却表是进程级共享字典，只写不清理会随见过的代码数量缓涨；写满上限时先剔过期条目。
        now = time.monotonic()
        with PortfolioRiskService._sector_lookup_cooldown_lock:
            for index in range(PortfolioRiskService._SECTOR_LOOKUP_COOLDOWN_MAX_ENTRIES):
                PortfolioRiskService._sector_lookup_cooldown_until[f"OLD{index}"] = now - 1.0

        service = _service(_FakeManager(boards=[]))
        service._mark_sector_lookup_failure("600519")

        with PortfolioRiskService._sector_lookup_cooldown_lock:
            remaining = dict(PortfolioRiskService._sector_lookup_cooldown_until)
        self.assertEqual(list(remaining), ["600519"])

    def test_non_cn_and_fund_positions_never_hit_the_source(self) -> None:
        manager = _FakeManager(boards=[{"name": "白酒", "type": "行业"}])

        result = self._sector(_service(manager), _snapshot(["AAPL"], market="us"))

        self.assertEqual(manager.calls, [])
        self.assertEqual(result["coverage"]["unclassified_count"], 1)
        self.assertEqual(result["coverage"]["skipped_count"], 0)


if __name__ == "__main__":
    unittest.main()
