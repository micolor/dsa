# -*- coding: utf-8 -*-
"""Service tests for the paper-trading engine."""

from __future__ import annotations

import os
from datetime import date, datetime
from unittest import mock

import pandas as pd
import pytest

from src.config import Config
from src.services import paper_notify as ps_notify
from src.services import paper_service as ps
from src.services.paper_service import PaperService, clear_bar_cache_for_tests
from src.storage import DatabaseManager, DecisionSignalRecord, utc_naive_now


def setup_function(_function):
    # The per-stock bar cache is a shared module-level cache keyed by stock code;
    # clear it so each test's seeded daily data is isolated (tests reuse "600519").
    clear_bar_cache_for_tests()


# Pinned so the cash assertions below do not depend on an ambient .env turning
# fees off, changing the commission rate, or adding slippage.
_FEE_ENV = {
    "PAPER_FEE_ENABLED": "true",
    "PAPER_FEE_COMMISSION_RATE": "0.00025",
    "PAPER_FEE_SLIPPAGE_BPS": "0",
}


@pytest.fixture()
def isolated_db(tmp_path):
    old_database_path = os.environ.get("DATABASE_PATH")
    old_fee_env = {key: os.environ.get(key) for key in _FEE_ENV}
    db_path = tmp_path / "paper_service.db"
    os.environ["DATABASE_PATH"] = str(db_path)
    os.environ.update(_FEE_ENV)
    Config.reset_instance()
    DatabaseManager.reset_instance()
    db = DatabaseManager.get_instance()
    try:
        yield db
    finally:
        DatabaseManager.reset_instance()
        Config.reset_instance()
        for key, value in old_fee_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if old_database_path is None:
            os.environ.pop("DATABASE_PATH", None)
        else:
            os.environ["DATABASE_PATH"] = old_database_path


@pytest.fixture()
def service(isolated_db):
    return PaperService(isolated_db)


def _service_with_fee_env(db, **env) -> PaperService:
    """Build a service with overridden fee env vars.

    `Config.get_instance()` is lazy, so setting the env before constructing the
    service is enough for the new settings to take effect.
    """
    os.environ.update({key: str(value) for key, value in env.items()})
    Config.reset_instance()
    return PaperService(db)


def _seed_daily(db, code, d, o, h, l, c):
    df = pd.DataFrame([{
        "date": d,
        "open": o, "high": h, "low": l, "close": c,
        "volume": 0, "amount": 0.0, "pct_chg": 0.0,
        "ma5": c, "ma10": c, "ma20": c, "volume_ratio": 1.0,
    }])
    db.save_daily_data(df, code, "test")


def _make_signal(db, action="buy", code="600519", entry_high=100.0, stop_loss=95.0,
                 target_price=115.0, created_at=None, market="cn"):
    with db.get_session() as session:
        row = DecisionSignalRecord(
            stock_code=code,
            stock_name="贵州茅台",
            market=market,
            source_type="analysis",
            source_agent="test",
            source_report_id=1001,
            trace_id="trace-x",
            market_phase="intraday",
            trigger_source="api",
            action=action,
            action_label=action,
            confidence=0.8,
            score=88,
            horizon="3d",
            entry_low=98.0,
            entry_high=entry_high,
            stop_loss=stop_loss,
            target_price=target_price,
            status="active",
            created_at=created_at or utc_naive_now(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row


def test_process_signal_opens_position(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    result = service.process_signal(sig.id)
    assert result["disposition"] == "opened"

    positions = service.get_positions(service.get_or_create_account()["account_id"])
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] == 2000  # 20% of 1e6 / 100
    # 成交 2000 @ 100 = 200,000：佣金 max(200000*0.00025, 5) = 50，过户费 200000*0.00001 = 2，
    # 买入不收印花税。成本价含费：(200000 + 52) / 2000 = 100.026。
    assert open_pos["avg_cost"] == pytest.approx(100.026)

    snapshot = service.get_snapshot(service.get_or_create_account()["account_id"])
    assert snapshot["cash"] == 1000000.0 - 200000.0 - 52.0


def test_process_signal_dedup(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    first = service.process_signal(sig.id)
    second = service.process_signal(sig.id)
    assert first["disposition"] == "opened"
    assert second["status"] == "skipped"


def test_hold_signal_ignored(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="hold", created_at=datetime(2026, 1, 5))

    result = service.process_signal(sig.id)
    assert result["disposition"] == "ignored"
    account_id = service.get_or_create_account()["account_id"]
    assert service.get_positions(account_id) == []


def test_buy_without_cash_reports_no_cash_instead_of_ignored(isolated_db, service):
    """现金耗尽时买入被跳过：与 hold/watch 的「无事可做」不是一回事。"""
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    account_id = service.reset_account(initial_capital=50000.0)["account_id"]
    # reset_account 只收正数，所以先把账户建出来再把现金清零。
    service.paper_repo.update_account(account_id, {"cash": 0.0})
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    result = service.process_signal(sig.id)

    assert result["disposition"] == "no_cash"
    assert service.get_positions(account_id) == []


def test_buy_below_one_lot_reports_lot_too_small(isolated_db, service):
    """有现金但买不起一手：成因是交易单位，不是没钱也不是无需动作。"""
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    # 2 万 × 20% 目标仓位 = 4000 元，按 100 元/股不足 100 股（A 股一手）。
    account_id = service.reset_account(initial_capital=20000.0)["account_id"]
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    result = service.process_signal(sig.id)

    assert result["disposition"] == "lot_too_small"
    assert service.get_positions(account_id) == []


def test_sell_without_position_reports_no_position(isolated_db, service):
    """没有可减的持仓：信号本身有动作，只是账户里没有对应仓位。"""
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="sell", created_at=datetime(2026, 1, 5))

    result = service.process_signal(sig.id)

    assert result["disposition"] == "no_position"


def test_add_below_one_lot_reports_lot_too_small_instead_of_hold(isolated_db, service):
    """加仓取整为 0 以前报 `hold`，等于把「买不动」说成「无需动作」。"""
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 100, 100, 100, 100)
    account_id = service.reset_account(initial_capital=50000.0)["account_id"]
    open_sig = _make_signal(
        isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5)
    )
    assert service.process_signal(open_sig.id)["disposition"] == "opened"

    # 「加仓但买不起一手」比看起来窄：目标权重闸门在现金闸门之前，`position_weight`
    # 又被夹在 (0, 1]，所以现金为 0 时 `current_value >= target_value` 会先返回 hold。
    # 要走到这里必须 C > 4 × 持仓市值：持仓 1 万、现金 40010 时
    # spend = 0.2 × (40010 - 40000) = 2 元，按 100 元/股不足一手。
    service.paper_repo.update_account(account_id, {"cash": 40010.0})
    add_sig = _make_signal(
        isolated_db, action="add", entry_high=100.0, created_at=datetime(2026, 1, 6)
    )

    result = service.process_signal(add_sig.id)

    assert result["disposition"] == "lot_too_small"


def test_sell_closes_position(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 105, 105, 105, 105)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    sell_sig = _make_signal(isolated_db, action="sell", created_at=datetime(2026, 1, 6))

    service.process_signal(buy_sig.id)
    result = service.process_signal(sell_sig.id)
    assert result["disposition"] == "closed"

    account_id = service.get_or_create_account()["account_id"]
    positions = service.get_positions(account_id)
    assert all(p["status"] == "closed" for p in positions)


def test_take_profit_exit(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    # D2 opens at 116, already above the 115 target: the target level was gone
    # before the session started, so the exit fills at the open, not at 115.
    _seed_daily(isolated_db, "600519", d2, 116, 120, 110, 118)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))

    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)

    snapshot = service.run_daily_valuation(account_id, as_of_date=d2)
    assert snapshot["trade_date"] == "2026-01-06"

    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0]["reason"] == "take_profit"
    assert sell_trades[0]["price"] == 116.0

    positions = service.get_positions(account_id)
    assert all(p["status"] == "closed" for p in positions)


def test_gap_down_open_resolves_to_stop_loss_not_ambiguous(isolated_db, service):
    # 止损与止盈当日都被触及，但开盘 90 已在 95 止损之下：止损在开盘即被击穿，
    # 先后可判定，应按止损成交（成交价 = 开盘 90，不是止损价 95）。
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 90, 120, 90, 95)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, stop_loss=95.0,
                           target_price=115.0, created_at=datetime(2026, 1, 5))

    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)
    service.run_daily_valuation(account_id, as_of_date=d2)

    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert sell_trades[0]["reason"] == "stop_loss"
    assert sell_trades[0]["price"] == 90.0


def test_gap_up_open_resolves_to_take_profit_not_ambiguous(isolated_db, service):
    # 开盘 120 已在 115 止盈之上 -> 止盈在开盘即达成，应先于止损。
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 120, 120, 90, 95)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, stop_loss=95.0,
                           target_price=115.0, created_at=datetime(2026, 1, 5))

    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)
    service.run_daily_valuation(account_id, as_of_date=d2)

    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert sell_trades[0]["reason"] == "take_profit"
    assert sell_trades[0]["price"] == 120.0


def test_stop_loss_precedence_when_the_open_is_between_both_levels(isolated_db, service):
    # 开盘 105 夹在止损 95 与止盈 115 之间：日内先碰哪一边无法从 OHLC 判定，
    # 残留的模糊情形按止损优先（保守），成交价取止损价 95（开盘未跳空越过止损）。
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 105, 120, 90, 100)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, stop_loss=95.0,
                           target_price=115.0, created_at=datetime(2026, 1, 5))

    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)
    service.run_daily_valuation(account_id, as_of_date=d2)

    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert sell_trades[0]["reason"] == "ambiguous_stop_loss"
    assert sell_trades[0]["price"] == 95.0


def test_daily_valuation_idempotent(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(sig.id)

    s1 = service.run_daily_valuation(account_id, as_of_date=d1)
    s2 = service.run_daily_valuation(account_id, as_of_date=d1)  # idempotent
    assert s1["net_value"] == s2["net_value"]
    curve = service.get_equity_curve(account_id)
    assert len([p for p in curve if p["trade_date"] == "2026-01-05"]) == 1


def test_backfill_replays_signals(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    result = service.backfill_history(account_id, from_date=date(2026, 1, 1), to_date=date(2026, 1, 31))
    assert result["signals_replayed"] == 1

    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] == 2000
    assert service.get_equity_curve(account_id)  # curve non-empty


def test_backfill_paginates_past_repo_cap(isolated_db, service):
    # The repo's list() caps page_size at 100; backfill must replay every signal
    # in the range, not silently drop the tail.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    for _ in range(150):
        _make_signal(isolated_db, action="hold", created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    result = service.backfill_history(account_id, from_date=date(2026, 1, 1), to_date=date(2026, 1, 31))
    assert result["signals_replayed"] == 150


def test_add_capped_by_target_weight_and_cash(isolated_db, service):
    # Repeated buy/add signals must not push a single position past its target
    # weight nor spend more than the cash on hand.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 100, 100, 100, 100)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)  # opens at ~20% of net value

    # Many more buy signals on the same name: the position is already at target, so
    # further adds are "hold" and cash stays put.
    for _ in range(20):
        add_sig = _make_signal(isolated_db, action="add", entry_high=100.0, created_at=datetime(2026, 1, 6))
        service.process_signal(add_sig.id)

    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    # 2000 shares @ 100 = 200,000 == target (20% of 1,000,000); no further buys.
    assert open_pos["quantity"] == 2000
    # 现金 = 100 万 - 20 万成交额 - 52 费用（佣金 50 + 过户费 2）。
    assert service.get_snapshot(account_id)["cash"] == 1000000.0 - 200000.0 - 52.0


def test_add_updates_stop_and_target(isolated_db, service):
    # A buy/add signal can carry a revised stop-loss / take-profit; an add must
    # update those lines on the open position instead of leaving them pinned to open.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "AAPL", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "AAPL", d2, 97, 97, 97, 97)
    buy_sig = _make_signal(isolated_db, action="buy", code="AAPL", market="us",
                           entry_high=100.0, stop_loss=95.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))
    add_sig = _make_signal(isolated_db, action="add", code="AAPL", market="us",
                           entry_high=97.0, stop_loss=90.0, target_price=110.0,
                           created_at=datetime(2026, 1, 6))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)
    # Re-price the position at d2 (97) so its market value drops below the 20%
    # target; otherwise the position sits exactly at target and the add is capped.
    service.run_daily_valuation(account_id, as_of_date=d2)
    result = service.process_signal(add_sig.id)
    assert result["disposition"] == "added"

    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] > 2000
    assert open_pos["stop_loss"] == 90.0
    assert open_pos["target_price"] == 110.0


def test_no_same_day_stop_out_on_entry(isolated_db, service):
    # Entry uses the day's high; the same day's low may dip below the stop-loss
    # without the position actually being stopped out. Exits only apply from the
    # day after entry.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    # D1: high 100 (entry), low 90 < stop 95 -> must NOT exit on the entry day.
    _seed_daily(isolated_db, "600519", d1, 100, 100, 90, 95)
    _seed_daily(isolated_db, "600519", d2, 90, 90, 90, 90)  # D2 breaches stop.
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, stop_loss=95.0,
                           created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)  # opens on D1; no same-day exit
    trades_after_open = service.get_trades(account_id)["items"]
    assert all(t["side"] == "buy" for t in trades_after_open)

    service.run_daily_valuation(account_id, as_of_date=d2)
    trades = service.get_trades(account_id)["items"]
    sell_trades = [t for t in trades if t["side"] == "sell"]
    assert sell_trades and sell_trades[0]["reason"] == "stop_loss"


def test_custom_position_weight(isolated_db):
    # A custom position weight must resize the opened position proportionally.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    service = PaperService(isolated_db, position_weight=0.5)

    service.process_signal(sig.id)
    account_id = service.get_or_create_account()["account_id"]
    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    # 50% of 1,000,000 / 100 = 5,000 shares (vs 2,000 at the default 20%).
    assert open_pos["quantity"] == 5000


def test_hk_lot_rounding(isolated_db, service):
    # HK trades in board lots too (approximated as 100), not plain integer shares.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "00700", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", code="00700", market="hk",
                       entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(sig.id)
    positions = service.get_positions(account_id)
    open_pos = next(p for p in positions if p["status"] == "open")
    assert open_pos["quantity"] == 2000  # 20% of 1e6 / 100, rounded to 100-lot
    assert open_pos["quantity"] % 100 == 0


def test_hold_signal_does_not_snapshot(isolated_db, service):
    # Dispositions that don't touch positions (hold/ignored) must not write a
    # redundant daily snapshot / pollute the equity curve.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="hold", created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(sig.id)
    assert service.get_equity_curve(account_id) == []


def test_buy_without_price_is_data_unavailable_and_retryable(isolated_db, service):
    # A buy with no entry price and no price bar is data-unavailable, NOT consumed,
    # so a later run (e.g. after the data source recovers) can still fill it
    # instead of permanently dropping the signal.
    sig = _make_signal(isolated_db, action="buy", entry_high=None, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    result = service.process_signal(sig.id)
    assert result["status"] == "data_unavailable"
    assert result["disposition"] == "data_unavailable"

    # Not consumed: positions untouched and no signal record written.
    assert service.get_positions(account_id) == []
    assert service.get_signals(account_id)["total"] == 0

    # Once the price is available, a later run (fresh service instance, as the
    # background valuation task recreates it) can still consume the same signal.
    _seed_daily(isolated_db, "600519", date(2026, 1, 5), 100, 100, 100, 100)
    result2 = PaperService(isolated_db).process_signal(sig.id)
    assert result2["disposition"] == "opened"


def test_resolve_valuation_date_no_positions_returns_today(isolated_db, service):
    # A fresh account with no open positions must fall back to the local date
    # rather than erroring or returning a stale resolved session.
    account_id = service.get_or_create_account()["account_id"]
    account = service.paper_repo.get_account(account_id)
    assert service.resolve_valuation_date(account) == date.today()


def test_latest_snapshot_date_after_valuation(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(sig.id)

    # The entry-day signal records a snapshot for its trade date (d1).
    assert service.latest_snapshot_date(account_id) == d1

    service.run_daily_valuation(account_id, as_of_date=d2)
    assert service.latest_snapshot_date(account_id) == d2


def test_post_close_signal_fills_on_the_next_session(isolated_db, service):
    # A signal produced after the market close (here on a Saturday) has no bar of
    # its own. It must roll to the next session instead of booking a fill on a
    # day the market was shut — the previous behaviour priced it with the plan's
    # limit on the timestamp date, which no bar could ever justify.
    session = date(2026, 8, 10)  # Monday after the 2026-08-08 (Saturday) signal
    _seed_daily(isolated_db, "600519", session, 105.0, 110.0, 98.0, 108.0)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0,
                       created_at=datetime(2026, 8, 8, 8, 40, 1))  # 16:40 Beijing
    account_id = service.get_or_create_account()["account_id"]

    result = service.process_signal(sig.id)
    assert result["disposition"] == "opened"

    # The limit 100 was reached (low 98), so the fill is the open/limit minimum,
    # and both the trade and the position land on the session, not the Saturday.
    trades = service.get_trades(account_id)["items"]
    assert trades[0]["trade_date"] == "2026-08-10"
    assert trades[0]["price"] == 100.0
    open_pos = next(p for p in service.get_positions(account_id) if p["status"] == "open")
    assert open_pos["entry_date"] == "2026-08-10"


def test_entry_fills_at_open_when_the_market_opens_below_the_limit(isolated_db, service):
    # The order fills at the session open when the market is already better than
    # the planned limit, instead of handing back the limit price.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 96.0, 99.0, 95.0, 98.0)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0,
                       created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(sig.id)

    trades = service.get_trades(account_id)["items"]
    assert trades[0]["price"] == 96.0


def test_entry_does_not_fill_when_the_session_never_reached_the_limit(isolated_db, service):
    # The session low (104) never traded down to the 100 limit: no fill. The
    # signal is consumed as `no_fill` rather than silently opening at a price the
    # market never offered.
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 105.0, 110.0, 104.0, 108.0)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0,
                       created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    result = service.process_signal(sig.id)
    assert result["disposition"] == "no_fill"

    assert service.get_positions(account_id) == []
    assert service.get_trades(account_id)["items"] == []
    # A no-fill is a real outcome, not a data gap: the signal stays consumed so a
    # stale plan does not re-fire on a later run.
    assert service.get_signals(account_id)["total"] == 1
    assert service.get_equity_curve(account_id) == []
    assert PaperService(isolated_db).process_signal(sig.id)["status"] == "skipped"


def test_take_profit_inside_the_session_range_still_fills_at_target(isolated_db, service):
    # A gap-aware exit must not leak into the ordinary case: when the session
    # opens below the target and merely trades up through it, the fill is the
    # target price itself.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 105.0, 120.0, 104.0, 118.0)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)
    service.run_daily_valuation(account_id, as_of_date=d2)

    sell_trades = [t for t in service.get_trades(account_id)["items"] if t["side"] == "sell"]
    assert sell_trades[0]["reason"] == "take_profit"
    assert sell_trades[0]["price"] == 115.0


def test_snapshot_on_an_exit_day_does_not_double_count_the_closed_position(isolated_db, service):
    # An exit credits cash AND removes the position in the same pass. The
    # snapshot used to sum the position records read *before* that pass, so it
    # counted the proceeds and the dead position both, inflating net value.
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 116, 120, 110, 118)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)  # 2000 shares @ 100 -> 200,000 成交 + 52 费用
    snapshot = service.run_daily_valuation(account_id, as_of_date=d2)

    # 卖出 2000 @ 116 = 232,000：佣金 58 + 印花税 116（卖出单边 0.05%）+ 过户费 2.32 = 176.32。
    sell_fee = 232000.0 * 0.00025 + 232000.0 * 0.0005 + 232000.0 * 0.00001
    assert snapshot["market_value"] == 0.0
    assert snapshot["cash"] == pytest.approx(1000000.0 - 200052.0 + 232000.0 - sell_fee)
    assert snapshot["net_value"] == snapshot["cash"]


def test_sell_charges_stamp_duty_and_buy_does_not(isolated_db, service):
    # A股印花税只在卖出单边收取；买入只付佣金+过户费。若两侧都按同一费率收，
    # 或者两侧都不收，下面成交流水里的费用就对不上。
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 116, 120, 110, 118)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)
    service.run_daily_valuation(account_id, as_of_date=d2)

    trades = service.get_trades(account_id)["items"]
    buy, sell = trades[1], trades[0]  # 最新的在前
    assert buy["side"] == "buy"
    assert buy["fee"] == pytest.approx(200000.0 * 0.00025 + 200000.0 * 0.00001)
    assert sell["side"] == "sell"
    assert sell["fee"] == pytest.approx(
        232000.0 * 0.00025 + 232000.0 * 0.0005 + 232000.0 * 0.00001
    )
    assert sell["fee"] > buy["fee"]


def test_commission_floor_applies_to_a_small_order(isolated_db, service):
    # 小单佣金按万2.5 只有 2.5 元，低于单笔最低 5 元，应按 5 元收。
    service.position_weight = 0.01  # 目标 1 万 -> 100 股 @ 100
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    service.process_signal(sig.id)

    trade = service.get_trades(service.get_or_create_account()["account_id"])["items"][0]
    assert trade["amount"] == 10000.0
    assert trade["fee"] == pytest.approx(5.0 + 10000.0 * 0.00001)


def test_fees_can_be_disabled(isolated_db):
    service = _service_with_fee_env(isolated_db, PAPER_FEE_ENABLED="false")
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    service.process_signal(sig.id)

    account_id = service.get_or_create_account()["account_id"]
    assert service.get_snapshot(account_id)["cash"] == 800000.0
    assert service.get_trades(account_id)["items"][0]["fee"] == 0.0


def test_slippage_moves_buy_up_and_sell_down(isolated_db):
    service = _service_with_fee_env(isolated_db, PAPER_FEE_SLIPPAGE_BPS="50")  # 0.5%
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 100, 100, 100, 100)
    # 无计划买点 -> 按收盘价成交的市价单，滑点全额生效。
    sig = _make_signal(isolated_db, action="buy", entry_high=None, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(sig.id)
    assert service.get_trades(account_id)["items"][0]["price"] == pytest.approx(100.5)

    # 卖出方向滑点同样不利：卖出价低于当日收盘价。
    sell_sig = _make_signal(isolated_db, action="sell", entry_high=None, created_at=datetime(2026, 1, 6))
    service.process_signal(sell_sig.id)
    assert service.get_trades(account_id)["items"][0]["price"] == pytest.approx(99.5)


def test_limit_buy_is_not_slipped_above_its_limit(isolated_db):
    # 限价买单不会成交在限价之上：当日区间已经打到限价，成交价就是限价本身，
    # 滑点不能把成交价推到 100 之上。
    service = _service_with_fee_env(isolated_db, PAPER_FEE_SLIPPAGE_BPS="50")
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 101, 101, 99, 100.5)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    service.process_signal(sig.id)

    trade = service.get_trades(service.get_or_create_account()["account_id"])["items"][0]
    assert trade["price"] == 100.0


def test_hk_stamp_duty_is_charged_on_both_sides(isolated_db, service):
    # 港股印花税 0.1% 双向，明显重于 A股；用 A股的单边规则建模会低估港股成本。
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "hk00700", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", code="hk00700", market="hk",
                       entry_high=100.0, created_at=datetime(2026, 1, 5))

    service.process_signal(sig.id)

    trade = service.get_trades(service.get_or_create_account()["account_id"])["items"][0]
    amount = trade["amount"]
    assert trade["fee"] == pytest.approx(
        amount * 0.00025 + amount * 0.001 + amount * 0.000105
    )


def test_buy_sized_to_cash_does_not_overdraw_on_fees(isolated_db, service):
    # 费用在成交额之外另扣。按全部现金下单时若不预留费用，买入后现金会变成负数。
    service.position_weight = 1.0  # 目标 = 全部资产，受可用现金约束
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    service.process_signal(sig.id)

    account_id = service.get_or_create_account()["account_id"]
    trade = service.get_trades(account_id)["items"][0]
    assert trade["quantity"] == 9900  # 10,000 股会超出「成交额 + 费用」的现金上限
    assert trade["amount"] + trade["fee"] <= 1000000.0
    assert service.get_snapshot(account_id)["cash"] >= 0


def test_trade_log_fees_reconcile_account_cash(isolated_db, service):
    # 成交流水必须能对平现金：初始资金 - 买入(成交额+费用) + 卖出(成交额-费用) == 现金。
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 116, 120, 110, 118)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    service.process_signal(buy_sig.id)
    service.run_daily_valuation(account_id, as_of_date=d2)

    expected = 1000000.0
    for trade in service.get_trades(account_id)["items"]:
        if trade["side"] == "buy":
            expected -= trade["amount"] + trade["fee"]
        else:
            expected += trade["amount"] - trade["fee"]
    assert service.get_snapshot(account_id)["cash"] == pytest.approx(expected)


def test_zero_commission_rate_keeps_cn_statutory_fees_only(isolated_db):
    # 佣金率填 0：美股归零，但 A股的印花税/过户费属法定费用、仍照收；
    # 单笔最低佣金 5 元也仍在。文档里承诺的正是这个口径。
    us = _service_with_fee_env(isolated_db, PAPER_FEE_COMMISSION_RATE="0")
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "AAPL", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", code="AAPL", market="us",
                       entry_high=100.0, created_at=datetime(2026, 1, 5))
    us.process_signal(sig.id)
    trade = us.get_trades(us.get_or_create_account()["account_id"])["items"][0]
    assert trade["fee"] == 0.0

    clear_bar_cache_for_tests()
    cn = _service_with_fee_env(isolated_db, PAPER_FEE_COMMISSION_RATE="0")
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    cn_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    cn.process_signal(cn_sig.id)
    cn_trade = cn.get_trades(cn.get_or_create_account()["account_id"])["items"][0]
    # 最低佣金 5 元 + 过户费 200000*0.00001 = 2。
    assert cn_trade["fee"] == pytest.approx(5.0 + 200000.0 * 0.00001)


def test_reset_archives_history_and_opens_account_with_new_capital(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    service.process_signal(sig.id)

    old_id = service.get_or_create_account()["account_id"]
    old_positions = service.get_positions(old_id)
    old_trades = service.get_trades(old_id)["items"]
    assert old_positions and old_trades

    new_account = service.reset_account(initial_capital=500000.0)

    assert new_account["account_id"] != old_id
    assert new_account["initial_capital"] == 500000.0
    assert new_account["cash"] == 500000.0
    assert new_account["snapshot"]["net_value"] == 500000.0
    assert new_account["snapshot"]["return_pct"] == 0.0

    # 老账户只是被归档：持仓/成交原样留在库里，仍按旧 account_id 可查。
    assert service.paper_repo.get_account(old_id).status == "archived"
    assert service.get_positions(old_id) == old_positions
    assert service.get_trades(old_id)["items"] == old_trades

    # 新账户是空账户，并且从此成为所有入口默认读到的那个。
    assert service.get_positions(new_account["account_id"]) == []
    assert service.get_trades(new_account["account_id"])["items"] == []
    assert service.get_or_create_account()["account_id"] == new_account["account_id"]


def test_reset_consumes_new_signals_into_the_new_account(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 100, 100, 100, 100)
    first = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    service.process_signal(first.id)
    old_id = service.get_or_create_account()["account_id"]
    old_positions = service.get_positions(old_id)

    clear_bar_cache_for_tests()
    new_account = service.reset_account(initial_capital=500000.0)
    new_id = new_account["account_id"]

    # 同一支股票的相同信号在新账户上必须能重新开仓：信号消费记录是按账户隔离的，
    # 重置后旧账户的「已消费」标记不会挡住新账户。
    again = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 6))
    result = service.process_signal(again.id)

    assert result["disposition"] == "opened"
    assert [p["stock_code"] for p in service.get_positions(new_id) if p["status"] == "open"] == ["600519"]
    # 新账户开仓不该回头改动旧账户的持仓。
    assert service.get_positions(old_id) == old_positions


def test_reset_without_capital_uses_configured_default(isolated_db):
    svc = _service_with_fee_env(isolated_db, PAPER_INITIAL_CAPITAL="250000")
    account = svc.reset_account()

    assert account["initial_capital"] == 250000.0
    assert account["cash"] == 250000.0
    assert account["snapshot"]["net_value"] == 250000.0


def test_reset_rejects_invalid_capital_without_touching_the_account(isolated_db, service):
    account_id = service.get_or_create_account()["account_id"]

    for bad in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            service.reset_account(initial_capital=bad)

    # 校验在归档之前：非法金额不会把账户归档掉、也不会留下半成品账户。
    current = service.get_or_create_account()
    assert current["account_id"] == account_id
    assert current["status"] == "active"


# ----------------------------------------------------------------------
# 成交通知：只走实时路径，回填静音
# ----------------------------------------------------------------------
def test_live_fill_notifies_with_cash_after(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    with mock.patch.object(ps, "send_paper_fill_notification", return_value=True) as notifier:
        assert service.process_signal(sig.id)["disposition"] == "opened"

    assert notifier.call_count == 1
    trade = notifier.call_args.args[0]
    assert (trade.stock_code, trade.side, trade.quantity) == ("600519", "buy", 2000.0)
    assert notifier.call_args.kwargs["disposition"] == "opened"
    # 通知里的「成交后现金」必须已经是扣款后的值，而不是成交前的。
    assert notifier.call_args.kwargs["cash_after"] == pytest.approx(1000000.0 - 200000.0 - 52.0)


def test_exit_fill_notifies_as_closed(isolated_db, service):
    d1 = date(2026, 1, 5)
    d2 = date(2026, 1, 6)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _seed_daily(isolated_db, "600519", d2, 116, 120, 110, 118)
    buy_sig = _make_signal(isolated_db, action="buy", entry_high=100.0, target_price=115.0,
                           created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]
    service.process_signal(buy_sig.id)

    with mock.patch.object(ps, "send_paper_fill_notification", return_value=True) as notifier:
        service.run_daily_valuation(account_id, as_of_date=d2)

    assert notifier.call_count == 1
    assert notifier.call_args.args[0].reason == "take_profit"
    assert notifier.call_args.kwargs["disposition"] == "closed"


def test_backfill_stays_silent(isolated_db, service):
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))
    account_id = service.get_or_create_account()["account_id"]

    with mock.patch.object(ps, "send_paper_fill_notification", return_value=True) as notifier:
        result = service.backfill_history(
            account_id, from_date=date(2026, 1, 1), to_date=date(2026, 1, 31)
        )

    assert result["signals_replayed"] == 1
    # 回填确实落了成交，只是全程不推送。
    assert [t for t in service.get_trades(account_id)["items"] if t["side"] == "buy"]
    notifier.assert_not_called()


def test_notifier_failure_does_not_break_the_fill(isolated_db):
    # 通知渠道整个不可用时，成交、净值与「已消费」标记都必须照常落库：
    # 吞异常发生在 paper_notify 内部，这里用真实实现 + 抛异常的 NotificationService 钉住它。
    svc = _service_with_fee_env(isolated_db, PAPER_NOTIFY_ENABLED="true")
    d1 = date(2026, 1, 5)
    _seed_daily(isolated_db, "600519", d1, 100, 100, 100, 100)
    sig = _make_signal(isolated_db, action="buy", entry_high=100.0, created_at=datetime(2026, 1, 5))

    with mock.patch.object(ps_notify, "NotificationService", side_effect=RuntimeError("boom")):
        result = svc.process_signal(sig.id)

    assert result["disposition"] == "opened"
    account_id = svc.get_or_create_account()["account_id"]
    assert [t["side"] for t in svc.get_trades(account_id)["items"]] == ["buy"]
    assert svc.process_signal(sig.id)["status"] == "skipped"
