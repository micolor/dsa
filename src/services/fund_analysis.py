# src/services/fund_analysis.py
#
# 场外基金报告的两层结构：
#
#   1. 确定性层（``build_fund_report``）：净值、区间收益、回撤、波动率、夏普、
#      持仓、资产配置——全部由数据源算出，不经过 LLM，永远可用。
#   2. LLM 增强层（``enrich_fund_report_with_llm``）：在事实之上给出集中度解读、
#      综合判断、申赎倾向与风险提示，挂在 ``dashboard["llm"]`` 下。
#
# 增强层是**可选**的：未配置 LLM 或调用失败时返回 ``None``，确定性报告照常产出
# （AGENTS.md §7：配置后增强能力，不配置也可运行）。两层不共享字段，因此不会
# 出现同一张卡片上两个互相矛盾的数据源。
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any, Optional
from data_provider.fund_fetcher import FundProfile

if TYPE_CHECKING:
    from src.analyzer import AnalysisResult

logger = logging.getLogger(__name__)

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
    """metrics 口径是小数比例（0.1234 表示 12.34%）。"""
    return "N/A" if x is None else f"{x*100:.1f}%"


def build_fund_llm_user_prompt(fund: FundProfile) -> str:
    """把确定性事实组织成 LLM 的用户上下文。

    只给事实（净值 / 收益 / 回撤 / 波动 / 夏普 / 持仓 / 配置），不给确定性层的
    结论文案——把 ``build_fund_report`` 的判断也喂进去，模型往往只会换个说法
    复述一遍，增强层就白加了。
    """
    lines = [
        f"基金代码: {fund.code}",
        f"基金名称: {fund.name}",
    ]
    if fund.fund_type:
        lines.append(f"基金类型: {fund.fund_type}")

    if fund.nav_history:
        latest = fund.nav_history[-1]
        lines.append("")
        lines.append(f"最新净值({latest.date}): {latest.unit_nav}")
        lines.append(
            "区间收益: "
            f"近1月 {fmt(fund.return_1m)} | 近3月 {fmt(fund.return_3m)} | "
            f"近6月 {fmt(fund.return_6m)} | 近1年 {fmt(fund.return_1y)}"
        )

    risk_lines = [
        f"最大回撤: {fmt(fund.max_drawdown)}",
        f"年化波动率: {fmt(fund.annual_volatility)}",
    ]
    if fund.sharpe is not None:
        risk_lines.append(f"夏普比率: {fund.sharpe:.2f}")
    if any("N/A" not in line for line in risk_lines):
        lines.append("")
        lines.extend(risk_lines)

    if fund.holdings:
        lines.append("")
        lines.append("前十大重仓(占净值比例):")
        for h in fund.holdings:
            pct = "N/A" if h.pct_of_nav is None else f"{h.pct_of_nav:.2f}%"
            lines.append(f"  {h.rank}. {h.stock_code} {h.stock_name} {pct}")

    alloc = fund.asset_allocation
    if alloc is not None:
        lines.append("")
        lines.append(
            f"资产配置({alloc.report_date}): "
            f"股票 {fmt_pct(alloc.stock_pct)} | 债券 {fmt_pct(alloc.bond_pct)} | "
            f"现金 {fmt_pct(alloc.cash_pct)}"
        )

    return "\n".join(lines)


def fmt_pct(x: Optional[float]) -> str:
    """资产配置口径已经是百分数值（88.0 表示 88%），不再乘 100。"""
    return "N/A" if x is None else f"{x:.1f}%"


def enrich_fund_report_with_llm(
    fund: FundProfile,
    *,
    analyzer: Any,
    report_language: str = "zh",
) -> Optional[dict]:
    """调用 LLM 生成基金解读，返回可直接放进 ``dashboard["llm"]`` 的字典。

    ``analyzer`` 由调用方注入（pipeline 复用已构造的 ``self.analyzer``），
    不在本函数内构造 LLM 客户端——把网络客户端的构造藏进服务函数会让测试
    无法绕开真实调用。

    LLM 是增强层，失败一律降级而不是抛错：未配置模型、所有模型都失败、模型
    返回的内容违反契约，都返回 ``None`` 并记一条 warning。调用方据此渲染
    「只有确定性部分」的报告，而不是整份报告消失。

    返回 ``None`` 的四种情形：
    - LLM 调用抛错；
    - 模型返回了内容但全部字段为 null（空壳区块不值得展示）；
    - 模型返回的内容违反 ``FundReportSchema``（如 sentiment_score 越界）；
    - 模型返回的不是 JSON 对象。
    """
    from src.schemas.fund_report_schema import FundReportSchema

    try:
        payload = analyzer.run_fund_analysis(
            build_fund_llm_user_prompt(fund),
            report_language=report_language,
        )
    except Exception as exc:
        logger.warning(
            "[%s] 基金 LLM 解读失败,降级为确定性报告: %s", fund.code, exc
        )
        return None

    if not isinstance(payload, dict):
        logger.warning(
            "[%s] 基金 LLM 解读返回 %s,降级为确定性报告",
            fund.code,
            type(payload).__name__,
        )
        return None

    try:
        block = FundReportSchema(**payload).model_dump()
    except Exception as exc:
        logger.warning(
            "[%s] 基金 LLM 解读未通过契约校验,降级为确定性报告: %s", fund.code, exc
        )
        return None

    if all(value is None for value in block.values()):
        logger.warning(
            "[%s] 基金 LLM 解读全为空字段,按未产出处理", fund.code
        )
        return None

    return block


def map_fund_report_to_report_result(
    report: dict,
    config=None,
    report_language: str = "zh",
    fund_llm: Optional[dict] = None,
) -> "AnalysisResult":
    """确定性地把 fund 报告字典映射为 AnalysisResult（无网络）。

    ``AnalysisResult`` 只要求 code/name/sentiment_score/trend_prediction/
    operation_advice，其余字段均有默认值；它没有 summary/metrics/report_type
    字段，因此分析摘要放入 ``analysis_summary``，结构化载荷放入 ``dashboard``。

    ``fund_llm`` 是 ``enrich_fund_report_with_llm`` 的产物（未产出时为 None），
    原样挂到 ``dashboard["llm"]``，不参与本函数任何判断。
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
        # 下游按此分数排序摘要，保持确定性取值：模型给的分数随调用波动，
        # 用它排序会让同一条基金记录在不同运行里跳来跳去。
        # LLM 分数只作为解读展示在 dashboard["llm"] 内。
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
            "llm": fund_llm,
        },
    )
