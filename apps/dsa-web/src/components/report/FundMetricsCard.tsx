import type React from 'react';
import { Badge, Card } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import type { ReportLanguage } from '../../types/analysis';

interface FundMetricsCardProps {
  /** 来自 `report.details.rawResult.dashboard`，类型为 `Record<string, unknown>`，需安全读取。 */
  dashboard?: unknown;
  language?: ReportLanguage;
}

interface FundDashboard {
  report_type?: string;
  metrics?: {
    return1M?: number | string;
    return3M?: number | string;
    return6M?: number | string;
    return1Y?: number | string;
    maxDrawdown?: number | string;
    annualVolatility?: number | string;
    sharpe?: number | string;
  };
  latestNav?: number | string;
  notInvestmentAdvice?: boolean;
}

/** 读取成有限数值，非法值统一回退为 null，避免 NaN/Infinity 污染展示。 */
const toFiniteNumber = (value: unknown): number | null => {
  if (typeof value === 'number') {
    return Number.isFinite(value) ? value : null;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }
    const parsed = Number(trimmed);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
};

const isFundDashboard = (value: unknown): value is FundDashboard =>
  Boolean(value && typeof value === 'object');

/**
 * 场外基金净值体检指标卡。
 *
 * 只展示净值与风险指标，不包含任何买入/止损/仓位建议（基金无股票式买卖点）。
 * 百分比字段按 `/100` 小数存（如 0.1234 表示 12.34%），统一 `(x*100).toFixed(1)+'%'`；
 * Sharpe 为比值，`x.toFixed(2)`。空值展示「—」（复用 `text.noValue`）。
 *
 * 风险等级客户端计算，镜像后端 `_risk_grade`（fund_analysis.py）：
 * - mdd < -0.20 或 vol > 0.30 → 高
 * - mdd < -0.08 或 vol > 0.15 → 中
 * - 否则低；mdd/vol 任一缺失 → 数据不足。
 */
export const FundMetricsCard: React.FC<FundMetricsCardProps> = ({ dashboard, language = 'zh' }) => {
  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);
  const raw = isFundDashboard(dashboard) ? dashboard : null;
  const metrics = raw?.metrics ?? {};
  // getDetail 会把整个响应（含 rawResult.dashboard）做 deep camel 转换，
  // 因此这里读取 camelCase 键（return1M/maxDrawdown/latestNav/notInvestmentAdvice）。
  const latestNav = toFiniteNumber(raw?.latestNav);

  const percent = (value: unknown): string => {
    const numeric = toFiniteNumber(value);
    return numeric === null ? text.noValue : `${(numeric * 100).toFixed(1)}%`;
  };

  const sharpe = toFiniteNumber(metrics.sharpe);
  const sharpeText = sharpe === null ? text.noValue : sharpe.toFixed(2);

  const mdd = toFiniteNumber(metrics.maxDrawdown);
  const vol = toFiniteNumber(metrics.annualVolatility);

  const riskGrade: {
    label: string;
    variant: 'success' | 'warning' | 'danger' | 'info';
  } = (() => {
    if (mdd === null || vol === null) {
      return { label: text.insufficientData, variant: 'info' };
    }
    if (mdd < -0.20 || vol > 0.30) {
      return { label: text.riskHigh, variant: 'danger' };
    }
    if (mdd < -0.08 || vol > 0.15) {
      return { label: text.riskMedium, variant: 'warning' };
    }
    return { label: text.riskLow, variant: 'success' };
  })();

  const metricItems: Array<{ label: string; value: string }> = [
    { label: text.return1m, value: percent(metrics.return1M) },
    { label: text.return3m, value: percent(metrics.return3M) },
    { label: text.return6m, value: percent(metrics.return6M) },
    { label: text.return1y, value: percent(metrics.return1Y) },
    { label: text.maxDrawdown, value: percent(metrics.maxDrawdown) },
    { label: text.annualVolatility, value: percent(metrics.annualVolatility) },
    { label: text.sharpe, value: sharpeText },
  ];

  const shouldShowDisclaimer = raw?.notInvestmentAdvice === true;

  return (
    <div data-testid="fund-metrics-card">
      <Card variant="bordered" padding="md" className="home-panel-card">
        <DashboardPanelHeader eyebrow={text.navMetrics} title={text.unitNav} className="mb-3" />
      <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
        <span className="text-3xl font-bold font-mono text-foreground">
          {latestNav === null ? text.noValue : String(latestNav)}
        </span>
        <Badge variant={riskGrade.variant} className="shrink-0 shadow-none" aria-label={`${text.riskGrade}: ${riskGrade.label}`}>
          {riskGrade.label}
        </Badge>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {metricItems.map((item) => (
          <div key={item.label} className="rounded-lg border border-subtle p-3">
            <p className="label-uppercase">{item.label}</p>
            <p className="mt-1 font-mono font-semibold text-foreground">{item.value}</p>
          </div>
        ))}
      </div>
        {shouldShowDisclaimer && (
          <p className="mt-4 rounded-lg border border-subtle bg-muted/30 px-3 py-2 text-xs text-muted-text" data-testid="fund-disclaimer">
            {text.fundDisclaimer}
          </p>
        )}
      </Card>
    </div>
  );
};

export default FundMetricsCard;
