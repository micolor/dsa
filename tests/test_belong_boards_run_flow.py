# -*- coding: utf-8 -*-
"""Regression tests for belong-board run-flow diagnostics."""

from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    current_diagnostic_snapshot,
    reset_run_diagnostic_context,
)
from data_provider.base import DataFetcherManager


def setup_function(_function):
    # belong-boards cache is a shared class-level cache; clear it so each test's
    # provider-run recording is isolated (cache is keyed by stock code "600519").
    DataFetcherManager.clear_belong_boards_cache_for_tests()


class _BoardFetcher:
    def __init__(self, name: str, result):
        self.name = name
        self.priority = 0
        self._result = result
        self.calls = 0

    def get_belong_board(self, _stock_code: str):
        self.calls += 1
        return self._result


class _FailingBoardFetcher(_BoardFetcher):
    def __init__(self, name: str, error: Exception):
        super().__init__(name, [])
        self._error = error

    def get_belong_board(self, _stock_code: str):
        self.calls += 1
        raise self._error


def _capture_belong_board_run(manager: DataFetcherManager):
    flow_events = []
    token = activate_run_diagnostic_context(
        trace_id="trace-boards",
        task_id="task-boards",
        query_id="query-boards",
        stock_code="600519",
        trigger_source="api",
        event_sink=flow_events.append,
    )
    try:
        boards = manager.get_belong_boards("600519")
        diagnostics = current_diagnostic_snapshot()
    finally:
        reset_run_diagnostic_context(token)
    return boards, diagnostics, flow_events


def test_get_belong_boards_records_successful_provider_run():
    manager = DataFetcherManager(
        fetchers=[
            _BoardFetcher(
                "BoardFetcher",
                [{"name": "白酒", "type": "行业"}],
            )
        ]
    )

    boards, diagnostics, flow_events = _capture_belong_board_run(manager)

    assert boards
    assert diagnostics is not None
    provider_runs = diagnostics["provider_runs"]
    assert len(provider_runs) == 1
    assert provider_runs[0]["data_type"] == "belong_boards"
    assert provider_runs[0]["provider"] == "BoardFetcher"
    assert provider_runs[0]["operation"] == "get_belong_board"
    assert provider_runs[0]["success"] is True
    assert provider_runs[0]["record_count"] == len(boards)
    assert [event["type"] for event in flow_events] == ["provider_run_started", "provider_run"]
    assert flow_events[0]["node_id"] == flow_events[1]["node_id"]
    assert flow_events[0]["node_id"] == "provider_belong_boards_boardfetcher_1"


def test_get_belong_boards_records_empty_attempt_and_fallback():
    manager = DataFetcherManager(
        fetchers=[
            _BoardFetcher("EmptyBoardFetcher", []),
            _BoardFetcher("FallbackBoardFetcher", [{"name": "电力设备", "type": "行业"}]),
        ]
    )

    boards, diagnostics, flow_events = _capture_belong_board_run(manager)

    assert boards
    assert diagnostics is not None
    provider_runs = diagnostics["provider_runs"]
    assert [run["provider"] for run in provider_runs] == ["EmptyBoardFetcher", "FallbackBoardFetcher"]
    assert [run["success"] for run in provider_runs] == [False, True]
    assert provider_runs[0]["error_type"] == "empty"
    assert provider_runs[0]["fallback_to"] == "FallbackBoardFetcher"
    assert len(flow_events) == 4


class _RecoveringBoardFetcher:
    """先失败，调用方把 ``failing`` 置 False 后恢复正常。"""

    name = "RecoveringBoardFetcher"
    priority = 0

    def __init__(self):
        self.calls = 0
        self.failing = True

    def get_belong_board(self, _stock_code: str):
        self.calls += 1
        if self.failing:
            raise RuntimeError("board source down")
        return [{"name": "白酒", "type": "行业"}]


def test_empty_result_is_not_cached_for_the_rest_of_the_day():
    """一次失败不能让关联板块在 24h 内对同一进程永久消失。

    修复前 ``boards = boards_result if boards_result is not None else []`` 之后
    无条件写缓存，于是「全部源失败」和「该股确实没有板块」被当成同一件事缓存
    24 小时：数据源恢复后再次分析同一只股票会命中缓存直接返回空，
    ``_attach_belong_boards_to_fundamental_context`` → 通知渲染器会对空列表
    提前返回，报告里的关联板块章节整天消失且没有任何错误提示。
    """
    fetcher = _RecoveringBoardFetcher()
    manager = DataFetcherManager(fetchers=[fetcher])

    assert manager.get_belong_boards("600519") == []
    assert fetcher.calls == 1

    fetcher.failing = False
    boards = manager.get_belong_boards("600519")

    assert boards == [{"name": "白酒", "type": "行业"}]
    assert fetcher.calls == 2


def test_successful_result_is_still_cached():
    """非空结果照旧走 24h 缓存，避免重复拉取。"""
    fetcher = _BoardFetcher("BoardFetcher", [{"name": "白酒", "type": "行业"}])
    manager = DataFetcherManager(fetchers=[fetcher])

    assert manager.get_belong_boards("600519")
    assert manager.get_belong_boards("600519")
    assert fetcher.calls == 1


def test_get_belong_boards_records_exception_attempt_and_fallback():
    manager = DataFetcherManager(
        fetchers=[
            _FailingBoardFetcher("FailingBoardFetcher", RuntimeError("board source down")),
            _BoardFetcher("FallbackBoardFetcher", [{"name": "电力设备", "type": "行业"}]),
        ]
    )

    boards, diagnostics, flow_events = _capture_belong_board_run(manager)

    assert boards
    assert diagnostics is not None
    provider_runs = diagnostics["provider_runs"]
    assert [run["provider"] for run in provider_runs] == ["FailingBoardFetcher", "FallbackBoardFetcher"]
    assert provider_runs[0]["success"] is False
    assert provider_runs[0]["error_type"] == "RuntimeError"
    assert provider_runs[0]["fallback_to"] == "FallbackBoardFetcher"
    assert provider_runs[1]["success"] is True
    assert len(flow_events) == 4
