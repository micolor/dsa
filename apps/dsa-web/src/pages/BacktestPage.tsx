import type React from 'react';
import { useState, useEffect, useCallback, useRef } from 'react';
import { Check, Download, Info, Minus, X } from 'lucide-react';
import { backtestApi } from '../api/backtest';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { ApiErrorAlert, Card, Badge, DatePicker, EmptyState, Loading, Pagination, Tooltip } from '../components/common';
import { StockAutocomplete } from '../components/StockAutocomplete';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText, type UiLanguage } from '../i18n/uiText';
import {
  BACKTEST_DIRECTION_EXPECTED_LABELS,
  BACKTEST_METRIC_HINTS,
  BACKTEST_MOVEMENT_LABELS,
  BACKTEST_OUTCOME_LABELS,
  BACKTEST_PHASE_FILTER_OPTIONS,
  BACKTEST_PHASE_LABELS,
  BACKTEST_STATUS_LABELS,
  BACKTEST_TEXT,
} from '../locales/featureText';
import type {
  BacktestResultItem,
  BacktestRunResponse,
  PerformanceMetrics,
  BacktestPhaseFilter,
} from '../types/backtest';
import { buildDecisionActionLabelMap, getDecisionActionLabel, getDecisionActionTone } from '../utils/decisionAction';
import { SELECT_CHEVRON_CLASS, SELECT_INPUT_CLASS } from '../utils/formClasses';
import { getMarketPhaseSummaryLabel } from '../utils/marketPhase';

const BACKTEST_COMPACT_INPUT_CLASS = `${SELECT_INPUT_CLASS} !h-10 text-center`;
type BacktestText = (typeof BACKTEST_TEXT)[UiLanguage];

// ============ Helpers ============

function pct(value?: number | null): string {
  if (value == null) return '--';
  return `${value.toFixed(1)}%`;
}

function phaseLabel(row: BacktestResultItem, language: UiLanguage): string {
  const label = getMarketPhaseSummaryLabel(row.marketPhaseSummary, language);
  if (label) {
    return label
      .replace('市场阶段: ', '')
      .replace('市场阶段：', '')
      .replace('Market phase: ', '');
  }
  return (row.marketPhase ? BACKTEST_PHASE_LABELS[language][row.marketPhase] : undefined) || row.marketPhase || '--';
}

function normalizeBacktestCode(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return undefined;
  return trimmed.toUpperCase();
}

function parseMinAgeDays(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }

  const parsed = parseInt(trimmed, 10);
  if (Number.isNaN(parsed) || parsed < 0) {
    return undefined;
  }

  return parsed;
}

function parseEvalWindowDays(value: string): number | undefined {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }

  const parsed = parseInt(trimmed, 10);
  if (Number.isNaN(parsed) || parsed < 1) {
    return undefined;
  }

  return parsed;
}

function labelFromMap(value: string | null | undefined, labels: Record<string, string>): string {
  if (!value) return '--';
  return labels[value] ?? value;
}

function outcomeBadge(outcome: string | undefined, language: UiLanguage) {
  const labels = BACKTEST_OUTCOME_LABELS[language];
  if (!outcome) return <Badge variant="default">--</Badge>;
  switch (outcome) {
    case 'win':
      return <Badge variant="success" glow>{labels.win}</Badge>;
    case 'loss':
      return <Badge variant="danger" glow>{labels.loss}</Badge>;
    case 'neutral':
      return <Badge variant="warning">{labels.neutral}</Badge>;
    default:
      return <Badge variant="default">{outcome}</Badge>;
  }
}

function statusBadge(status: string, language: UiLanguage) {
  const labels = BACKTEST_STATUS_LABELS[language];
  switch (status) {
    case 'completed':
      return <Badge variant="success">{labels.completed}</Badge>;
    case 'insufficient':
    case 'insufficient_data':
      return <Badge variant="warning">{labels.insufficient}</Badge>;
    case 'error':
      return <Badge variant="danger">{labels.error}</Badge>;
    default:
      return <Badge variant="default">{status}</Badge>;
  }
}

function actualMovementBadge(movement: string | null | undefined, language: UiLanguage) {
  const labels = BACKTEST_MOVEMENT_LABELS[language];
  switch (movement) {
    case 'up':
      return <Badge variant="danger">{labels.up}</Badge>;
    case 'down':
      return <Badge variant="success">{labels.down}</Badge>;
    case 'flat':
      return <Badge variant="warning">{labels.flat}</Badge>;
    default:
      return <Badge variant="default">--</Badge>;
  }
}

function boolIcon(value: boolean | null | undefined, text: BacktestText) {
  if (value === true) {
    return (
      <Badge variant="success" aria-label={text.yes}>
        <Check className="h-3.5 w-3.5" />
      </Badge>
    );
  }

  if (value === false) {
    return (
      <Badge variant="danger" aria-label={text.no}>
        <X className="h-3.5 w-3.5" />
      </Badge>
    );
  }

  return (
    <Badge variant="default" aria-label={text.unknown}>
      <Minus className="h-3.5 w-3.5" />
    </Badge>
  );
}

// ============ Metric Row ============

const MetricRow: React.FC<{ label: string; value: string; accent?: boolean; hint?: string }> = ({ label, value, accent, hint }) => (
  <div className="flex items-center justify-between gap-2 py-2">
    <span className="flex items-center gap-1 text-xs text-muted-text">
      <span>{label}</span>
      {hint ? (
        <Tooltip content={hint} focusable>
          <Info className="h-3 w-3 cursor-help text-muted-text" aria-hidden="true" />
        </Tooltip>
      ) : null}
    </span>
    <span className={accent ? 'text-sm font-semibold tabular-nums text-foreground' : 'text-sm tabular-nums text-secondary-text'}>
      {value}
    </span>
  </div>
);

function phaseBreakdownText(metrics: PerformanceMetrics, language: UiLanguage): string | null {
  const breakdown = metrics.diagnostics?.phaseBreakdown;
  if (!breakdown || typeof breakdown !== 'object') return null;
  const item = breakdown as Record<string, unknown>;
  const phaseLabels = BACKTEST_PHASE_LABELS[language];
  const parts = [
    [phaseLabels.premarket, item.premarket],
    [phaseLabels.intraday, item.intraday],
    [phaseLabels.postmarket, item.postmarket],
    [phaseLabels.unknown, item.unknown],
  ]
    .map(([label, value]) => `${label} ${Number(value || 0)}`)
    .join(' / ');
  return parts;
}

// ============ Performance Card ============

const PerformanceCard: React.FC<{ metrics: PerformanceMetrics; title: string; language: UiLanguage }> = ({ metrics, title, language }) => {
  const text = BACKTEST_TEXT[language];
  const hints = BACKTEST_METRIC_HINTS[language];
  const phaseText = phaseBreakdownText(metrics, language);
  return (
    <Card padding="md" title={title} className="animate-fade-in">
      <MetricRow label={text.directionAccuracy} value={pct(metrics.directionAccuracyPct)} accent hint={hints.directionAccuracy} />
      <MetricRow label={text.winRate} value={pct(metrics.winRatePct)} accent hint={hints.winRate} />
      <MetricRow label={text.avgSimulatedReturn} value={pct(metrics.avgSimulatedReturnPct)} hint={hints.avgSimulatedReturn} />
      <MetricRow label={text.avgStockReturn} value={pct(metrics.avgStockReturnPct)} hint={hints.avgStockReturn} />
      <MetricRow label={text.stopLossTriggerRate} value={pct(metrics.stopLossTriggerRate)} hint={hints.stopLossTriggerRate} />
      <MetricRow label={text.takeProfitTriggerRate} value={pct(metrics.takeProfitTriggerRate)} hint={hints.takeProfitTriggerRate} />
      <MetricRow label={text.avgDaysToFirstHit} value={metrics.avgDaysToFirstHit != null ? metrics.avgDaysToFirstHit.toFixed(1) : '--'} hint={hints.avgDaysToFirstHit} />
      <div className="mt-1 flex items-center justify-between border-t border-border/60 pt-2">
        <span className="flex items-center gap-1 text-xs text-muted-text">
          <span>{text.evaluationCount}</span>
          <Tooltip content={hints.evaluationCount} focusable>
            <Info className="h-3 w-3 cursor-help text-muted-text" aria-hidden="true" />
          </Tooltip>
        </span>
        <span className="text-xs text-secondary-text font-mono">
          {Number(metrics.completedCount)} / {Number(metrics.totalEvaluations)}
        </span>
      </div>
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1 text-xs text-muted-text">
          <span>{text.outcomeSummary}</span>
          <Tooltip content={hints.outcomeSummary} focusable>
            <Info className="h-3 w-3 cursor-help text-muted-text" aria-hidden="true" />
          </Tooltip>
        </span>
        <span className="text-xs font-mono">
          <span className="text-success">{metrics.winCount}</span>
          {' / '}
          <span className="text-danger">{metrics.lossCount}</span>
          {' / '}
          <span className="text-warning">{metrics.neutralCount}</span>
        </span>
      </div>
      {phaseText ? (
        <div className="mt-3 border-t border-white/10 pt-2 text-xs text-muted-text">
          {formatUiText(text.phaseDistribution, { text: phaseText })}
        </div>
      ) : null}
    </Card>
  );
};

// ============ Run Summary ============

const RunSummary: React.FC<{ data: BacktestRunResponse; language: UiLanguage }> = ({ data, language }) => {
  const text = BACKTEST_TEXT[language];
  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-secondary-text">
      <span>{text.processed} <span className="font-semibold text-foreground">{data.processed}</span></span>
      <span>{text.saved} <span className="font-semibold text-success">{data.saved}</span></span>
      <span>{text.completed} <span className="font-semibold text-success">{data.completed}</span></span>
      <span>{text.insufficient} <span className="font-semibold text-warning">{data.insufficient}</span></span>
      {data.errors > 0 && (
        <span>{text.errors} <span className="font-semibold text-danger">{data.errors}</span></span>
      )}
      {data.message && (
        <span className="text-muted-text">{data.message}</span>
      )}
    </div>
  );
};

// ============ Export CSV ============

function exportResultsCsv(
  results: BacktestResultItem[],
  actionLabels: Record<string, string>,
  text: BacktestText,
  language: UiLanguage,
): void {
  const escape = (value: string | number | null | undefined) => `"${String(value ?? '').replace(/"/g, '""')}"`;
  const header = [text.stock, text.analysisDate, text.phase, text.aiPrediction, text.actualPerformance, text.result, text.status, text.stopLossTakeProfit, text.simulatedTrade]
    .map(escape)
    .join(',');
  const rows = results.map((row) => {
    const actionLabel = getDecisionActionLabel(row.action, row.actionLabel, null, null, actionLabels);
    const prediction = [actionLabel, row.trendPrediction, row.operationAdvice].filter(Boolean).join(' / ');
    return [
      `${row.code}${row.stockName ? ` ${row.stockName}` : ''}`,
      row.analysisDate ?? '',
      phaseLabel(row, language),
      prediction,
      row.actualReturnPct != null ? `${row.actualReturnPct}%` : '',
      row.outcome ?? '',
      row.evalStatus ?? '',
      `${row.stopLoss ?? ''}/${row.takeProfit ?? ''}`,
      [
        row.simulatedEntryPrice != null ? `${row.simulatedEntryPrice}→${row.simulatedExitPrice ?? ''}` : '',
        row.simulatedReturnPct != null ? `${row.simulatedReturnPct}%` : '',
        row.simulatedExitReason ?? '',
      ].filter(Boolean).join(' '),
    ].map(escape).join(',');
  });
  const csv = [header, ...rows].join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `backtest-results-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

// ============ Main Page ============

const BacktestPage: React.FC = () => {
  const { language, t } = useUiLanguage();
  const text = BACKTEST_TEXT[language];
  const phaseFilterOptions = BACKTEST_PHASE_FILTER_OPTIONS[language];
  const actionLabels = buildDecisionActionLabelMap(t);

  // Set page title
  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  // Input state
  const [codeFilter, setCodeFilter] = useState('');
  const [analysisDateFrom, setAnalysisDateFrom] = useState('');
  const [analysisDateTo, setAnalysisDateTo] = useState('');
  const [phaseFilter, setPhaseFilter] = useState<BacktestPhaseFilter>('all');
  const [evalDays, setEvalDays] = useState('');
  const [minAgeDays, setMinAgeDays] = useState('');
  const [forceRerun, setForceRerun] = useState(false);
  const [isRunning, setIsRunning] = useState(false);
  const [runResult, setRunResult] = useState<BacktestRunResponse | null>(null);
  const [runError, setRunError] = useState<ParsedApiError | null>(null);
  const [pageError, setPageError] = useState<ParsedApiError | null>(null);

  // Results state
  const [results, setResults] = useState<BacktestResultItem[]>([]);
  const [totalResults, setTotalResults] = useState(0);
  const [currentPage, setCurrentPage] = useState(1);
  const [isLoadingResults, setIsLoadingResults] = useState(false);
  const pageSize = 20;
  // 并发请求守卫：mountedRef 防止卸载后 setState；
  // resultsRequestRef / perfRequestRef 只让"最新一次请求"生效，丢弃过期慢响应。
  const mountedRef = useRef(true);
  const resultsRequestRef = useRef(0);
  const perfRequestRef = useRef(0);

  // Performance state
  const [overallPerf, setOverallPerf] = useState<PerformanceMetrics | null>(null);
  const [stockPerf, setStockPerf] = useState<PerformanceMetrics | null>(null);
  const [isLoadingPerf, setIsLoadingPerf] = useState(false);
  // appliedWindowDays: the eval window the currently-loaded rows/metrics were queried with.
  // Column headers / mode description must describe the LOADED cohort, not the raw input
  // (which changes on every keystroke) — otherwise the UI would render columns for a window
  // that the rows in view were never filtered by. Updated whenever a query actually runs.
  const [appliedWindowDays, setAppliedWindowDays] = useState<number | null>(null);
  const isNextDayValidation = appliedWindowDays === 1;
  const showNextDayActualColumns = isNextDayValidation;

  // Mount guard
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Fetch results
  const fetchResults = useCallback(async (
    page = 1,
    code?: string,
    windowDays?: number,
    startDate?: string,
    endDate?: string,
    phase?: BacktestPhaseFilter,
  ) => {
    const requestId = ++resultsRequestRef.current;
    setIsLoadingResults(true);
    try {
      const response = await backtestApi.getResults({
        code: code || undefined,
        evalWindowDays: windowDays,
        analysisDateFrom: startDate || undefined,
        analysisDateTo: endDate || undefined,
        analysisPhase: phase && phase !== 'all' ? phase : undefined,
        page,
        limit: pageSize,
      });
      if (!mountedRef.current || requestId !== resultsRequestRef.current) return;
      setResults(response.items);
      setTotalResults(response.total);
      setCurrentPage(response.page);
      setPageError(null);
    } catch (err) {
      if (!mountedRef.current || requestId !== resultsRequestRef.current) return;
      console.error('Failed to fetch backtest results:', err);
      setPageError(getParsedApiError(err));
    } finally {
      // 只让最新一次请求关闭 loading，避免过期请求提前清除加载态
      if (mountedRef.current && requestId === resultsRequestRef.current) {
        setIsLoadingResults(false);
      }
    }
  }, []);

  // Fetch performance
  const fetchPerformance = useCallback(async (
    code?: string,
    windowDays?: number,
    startDate?: string,
    endDate?: string,
    phase?: BacktestPhaseFilter,
  ) => {
    const requestId = ++perfRequestRef.current;
    setIsLoadingPerf(true);
    try {
      const overall = await backtestApi.getOverallPerformance({
        evalWindowDays: windowDays,
        analysisDateFrom: startDate || undefined,
        analysisDateTo: endDate || undefined,
        analysisPhase: phase && phase !== 'all' ? phase : undefined,
      });
      if (!mountedRef.current || requestId !== perfRequestRef.current) return;
      setOverallPerf(overall);

      if (code) {
        const stock = await backtestApi.getStockPerformance(code, {
          evalWindowDays: windowDays,
          analysisDateFrom: startDate || undefined,
          analysisDateTo: endDate || undefined,
          analysisPhase: phase && phase !== 'all' ? phase : undefined,
        });
        if (!mountedRef.current || requestId !== perfRequestRef.current) return;
        setStockPerf(stock);
      } else {
        setStockPerf(null);
      }
      setPageError(null);
    } catch (err) {
      if (!mountedRef.current || requestId !== perfRequestRef.current) return;
      console.error('Failed to fetch performance:', err);
      setPageError(getParsedApiError(err));
    } finally {
      if (mountedRef.current && requestId === perfRequestRef.current) {
        setIsLoadingPerf(false);
      }
    }
  }, []);

  // Initial load — fetch performance first, then filter results by its window
  useEffect(() => {
    const init = async () => {
      // Get latest performance (unfiltered returns most recent summary)
      const perfId = ++perfRequestRef.current;
      const overall = await backtestApi.getOverallPerformance();
      if (!mountedRef.current || perfId !== perfRequestRef.current) return;
      setOverallPerf(overall);
      // Use the summary's eval_window_days to filter results consistently
      const windowDays = overall?.evalWindowDays;
      if (windowDays && !evalDays) {
        setEvalDays(String(windowDays));
      }
      setAppliedWindowDays(windowDays ?? null);
      fetchResults(1, undefined, windowDays, undefined, undefined, 'all');
    };
    init();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Run backtest
  const handleRun = async () => {
    setIsRunning(true);
    setRunResult(null);
    setRunError(null);
    try {
      const code = normalizeBacktestCode(codeFilter);
      const requestedEvalWindowDays = parseEvalWindowDays(evalDays);
      const dateFrom = analysisDateFrom || undefined;
      const dateTo = analysisDateTo || undefined;
      const response = await backtestApi.run({
        code,
        force: forceRerun || undefined,
        minAgeDays: forceRerun ? 0 : parseMinAgeDays(minAgeDays),
        evalWindowDays: requestedEvalWindowDays,
        analysisDateFrom: dateFrom,
        analysisDateTo: dateTo,
      });
      setRunResult(response);
      const effectiveEvalWindowDays =
        response.appliedEvalWindowDays
        ?? requestedEvalWindowDays
        ?? parseEvalWindowDays(evalDays)
        ?? overallPerf?.evalWindowDays;
      if (effectiveEvalWindowDays != null) {
        setEvalDays(String(effectiveEvalWindowDays));
        setAppliedWindowDays(effectiveEvalWindowDays);
      }
      // Refresh data with same eval_window_days
      fetchResults(1, code, effectiveEvalWindowDays, dateFrom, dateTo, phaseFilter);
      fetchPerformance(code, effectiveEvalWindowDays, dateFrom, dateTo, phaseFilter);
    } catch (err) {
      setRunError(getParsedApiError(err));
    } finally {
      setIsRunning(false);
    }
  };

  // Filter by code. The window used reflects what the current cohort was built with, so an
  // input change is only applied here (on an explicit filter), not on every keystroke.
  const handleFilter = () => {
    const code = normalizeBacktestCode(codeFilter);
    const windowDays = parseEvalWindowDays(evalDays) ?? appliedWindowDays ?? overallPerf?.evalWindowDays ?? undefined;
    setCurrentPage(1);
    setAppliedWindowDays(windowDays ?? null);
    fetchResults(1, code, windowDays, analysisDateFrom, analysisDateTo, phaseFilter);
    fetchPerformance(code, windowDays, analysisDateFrom, analysisDateTo, phaseFilter);
  };

  const handleShowNextDay = () => {
    const code = normalizeBacktestCode(codeFilter);
    setEvalDays('1');
    setAppliedWindowDays(1);
    setCurrentPage(1);
    fetchResults(1, code, 1, analysisDateFrom, analysisDateTo, phaseFilter);
    fetchPerformance(code, 1, analysisDateFrom, analysisDateTo, phaseFilter);
  };

  // 评估窗口在失焦时套用：让输入、1 日验证 chip、列头与已加载 cohort 保持一致。
  // 若当前值已是已加载窗口（例如刚输入 "1" 触发过 1 日验证），则不重复刷新。
  const handleEvalWindowBlur = () => {
    const windowDays = parseEvalWindowDays(evalDays);
    if (windowDays == null || windowDays === appliedWindowDays) return;
    const code = normalizeBacktestCode(codeFilter);
    setAppliedWindowDays(windowDays);
    setCurrentPage(1);
    fetchResults(1, code, windowDays, analysisDateFrom, analysisDateTo, phaseFilter);
    fetchPerformance(code, windowDays, analysisDateFrom, analysisDateTo, phaseFilter);
  };

  // Pagination
  const totalPages = Math.ceil(totalResults / pageSize);
  const handlePageChange = (page: number) => {
    // Pagination must stay within the cohort the current rows were loaded with, and not
    // pick up a half-typed input value (which would mix windows across pages).
    fetchResults(page, normalizeBacktestCode(codeFilter), appliedWindowDays ?? undefined, analysisDateFrom, analysisDateTo, phaseFilter);
  };

  return (
    <div className="flex h-full w-full flex-col rounded-[1.5rem] bg-transparent px-4 pb-6 pt-4 md:px-6">
      {/* Header */}
      <header className="flex-shrink-0 border-b border-border/40 px-3 py-3 sm:px-4">
        <div className="flex w-full flex-col gap-3">
          {/* 顶部用途说明 */}
          <p className="text-xs text-secondary-text">{text.purpose}</p>

          {/* 主操作行：股票搜索 + 运行回测（对照首页：搜索框 flex-1 + 主按钮） */}
          <div className="flex min-w-0 flex-wrap items-center gap-2.5">
            <div className="relative min-w-0 flex-[2_1_260px] max-w-2xl">
              <StockAutocomplete
                value={codeFilter}
                onChange={(v) => setCodeFilter(v.toUpperCase())}
                onSubmit={() => handleFilter()}
                placeholder={text.codePlaceholder}
                ariaLabel={text.codePlaceholder}
                disabled={isRunning}
              />
              {codeFilter ? (
                <button
                  type="button"
                  onClick={() => setCodeFilter('')}
                  aria-label={text.clearCodeFilter}
                  className="absolute inset-y-0 right-2 my-auto flex h-7 w-7 items-center justify-center rounded-lg text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
                >
                  <X className="h-4 w-4" />
                </button>
              ) : null}
            </div>
            <button
              type="button"
              onClick={handleRun}
              disabled={isRunning}
              className="btn-primary flex h-10 flex-1 items-center justify-center gap-1.5 whitespace-nowrap md:flex-none"
            >
              {isRunning ? (
                <>
                  <svg className="w-3.5 h-3.5 animate-spin" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  {text.running}
                </>
              ) : (
                text.runBacktest
              )}
            </button>
          </div>

          {/* 参数行：评估窗口/1日验证 · 日期 · 阶段 · 最小天龄/强制重跑 · 筛选结果 */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <Tooltip content={text.evalWindowHint} focusable>
                <span className="text-xs text-muted-text">{text.evalWindow}</span>
              </Tooltip>
              <input
                type="number"
                min={1}
                max={120}
                value={evalDays}
                onChange={(e) => setEvalDays(e.target.value)}
                onBlur={handleEvalWindowBlur}
                placeholder="10"
                disabled={isRunning}
                className={`${BACKTEST_COMPACT_INPUT_CLASS} w-24 text-center tabular-nums`}
              />
              <span className="text-xs text-muted-text">{text.dayUnit}</span>
            </div>
            <button
              type="button"
              onClick={handleShowNextDay}
              aria-pressed={evalDays === '1'}
              disabled={isLoadingResults || isLoadingPerf || isRunning}
              className={`inline-flex !h-10 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                evalDays === '1' ? 'bg-primary/15 text-primary shadow-inner' : 'text-secondary-text hover:bg-hover hover:text-foreground'
              }`}
            >
              {text.oneDayValidation}
            </button>
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <span className="text-xs text-muted-text">{text.startDate}</span>
              <DatePicker
                className="w-36 !h-10"
                value={analysisDateFrom}
                onChange={setAnalysisDateFrom}
                ariaLabel={text.startDateAria}
                disabled={isRunning}
              />
            </div>
            <span className="text-xs text-muted-text">~</span>
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <span className="text-xs text-muted-text">{text.endDate}</span>
              <DatePicker
                className="w-36 !h-10"
                value={analysisDateTo}
                onChange={setAnalysisDateTo}
                ariaLabel={text.endDateAria}
                disabled={isRunning}
              />
            </div>
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <span className="text-xs text-muted-text">{text.phase}</span>
              <select
                value={phaseFilter}
                onChange={(e) => setPhaseFilter(e.target.value as BacktestPhaseFilter)}
                disabled={isRunning}
                className={`${SELECT_CHEVRON_CLASS} !h-10 w-28`}
              >
                {phaseFilterOptions.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <Tooltip content={text.minAgeDaysHint} focusable>
                <span className="text-xs text-muted-text">{text.minAgeDays}</span>
              </Tooltip>
              <input
                type="number"
                min={0}
                value={minAgeDays}
                onChange={(e) => setMinAgeDays(e.target.value)}
                placeholder="14"
                disabled={isRunning}
                className={`${BACKTEST_COMPACT_INPUT_CLASS} w-20 text-center tabular-nums`}
              />
              <span className="text-xs text-muted-text">{text.dayUnit}</span>
            </div>
            <button
              type="button"
              onClick={() => setForceRerun(!forceRerun)}
              aria-pressed={forceRerun}
              disabled={isRunning}
              className={`inline-flex !h-10 items-center gap-1.5 rounded-lg px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                forceRerun ? 'bg-primary/15 text-primary shadow-inner' : 'text-secondary-text hover:bg-hover hover:text-foreground'
              }`}
            >
              {text.forceRerun}
            </button>
            <button
              type="button"
              onClick={handleFilter}
              disabled={isLoadingResults || isRunning}
              className="btn-secondary flex h-10 items-center gap-1.5 whitespace-nowrap"
            >
              {isLoadingResults ? (
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
              ) : null}
              {text.filter}
            </button>
          </div>

          {/* 说明「运行回测」与「筛选结果」的区别（常驻可见，避免仅悬停才发现） */}
          <p className="text-xs text-muted-text">{text.runVsFilter}</p>
        </div>
        {runResult && (
          <div className="mt-2 max-w-4xl">
            <RunSummary data={runResult} language={language} />
          </div>
        )}
        {runError && (
          <ApiErrorAlert error={runError} className="mt-2 max-w-4xl" />
        )}
        <p className="mt-2 text-xs text-muted-text">
          {isNextDayValidation
            ? text.oneDayModeDescription
            : text.windowModeDescription}
        </p>
      </header>

      {/* Main content */}
      <main className="flex min-h-0 flex-1 flex-col gap-3 overflow-hidden p-3 lg:flex-row">
        {/* Left sidebar - Performance */}
        <aside className="flex max-h-[38vh] flex-col gap-3 overflow-y-auto lg:max-h-none lg:w-72 lg:flex-shrink-0">
          {isLoadingPerf ? (
            <Loading className="py-8" />
          ) : overallPerf && overallPerf.completedCount > 0 ? (
            <PerformanceCard metrics={overallPerf} title={text.overallPerformance} language={language} />
          ) : (
            <EmptyState
              title={text.noMetricsTitle}
              description={text.noMetricsDescription}
              className="flex h-full min-h-[12rem] flex-col items-center justify-center border-dashed bg-card/45 shadow-none"
            />
          )}

          {stockPerf && (
            <PerformanceCard metrics={stockPerf} title={`${stockPerf.code || codeFilter}`} language={language} />
          )}
        </aside>

        {/* Results table */}
        <section className="min-h-0 flex-1 overflow-y-auto">
          {pageError ? (
            <ApiErrorAlert error={pageError} className="mb-3" />
          ) : null}
          {isLoadingResults ? (
            <Loading label={text.loadingResults} className="h-64" />
          ) : results.length === 0 ? (
            <EmptyState
              title={text.noResultsTitle}
              description={text.noResultsDescription}
              className="flex h-full min-h-[16rem] flex-col items-center justify-center border-dashed bg-card/45 shadow-none"
              icon={(
                <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
                </svg>
              )}
              action={<p className="mt-1 max-w-md text-xs leading-relaxed text-muted-text">{text.noResultsGuide}</p>}
            />
          ) : (
            <Card title={isNextDayValidation ? text.nextDayValidation : text.resultSet} padding="md" className="animate-fade-in">
              <div className="mb-3 flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="truncate text-xs text-secondary-text">
                    {codeFilter.trim() ? formatUiText(text.filteredStock, { code: codeFilter.trim() }) : text.allStocks}
                    {appliedWindowDays ? ` · ${formatUiText(text.dayWindow, { days: String(appliedWindowDays) })}` : ''}
                    {phaseFilter !== 'all' ? ` · ${phaseFilterOptions.find((item) => item.value === phaseFilter)?.label ?? phaseFilter}` : ''}
                    {analysisDateFrom ? ` · ${formatUiText(text.fromDate, { date: analysisDateFrom })}` : ''}
                    {analysisDateTo ? ` · ${formatUiText(text.toDate, { date: analysisDateTo })}` : ''}
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-3">
                  <button
                    type="button"
                    onClick={() => exportResultsCsv(results, actionLabels, text, language)}
                    disabled={results.length === 0 || isRunning}
                    className="btn-secondary inline-flex items-center gap-1.5 !px-3 !py-1.5 !text-xs"
                  >
                    <Download className="h-3.5 w-3.5" />
                    {text.exportCsv}
                  </button>
                  <span className="text-[11px] text-muted-text">{text.scrollHint}</span>
                </div>
              </div>

              <div className="overflow-hidden rounded-xl border border-border/60 bg-card/50">
                <div className="overflow-x-auto">
                <table className="w-full min-w-[900px] text-left text-sm">
                  <thead className="bg-surface-2/70 text-left text-xs uppercase tracking-[0.16em] text-secondary-text">
                    <tr>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.stock}</th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.analysisDate}</th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.phase}</th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.aiPrediction}</th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">
                        {showNextDayActualColumns ? text.actualPerformance : text.windowReturn}
                      </th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">
                        {showNextDayActualColumns ? text.accuracy : text.directionMatch}
                      </th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.result}</th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.status}</th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.stopLossTakeProfit}</th>
                      <th className="px-4 py-3 font-medium whitespace-nowrap">{text.simulatedTrade}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {results.map((row) => {
                      const actionLabel = getDecisionActionLabel(row.action, row.actionLabel, null, null, actionLabels);
                      const predictionParts = [actionLabel, row.trendPrediction, row.operationAdvice]
                        .filter((part): part is string => Boolean(part));

                      return (
                        <tr
                          key={row.analysisHistoryId}
                          className="transition-colors hover:bg-hover/60"
                        >
                          <td className="px-4 py-3 font-mono text-primary">
                            <div className="flex flex-col">
                              <span>{row.code}</span>
                              <span className="text-xs text-muted-text">{row.stockName || '--'}</span>
                            </div>
                          </td>
                          <td className="px-4 py-3 whitespace-nowrap text-secondary-text">{row.analysisDate || '--'}</td>
                          <td className="px-4 py-3 text-secondary-text">
                            <Tooltip content={phaseLabel(row, language)}>
                              <span className="block max-w-[200px] truncate whitespace-nowrap">{phaseLabel(row, language)}</span>
                            </Tooltip>
                          </td>
                          <td className="px-4 py-3 max-w-[220px] text-foreground">
                            {predictionParts.length ? (
                              <Tooltip
                                content={predictionParts.join(' / ')}
                                focusable
                              >
                                <div className="flex flex-col gap-1">
                                  {actionLabel ? (
                                    <span className="flex items-center">
                                      <Badge variant={getDecisionActionTone(row.action, row.actionLabel, null)}>{actionLabel}</Badge>
                                    </span>
                                  ) : null}
                                  {row.trendPrediction && row.trendPrediction !== actionLabel ? (
                                    <span className="block min-w-0 max-w-[220px] truncate text-xs text-secondary-text">{row.trendPrediction}</span>
                                  ) : null}
                                  {row.operationAdvice && row.operationAdvice !== actionLabel ? (
                                    <span className="block min-w-0 max-w-[220px] truncate text-xs text-secondary-text">{row.operationAdvice}</span>
                                  ) : null}
                                </div>
                              </Tooltip>
                            ) : (
                              '--'
                            )}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              {actualMovementBadge(row.actualMovement, language)}
                              <span className={
                                row.actualReturnPct != null
                                  ? row.actualReturnPct > 0 ? 'text-danger' : row.actualReturnPct < 0 ? 'text-success' : 'text-secondary-text'
                                  : 'text-muted-text'
                              }>
                                {pct(row.actualReturnPct)}
                              </span>
                            </div>
                          </td>
                          <td className="px-4 py-3">
                            <span className="flex items-center gap-2 whitespace-nowrap">
                              {boolIcon(row.directionCorrect, text)}
                              <span className="text-muted-text">
                                {row.directionExpected ? labelFromMap(row.directionExpected, BACKTEST_DIRECTION_EXPECTED_LABELS[language]) : ''}
                              </span>
                            </span>
                          </td>
                          <td className="px-4 py-3">{outcomeBadge(row.outcome, language)}</td>
                          <td className="px-4 py-3">{statusBadge(row.evalStatus, language)}</td>
                          <td className="px-4 py-3">
                            {row.stopLoss != null || row.takeProfit != null ? (
                              <div className="flex flex-col gap-0.5 text-secondary-text">
                                <span>
                                  {text.stopLoss} {row.stopLoss != null ? row.stopLoss : '--'}
                                  {row.hitStopLoss ? ' ✓' : ''}
                                </span>
                                <span>
                                  {text.takeProfit} {row.takeProfit != null ? row.takeProfit : '--'}
                                  {row.hitTakeProfit ? ' ✓' : ''}
                                </span>
                              </div>
                            ) : '--'}
                          </td>
                          <td className="px-4 py-3">
                            {row.simulatedEntryPrice != null ? (
                              <div className="flex flex-col gap-0.5">
                                <span className="text-secondary-text">
                                  {row.simulatedEntryPrice} → {row.simulatedExitPrice ?? '--'}
                                </span>
                                <span className={
                                  row.simulatedReturnPct != null
                                    ? row.simulatedReturnPct > 0 ? 'text-danger' : row.simulatedReturnPct < 0 ? 'text-success' : 'text-secondary-text'
                                    : 'text-muted-text'
                                }>
                                  {pct(row.simulatedReturnPct)}
                                  {row.simulatedExitReason ? ` · ${row.simulatedExitReason}` : ''}
                                </span>
                              </div>
                            ) : '--'}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                </div>
              </div>

              {/* Pagination */}
              <div className="mt-4">
                <Pagination
                  currentPage={currentPage}
                  totalPages={totalPages}
                  onPageChange={handlePageChange}
                  disabled={isRunning}
                />
              </div>

              <p className="text-xs text-muted-text text-center mt-2">
                {formatUiText(text.totalPage, { total: totalResults, page: currentPage, pages: Math.max(totalPages, 1) })}
              </p>
            </Card>
          )}
        </section>
      </main>
    </div>
  );
};

export default BacktestPage;
