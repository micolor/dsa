# -*- coding: utf-8 -*-
"""
确定性风险/仓位引擎
===================

目标：把「suggested_position / stop_loss」从 LLM 散文变成基于 ATR 的确定性数字兜底。

职责(全部确定性、无 LLM、无随机性)：
- compute_atr: Wilder 口径 ATR(14)
- derive_stop_loss / derive_take_profit: 由 ATR 推导止损/止盈价
- compute_position_size: 由单手风险推导仓位比例(0-1)

不覆盖 LLM 已给出的合理值：只在 orchestrator 侧对「缺失/哨兵值」做兜底填充，
本模块只负责计算，不做覆盖决策。
"""

from __future__ import annotations

import pandas as pd

# A 股默认参数，可后续接入配置项
ATR_PERIOD = 14          # ATR 周期
ATR_STOP_MULTIPLIER = 2.0  # 止损 = entry - 2 * ATR
ATR_TP_MULTIPLIER = 2.0    # 止盈距离按风险(2*ATR)的 N 倍
DEFAULT_RISK_PER_TRADE_PCT = 0.01   # 每笔风险占资金 1%
DEFAULT_MAX_POSITION_PCT = 0.30     # 单标的仓位上限 30%
RISK_REWARD_RATIO = 1.5   # taker profit = entry + 2*ATR * 1.5


def _to_frame(bars: pd.DataFrame | list[dict] | None) -> pd.DataFrame | None:
    """归一化输入为 DataFrame；不支持的数据返回 None。"""
    if bars is None:
        return None
    if isinstance(bars, pd.DataFrame):
        df = bars
    elif isinstance(bars, list) and bars:
        df = pd.DataFrame(bars)
    else:
        return None
    if df is None or df.empty:
        return None
    if not {"high", "low", "close"}.issubset(df.columns):
        return None
    return df


def compute_atr(bars: pd.DataFrame | list[dict] | None, period: int = ATR_PERIOD) -> float | None:
    """计算 Wilder 口径 ATR(period)。

    输入为 OHLC 的 DataFrame 或 list[dict]，输出最新一日的 ATR；无有效数据返回 None。
    """
    df = _to_frame(bars)
    if df is None:
        return None

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")

    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)

    atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
    atr = atr_series.iloc[-1]
    if pd.isna(atr) or float(atr) <= 0:
        return None
    return float(atr)


def derive_stop_loss(
    entry_price: float | None,
    atr: float | None,
    multiplier: float = ATR_STOP_MULTIPLIER,
) -> float | None:
    """由入场价与 ATR 推导止损价；输入无效返回 None。"""
    if entry_price is None or atr is None or atr <= 0:
        return None
    stop = float(entry_price) - multiplier * atr
    if stop <= 0:
        return None
    return round(stop, 2)


def derive_take_profit(
    entry_price: float | None,
    atr: float | None,
    *,
    stop_multiplier: float = ATR_STOP_MULTIPLIER,
    risk_reward: float = RISK_REWARD_RATIO,
) -> float | None:
    """由入场价与 ATR 推导止盈价；输入无效返回 None。"""
    if entry_price is None or atr is None or atr <= 0:
        return None
    risk = stop_multiplier * atr
    take = float(entry_price) + risk * risk_reward
    if take <= 0:
        return None
    return round(take, 2)


def compute_position_size(
    entry_price: float | None,
    atr: float | None,
    *,
    equity: float | None,
    risk_per_trade_pct: float = DEFAULT_RISK_PER_TRADE_PCT,
    stop_multiplier: float = ATR_STOP_MULTIPLIER,
    max_position_pct: float = DEFAULT_MAX_POSITION_PCT,
    concentration_scale: float = 1.0,
) -> float | None:
    """计算建议仓位比例(0-1)，返回 None 表示无法计算。

    逻辑：单手相对风险 = stop_multiplier * atr；风险金额 = equity * risk_per_trade_pct；
    可买股数 = 风险金额 / 单手相对风险；仓位 = 股数 * 入场价 / equity，夹在 [0, max_position_pct]，
    并按 portfolio concentration_scale (<=1 时压缩) 缩放。
    """
    if entry_price is None or atr is None or atr <= 0:
        return None
    if equity is None or equity <= 0:
        return None
    risk_per_share = stop_multiplier * atr
    if risk_per_share <= 0:
        return None
    risk_currency = float(equity) * max(0.0, float(risk_per_trade_pct))
    shares = risk_currency / risk_per_share
    position_value = shares * float(entry_price)
    ratio = position_value / float(equity)
    ratio = max(0.0, min(ratio, max(0.0, float(max_position_pct))))
    scale = max(0.0, min(1.0, float(concentration_scale)))
    ratio *= scale
    return round(ratio, 4)


def position_size_to_cheng(ratio: float | None) -> str | None:
    """把仓位比例(0-1)转成「X成仓」文案；无法计算返回 None。

    成 = 10%；0.3 -> 「3成仓」，0.15 -> 「1.5成仓」。
    """
    if ratio is None or ratio <= 0:
        return None
    cheng = float(ratio) * 10
    text = f"{cheng:.1f}".rstrip("0").rstrip(".")
    return f"{text}成仓"
