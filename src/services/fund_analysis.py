# src/services/fund_analysis.py
from __future__ import annotations
from typing import TYPE_CHECKING, Optional
from data_provider.fund_fetcher import FundProfile

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult

# 无需 LLM 的确定性结论:报告骨架;LLM 层作为增强由下游 Prompt 任务补充。
def _risk_grade(mdd, vol):
    if mdd is None or vol is None:
        return "数据不足"
    if mdd < -0.20 or vol > 0.30:
        return "高"
    if mdd < -0.08 or vol > 0.15:
        return "中"
    return "低"

def _holdings_to_dicts(holdings) -> list:
    """把 FundProfile.holdings（FundHolding 列表）序列化为可存储字典。"""
    return [
        {
            "rank": h.rank,
            "stock_code": h.stock_code,
            "stock_name": h.stock_name,
            "pct_of_nav": h.pct_of_nav,
            "share_count": h.share_count,
            "market_value": h.market_value,
        }
        for h in holdings
    ]


def _alloc_to_dict(a) -> Optional[dict]:
    """把 FundProfile.asset_allocation（FundAssetAllocation）序列化为字典。"""
    if a is None:
        return None
    return {
        "report_date": a.report_date,
        "stock_pct": a.stock_pct,
        "bond_pct": a.bond_pct,
        "cash_pct": a.cash_pct,
        "net_asset": a.net_asset,
    }


def build_fund_report(fund: FundProfile, risk_free: float = 0.02) -> dict:
    latest = fund.nav_history[-1] if fund.nav_history else None
    m = {
        "return_1m": fund.return_1m, "return_3m": fund.return_3m,
        "return_6m": fund.return_6m, "return_1y": fund.return_1y,
        "max_drawdown": fund.max_drawdown, "annual_volatility": fund.annual_volatility,
        "sharpe": fund.sharpe,
    }
    risk = _risk_grade(fund.max_drawdown, fund.annual_volatility)
    trend = "上行" if (fund.return_3m or 0) > 0 and (fund.return_1y or 0) > 0 else "震荡"
    if (fund.return_1y or 0) < -0.1:
        trend = "下行"
    # 净值体检：只描述风险/走势，不下任何持有/加减仓等仓位判断（基金无买卖点）。
    if risk == "高":
        advice = "风险偏高,注意波动"
    elif trend == "下行":
        advice = "近期走势偏弱,注意回撤"
    elif risk == "中":
        advice = "风险中等,涨跌波动较明显"
    else:
        advice = "风险较低,走势相对平稳"
    summary = (
        f"{fund.name}({fund.code}) 近1年收益 {fmt(fund.return_1y)}、最大回撤 {fmt(fund.max_drawdown)};"
        f"风险等级:{risk}。基于净值序列,非股票式信号,不构成投资建议。"
    )
    return {
        "report_type": "fund",
        "code": fund.code, "name": fund.name,
        "sentiment_score": 50,  # 无买卖档,固定中性(下游摘要按分数排序用)
        "operation_advice": advice,
        "trend_prediction": trend,
        "summary": summary,
        "metrics": m, "latest_nav": latest.unit_nav if latest else None,
        "not_investment_advice": True,
        "holdings": _holdings_to_dicts(fund.holdings),
        "asset_allocation": _alloc_to_dict(fund.asset_allocation),
    }

def fmt(x: Optional[float]) -> str:
    return "N/A" if x is None else f"{x*100:.1f}%"

def map_fund_report_to_report_result(
    report: dict,
    config=None,
    report_language: str = "zh",
) -> "AnalysisResult":
    """确定性地把 fund 报告字典映射为 AnalysisResult（无 LLM，无网络）。

    ``AnalysisResult`` 只要求 code/name/sentiment_score/trend_prediction/
    operation_advice，其余字段均有默认值；它没有 summary/metrics/report_type
    字段，因此分析摘要放入 ``analysis_summary``，结构化载荷放入 ``dashboard``。
    """
    from src.analyzer import AnalysisResult

    # 配置提供且入参为空时，从 config.report_language 安全读取。
    if not report_language and config is not None:
        report_language = getattr(config, "report_language", "zh") or "zh"
    report_language = report_language or "zh"

    code = str(report.get("code") or "")
    name = str(report.get("name") or code or "")

    return AnalysisResult(
        code=code,
        name=name,
        sentiment_score=report.get("sentiment_score", 50),  # 无买卖档,中性
        trend_prediction=str(report.get("trend_prediction", "震荡")),
        operation_advice=str(report.get("operation_advice", "风险与走势信息,仅供参考")),
        decision_type="hold",
        confidence_level="中",
        report_language=report_language,
        success=True,
        analysis_summary=str(report.get("summary", "")),
        dashboard={
            "report_type": "fund",
            "metrics": report.get("metrics"),
            "latest_nav": report.get("latest_nav"),
            "not_investment_advice": True,
            "holdings": report.get("holdings"),
            "asset_allocation": report.get("asset_allocation"),
        },
    )
