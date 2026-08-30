# -*- coding: utf-8 -*-
"""KDJ/BOLL indicator tests and report-schema compatibility for StockTrendAnalyzer."""

import unittest

import numpy as np
import pandas as pd

from src.schemas.report_schema import DataPerspective, TrendStatus
from src.stock_analyzer import BOLLStatus, KDJStatus, StockTrendAnalyzer


def _make_ohlc_df(close_values, *, length: int | None = 60) -> pd.DataFrame:
    """构造带 OHLC 的日线 df（close 波动足够算出 ATR/KDJ/BOLL）。"""
    closes = list(close_values)
    if length is not None and len(closes) < length:
        # 线性外推补齐到 length 根
        base = closes
        step = (base[-1] - base[-3]) / 2 if len(base) >= 3 else 1.0
        while len(closes) < length:
            closes.append(closes[-1] + step)
    df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=len(closes), freq="D"),
        "open": closes,
        "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes],
        "close": closes,
        "volume": [1_000_000] * len(closes),
    })
    return df


class KdjBollCalculationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = StockTrendAnalyzer()

    def test_calculate_kdj_matches_alert_formula(self) -> None:
        """报告端 KDJ 的 K/D 应与告警端 _evaluate_kdj 同一公式。"""
        closes = [10, 11, 12, 11, 13, 12, 14, 15, 13, 16, 17, 15, 18, 19, 28, 33, 31, 36, 38, 40, 37, 42, 45, 41, 48, 50]
        df = _make_ohlc_df(closes, length=None)
        analyzed = self.analyzer._calculate_kdj(df)
        latest_k = float(analyzed["KDJ_K"].iloc[-1])
        latest_d = float(analyzed["KDJ_D"].iloc[-1])

        # 告警端公式（与 alert_indicators._evaluate_kdj 一致：RSV->ewm alpha=1/3）
        period, k_period, d_period = 9, 3, 3
        lowest = df["low"].rolling(window=period).min()
        highest = df["high"].rolling(window=period).max()
        denom = highest - lowest
        rsv = ((df["close"] - lowest) / denom.mask(denom == 0) * 100).fillna(50)
        k_alert = rsv.ewm(alpha=1 / k_period, adjust=False).mean()
        d_alert = k_alert.ewm(alpha=1 / d_period, adjust=False).mean()

        self.assertAlmostEqual(latest_k, float(k_alert.iloc[-1]), places=6)
        self.assertAlmostEqual(latest_d, float(d_alert.iloc[-1]), places=6)
        self.assertTrue(np.isfinite(float(analyzed["KDJ"].iloc[-1])))

    def test_calculate_boll_uses_20_ma_and_two_std(self) -> None:
        closes = [10 + i * 0.1 for i in range(80)]
        df = self.analyzer._calculate_boll(_make_ohlc_df(closes))
        latest = df.iloc[-1]
        expected_mid = float(df["close"].iloc[-20:].mean())
        expected_std = float(df["close"].iloc[-20:].std())

        self.assertAlmostEqual(float(latest["BOLL_MID"]), expected_mid, places=6)
        self.assertAlmostEqual(float(latest["BOLL_UPPER"]), expected_mid + 2 * expected_std, places=6)
        self.assertAlmostEqual(float(latest["BOLL_LOWER"]), expected_mid - 2 * expected_std, places=6)

    def test_to_dict_includes_kdj_and_boll_keys(self) -> None:
        df = _make_ohlc_df([10, 11, 12, 11, 13, 12, 14, 15, 13, 16, 17, 15, 18, 19, 20, 21, 19, 22, 23, 21, 24, 25, 23, 26])
        result = self.analyzer.analyze(df, "600519")
        data = result.to_dict()

        self.assertIn("kdj_k", data)
        self.assertIn("kdj_d", data)
        self.assertIn("kdj_j", data)
        self.assertIn("kdj_status", data)
        self.assertIn("kdj_signal", data)
        self.assertIn("boll_upper", data)
        self.assertIn("boll_mid", data)
        self.assertIn("boll_lower", data)
        self.assertIn("boll_status", data)
        self.assertIn("boll_signal", data)

    def test_analyze_produces_kdj_and_boll(self) -> None:
        closes = [10 + (i % 7) for i in range(80)]
        df = _make_ohlc_df(closes)
        result = self.analyzer.analyze(df, "600519")

        self.assertNotEqual(result.kdj_signal, "数据不足")
        self.assertNotEqual(result.boll_signal, "数据不足")
        self.assertTrue(np.isfinite(result.kdj_k))
        self.assertTrue(np.isfinite(result.boll_mid))
        self.assertGreater(result.boll_upper, result.boll_mid)
        self.assertLess(result.boll_lower, result.boll_mid)


class KdjBollSignalUseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = StockTrendAnalyzer()

    def _df_with_kdj(self, k_deltas: list[float]) -> pd.DataFrame:
        """构造 KDJ_K/KDJ_D/KDJ 已给定列，用于检验 _analyze_kdj 状态判定。

        padding 行前置、crafted 行后置，保证最后两根是我们要测的 K/D 组合。
        """
        padding = [{"KDJ_K": 50.0, "KDJ_D": 50.0, "KDJ": 50.0}] * 11
        rows = list(padding)
        base_k, base_d = 50.0, 50.0
        for delta in k_deltas:
            k = base_k + delta
            d = base_d
            rows.append({"KDJ_K": k, "KDJ_D": d, "KDJ": 3 * k - 2 * d})
            base_k, base_d = k, d
        return pd.DataFrame(rows)

    def test_kdj_state_machine(self) -> None:
        from src.stock_analyzer import TrendAnalysisResult

        # 金叉：上根 K-D<=0，本根 K-D>0
        result = TrendAnalysisResult(code="600519")
        golden = self._df_with_kdj([-5.0, 10.0])
        self.analyzer._analyze_kdj(golden, result)
        self.assertEqual(result.kdj_status, KDJStatus.GOLDEN_CROSS)
        self.assertIn("金叉", result.kdj_signal)

        # 死叉：上根 K-D>=0，本根 K-D<0
        result2 = TrendAnalysisResult(code="600519")
        death = self._df_with_kdj([5.0, -10.0])
        self.analyzer._analyze_kdj(death, result2)
        self.assertEqual(result2.kdj_status, KDJStatus.DEATH_CROSS)

    def test_boll_status_above_upper(self) -> None:
        from src.stock_analyzer import TrendAnalysisResult

        result = TrendAnalysisResult(code="600519")
        closes = [10 + (i % 7) for i in range(60)]
        df = self.analyzer._calculate_boll(_make_ohlc_df([float(c) for c in closes]))
        # 强制收盘价高于上轨
        df = df.copy()
        df["close"] = df["close"].astype(float)
        df.loc[df.index[-1], "close"] = float(df["BOLL_UPPER"].iloc[-1]) * 1.02
        self.analyzer._analyze_boll(df, result)
        self.assertEqual(result.boll_status, BOLLStatus.ABOVE_UPPER)


class ReportSchemaCompatibilityTest(unittest.TestCase):
    def test_trend_status_accepts_kdj_and_boll(self) -> None:
        ts = TrendStatus(ma_alignment="bull", kdj={"k": 70.0, "d": 60.0, "j": 90.0}, boll={"upper": 100.0, "mid": 90.0, "lower": 80.0})
        self.assertEqual(ts.kdj["k"], 70.0)
        self.assertEqual(ts.boll["mid"], 90.0)

    def test_trend_status_optional_without_kdj_boll(self) -> None:
        ts = TrendStatus(ma_alignment="neutral")
        self.assertIsNone(ts.kdj)
        self.assertIsNone(ts.boll)

    def test_data_perspective_accepts_optional_kdj_boll(self) -> None:
        dp = DataPerspective(trend_status={"ma_alignment": "bull"}, kdj={"k": 1}, boll={"mid": 2})
        self.assertIsNotNone(dp.kdj)
        self.assertIsNotNone(dp.boll)
        empty = DataPerspective()
        self.assertIsNone(empty.kdj)
        self.assertIsNone(empty.boll)


if __name__ == "__main__":
    unittest.main()
