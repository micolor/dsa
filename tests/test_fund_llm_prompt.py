# -*- coding: utf-8 -*-
"""场外基金 LLM 接入层测试。

约束三件事：

1. Prompt 声明的字段集必须与 ``FundReportSchema`` 完全一致，二者不得漂移；
2. 基金报告不得引入股票概念（涨跌停 / 龙虎榜 / 北向资金 / 筹码 / 买卖档），
   输出语言段的措辞也不能把 ``decision_type``、``buy|hold|sell`` 带进来；
3. ``run_fund_analysis`` 失败时必须**抛出**，由 pipeline 决定是否降级到
   确定性报告——不在这一层静默吞掉，否则调用方分不清「LLM 成功但内容为空」
   和「LLM 整条链路挂了」。
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    from tests.litellm_stub import ensure_litellm_stub

    ensure_litellm_stub()

from src.analyzer import FUND_REPORT_FIELDS, GeminiAnalyzer
from src.schemas.fund_report_schema import FundReportSchema


def _analyzer() -> GeminiAnalyzer:
    with patch.object(GeminiAnalyzer, "_init_litellm", return_value=None):
        return GeminiAnalyzer()


# ---------------------------------------------------------------- 契约一致性


def test_prompt_field_contract_matches_schema():
    """Prompt 里通告的字段集就是 schema 的字段集，两边改一边就会红。"""
    assert set(FUND_REPORT_FIELDS) == set(FundReportSchema.model_fields)


@pytest.mark.parametrize("lang", ["zh", "en", "ko"])
def test_rendered_prompt_declares_every_contract_field(lang):
    prompt = _analyzer()._get_fund_system_prompt(lang)

    for field in FUND_REPORT_FIELDS:
        assert field in prompt, f"{lang} prompt 缺少字段 {field}"


# ------------------------------------------------------------ 基金语义边界


def test_prompt_forbids_stock_concepts():
    """基金只有每日净值，没有盘中行情；股票概念必须被显式排除。"""
    prompt = _analyzer()._get_fund_system_prompt("zh")

    assert "不得使用股票概念" in prompt
    for concept in ("涨跌停", "龙虎榜", "北向资金", "筹码"):
        assert concept in prompt, f"prompt 未排除股票概念：{concept}"
    assert "申赎" in prompt, "基金的操作倾向是申赎，不是买卖"


@pytest.mark.parametrize("lang", ["zh", "en", "ko"])
def test_output_language_section_does_not_import_stock_semantics(lang):
    """输出语言段不得照抄股票版（``decision_type`` / ``buy|hold|sell``）。

    股票 prompt 的该段落要求 ``decision_type`` 保持 ``buy|hold|sell``，
    照抄会让基金报告被要求产出买卖档，与申赎语义冲突。
    """
    prompt = _analyzer()._get_fund_system_prompt(lang)

    assert "decision_type" not in prompt
    assert "buy|hold|sell" not in prompt


# -------------------------------------------------------------- 语言选择


def test_language_selection_appends_a_matching_output_language_section():
    analyzer = _analyzer()

    zh = analyzer._get_fund_system_prompt("zh")
    en = analyzer._get_fund_system_prompt("en")
    ko = analyzer._get_fund_system_prompt("ko")

    assert "输出语言" in zh and "必须使用中文" in zh
    assert "Output Language" in en and "English" in en
    assert "Output Language" in ko and "Korean" in ko

    # 三种语言共用同一份中文基底，只是追加的语言段落不同。
    assert zh != en and en != ko


def test_unknown_language_falls_back_to_chinese():
    """未知取值不得抛错，按既有惯例落到默认语言。"""
    assert "输出语言" in _analyzer()._get_fund_system_prompt("fr")


# ---------------------------------------------------------------- 调用层


def test_run_fund_analysis_returns_the_parsed_json():
    analyzer = _analyzer()
    runtime_config = SimpleNamespace(llm_temperature=0.3, report_language="zh")
    payload = '{"fund_name": "中欧医疗创新股票C", "sentiment_score": 60}'

    with patch.object(analyzer, "_get_runtime_config", return_value=runtime_config), patch.object(
        analyzer, "_call_litellm", return_value=(payload, "model-x", {})
    ) as call:
        result = analyzer.run_fund_analysis("USER PROMPT")

    assert result == {"fund_name": "中欧医疗创新股票C", "sentiment_score": 60}
    assert call.call_args.args[0] == "USER PROMPT"
    # 系统 prompt 由分析器自己构造，调用方不传。
    assert call.call_args.kwargs["system_prompt"] == analyzer._get_fund_system_prompt("zh")


def test_run_fund_analysis_follows_the_requested_report_language():
    analyzer = _analyzer()
    runtime_config = SimpleNamespace(llm_temperature=0.3, report_language="zh")

    with patch.object(analyzer, "_get_runtime_config", return_value=runtime_config), patch.object(
        analyzer, "_call_litellm", return_value=("{}", "model-x", {})
    ) as call:
        analyzer.run_fund_analysis("USER PROMPT", report_language="en")

    assert "Output Language" in call.call_args.kwargs["system_prompt"]


def test_run_fund_analysis_falls_back_to_the_runtime_report_language():
    analyzer = _analyzer()
    runtime_config = SimpleNamespace(llm_temperature=0.3, report_language="ko")

    with patch.object(analyzer, "_get_runtime_config", return_value=runtime_config), patch.object(
        analyzer, "_call_litellm", return_value=("{}", "model-x", {})
    ) as call:
        analyzer.run_fund_analysis("USER PROMPT")

    assert "Korean" in call.call_args.kwargs["system_prompt"]


def test_run_fund_analysis_propagates_model_failure_instead_of_swallowing_it():
    """整条链路失败必须冒泡，让 pipeline 决定降级；这里静默返回空字典会掩盖故障。"""
    analyzer = _analyzer()
    runtime_config = SimpleNamespace(llm_temperature=0.3, report_language="zh")

    with patch.object(analyzer, "_get_runtime_config", return_value=runtime_config), patch.object(
        analyzer,
        "_call_litellm",
        side_effect=RuntimeError("all models failed"),
    ):
        with pytest.raises(RuntimeError, match="all models failed"):
            analyzer.run_fund_analysis("USER PROMPT")
