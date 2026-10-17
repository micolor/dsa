import { useCallback, useEffect, useRef, useState } from 'react';
import { fundNavReviewsApi } from '../../api/fundNavReviews';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { FundNavReviewItem } from '../../types/fundNavReviews';
import { formatSignedPct } from '../../utils/portfolioFormat';
import { ApiErrorAlert, EmptyState, Loading } from '../common';

const SUMMARY_LIMIT = 5;
const FUND_CODE_RE = /^fund:\s*(\d{6})$/i;

interface FundNavReviewSummaryProps {
  fundCode: string;
}

/**
 * 单只基金报告页的紧凑净值复盘摘要。
 *
 * 只展示最近几条锚点的区间表现，数据来自现有的复盘 list 接口；报告主体
 * 结构不变。``fundCode`` 必须是 ``fund:<6位代码>``，否则不请求。
 */
export function FundNavReviewSummary({ fundCode }: FundNavReviewSummaryProps) {
  const { t } = useUiLanguage();
  const match = FUND_CODE_RE.exec(fundCode.trim());
  const normalizedCode = match?.[1] ?? '';
  const [items, setItems] = useState<FundNavReviewItem[]>([]);
  const [loading, setLoading] = useState(Boolean(normalizedCode));
  const [error, setError] = useState<ParsedApiError | null>(null);
  const requestIdRef = useRef(0);

  const load = useCallback(async () => {
    if (!normalizedCode) return;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    setLoading(true);
    try {
      const response = await fundNavReviewsApi.list({
        fundCode: normalizedCode,
        page: 1,
        pageSize: SUMMARY_LIMIT,
      });
      if (requestIdRef.current !== requestId) return;
      setItems(response.items);
      setError(null);
    } catch (err) {
      if (requestIdRef.current !== requestId) return;
      setItems([]);
      setError(getParsedApiError(err));
    } finally {
      if (requestIdRef.current === requestId) {
        setLoading(false);
      }
    }
  }, [normalizedCode]);

  useEffect(() => {
    if (!normalizedCode) return;
    void load();
  }, [load, normalizedCode]);

  if (!normalizedCode) return null;

  const basisLabel = (value: string | null | undefined) => {
    if (value === 'dividend_reinvested') return t('fundReviews.basis.dividendReinvested');
    if (value === 'acc_nav') return t('fundReviews.basis.accNav');
    if (value === 'unit_nav') return t('fundReviews.basis.unitNav');
    return '--';
  };

  return (
    <section
      aria-label={t('fundReviews.report.title')}
      className="rounded-2xl border border-border/60 bg-card p-4 shadow-soft-card"
    >
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground">{t('fundReviews.report.title')}</h3>
        <span className="text-xs text-secondary-text">
          {t('fundReviews.report.latest', { count: SUMMARY_LIMIT })}
        </span>
      </div>

      {error ? (
        <ApiErrorAlert
          error={{ ...error, title: t('fundReviews.report.loadFailed') }}
          actionLabel={t('common.retry')}
          onAction={() => void load()}
        />
      ) : loading ? (
        <Loading className="h-24" label={t('fundReviews.report.loading')} />
      ) : items.length === 0 ? (
        <EmptyState
          title={t('fundReviews.report.empty')}
          description={t('fundReviews.report.emptyDescription')}
        />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm" aria-label={t('fundReviews.report.title')}>
            <thead className="border-b border-border/60 text-xs text-muted-text">
              <tr>
                <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.ledger.anchorDate')}</th>
                <th className="py-2 pr-2 text-left whitespace-nowrap">{t('fundReviews.ledger.horizon')}</th>
                <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.ledger.return')}</th>
                <th className="py-2 pr-2 text-right tabular-nums whitespace-nowrap">{t('fundReviews.ledger.drawdown')}</th>
                <th className="py-2 text-left whitespace-nowrap">{t('fundReviews.ledger.basis')}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => {
                const returnPct = item.restoredReturnPct ?? item.navReturnPct;
                const drawdownPct = item.restoredMaxDrawdownPct ?? item.maxDrawdownPct;
                return (
                  <tr key={item.id} className="border-b border-border/40 last:border-0">
                    <td className="py-2 pr-2 whitespace-nowrap tabular-nums text-secondary-text">
                      {item.anchorDate}
                    </td>
                    <td className="py-2 pr-2 whitespace-nowrap text-secondary-text">{item.horizon}</td>
                    <td className="py-2 pr-2 text-right tabular-nums text-foreground">
                      {formatSignedPct(returnPct)}
                    </td>
                    <td className="py-2 pr-2 text-right tabular-nums text-secondary-text">
                      {formatSignedPct(drawdownPct)}
                    </td>
                    <td className="py-2 whitespace-nowrap text-secondary-text">
                      {basisLabel(item.restoredBasis)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
