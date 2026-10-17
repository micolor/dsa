import { useCallback, useEffect, useRef, useState } from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { fundNavReviewsApi } from '../../api/fundNavReviews';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import {
  ApiErrorAlert,
  Button,
  Collapsible,
  EmptyState,
  InlineAlert,
  Loading,
  Pagination,
  Select,
  StatCard,
} from '../common';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type {
  FundNavReviewHorizon,
  FundNavReviewItem,
  FundNavReviewRunResponse,
  FundNavReviewStatsResponse,
  FundNavReviewStatus,
} from '../../types/fundNavReviews';
import { cn } from '../../utils/cn';
import { formatPct, formatSignedPct } from '../../utils/portfolioFormat';
import { FundNavTrendChart } from './FundNavTrendChart';

const PAGE_SIZE = 10;
const HORIZONS: FundNavReviewHorizon[] = ['1m', '3m', '6m', '1y'];
const STATUSES: FundNavReviewStatus[] = ['pending', 'evaluated', 'unable'];
type Basis = 'restored' | 'unit';

function returnClass(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value) || value === 0) return 'text-secondary-text';
  return value > 0 ? 'text-success' : 'text-danger';
}

function formatLocalDate(value: Date): string {
  const year = value.getFullYear();
  const month = `${value.getMonth() + 1}`.padStart(2, '0');
  const day = `${value.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function trendWindow(): { startDate: string; endDate: string } {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 365);
  return { startDate: formatLocalDate(start), endDate: formatLocalDate(end) };
}

/**
 * 场外基金净值复盘面板。
 *
 * 数据来自独立的 `fund_nav_reviews` 台账：每次 `fund:<code>` 体检写入锚点，
 * 到期后按净值记录条数评估区间收益与回撤。这里只展示结果，不生成买卖建议，
 * 也不依赖 `decision_signals`。
 */
export function FundNavReviewPanel() {
  const { t } = useUiLanguage();
  const [open, setOpen] = useState(false);
  const [horizon, setHorizon] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const [basis, setBasis] = useState<Basis>('restored');
  const [selectedFundCode, setSelectedFundCode] = useState('');

  const [items, setItems] = useState<FundNavReviewItem[]>([]);
  const [total, setTotal] = useState(0);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<ParsedApiError | null>(null);

  const [stats, setStats] = useState<FundNavReviewStatsResponse | null>(null);
  const [statsLoading, setStatsLoading] = useState(false);
  const [statsError, setStatsError] = useState<ParsedApiError | null>(null);

  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<ParsedApiError | null>(null);
  const [runResult, setRunResult] = useState<FundNavReviewRunResponse | null>(null);

  const listRequestIdRef = useRef(0);
  const statsRequestIdRef = useRef(0);

  const loadList = useCallback(async () => {
    const requestId = listRequestIdRef.current + 1;
    listRequestIdRef.current = requestId;
    setListLoading(true);
    try {
      const response = await fundNavReviewsApi.list({
        horizon: (horizon || undefined) as FundNavReviewHorizon | undefined,
        evalStatus: (status || undefined) as FundNavReviewStatus | undefined,
        page,
        pageSize: PAGE_SIZE,
      });
      if (listRequestIdRef.current !== requestId) return;
      setItems(response.items);
      setTotal(response.total);
      setListError(null);
    } catch (err) {
      if (listRequestIdRef.current !== requestId) return;
      setItems([]);
      setTotal(0);
      setListError(getParsedApiError(err));
    } finally {
      if (listRequestIdRef.current === requestId) {
        setListLoading(false);
      }
    }
  }, [horizon, page, status]);

  const loadStats = useCallback(async () => {
    const requestId = statsRequestIdRef.current + 1;
    statsRequestIdRef.current = requestId;
    setStatsLoading(true);
    try {
      const response = await fundNavReviewsApi.getStats(horizon ? [horizon] : undefined);
      if (statsRequestIdRef.current !== requestId) return;
      setStats(response);
      setStatsError(null);
    } catch (err) {
      if (statsRequestIdRef.current !== requestId) return;
      setStats(null);
      setStatsError(getParsedApiError(err));
    } finally {
      if (statsRequestIdRef.current === requestId) {
        setStatsLoading(false);
      }
    }
  }, [horizon]);

  useEffect(() => {
    if (!open) return;
    void loadList();
  }, [open, loadList]);

  useEffect(() => {
    if (!open) return;
    void loadStats();
  }, [open, loadStats]);

  const handleRefresh = useCallback(() => {
    void loadList();
    void loadStats();
  }, [loadList, loadStats]);

  const handleRun = useCallback(async () => {
    setRunning(true);
    setRunError(null);
    setRunResult(null);
    try {
      const response = await fundNavReviewsApi.run({
        horizons: horizon ? [horizon as FundNavReviewHorizon] : undefined,
      });
      setRunResult(response);
      await Promise.all([loadList(), loadStats()]);
    } catch (err) {
      setRunError(getParsedApiError(err));
    } finally {
      setRunning(false);
    }
  }, [horizon, loadList, loadStats]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const statusLabel = (value: string) => {
    if (value === 'pending' || value === 'evaluated' || value === 'unable') {
      return t(`fundReviews.status.${value}`);
    }
    return value || '--';
  };
  const basisLabel = (value: string | null | undefined) => {
    if (value === 'dividend_reinvested' || value === 'acc_nav' || value === 'unit_nav') {
      return t(`fundReviews.basis.${value === 'dividend_reinvested' ? 'dividendReinvested' : value === 'acc_nav' ? 'accNav' : 'unitNav'}`);
    }
    return '--';
  };
  const statsReturn = basis === 'restored' ? stats?.avgRestoredReturnPct : stats?.avgNavReturnPct;
  const statsDrawdown = basis === 'restored' ? stats?.avgRestoredMaxDrawdownPct : stats?.avgMaxDrawdownPct;
  const statsPositiveRate = basis === 'restored' ? stats?.restoredPositiveRatePct : stats?.positiveRatePct;
  const statsEvaluated = basis === 'restored' ? stats?.restoredEvaluated ?? 0 : stats?.evaluated ?? 0;
  const itemReturn = (item: FundNavReviewItem) =>
    basis === 'restored' ? item.restoredReturnPct : item.navReturnPct;
  const itemDrawdown = (item: FundNavReviewItem) =>
    basis === 'restored' ? item.restoredMaxDrawdownPct : item.maxDrawdownPct;
  const trend = trendWindow();

  return (
    <Collapsible
      title={t('fundReviews.title')}
      icon={<Activity className="h-4 w-4" />}
      open={open}
      onOpenChange={setOpen}
      scrollable
    >
      <div className="space-y-4">
        <p className="text-xs text-secondary-text">{t('fundReviews.description')}</p>

        <div className="flex flex-wrap items-end gap-2">
          <div className="w-32">
            <Select
              value={horizon}
              onChange={(value) => {
                setHorizon(value);
                setPage(1);
              }}
              ariaLabel={t('fundReviews.filter.horizon')}
              options={[
                { value: '', label: t('fundReviews.filter.allHorizons') },
                ...HORIZONS.map((item) => ({ value: item, label: item })),
              ]}
            />
          </div>
          <div className="w-32">
            <Select
              value={status}
              onChange={(value) => {
                setStatus(value);
                setPage(1);
              }}
              ariaLabel={t('fundReviews.filter.status')}
              options={[
                { value: '', label: t('fundReviews.filter.allStatuses') },
                ...STATUSES.map((item) => ({ value: item, label: t(`fundReviews.status.${item}`) })),
              ]}
            />
          </div>
          <div className="flex items-center gap-1" role="group" aria-label={t('fundReviews.basis.label')}>
            <Button
              type="button"
              variant={basis === 'restored' ? 'secondary' : 'outline'}
              size="sm"
              aria-pressed={basis === 'restored'}
              onClick={() => setBasis('restored')}
            >
              {t('fundReviews.basis.restored')}
            </Button>
            <Button
              type="button"
              variant={basis === 'unit' ? 'secondary' : 'outline'}
              size="sm"
              aria-pressed={basis === 'unit'}
              onClick={() => setBasis('unit')}
            >
              {t('fundReviews.basis.unit')}
            </Button>
          </div>
          <Button type="button" variant="secondary" size="sm" onClick={handleRefresh} disabled={listLoading || statsLoading || running}>
            <RefreshCw className={cn('h-4 w-4', (listLoading || statsLoading) && 'animate-spin')} />
            <span>{t('fundReviews.refresh')}</span>
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={() => void handleRun()} disabled={running}>
            <RefreshCw className={cn('h-4 w-4', running && 'animate-spin')} />
            <span>{running ? t('fundReviews.running') : t('fundReviews.run')}</span>
          </Button>
        </div>

        <p className="text-xs text-secondary-text">{t('fundReviews.runHint')}</p>
        <p className="text-xs text-secondary-text">{t('fundReviews.basis.note')}</p>

        {runError ? <ApiErrorAlert error={runError} onDismiss={() => setRunError(null)} /> : null}
        {runResult ? (
          <InlineAlert
            variant="success"
            title={t('fundReviews.runResultTitle')}
            message={t('fundReviews.runResult', {
              evaluated: runResult.evaluated,
              pending: runResult.pending,
              unable: runResult.unable,
              skipped: runResult.skipped,
            })}
          />
        ) : null}

        {statsError ? (
          <ApiErrorAlert error={statsError} actionLabel={t('common.retry')} onAction={() => void loadStats()} />
        ) : null}

        {statsLoading && !stats ? (
          <Loading className="h-24" label={t('fundReviews.loading')} />
        ) : stats ? (
          <>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <StatCard
                label={t('fundReviews.stats.evaluated')}
                value={statsEvaluated}
                hint={t('fundReviews.stats.pendingHint', { count: stats.pending })}
              />
              <StatCard
                label={t('fundReviews.stats.positiveRate')}
                value={formatPct(statsPositiveRate)}
              />
              <StatCard
                label={t('fundReviews.stats.avgReturn')}
                value={formatSignedPct(statsReturn)}
              />
              <StatCard
                label={t('fundReviews.stats.avgDrawdown')}
                value={formatSignedPct(statsDrawdown)}
              />
            </div>

            {stats.breakdowns.month.length > 0 ? (
              <div>
                <h4 className="mb-2 text-sm font-semibold text-foreground">{t('fundReviews.month.title')}</h4>
                <div className="overflow-x-auto">
                  <table className="min-w-[640px] w-full text-sm" aria-label={t('fundReviews.month.title')}>
                    <thead className="border-b border-border/60 text-xs text-muted-text">
                      <tr>
                        <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.month.month')}</th>
                        <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.month.samples')}</th>
                        <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.month.anchors')}</th>
                        <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.month.positiveRate')}</th>
                        <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.month.avgReturn')}</th>
                        <th className="py-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.month.avgDrawdown')}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {stats.breakdowns.month.map((bucket) => (
                        <tr key={bucket.value} className="border-b border-border/40 last:border-0">
                          <td className="py-2 pr-2 whitespace-nowrap text-foreground">{bucket.value}</td>
                          <td className="py-2 pr-2 text-right tabular-nums text-secondary-text">{bucket.total}</td>
                          <td className="py-2 pr-2 text-right tabular-nums text-secondary-text">{bucket.anchorCount}</td>
                          <td className="py-2 pr-2 text-right tabular-nums text-secondary-text">
                            {formatPct(basis === 'restored' ? bucket.restoredPositiveRatePct : bucket.positiveRatePct)}
                          </td>
                          <td className={cn('py-2 pr-2 text-right tabular-nums', returnClass(basis === 'restored' ? bucket.avgRestoredReturnPct : bucket.avgNavReturnPct))}>
                            {formatSignedPct(basis === 'restored' ? bucket.avgRestoredReturnPct : bucket.avgNavReturnPct)}
                          </td>
                          <td className={cn('py-2 text-right tabular-nums', returnClass(basis === 'restored' ? bucket.avgRestoredMaxDrawdownPct : bucket.avgMaxDrawdownPct))}>
                            {formatSignedPct(basis === 'restored' ? bucket.avgRestoredMaxDrawdownPct : bucket.avgMaxDrawdownPct)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            ) : null}
          </>
        ) : null}

        {selectedFundCode ? (
          <FundNavTrendChart
            fundCode={selectedFundCode}
            startDate={trend.startDate}
            endDate={trend.endDate}
          />
        ) : null}

        <div>
          <div className="mb-2 flex items-center justify-between gap-2">
            <h4 className="text-sm font-semibold text-foreground">{t('fundReviews.ledger.title')}</h4>
            <span className="text-xs text-secondary-text">
              {t('fundReviews.ledger.count', { count: total })}
            </span>
          </div>
          <p className="mb-2 text-xs text-secondary-text">{t('fundReviews.trend.pickHint')}</p>

          {listError ? (
            <ApiErrorAlert error={listError} actionLabel={t('common.retry')} onAction={handleRefresh} />
          ) : listLoading ? (
            <Loading className="h-40" label={t('fundReviews.loading')} />
          ) : items.length === 0 ? (
            <EmptyState
              title={t('fundReviews.emptyTitle')}
              description={t('fundReviews.emptyDescription')}
              icon={<Activity className="h-6 w-6" />}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="min-w-[960px] w-full text-sm" aria-label={t('fundReviews.ledger.title')}>
                <thead className="border-b border-border/60 text-xs text-muted-text">
                  <tr>
                    <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.ledger.anchorDate')}</th>
                    <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.ledger.fund')}</th>
                    <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.ledger.horizon')}</th>
                    <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.ledger.anchorNav')}</th>
                    <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.ledger.evalDate')}</th>
                    <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.ledger.evalNav')}</th>
                    <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.ledger.return')}</th>
                    <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.ledger.drawdown')}</th>
                    <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.ledger.basis')}</th>
                    <th className="py-2 text-left whitespace-nowrap">{t('fundReviews.ledger.status')}</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr key={item.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2 pr-2 whitespace-nowrap tabular-nums text-secondary-text">{item.anchorDate}</td>
                      <td className="py-2 pr-2 whitespace-nowrap text-foreground">
                        <button
                          type="button"
                          aria-pressed={selectedFundCode === item.fundCode}
                          onClick={() => setSelectedFundCode(item.fundCode)}
                          className={cn(
                            'tabular-nums underline-offset-4 hover:underline',
                            selectedFundCode === item.fundCode
                              ? 'font-semibold text-primary'
                              : 'text-foreground',
                          )}
                        >
                          {item.fundCode}
                        </button>
                        <span className="ml-2 text-secondary-text">{item.fundName}</span>
                      </td>
                      <td className="py-2 pr-2 whitespace-nowrap text-secondary-text">{item.horizon}</td>
                      <td className="py-2 pr-2 text-right tabular-nums text-secondary-text">{item.anchorNav.toFixed(4)}</td>
                      <td className="py-2 pr-2 whitespace-nowrap tabular-nums text-secondary-text">{item.evalDate ?? '--'}</td>
                      <td className="py-2 pr-2 text-right tabular-nums text-secondary-text">
                        {item.evalNav == null ? '--' : item.evalNav.toFixed(4)}
                      </td>
                      <td className={cn('py-2 pr-2 text-right tabular-nums', returnClass(itemReturn(item)))}>
                        {formatSignedPct(itemReturn(item))}
                      </td>
                      <td className={cn('py-2 pr-2 text-right tabular-nums', returnClass(itemDrawdown(item)))}>
                        {formatSignedPct(itemDrawdown(item))}
                      </td>
                      <td className="py-2 pr-2 whitespace-nowrap text-secondary-text">
                        {basisLabel(item.restoredBasis)}
                      </td>
                      <td className="py-2 whitespace-nowrap text-secondary-text">
                        {statusLabel(item.evalStatus)}
                        {item.unableReason ? <span className="ml-1 text-xs text-muted-text">({item.unableReason})</span> : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {!listError && total > PAGE_SIZE ? (
            <div className="mt-3">
              <Pagination currentPage={page} totalPages={totalPages} onPageChange={setPage} />
            </div>
          ) : null}
        </div>
      </div>
    </Collapsible>
  );
}
