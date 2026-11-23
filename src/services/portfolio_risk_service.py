# -*- coding: utf-8 -*-
"""Portfolio risk service for concentration, drawdown and stop-loss proximity."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

from src.config import Config, get_config
from data_provider.fund_fetcher import is_fund_code
from src.repositories.portfolio_repo import PortfolioRepository
from src.services.decision_signal_service import DecisionSignalService
from src.services.decision_signal_summary import summarize_decision_signal
from src.services.portfolio_service import PortfolioService

logger = logging.getLogger(__name__)

DEFENSIVE_DECISION_SIGNAL_ACTIONS = ("sell", "reduce", "alert")

# 回撤回填每次调用最多补齐的最近缺失交易日数。首屏 /risk 不应一次性补齐整个
# lookback 窗口（逐日逐账户重算快照），否则首次加载会被拖慢；改为每次补齐最近一批，
# 后续调用再增量补齐历史，权衡为「首屏有界延迟 + 回撤窗口渐进完整」。
DRAWDOWN_BACKFILL_BATCH_DAYS = 30

# sector_concentration 只是风险面板的装饰性字段，但它依赖的板块归属查询目前只有
# efinance 的 `get_belong_board` 一个来源：该接口对部分代码稳定失败，而 data provider
# 每次真正调用前都要付限速休眠（efinance 1.5~3s）。失败一次后在同一进程内冷却一段时间，
# 避免每次刷新风险面板都重复付这笔成本。成功结果由 DataFetcherManager 的 24h
# belong-boards 缓存兜住（空结果不缓存，见 data_provider/base.py），不会走到这里。
SECTOR_LOOKUP_FAILURE_COOLDOWN_SECONDS = 600.0

# 单次风险面板请求内板块查询的时间预算（是否继续发起查询的门槛）。超出后剩余持仓按
# UNCLASSIFIED 处理并计入 coverage["skipped_count"]，避免多只持仓时首屏被拖成几十秒。
# 注意：已在进行的查询不会被中断，最坏耗时约为「预算 + 单次查询耗时」。
SECTOR_LOOKUP_BUDGET_SECONDS = 3.0


class PortfolioRiskService:
    """Compute portfolio risk blocks on top of replayed snapshot data."""

    # 进程内共享：每个请求都会新建 service 实例，冷却状态必须落在类上。
    sector_lookup_failure_cooldown_seconds = SECTOR_LOOKUP_FAILURE_COOLDOWN_SECONDS
    sector_lookup_budget_seconds = SECTOR_LOOKUP_BUDGET_SECONDS
    _sector_lookup_cooldown_lock = RLock()
    _sector_lookup_cooldown_until: Dict[str, float] = {}
    _SECTOR_LOOKUP_COOLDOWN_MAX_ENTRIES = 512

    def __init__(
        self,
        *,
        repo: Optional[PortfolioRepository] = None,
        portfolio_service: Optional[PortfolioService] = None,
        decision_signal_service: Optional[DecisionSignalService] = None,
        config: Optional[Config] = None,
    ):
        self.repo = repo or PortfolioRepository()
        self.portfolio_service = portfolio_service or PortfolioService(repo=self.repo)
        self.decision_signal_service = decision_signal_service or DecisionSignalService(portfolio_repo=self.repo)
        self.config = config or get_config()
        self._data_manager = None
        self._data_manager_init_error = ""

    def get_risk_report(
        self,
        *,
        account_id: Optional[int] = None,
        as_of: Optional[date] = None,
        cost_method: str = "fifo",
        include_realtime: bool = True,
    ) -> Dict[str, Any]:
        as_of_date = as_of or date.today()
        snapshot = self.portfolio_service.get_portfolio_snapshot(
            account_id=account_id,
            as_of=as_of_date,
            cost_method=cost_method,
            include_realtime=include_realtime,
        )

        thresholds = {
            "concentration_alert_pct": float(getattr(self.config, "portfolio_risk_concentration_alert_pct", 35.0)),
            "drawdown_alert_pct": float(getattr(self.config, "portfolio_risk_drawdown_alert_pct", 15.0)),
            "stop_loss_alert_pct": float(getattr(self.config, "portfolio_risk_stop_loss_alert_pct", 10.0)),
            "stop_loss_near_ratio": float(getattr(self.config, "portfolio_risk_stop_loss_near_ratio", 0.8)),
            "lookback_days": int(getattr(self.config, "portfolio_risk_lookback_days", 180)),
        }

        concentration = self._build_concentration(
            snapshot,
            thresholds["concentration_alert_pct"],
            as_of_date=as_of_date,
        )
        sector_concentration = self._build_sector_concentration(
            snapshot,
            thresholds["concentration_alert_pct"],
            as_of_date=as_of_date,
        )
        self._ensure_drawdown_snapshot_window(
            account_id=account_id,
            as_of_date=as_of_date,
            cost_method=cost_method,
            lookback_days=thresholds["lookback_days"],
            include_realtime=include_realtime,
        )
        drawdown = self._build_drawdown(
            account_id=account_id,
            as_of_date=as_of_date,
            cost_method=cost_method,
            threshold_pct=thresholds["drawdown_alert_pct"],
            lookback_days=thresholds["lookback_days"],
        )
        stop_loss = self._build_stop_loss(snapshot, thresholds)
        decision_signal_risk = self._build_decision_signal_risk(snapshot)

        return {
            "as_of": as_of_date.isoformat(),
            "account_id": account_id,
            "cost_method": cost_method,
            "currency": snapshot["currency"],
            "thresholds": thresholds,
            "concentration": concentration,
            "sector_concentration": sector_concentration,
            "drawdown": drawdown,
            "stop_loss": stop_loss,
            "decision_signal_risk": decision_signal_risk,
        }

    def _build_decision_signal_risk(
        self,
        snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        try:
            held_positions = self._held_position_identities(snapshot)
            if not held_positions:
                return self._empty_decision_signal_risk(available=True)
            stock_identities = sorted({
                (position["market"], position["signal_stock_code"])
                for position in held_positions
            })

            defensive_actions = set(DEFENSIVE_DECISION_SIGNAL_ACTIONS)
            latest_by_identity: Dict[Tuple[str, str], Dict[str, Any]] = {}
            page = 1
            while True:
                response = self.decision_signal_service.list_signals(
                    stock_identities=stock_identities,
                    status="active",
                    page=page,
                    page_size=100,
                )
                items = response.get("items", []) if isinstance(response, dict) else []
                for item in items:
                    if str(item.get("action") or "") not in defensive_actions:
                        continue
                    key = (
                        str(item.get("market") or "").strip().lower(),
                        str(item.get("stock_code") or "").strip().upper(),
                    )
                    if key[0] and key[1] and key not in latest_by_identity:
                        latest_by_identity[key] = item
                total = int(response.get("total", 0) or 0) if isinstance(response, dict) else 0
                if page * 100 >= total or not items:
                    break
                page += 1

            risk_items: List[Dict[str, Any]] = []
            action_counts = {action: 0 for action in DEFENSIVE_DECISION_SIGNAL_ACTIONS}
            seen: set[Tuple[Optional[int], str, str, int]] = set()
            for position in held_positions:
                signal = latest_by_identity.get((position["market"], position["signal_stock_code"]))
                summary = summarize_decision_signal(signal)
                if not summary:
                    continue
                action = str(summary.get("action") or "")
                if action not in action_counts:
                    continue
                signal_id = int(summary.get("id") or 0)
                dedupe_key = (position["account_id"], position["market"], position["signal_stock_code"], signal_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                action_counts[action] += 1
                risk_items.append({
                    "account_id": position["account_id"],
                    "symbol": position["symbol"],
                    "market": position["market"],
                    "signal": summary,
                })

            return {
                "available": True,
                "total": len(risk_items),
                "actions": action_counts,
                "items": risk_items,
            }
        except Exception:
            logger.exception("[PortfolioRiskService] Decision signal risk unavailable")
            return self._empty_decision_signal_risk(available=False)

    @staticmethod
    def _empty_decision_signal_risk(*, available: bool) -> Dict[str, Any]:
        return {
            "available": available,
            "total": 0,
            "actions": {action: 0 for action in DEFENSIVE_DECISION_SIGNAL_ACTIONS},
            "items": [],
        }

    @staticmethod
    def _held_position_identities(snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        positions: List[Dict[str, Any]] = []
        for account in snapshot.get("accounts", []) or []:
            account_id = account.get("account_id")
            for pos in account.get("positions", []) or []:
                symbol = str(pos.get("symbol") or "").strip().upper()
                market = str(pos.get("market") or "").strip().lower()
                # 场外基金不做股票决策信号归一化（按净值估值，无股票档位）
                if not symbol or is_fund_code(symbol) or market not in {"cn", "hk", "us", "jp", "kr", "tw"}:
                    continue
                signal_stock_code = DecisionSignalService.normalize_stock_code_for_signal(symbol, market=market)
                positions.append({
                    "account_id": account_id,
                    "symbol": symbol,
                    "market": market,
                    "signal_stock_code": signal_stock_code,
                })
        return positions

    def _ensure_drawdown_snapshot_window(
        self,
        *,
        account_id: Optional[int],
        as_of_date: date,
        cost_method: str,
        lookback_days: int,
        include_realtime: bool,
    ) -> None:
        if lookback_days <= 0:
            return

        start_date = self._resolve_backfill_start_date(
            account_id=account_id,
            as_of_date=as_of_date,
            lookback_days=lookback_days,
        )
        if start_date > as_of_date:
            return

        existing_rows = self.repo.list_daily_snapshots_for_risk(
            as_of=as_of_date,
            cost_method=cost_method,
            account_id=account_id,
            lookback_days=lookback_days,
        )
        batch_days = max(1, DRAWDOWN_BACKFILL_BATCH_DAYS)

        def _day_iter() -> List[date]:
            days: List[date] = []
            current = start_date
            while current <= as_of_date:
                days.append(current)
                current += timedelta(days=1)
            return days

        if account_id is not None:
            existing_dates = {row.snapshot_date for row in existing_rows if int(row.account_id) == int(account_id)}
            missing = [day for day in _day_iter() if day not in existing_dates]
            # 只补齐最近 batch_days 个缺失日，避免首屏被整窗回填拖慢。
            for current_date in missing[-batch_days:]:
                self.portfolio_service.get_portfolio_snapshot(
                    account_id=account_id,
                    as_of=current_date,
                    cost_method=cost_method,
                    include_realtime=include_realtime,
                )
                existing_dates.add(current_date)
            return

        account_ids = [int(account.id) for account in self.repo.list_accounts(include_inactive=False)]
        if not account_ids:
            return
        existing_pairs = {(int(row.account_id), row.snapshot_date) for row in existing_rows}
        missing = [
            day for day in _day_iter()
            if not all((aid, day) in existing_pairs for aid in account_ids)
        ]
        for current_date in missing[-batch_days:]:
            self.portfolio_service.get_portfolio_snapshot(
                account_id=None,
                as_of=current_date,
                cost_method=cost_method,
                include_realtime=include_realtime,
            )
            for aid in account_ids:
                existing_pairs.add((aid, current_date))

    def _resolve_backfill_start_date(
        self,
        *,
        account_id: Optional[int],
        as_of_date: date,
        lookback_days: int,
    ) -> date:
        window_start = as_of_date - timedelta(days=lookback_days)
        if account_id is not None:
            first_activity = self.repo.get_first_activity_date(account_id=account_id, as_of=as_of_date)
            return max(window_start, first_activity or as_of_date)

        first_activity_candidates: List[date] = []
        for account in self.repo.list_accounts(include_inactive=False):
            first_activity = self.repo.get_first_activity_date(account_id=int(account.id), as_of=as_of_date)
            if first_activity is not None:
                first_activity_candidates.append(first_activity)
        if not first_activity_candidates:
            return as_of_date
        return max(window_start, min(first_activity_candidates))

    @staticmethod
    def _normalize_concentration_symbol(symbol: str, *, market: str) -> str:
        """归一化代码用于集中度合并，避免同一股票以不同格式（600519/SH600519/600519.SH）被拆成多条。"""
        if not market:
            return symbol
        try:
            return DecisionSignalService.normalize_stock_code_for_signal(symbol, market=market)
        except Exception:
            return symbol

    def _build_concentration(self, snapshot: Dict[str, Any], threshold_pct: float, *, as_of_date: date) -> Dict[str, Any]:
        total_mv = float(snapshot.get("total_market_value", 0.0) or 0.0)
        exposure_by_symbol: Dict[str, float] = {}
        display_by_key: Dict[str, str] = {}
        for account in snapshot.get("accounts", []):
            for pos in account.get("positions", []):
                raw_symbol = str(pos.get("symbol") or "").strip().upper()
                if not raw_symbol:
                    continue
                market = str(pos.get("market") or account.get("market") or "").strip().lower()
                key_symbol = self._normalize_concentration_symbol(raw_symbol, market=market)
                market_value = float(pos.get("market_value_base") or 0.0)
                valuation_currency = str(pos.get("valuation_currency") or account.get("base_currency") or "CNY")
                converted, _, _ = self.portfolio_service.convert_amount(
                    amount=market_value,
                    from_currency=valuation_currency,
                    to_currency="CNY",
                    as_of_date=as_of_date,
                )
                exposure_by_symbol[key_symbol] = exposure_by_symbol.get(key_symbol, 0.0) + converted
                display_by_key.setdefault(key_symbol, raw_symbol)

        rows = []
        for key_symbol, exposure in exposure_by_symbol.items():
            weight = (exposure / total_mv * 100.0) if total_mv > 0 else 0.0
            rows.append(
                {
                    "symbol": display_by_key[key_symbol],
                    "market_value_base": round(exposure, 6),
                    "weight_pct": round(weight, 4),
                    "is_alert": bool(weight >= threshold_pct),
                }
            )
        rows.sort(key=lambda item: item["market_value_base"], reverse=True)

        top_weight = rows[0]["weight_pct"] if rows else 0.0
        return {
            "total_market_value": round(total_mv, 6),
            "top_weight_pct": round(float(top_weight), 4),
            "alert": bool(top_weight >= threshold_pct),
            "top_positions": rows[:10],
        }

    def _build_sector_concentration(
        self,
        snapshot: Dict[str, Any],
        threshold_pct: float,
        *,
        as_of_date: date,
    ) -> Dict[str, Any]:
        total_mv = float(snapshot.get("total_market_value", 0.0) or 0.0)
        sector_exposure: Dict[str, float] = {}
        sector_symbols: Dict[str, set] = {}
        coverage = {
            "classified_count": 0,
            "unclassified_count": 0,
            "failed_count": 0,
            # 因冷却/预算而没真正发起板块查询的持仓数（unclassified_count 的子集）。
            "skipped_count": 0,
        }
        errors: List[str] = []
        board_cache: Dict[Tuple[str, str], str] = {}
        deadline = time.monotonic() + max(0.0, float(self.sector_lookup_budget_seconds or 0.0))

        for account in snapshot.get("accounts", []):
            for pos in account.get("positions", []):
                symbol = str(pos.get("symbol") or "").strip().upper()
                market = str(pos.get("market") or account.get("market") or "").strip().lower()
                if not symbol:
                    continue
                # 归一化用于同目录重个股计数（symbol_count），板块查询仍用原始代码，避免改变 data provider 行为。
                key_symbol = self._normalize_concentration_symbol(symbol, market=market)

                market_value = float(pos.get("market_value_base") or 0.0)
                valuation_currency = str(pos.get("valuation_currency") or account.get("base_currency") or "CNY")
                converted, _, _ = self.portfolio_service.convert_amount(
                    amount=market_value,
                    from_currency=valuation_currency,
                    to_currency="CNY",
                    as_of_date=as_of_date,
                )

                sector = self._resolve_primary_sector(
                    symbol=symbol,
                    market=market,
                    board_cache=board_cache,
                    coverage=coverage,
                    errors=errors,
                    deadline=deadline,
                )
                sector_exposure[sector] = sector_exposure.get(sector, 0.0) + converted
                sector_symbols.setdefault(sector, set()).add(key_symbol)

        rows = []
        for sector, exposure in sector_exposure.items():
            weight = (exposure / total_mv * 100.0) if total_mv > 0 else 0.0
            rows.append(
                {
                    "sector": sector,
                    "market_value_base": round(exposure, 6),
                    "weight_pct": round(weight, 4),
                    "symbol_count": len(sector_symbols.get(sector, set())),
                    "is_alert": bool(weight >= threshold_pct),
                }
            )
        rows.sort(key=lambda item: item["market_value_base"], reverse=True)
        top_weight = rows[0]["weight_pct"] if rows else 0.0

        return {
            "total_market_value": round(total_mv, 6),
            "top_weight_pct": round(float(top_weight), 4),
            "alert": bool(top_weight >= threshold_pct),
            "top_sectors": rows[:10],
            "coverage": coverage,
            "errors": errors[:20],
        }

    def _resolve_primary_sector(
        self,
        *,
        symbol: str,
        market: str,
        board_cache: Dict[Tuple[str, str], str],
        coverage: Dict[str, int],
        errors: List[str],
        deadline: Optional[float] = None,
    ) -> str:
        cache_key = (symbol, market)
        if cache_key in board_cache:
            return board_cache[cache_key]

        if market != "cn" or is_fund_code(symbol):
            return self._mark_sector_unclassified(cache_key, board_cache, coverage)

        # 失败冷却内先不发起查询：板块来源目前只有 efinance，失败一次后每次重试都要先付
        # 限速休眠（1.5~3s），而风险面板每次刷新都会重新跑这个装饰性字段。
        if self._is_sector_lookup_cooling_down(symbol):
            return self._mark_sector_unclassified(cache_key, board_cache, coverage, skipped=True)

        # 预算内未轮到查询时同样按 UNCLASSIFIED 处理，保证单次请求的有界延迟。
        if deadline is not None and time.monotonic() >= deadline:
            return self._mark_sector_unclassified(cache_key, board_cache, coverage, skipped=True)

        try:
            boards = self._fetch_belong_boards(symbol)
            sector_name = self._pick_primary_board_name(boards or [])
        except Exception as exc:
            self._mark_sector_lookup_failure(symbol)
            coverage["failed_count"] += 1
            errors.append(f"{symbol}: {exc}")
            return self._mark_sector_unclassified(cache_key, board_cache, coverage)

        if boards is None:
            # data manager 不可用不是「这只代码没板块」，不写冷却，下次请求再试。
            return self._mark_sector_unclassified(cache_key, board_cache, coverage)

        if sector_name:
            self._clear_sector_lookup_failure(symbol)
            coverage["classified_count"] += 1
            board_cache[cache_key] = sector_name
            return sector_name

        # 所有源都答不上来（空结果）与「该股确实无板块」无法区分，但两者都不应该
        # 在同一个进程里反复重试，统一进入冷却。
        self._mark_sector_lookup_failure(symbol)
        return self._mark_sector_unclassified(cache_key, board_cache, coverage)

    @staticmethod
    def _mark_sector_unclassified(
        cache_key: Tuple[str, str],
        board_cache: Dict[Tuple[str, str], str],
        coverage: Dict[str, int],
        *,
        skipped: bool = False,
    ) -> str:
        coverage["unclassified_count"] += 1
        if skipped:
            coverage["skipped_count"] = coverage.get("skipped_count", 0) + 1
        board_cache[cache_key] = "UNCLASSIFIED"
        return board_cache[cache_key]

    def _is_sector_lookup_cooling_down(self, symbol: str) -> bool:
        cooldown = float(self.sector_lookup_failure_cooldown_seconds or 0.0)
        if cooldown <= 0:
            return False
        now = time.monotonic()
        with self._sector_lookup_cooldown_lock:
            until = self._sector_lookup_cooldown_until.get(symbol)
            if until is None:
                return False
            if until <= now:
                self._sector_lookup_cooldown_until.pop(symbol, None)
                return False
            return True

    def _mark_sector_lookup_failure(self, symbol: str) -> None:
        cooldown = float(self.sector_lookup_failure_cooldown_seconds or 0.0)
        if cooldown <= 0:
            return
        now = time.monotonic()
        with self._sector_lookup_cooldown_lock:
            # 冷却表是进程级共享的，只写入不清理的话会随着见过的代码数量缓涨；
            # 超过上限时先剔掉已过期条目（正在冷却的保留）。
            if len(self._sector_lookup_cooldown_until) >= self._SECTOR_LOOKUP_COOLDOWN_MAX_ENTRIES:
                expired = [key for key, until in self._sector_lookup_cooldown_until.items() if until <= now]
                for key in expired:
                    self._sector_lookup_cooldown_until.pop(key, None)
            self._sector_lookup_cooldown_until[symbol] = now + cooldown

    def _clear_sector_lookup_failure(self, symbol: str) -> None:
        with self._sector_lookup_cooldown_lock:
            self._sector_lookup_cooldown_until.pop(symbol, None)

    @classmethod
    def clear_sector_lookup_cooldown_for_tests(cls) -> None:
        """清空进程内失败冷却（测试隔离用，与 belong-boards 缓存的约定一致）。"""
        with cls._sector_lookup_cooldown_lock:
            cls._sector_lookup_cooldown_until.clear()

    def _fetch_belong_boards(self, symbol: str) -> Optional[List[Dict[str, Any]]]:
        """取标的所属板块；``None`` 表示 data manager 不可用（与「拿到了空结果」区分）。"""
        manager = self._get_data_manager()
        if manager is None:
            return None
        result = manager.get_belong_boards(symbol)
        if isinstance(result, list):
            return result
        return []

    @staticmethod
    def _pick_primary_board_name(boards: List[Dict[str, Any]]) -> Optional[str]:
        if not boards:
            return None

        preferred: Optional[str] = None
        fallback: Optional[str] = None
        for item in boards:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            if fallback is None:
                fallback = name
            type_text = str(item.get("type") or "").strip().lower()
            if "行业" in type_text or "industry" in type_text:
                preferred = name
                break
        return preferred or fallback

    def _get_data_manager(self):
        if self._data_manager is not None:
            return self._data_manager
        if self._data_manager_init_error:
            return None
        try:
            from data_provider import DataFetcherManager

            self._data_manager = DataFetcherManager()
            return self._data_manager
        except Exception as exc:  # pragma: no cover - fail-open initialization
            self._data_manager_init_error = str(exc)
            return None

    def _build_drawdown(
        self,
        *,
        account_id: Optional[int],
        as_of_date: date,
        cost_method: str,
        threshold_pct: float,
        lookback_days: int,
    ) -> Dict[str, Any]:
        rows = self.repo.list_daily_snapshots_for_risk(
            as_of=as_of_date,
            cost_method=cost_method,
            account_id=account_id,
            lookback_days=lookback_days,
        )
        if not rows:
            return {
                "series_points": 0,
                "max_drawdown_pct": 0.0,
                "current_drawdown_pct": 0.0,
                "alert": False,
                "fx_stale": False,
                "series": [],
            }

        grouped: Dict[str, float] = {}
        stale_flag = False
        for row in rows:
            key = row.snapshot_date.isoformat()
            converted, stale, _ = self.portfolio_service.convert_amount(
                amount=float(row.total_equity or 0.0),
                from_currency=str(row.base_currency or "CNY"),
                to_currency="CNY",
                as_of_date=row.snapshot_date,
            )
            grouped[key] = grouped.get(key, 0.0) + converted
            stale_flag = stale_flag or stale or bool(row.fx_stale)

        series: List[Tuple[str, float]] = sorted(grouped.items(), key=lambda item: item[0])
        peak = 0.0
        max_drawdown = 0.0
        current_drawdown = 0.0
        equity_series: List[Dict[str, Any]] = []
        for date_key, equity in series:
            peak = max(peak, equity)
            if peak <= 0:
                drawdown = 0.0
            else:
                drawdown = (peak - equity) / peak * 100.0
            max_drawdown = max(max_drawdown, drawdown)
            current_drawdown = drawdown
            equity_series.append({
                "date": date_key,
                "equity": round(equity, 2),
                "drawdown_pct": round(drawdown, 4),
            })

        return {
            "series_points": len(series),
            "max_drawdown_pct": round(max_drawdown, 4),
            "current_drawdown_pct": round(current_drawdown, 4),
            "alert": bool(max_drawdown >= threshold_pct),
            "fx_stale": stale_flag,
            "series": equity_series,
        }

    @staticmethod
    def _build_stop_loss(snapshot: Dict[str, Any], thresholds: Dict[str, Any]) -> Dict[str, Any]:
        stop_loss_pct = float(thresholds["stop_loss_alert_pct"])
        near_ratio = float(thresholds["stop_loss_near_ratio"])
        near_threshold = stop_loss_pct * near_ratio

        warnings: List[Dict[str, Any]] = []
        for account in snapshot.get("accounts", []):
            for pos in account.get("positions", []):
                # 场外基金按净值，无股票式止损语义，跳过
                if is_fund_code(str(pos.get("symbol") or "")):
                    continue
                avg_cost = float(pos.get("avg_cost", 0.0) or 0.0)
                last_price = float(pos.get("last_price", 0.0) or 0.0)
                # 缺价（last_price<=0，price_available=False）时无法判断止损位置，
                # 否则会按 (avg_cost-0)/avg_cost 误算成 100% 亏损而误报。
                if avg_cost <= 0 or last_price <= 0:
                    continue
                loss_pct = max(0.0, (avg_cost - last_price) / avg_cost * 100.0)
                if loss_pct < near_threshold:
                    continue
                warnings.append(
                    {
                        "account_id": account.get("account_id"),
                        "symbol": pos.get("symbol"),
                        "avg_cost": round(avg_cost, 8),
                        "last_price": round(last_price, 8),
                        "loss_pct": round(loss_pct, 4),
                        "near_threshold_pct": round(near_threshold, 4),
                        "is_triggered": bool(loss_pct >= stop_loss_pct),
                    }
                )

        warnings.sort(key=lambda item: item["loss_pct"], reverse=True)
        return {
            "near_alert": len(warnings) > 0,
            "triggered_count": sum(1 for item in warnings if item["is_triggered"]),
            "near_count": len(warnings),
            "items": warnings[:20],
        }
