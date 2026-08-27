import type React from 'react';
import { FileText, PieChart, ShieldAlert, TrendingUp, WalletCards } from 'lucide-react';
import type { AnalysisReport, FundReportPayload, ReportLanguage } from '../../types/analysis';
import { normalizeReportLanguage } from '../../utils/reportLanguage';
import { Card } from '../common';

interface FundReportViewProps {
  report?: AnalysisReport;
  recordId?: number;
  reportLanguage?: ReportLanguage;
  className?: string;
  onOpenRunFlow?: (recordId: number) => void;
}

const FUND_REPORT_TEXT: Record<ReportLanguage, {
  fundOverview: string;
  performanceDrawdown: string;
  topHoldings: string;
  analysisAdvice: string;
  riskWarning: string;
  fundName: string;
  fundType: string;
  manager: string;
  scale: string;
  inceptionDate: string;
  intervalReturn: string;
  maxDrawdown: string;
  currentDrawdown: string;
  holdingsConcentration: string;
  code: string;
  name: string;
  ratio: string;
  quarter: string;
  sentimentScore: string;
  noFundData: string;
}> = {
  zh: {
    fundOverview: '基金概览',
    performanceDrawdown: '净值与回撤',
    topHoldings: '重仓股',
    analysisAdvice: '分析与申赎建议',
    riskWarning: '风险提示',
    fundName: '名称',
    fundType: '类型',
    manager: '基金经理',
    scale: '规模',
    inceptionDate: '成立日期',
    intervalReturn: '区间收益',
    maxDrawdown: '最大回撤',
    currentDrawdown: '当前回撤',
    holdingsConcentration: '持仓集中度',
    code: '代码',
    name: '名称',
    ratio: '占比',
    quarter: '季度',
    sentimentScore: '情绪评分',
    noFundData: '暂无基金数据',
  },
  en: {
    fundOverview: 'Fund Overview',
    performanceDrawdown: 'Performance & Drawdown',
    topHoldings: 'Top Holdings',
    analysisAdvice: 'Analysis & Redemption Advice',
    riskWarning: 'Risk Warning',
    fundName: 'Name',
    fundType: 'Type',
    manager: 'Manager',
    scale: 'AUM',
    inceptionDate: 'Inception Date',
    intervalReturn: 'Interval Return',
    maxDrawdown: 'Max Drawdown',
    currentDrawdown: 'Current Drawdown',
    holdingsConcentration: 'Holdings Concentration',
    code: 'Code',
    name: 'Name',
    ratio: 'Ratio',
    quarter: 'Quarter',
    sentimentScore: 'Sentiment',
    noFundData: 'No fund data available',
  },
  ko: {
    fundOverview: '펀드 개요',
    performanceDrawdown: '성과 및 드로다운',
    topHoldings: '주요 보유 종목',
    analysisAdvice: '분석 및 환매 조언',
    riskWarning: '리스크 경고',
    fundName: '이름',
    fundType: '유형',
    manager: '매니저',
    scale: '규모',
    inceptionDate: '설정일',
    intervalReturn: '구간 수익률',
    maxDrawdown: '최대 드로다운',
    currentDrawdown: '현재 드로다운',
    holdingsConcentration: '보유 집중도',
    code: '코드',
    name: '이름',
    ratio: '비중',
    quarter: '분기',
    sentimentScore: '감정 점수',
    noFundData: '펀드 데이터 없음',
  },
};

const FINITE_PLACEHOLDER = '—';

const percentLabel = (value: number | null | undefined): string => {
  if (value === null || value === undefined) {
    return FINITE_PLACEHOLDER;
  }
  return `${value}%`;
};

const dashOrValue = (value: string | null | undefined): string =>
  value && value.trim() ? value : FINITE_PLACEHOLDER;

const FieldPill: React.FC<{ label: string; value?: React.ReactNode }> = ({ label, value }) => (
  <div className="rounded-lg border border-subtle p-3">
    <p className="label-uppercase">{label}</p>
    <p className="mt-1 font-semibold text-foreground">
      {value === null || value === undefined || value === FINITE_PLACEHOLDER
        ? FINITE_PLACEHOLDER
        : value}
    </p>
  </div>
);

export const FundReportView: React.FC<FundReportViewProps> = ({
  report,
  reportLanguage = 'zh',
  className = '',
}) => {
  const normalizedReportLanguage = normalizeReportLanguage(reportLanguage);
  const text = FUND_REPORT_TEXT[normalizedReportLanguage];
  const fund: FundReportPayload | undefined = report?.details?.fund;

  if (!fund) {
    return (
      <Card variant="bordered" padding="md" className="home-panel-card text-left">
        <p className="text-sm text-secondary-text">{text.noFundData}</p>
      </Card>
    );
  }

  const holdings = Array.isArray(fund.topHoldings) ? fund.topHoldings : [];
  const title = fund.fundName || report?.meta?.stockName || 'Fund Report';

  return (
    <div className={`animate-fade-in space-y-4 pb-8 ${className}`}>
      <Card variant="gradient" padding="md" className="home-report-hero text-left">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="mb-2 inline-flex items-center gap-2 text-xs font-semibold text-secondary-text">
              <WalletCards className="h-4 w-4" aria-hidden="true" />
              <span>FUND</span>
            </div>
            <h2 className="text-[26px] font-bold leading-tight text-foreground sm:text-[30px]">
              {title}
            </h2>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-text">
              {report?.meta?.stockCode ? (
                <span className="home-accent-chip px-2 py-0.5 font-mono">{report.meta.stockCode}</span>
              ) : null}
              {report?.meta?.createdAt ? (
                <span>{new Date(report.meta.createdAt).toLocaleString()}</span>
              ) : null}
              {typeof fund.sentimentScore === 'number' ? (
                <span className="home-accent-chip px-2 py-0.5">
                  {text.sentimentScore}: {fund.sentimentScore}
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </Card>

      <Card variant="bordered" padding="md" className="home-panel-card text-left">
        <div className="mb-3 flex items-center gap-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <FileText className="h-4 w-4" aria-hidden="true" />
          </span>
          <h3 className="text-base font-semibold text-foreground">{text.fundOverview}</h3>
        </div>
        <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-3 xl:grid-cols-5">
          <FieldPill label={text.fundName} value={dashOrValue(fund.fundName)} />
          <FieldPill label={text.fundType} value={dashOrValue(fund.fundType)} />
          <FieldPill label={text.manager} value={dashOrValue(fund.manager)} />
          <FieldPill label={text.scale} value={dashOrValue(fund.scale)} />
          <FieldPill label={text.inceptionDate} value={dashOrValue(fund.inceptionDate)} />
        </div>
        {fund.holdingsConcentration ? (
          <div className="mt-3 rounded-lg border border-subtle p-3">
            <p className="label-uppercase">{text.holdingsConcentration}</p>
            <p className="mt-1 text-sm text-foreground">{fund.holdingsConcentration}</p>
          </div>
        ) : null}
      </Card>

      <Card variant="bordered" padding="md" className="home-panel-card text-left">
        <div className="mb-3 flex items-center gap-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <TrendingUp className="h-4 w-4" aria-hidden="true" />
          </span>
          <h3 className="text-base font-semibold text-foreground">{text.performanceDrawdown}</h3>
        </div>
        <div className="grid grid-cols-2 gap-2 text-sm md:grid-cols-3">
          <FieldPill label={text.intervalReturn} value={percentLabel(fund.intervalReturn)} />
          <FieldPill label={text.maxDrawdown} value={percentLabel(fund.maxDrawdown)} />
          <FieldPill label={text.currentDrawdown} value={percentLabel(fund.currentDrawdown)} />
        </div>
      </Card>

      <Card variant="bordered" padding="md" className="home-panel-card text-left">
        <div className="mb-3 flex items-center gap-2">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <PieChart className="h-4 w-4" aria-hidden="true" />
          </span>
          <h3 className="text-base font-semibold text-foreground">{text.topHoldings}</h3>
        </div>
        {holdings.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="text-left text-xs uppercase text-muted-text">
                <tr>
                  <th className="px-2 py-2">{text.code}</th>
                  <th className="px-2 py-2">{text.name}</th>
                  <th className="px-2 py-2">{text.ratio}</th>
                  <th className="px-2 py-2">{text.quarter}</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-subtle">
                {holdings.map((holding, index) => (
                  <tr key={`${holding.code}-${index}`}>
                    <td className="px-2 py-2 font-mono text-secondary-text">{holding.code || '—'}</td>
                    <td className="px-2 py-2 font-medium text-foreground">{holding.name || '—'}</td>
                    <td className="px-2 py-2 text-secondary-text">
                      {holding.ratio === null || holding.ratio === undefined ? '—' : `${holding.ratio}%`}
                    </td>
                    <td className="px-2 py-2 text-secondary-text">{holding.quarter || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-sm text-secondary-text">{text.noFundData}</p>
        )}
      </Card>

      {fund.analysisSummary || fund.operationAdvice ? (
        <Card variant="bordered" padding="md" className="home-panel-card text-left">
          <div className="mb-3 flex items-center gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <FileText className="h-4 w-4" aria-hidden="true" />
            </span>
            <h3 className="text-base font-semibold text-foreground">{text.analysisAdvice}</h3>
          </div>
          <div className="space-y-3 text-sm leading-6 text-foreground">
            {fund.analysisSummary ? <p>{fund.analysisSummary}</p> : null}
            {fund.operationAdvice ? <p>{fund.operationAdvice}</p> : null}
          </div>
        </Card>
      ) : null}

      {fund.riskWarning ? (
        <Card variant="bordered" padding="md" className="home-panel-card text-left">
          <div className="mb-3 flex items-center gap-2">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-danger/10 text-danger">
              <ShieldAlert className="h-4 w-4" aria-hidden="true" />
            </span>
            <h3 className="text-base font-semibold text-foreground">{text.riskWarning}</h3>
          </div>
          <p className="text-sm leading-6 text-foreground">{fund.riskWarning}</p>
        </Card>
      ) : null}
    </div>
  );
};
