import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FundMetricsCard } from '../FundMetricsCard';

/**
 * 基金卡片有两层载荷：
 * - 事实层（metrics / holdings / assetAllocation）来自后端确定性计算；
 * - 解读层（llm）来自 LLM，未配置模型或调用失败时为 null。
 *
 * 这里守的是解读层的渲染边界：有内容才渲染、半截内容不留空行、
 * 老报告（没有 llm 键）不能因此崩。
 */
const baseDashboard = {
  reportType: 'fund',
  metrics: { return1M: 0.1234, maxDrawdown: -0.2, annualVolatility: 0.32, sharpe: 0.75 },
  latestNav: 1.2345,
  notInvestmentAdvice: true,
};

const llmBlock = {
  holdingsConcentration: '前十大重仓占净值 68%，集中度较高',
  analysisSummary: '近一年回撤明显但已收复大半。',
  operationAdvice: '可考虑分批申购，回撤超 20% 时暂停。',
  riskWarning: '重仓股集中在单一行业。',
  sentimentScore: 60,
};

describe('FundMetricsCard LLM 解读层', () => {
  it('渲染解读区块的全部字段与情绪分', () => {
    render(<FundMetricsCard dashboard={{ ...baseDashboard, llm: llmBlock }} />);

    expect(screen.getByTestId('fund-llm-insight')).toBeInTheDocument();
    expect(screen.getByText('AI 解读')).toBeInTheDocument();
    expect(screen.getByText('持仓集中度')).toBeInTheDocument();
    expect(screen.getByText('前十大重仓占净值 68%，集中度较高')).toBeInTheDocument();
    expect(screen.getByText('综合解读')).toBeInTheDocument();
    expect(screen.getByText('近一年回撤明显但已收复大半。')).toBeInTheDocument();
    // 基金是申赎语义，不得出现买卖点措辞
    expect(screen.getByText('申赎建议')).toBeInTheDocument();
    expect(screen.getByText('风险提示')).toBeInTheDocument();
    expect(screen.getByText('情绪分 60')).toBeInTheDocument();
  });

  it('未配置 LLM（llm 为 null）时整段不渲染', () => {
    render(<FundMetricsCard dashboard={{ ...baseDashboard, llm: null }} />);

    expect(screen.queryByTestId('fund-llm-insight')).not.toBeInTheDocument();
    // 事实层不受影响
    expect(screen.getByTestId('fund-metrics-card')).toBeInTheDocument();
    expect(screen.getByText('12.3%')).toBeInTheDocument();
  });

  it('老报告没有 llm 键时照常渲染', () => {
    render(<FundMetricsCard dashboard={baseDashboard} />);

    expect(screen.queryByTestId('fund-llm-insight')).not.toBeInTheDocument();
    expect(screen.getByText('12.3%')).toBeInTheDocument();
  });

  it('全空字段（后端已判为未产出）时不留空壳区块', () => {
    render(
      <FundMetricsCard
        dashboard={{
          ...baseDashboard,
          llm: {
            holdingsConcentration: null,
            analysisSummary: null,
            operationAdvice: null,
            riskWarning: null,
            sentimentScore: null,
          },
        }}
      />,
    );

    expect(screen.queryByTestId('fund-llm-insight')).not.toBeInTheDocument();
  });

  it('只回了一半字段时只渲染有内容的那几行', () => {
    render(
      <FundMetricsCard
        dashboard={{
          ...baseDashboard,
          llm: { analysisSummary: '只有综合解读', sentimentScore: 45 },
        }}
      />,
    );

    expect(screen.getByText('只有综合解读')).toBeInTheDocument();
    expect(screen.getByText('情绪分 45')).toBeInTheDocument();
    // 空字段不留空行
    expect(screen.queryByText('持仓集中度')).not.toBeInTheDocument();
    expect(screen.queryByText('申赎建议')).not.toBeInTheDocument();
    expect(screen.queryByText('风险提示')).not.toBeInTheDocument();
  });

  it('只有情绪分没有文字时仍渲染区块', () => {
    render(
      <FundMetricsCard dashboard={{ ...baseDashboard, llm: { sentimentScore: 70 } }} />,
    );

    expect(screen.getByTestId('fund-llm-insight')).toBeInTheDocument();
    expect(screen.getByText('情绪分 70')).toBeInTheDocument();
  });

  it('按语言渲染解读字段标题', () => {
    render(
      <FundMetricsCard
        dashboard={{ ...baseDashboard, llm: { analysisSummary: 'summary' } }}
        language="en"
      />,
    );

    expect(screen.getByText('AI Insight')).toBeInTheDocument();
    expect(screen.getByText('Analysis Summary')).toBeInTheDocument();
  });
});
