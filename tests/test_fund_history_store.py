# -*- coding: utf-8 -*-
"""
===================================
场外基金净值体检 - 分析历史存储往返测试
===================================

职责：
1. 验证基金 AnalysisResult 能通过 save_analysis_history 落库。
2. 验证落库记录能通过 HistoryService.get_history_list 读回，字段一致。
3. 验证 get_history_detail_by_id（DB 级）能以 report_type="fund" 读回单条详情。

说明：基金记录不含买点/止损，故 buy/sell 相关字段应保持 None。本用例覆盖 DB 级
保存/读回（HistoryService + save_analysis_history）；get_history_detail /
get_history_markdown 这些 API endpoint 需要完整 FastAPI app（路由挂载 + 依赖注入），
不在此离线用例中模拟真实风险层，故仅覆盖 DB 级往返，API 级路径按要求注明为未覆盖。
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

from src.config import Config
from src.storage import DatabaseManager
from src.analyzer import AnalysisResult
from src.services.history_service import HistoryService

import src.auth as auth  # noqa: E402  (需在设置环境变量后由模块内 singleton 使用)


def _build_fund_result() -> AnalysisResult:
    """构造一个真实的基金体检 AnalysisResult（显式字段，不走网络抓取）。"""
    return AnalysisResult(
        code="003095",
        name="中欧医疗健康混合A",
        sentiment_score=50,
        trend_prediction="震荡",
        operation_advice="可继续持有",
        analysis_summary="基于净值序列，非股票式信号，不构成投资建议。",
        dashboard={"report_type": "fund", "not_investment_advice": True},
    )


def _build_fund_result_full_dashboard() -> AnalysisResult:
    """构造带完整净值的基金体检结果（metrics + latest_nav），覆盖 markdown 生成路径。"""
    return AnalysisResult(
        code="003095",
        name="中欧医疗健康混合A",
        sentiment_score=50,
        trend_prediction="震荡",
        operation_advice="可继续持有",
        analysis_summary=(
            "中欧医疗健康混合A(003095) 近1年收益 5.0%、最大回撤 -12.0%;"
            "风险等级:中。基于净值序列,非股票式信号,不构成投资建议。"
        ),
        dashboard={
            "report_type": "fund",
            "metrics": {
                "return_1m": 0.01,
                "return_3m": 0.02,
                "return_6m": 0.03,
                "return_1y": 0.05,
                "max_drawdown": -0.12,
                "annual_volatility": 0.18,
                "sharpe": 1.2,
            },
            "latest_nav": 1.0240,
            "not_investment_advice": True,
        },
    )


def _build_full_stock_result() -> AnalysisResult:
    """构造 A 股全量报告结果（report_type="full"），用于股票 markdown 不回归校验。"""
    return AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=60,
        trend_prediction="上行",
        operation_advice="可关注",
        analysis_summary="白酒龙头，短线偏强。",
        dashboard={
            "report_type": "full",
            "core_conclusion": {
                "one_sentence": "白酒龙头，短线偏强。",
                "position_advice": {"no_position": "可关注", "has_position": "继续持有"},
            },
            "battle_plan": {
                "sniper_points": {
                    "ideal_buy": "1700.00",
                    "secondary_buy": "1650.00",
                    "stop_loss": "1600.00",
                    "take_profit": "1900.00",
                }
            },
        },
    )


class FundHistoryStoreTestCase(unittest.TestCase):
    """基金分析历史存储往返测试"""

    def setUp(self) -> None:
        """为每个用例初始化独立数据库"""
        auth._auth_enabled = False
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_fund_history.db")
        self._original_env = {
            key: os.environ.get(key)
            for key in (
                "ENV_FILE",
                "DATABASE_PATH",
            )
        }
        self._env_path = os.path.join(self._temp_dir.name, ".env")
        with open(self._env_path, "w", encoding="utf-8") as env_file:
            env_file.write("STOCK_LIST=600519,000001\n")

        os.environ["ENV_FILE"] = self._env_path
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        """清理资源"""
        Config._instance = None
        DatabaseManager.reset_instance()
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._temp_dir.cleanup()

    def test_fund_result_saves_and_round_trips(self) -> None:
        """基金结果落库返回 id>0，且能通过 get_history_list 读回一致字段。"""
        fr = _build_fund_result()
        history_id = self.db.save_analysis_history(
            result=fr,
            query_id="fq1",
            report_type="fund",
            news_content="",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertIsInstance(history_id, int)
        self.assertGreater(history_id, 0)

        service = HistoryService(self.db)
        listing = service.get_history_list(report_type="fund", page=1, limit=20)
        self.assertEqual(listing["total"], 1)
        self.assertEqual(len(listing["items"]), 1)
        item = listing["items"][0]
        self.assertEqual(item["report_type"], "fund")
        self.assertEqual(item["stock_code"], "003095")
        self.assertEqual(item["stock_name"], "中欧医疗健康混合A")
        self.assertEqual(item["sentiment_score"], 50)
        self.assertIn("不构成投资建议", item["analysis_summary"])

    def test_fund_record_has_no_stock_signals(self) -> None:
        """基金记录不应带股票式买点/止损信号（detail 层 bu/sell 字段应为 None）。"""
        fr = _build_fund_result()
        history_id = self.db.save_analysis_history(
            result=fr,
            query_id="fq1",
            report_type="fund",
            news_content="",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(history_id, 0)

        detail = HistoryService(self.db).get_history_detail_by_id(history_id)
        self.assertIsNotNone(detail)
        if detail is not None:
            self.assertIsNone(detail["ideal_buy"])
            self.assertIsNone(detail["secondary_buy"])
            self.assertIsNone(detail["stop_loss"])
            self.assertIsNone(detail["take_profit"])

    def test_fund_detail_round_trip_via_history_service(self) -> None:
        """经 HistoryService.get_history_detail_by_id（DB 级）读回单条详情，report_type 应为 fund。"""
        fr = _build_fund_result()
        history_id = self.db.save_analysis_history(
            result=fr,
            query_id="fq1",
            report_type="fund",
            news_content="",
            context_snapshot=None,
            save_snapshot=False,
        )
        self.assertGreater(history_id, 0)

        detail = HistoryService(self.db).get_history_detail_by_id(history_id)
        self.assertIsNotNone(detail)
        if detail is not None:
            self.assertEqual(detail["report_type"], "fund")
            self.assertEqual(detail["stock_code"], "003095")
            self.assertEqual(detail["sentiment_score"], 50)
            self.assertIn("不构成投资建议", detail["analysis_summary"])

    def _save(self, result: AnalysisResult, report_type: str, query_id: str = "q1") -> int:
        """落库一条分析历史记录，返回主键 id。"""
        return self.db.save_analysis_history(
            result=result,
            query_id=query_id,
            report_type=report_type,
            news_content="",
            context_snapshot=None,
            save_snapshot=False,
        )

    def test_fund_markdown_contains_fund_content(self) -> None:
        """fund 记录 get_markdown_report 输出基金净值体检，且不含股票买卖点关键词。"""
        history_id = self._save(_build_fund_result_full_dashboard(), "fund")
        md = HistoryService(self.db).get_markdown_report(str(history_id))
        self.assertIsNotNone(md)
        # 基金名 / 代码
        self.assertIn("中欧医疗健康混合A", md)
        self.assertIn("003095", md)
        # 单位净值 / 风险等级 / 免责声明
        self.assertIn("1.0240", md)
        self.assertIn("风险等级", md)
        self.assertIn("不构成投资建议", md)
        # 不允许出现股票式买卖点 / 作战计划关键词
        for kw in ("策略点位", "狙击点位", "止损", "止盈"):
            self.assertNotIn(kw, md)

    def test_full_stock_markdown_not_regressed(self) -> None:
        """非 fund（report_type="full"）记录仍走股票 markdown（不作弊、不回归）。"""
        history_id = self._save(_build_full_stock_result(), "full")
        md = HistoryService(self.db).get_markdown_report(str(history_id))
        self.assertIsNotNone(md)
        self.assertIn("600519", md)
        self.assertIn("止损", md)  # 股票骨架仍含买卖点，确认未误伤股票路径
        self.assertNotIn("基金净值体检", md)


if __name__ == "__main__":
    unittest.main()
