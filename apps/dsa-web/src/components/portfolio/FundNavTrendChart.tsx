import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { fundNavReviewsApi } from '../../api/fundNavReviews';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { FundNavRangeResponse } from '../../types/fundNavReviews';
import { ApiErrorAlert, Button, EmptyState, Loading } from '../common';
import type { FundNavTrendBasis, FundNavTrendPoint } from './FundNavTrendChartImpl';

const FundNavTrendChartImpl = lazy(() => import('./FundNavTrendChartImpl'));

const BASIS_OPTIONS: FundNavTrendBasis[] = ['adjusted', 'unit', 'acc'];

interface FundNavTrendChartProps {
  fundCode: string;
  startDate: string;
  endDate: string;
  height?: number;
}

/**
 * 单只基金的区间净值走势。
 *
 * 口径：复权（默认）/ 单位 / 累计。分红数据不可用时后端把 `adjusted_nav` 置空，
 * 这里禁用复权并自动落到累计口径，避免画出一条不存在的复权线。
 * recharts 由 `FundNavTrendChartImpl` 懒加载，不进入首屏 bundle。
 */
export function FundNavTrendChart({
  fundCode,
  startDate,
  endDate,
  height = 240,
}: FundNavTrendChartProps) {
  const { t } = useUiLanguage();
  const [basis, setBasis] = useState<FundNavTrendBasis>('adjusted');
  const [data, setData] = useState<FundNavRangeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const requestIdRef = useRef(0);

  const load = useCallback(async () => {
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    try {
      const response = await fundNavReviewsApi.getNavRange({
        fund_code: fundCode,
        start_date: startDate,
        end_date: endDate,
      });
      if (requestIdRef.current !== requestId) return;
      setData(response);
      setError(null);
      setBasis((current) =>
        response.adjustmentStatus === 'unavailable' && current === 'adjusted'
          ? 'acc'
          : current,
      );
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setData(null);
      setError(getParsedApiError(err));
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [endDate, fundCode, startDate]);

  useEffect(() => {
    setBasis('adjusted');
    void load();
  }, [load]);

  const adjustedAvailable = data?.adjustmentStatus !== 'unavailable';
  const points: FundNavTrendPoint[] = (data?.items ?? []).map((item) => ({
    date: item.date,
    unit: item.unitNav,
    acc: item.accNav,
    adjusted: item.adjustedNav,
  }));
  const activeBasis = adjustedAvailable ? basis : basis === 'adjusted' ? 'acc' : basis;
  const basisLabel = (value: FundNavTrendBasis) => {
    if (value === 'adjusted') return t('fundReviews.trend.adjusted');
    if (value === 'acc') return t('fundReviews.trend.acc');
    return t('fundReviews.trend.unit');
  };

  return (
    <section aria-label={t('fundReviews.trend.title')} className="space-y-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="text-sm font-semibold text-foreground">{t('fundReviews.trend.title')}</h4>
        <div className="flex items-center gap-1" role="group" aria-label={t('fundReviews.trend.basis')}>
          {BASIS_OPTIONS.map((option) => {
            const disabled = option === 'adjusted' && !adjustedAvailable;
            return (
              <Button
                key={option}
                type="button"
                variant={activeBasis === option ? 'secondary' : 'outline'}
                size="xsm"
                aria-pressed={activeBasis === option}
                disabled={disabled}
                onClick={() => setBasis(option)}
              >
                {basisLabel(option)}
              </Button>
            );
          })}
        </div>
      </div>

      <p className="text-xs text-secondary-text">
        <span className="tabular-nums">{fundCode}</span>
        <span className="ml-2">{`${startDate} ~ ${endDate}`}</span>
      </p>

      {!adjustedAvailable ? (
        <p className="text-xs text-secondary-text">{t('fundReviews.trend.unavailable')}</p>
      ) : null}
      {data?.truncated ? (
        <p className="text-xs text-secondary-text">
          {t('fundReviews.trend.truncated', { count: data.items.length })}
        </p>
      ) : null}

      {error ? (
        <ApiErrorAlert
          error={{ ...error, title: t('fundReviews.trend.loadFailed') }}
          actionLabel={t('common.retry')}
          onAction={() => void load()}
        />
      ) : loading ? (
        <Loading className="h-40" label={t('fundReviews.trend.loading')} />
      ) : points.length === 0 ? (
        <EmptyState
          title={t('fundReviews.trend.empty')}
          description={t('fundReviews.trend.emptyDescription')}
        />
      ) : (
        <Suspense fallback={<Loading className="h-40" label={t('fundReviews.trend.loading')} />}>
          <FundNavTrendChartImpl points={points} basis={activeBasis} height={height} />
        </Suspense>
      )}
    </section>
  );
}
