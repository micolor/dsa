import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AnalysisReport } from '../../../types/analysis';
import { ReportSummary } from '../ReportSummary';

vi.mock('../../../api/history', () => ({
  historyApi: {
    getNews: vi.fn().mockResolvedValue({ total: 0, items: [] }),
    getDiagnostics: vi.fn().mockResolvedValue({}),
  },
}));

vi.mock('../../../api/stocks', () => ({
  stocksApi: {
    getStockHistory: vi.fn().mockResolvedValue({ data: [] }),
  },
}));

const baseMeta = {
  queryId: 'q-1',
  stockCode: '600519',
  stockName: '贵州茅台',
  reportLanguage: 'zh' as const,
  createdAt: '2026-09-05T08:00:00Z',
};

const baseSummary = {
  analysisSummary: '摘要',
  operationAdvice: '持有',
  trendPrediction: '震荡',
  sentimentScore: 50,
};

const fundReport = (overrides: Partial<AnalysisReport> = {}): AnalysisReport => ({
  meta: {
    ...baseMeta,
    stockCode: 'fund:012345',
    stockName: '某指数增强基金',
    reportType: 'fund' as const,
  },
  summary: baseSummary,
  strategy: { idealBuy: '1.0', secondaryBuy: '0.9', stopLoss: '0.8', takeProfit: '1.2' },
  details: {
    rawResult: {
      // 真实链路 getDetail 会对整个响应做 deep camel 转换，
      // 故这里的 dashboard.metrics 使用 camelCase 键（return1M/maxDrawdown/latestNav/notInvestmentAdvice）。
      dashboard: {
        report_type: 'fund',
        metrics: {
          return1M: 0.1234,
          return3M: 0.0567,
          return6M: -0.0234,
          return1Y: 0.4567,
          maxDrawdown: -0.20,
          annualVolatility: 0.32,
          sharpe: 0.752,
        },
        latestNav: 1.2345,
        notInvestmentAdvice: true,
      },
    },
  },
  ...overrides,
});

describe('ReportSummary', () => {
  it('renders FundMetricsCard and omits StockPriceChart/ReportStrategy for fund reports', () => {
    render(<ReportSummary data={fundReport()} />);

    expect(screen.getByTestId('fund-metrics-card')).toBeInTheDocument();
    // 基金报告不得出现股票式 K 线图与买卖点策略区
    expect(screen.queryByTestId('stock-price-chart')).not.toBeInTheDocument();
    expect(screen.queryByTestId('stock-price-chart-loading')).not.toBeInTheDocument();
    expect(screen.queryByText('狙击点位')).not.toBeInTheDocument();
    expect(screen.queryByText('理想买入')).not.toBeInTheDocument();
    expect(screen.queryByText('止损价位')).not.toBeInTheDocument();
  });

  it('formats fund metrics as percent/sharpe and shows the disclaimer', () => {
    render(<ReportSummary data={fundReport()} />);

    expect(screen.getByText('12.3%')).toBeInTheDocument();
    expect(screen.getByText('5.7%')).toBeInTheDocument();
    expect(screen.getByText('-2.3%')).toBeInTheDocument();
    expect(screen.getByText('45.7%')).toBeInTheDocument();
    expect(screen.getByText('-20.0%')).toBeInTheDocument();
    expect(screen.getByText('32.0%')).toBeInTheDocument();
    expect(screen.getByText('0.75')).toBeInTheDocument();
    expect(screen.getByText('1.2345')).toBeInTheDocument();
    // 风险等级镜像后端 _risk_grade：vol>0.30 → 高
    expect(screen.getByLabelText('风险等级: 高')).toBeInTheDocument();
    expect(screen.getByTestId('fund-disclaimer')).toBeInTheDocument();
    expect(screen.getByText('不构成投资建议')).toBeInTheDocument();
  });

  it('still renders StockPriceChart for non-fund reports (no regression)', () => {
    const nonFundReport: AnalysisReport = {
      meta: {
        ...baseMeta,
        stockCode: '600519',
        stockName: '贵州茅台',
        reportType: 'full' as const,
      },
      summary: baseSummary,
      strategy: { idealBuy: '1.0', secondaryBuy: '0.9', stopLoss: '0.8', takeProfit: '1.2' },
      details: {
        rawResult: { dashboard: undefined },
      },
    };

    render(<ReportSummary data={nonFundReport} />);

    // 非基金路径保留 K 线图与买卖点策略区
    expect(screen.getByTestId('stock-price-chart-loading')).toBeInTheDocument();
    expect(screen.getByText('狙击点位')).toBeInTheDocument();
    expect(screen.queryByTestId('fund-metrics-card')).not.toBeInTheDocument();
  });
});
