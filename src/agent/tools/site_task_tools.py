# -*- coding: utf-8 -*-
"""Long-running site operations exposed as background tasks.

Each handler submits a real background task through
``src.services.site_task_submitter`` and returns the ``action_task`` envelope the
agent runner forwards to the chat UI (``kind``/``summary``/``task_id``/
``status_endpoint``).  The status endpoint is intentionally the shared analysis
task status route: every submitted job is tracked by the same task queue.
"""

from __future__ import annotations

import json
from datetime import date
from typing import List, Optional

from src.agent.tools.registry import ToolDefinition, ToolParameter, ToolPolicy
from src.services.site_task_submitter import (
    submit_backtest,
    submit_fund_nav_backfill,
    submit_fund_nav_reviews,
    submit_intelligence_fetch,
    submit_market_review,
    submit_paper_backfill,
    submit_screening,
    submit_signal_outcomes,
    submit_stock_analysis,
)

_TASK_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    execution_mode="task",
    risk_level="low",
    cancellation_safe=False,
)

# Tools that accept a stock_code parameter must declare the stock scope
# dimension to satisfy strict tool-surface validation.
_STOCK_TASK_POLICY = ToolPolicy.declared(
    read_only=False,
    side_effects=["write"],
    scope_dimensions=["stock"],
    execution_mode="task",
    risk_level="low",
    cancellation_safe=False,
)


def _envelope(kind: str, summary: str, task) -> str:
    """Serialize the task event envelope consumed by the agent runner."""
    return json.dumps(
        {
            "kind": kind,
            "summary": summary,
            "task_id": str(task.task_id),
            "status_endpoint": f"/api/v1/analysis/status/{task.task_id}",
        },
        ensure_ascii=False,
    )


def _handle_start_stock_analysis(
    stock_code: str,
    report_type: str = "detailed",
    force_refresh: bool = False,
) -> str:
    task = submit_stock_analysis(
        stock_code, report_type=report_type, force_refresh=bool(force_refresh)
    )
    return _envelope("stock_analysis", f"已提交「{stock_code}」分析任务", task)


def _handle_start_market_review(
    region: Optional[str] = None,
    **_ignored: object,
) -> str:
    # 助手触发的大盘复盘永远不推送通知：该值由服务端固定，模型无法通过
    # 参数覆盖。``**_ignored`` 只用于吞掉模型仍按旧 schema 传入的
    # ``send_notification``，避免整次调用因多余参数失败。
    task = submit_market_review(send_notification=False, region=region)
    return _envelope("market_review", "已提交大盘复盘任务", task)


def _handle_run_backtest(
    code: Optional[str] = None,
    force: bool = False,
    limit: int = 200,
) -> str:
    task = submit_backtest(code, force=bool(force), limit=int(limit))
    target = f"「{code}」" if code else "全量"
    return _envelope("backtest_run", f"已提交{target}回测任务", task)


def _handle_start_screening(
    strategy: str,
    market: str = "cn",
    max_results: int = 20,
    variant_seed: str = "",
) -> str:
    task = submit_screening(
        strategy=strategy,
        market=market,
        max_results=int(max_results),
        variant_seed=variant_seed,
    )
    return _envelope("screening_run", f"已提交「{strategy}」选股任务", task)


def _handle_fetch_intelligence() -> str:
    task = submit_intelligence_fetch()
    return _envelope("intelligence_fetch", "已提交情报抓取任务", task)


def _handle_backfill_fund_nav_reviews(
    fund_code: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    dry_run: bool = True,
    resolve_missing: bool = False,
) -> str:
    task = submit_fund_nav_backfill(
        fund_code=fund_code,
        since=date.fromisoformat(since) if since else None,
        until=date.fromisoformat(until) if until else None,
        dry_run=bool(dry_run),
        resolve_missing=bool(resolve_missing),
    )
    return _envelope("fund_nav_backfill", "已提交基金净值复盘补录任务", task)


def _handle_run_fund_nav_reviews(
    fund_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
) -> str:
    task = submit_fund_nav_reviews(
        fund_code=fund_code, horizons=horizons, force=bool(force)
    )
    return _envelope("fund_nav_run", "已提交基金净值复盘评估任务（不设条数上限）", task)


def _handle_run_signal_outcomes(
    stock_code: Optional[str] = None,
    horizons: Optional[List[str]] = None,
    force: bool = False,
) -> str:
    task = submit_signal_outcomes(
        stock_code=stock_code, horizons=horizons, force=bool(force)
    )
    return _envelope("signal_outcomes_run", "已提交信号后验评估任务（不设条数上限）", task)


def _handle_backfill_paper_signals(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    account_id: Optional[int] = None,
) -> str:
    from src.services.paper_service import PaperService

    service = PaperService()
    account = int(account_id) if account_id is not None else int(
        service.get_or_create_account()["account_id"]
    )
    start = date.fromisoformat(from_date) if from_date else date.today().replace(day=1)
    task = submit_paper_backfill(
        account,
        start,
        date.fromisoformat(to_date) if to_date else None,
    )
    return _envelope("paper_backfill", "已提交模拟盘信号回填任务", task)


start_stock_analysis = ToolDefinition(
    name="start_stock_analysis",
    description="提交一只股票的分析任务，后台执行；返回任务 ID 供查询进度。",
    parameters=[
        ToolParameter(name="stock_code", type="string", description="股票代码", required=True),
        ToolParameter(name="report_type", type="string", description="报告类型，默认 detailed", required=False),
        ToolParameter(name="force_refresh", type="boolean", description="忽略缓存强制重跑", required=False),
    ],
    handler=_handle_start_stock_analysis,
    category="site_task",
    policy=_STOCK_TASK_POLICY,
)

start_market_review = ToolDefinition(
    name="start_market_review",
    description="提交大盘复盘任务，后台执行；助手发起时不推送通知。",
    parameters=[
        ToolParameter(name="region", type="string", description="市场区域 cn/hk/us", required=False),
    ],
    handler=_handle_start_market_review,
    category="site_task",
    policy=_TASK_POLICY,
)

run_backtest = ToolDefinition(
    name="run_backtest",
    description="提交后验/回测任务，可指定股票或全量。",
    parameters=[
        ToolParameter(name="code", type="string", description="股票代码，省略为全量", required=False),
        ToolParameter(name="force", type="boolean", description="强制重算", required=False),
        ToolParameter(name="limit", type="integer", description="最多评估条数，默认 200", required=False),
    ],
    handler=_handle_run_backtest,
    category="site_task",
    policy=_TASK_POLICY,
)

start_screening = ToolDefinition(
    name="start_screening",
    description="提交选股任务，后台执行。",
    parameters=[
        ToolParameter(name="strategy", type="string", description="策略 ID", required=True),
        ToolParameter(name="market", type="string", description="市场，默认 cn", required=False),
        ToolParameter(name="max_results", type="integer", description="候选数量上限，默认 20", required=False),
        ToolParameter(name="variant_seed", type="string", description="结果变体种子", required=False),
    ],
    handler=_handle_start_screening,
    category="site_task",
    policy=_TASK_POLICY,
)

fetch_intelligence = ToolDefinition(
    name="fetch_intelligence",
    description="抓取所有已启用情报源，后台执行。",
    parameters=[],
    handler=_handle_fetch_intelligence,
    category="site_task",
    policy=_TASK_POLICY,
)

backfill_fund_nav_reviews = ToolDefinition(
    name="backfill_fund_nav_reviews",
    description="从历史基金报告补录净值复盘锚点，后台执行；默认 dry-run 不写入。",
    parameters=[
        ToolParameter(name="fund_code", type="string", description="基金代码", required=False),
        ToolParameter(name="since", type="string", description="起始日期 YYYY-MM-DD", required=False),
        ToolParameter(name="until", type="string", description="结束日期 YYYY-MM-DD", required=False),
        ToolParameter(name="dry_run", type="boolean", description="默认 true", required=False),
        ToolParameter(name="resolve_missing", type="boolean", description="旧报告联网匹配锚点", required=False),
    ],
    handler=_handle_backfill_fund_nav_reviews,
    category="site_task",
    policy=_TASK_POLICY,
)

run_fund_nav_reviews = ToolDefinition(
    name="run_fund_nav_reviews",
    description="评估所有到期基金复盘锚点，后台持续执行直到没有可评估行；不设条数上限。",
    parameters=[
        ToolParameter(name="fund_code", type="string", description="只评估该基金", required=False),
        ToolParameter(name="horizons", type="array", description="1m/3m/6m/1y 子集", required=False),
        ToolParameter(name="force", type="boolean", description="忽略冷却强制重算", required=False),
    ],
    handler=_handle_run_fund_nav_reviews,
    category="site_task",
    policy=_TASK_POLICY,
)

run_signal_outcomes = ToolDefinition(
    name="run_signal_outcomes",
    description="评估决策信号后验结果，后台持续执行直到没有可评估行；不设条数上限。",
    parameters=[
        ToolParameter(name="stock_code", type="string", description="只评估该股票", required=False),
        ToolParameter(name="horizons", type="array", description="周期子集", required=False),
        ToolParameter(name="force", type="boolean", description="强制重算", required=False),
    ],
    handler=_handle_run_signal_outcomes,
    category="site_task",
    policy=_STOCK_TASK_POLICY,
)

backfill_paper_signals = ToolDefinition(
    name="backfill_paper_signals",
    description="回填模拟盘历史信号成交，后台执行。",
    parameters=[
        ToolParameter(name="from_date", type="string", description="起始日期 YYYY-MM-DD，默认本月 1 日", required=False),
        ToolParameter(name="to_date", type="string", description="结束日期 YYYY-MM-DD", required=False),
        ToolParameter(name="account_id", type="integer", description="模拟盘账户 ID", required=False),
    ],
    handler=_handle_backfill_paper_signals,
    category="site_task",
    policy=_TASK_POLICY,
)

ALL_SITE_TASK_TOOLS = [
    start_stock_analysis,
    start_market_review,
    run_backtest,
    start_screening,
    fetch_intelligence,
    backfill_fund_nav_reviews,
    run_fund_nav_reviews,
    run_signal_outcomes,
    backfill_paper_signals,
]
