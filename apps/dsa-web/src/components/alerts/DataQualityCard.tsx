import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, RefreshCw } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { dataQualityApi } from '../../api/dataQuality';
import { ApiErrorAlert, Button, EmptyState, Loading, Pagination, Select } from '../common';
import { SettingsSectionCard } from '../settings/SettingsSectionCard';
import type { DataQualityDiscrepancyItem } from '../../types/dataQuality';
import type { UiTextKey } from '../../i18n/uiText';

export type DataQualityCardProps = {
  t: (key: UiTextKey, params?: Record<string, string | number>) => string;
  disabled: boolean;
};

const DATA_QUALITY_PAGE_SIZE = 20;

type DataQualityIssueFilter = 'all' | 'price_discrepancy' | 'date_mismatch' | 'field_missing';

/** issue token -> i18n key；未知类型回退 null，由调用方展示原始 token。 */
function dataQualityIssueLabelKey(issue: string): UiTextKey | null {
  if (issue === 'price_discrepancy') return 'settings.dataQualityIssuePrice';
  if (issue === 'date_mismatch') return 'settings.dataQualityIssueDate';
  if (issue === 'field_missing') return 'settings.dataQualityIssueField';
  return null;
}

export const DataQualityCard: React.FC<DataQualityCardProps> = ({ t, disabled }) => {
  const [items, setItems] = useState<DataQualityDiscrepancyItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [marketFilter, setMarketFilter] = useState('all');
  const [issueFilter, setIssueFilter] = useState<DataQualityIssueFilter>('all');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const requestIdRef = useRef(0);
  const pageRef = useRef(1);

  const load = useCallback(async (pageOverride?: number) => {
    const requestedPage = pageOverride ?? pageRef.current;
    pageRef.current = requestedPage;
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const isLatestRequest = () => requestIdRef.current === requestId;
    setLoading(true);
    try {
      const response = await dataQualityApi.getDiscrepancies({
        market: marketFilter === 'all' ? undefined : marketFilter,
        issueType: issueFilter === 'all' ? undefined : issueFilter,
        page: requestedPage,
        pageSize: DATA_QUALITY_PAGE_SIZE,
      });
      if (!isLatestRequest()) return;
      setItems(response.items);
      setTotal(response.total);
      setPage(requestedPage);
      setError(null);
    } catch (err) {
      if (!isLatestRequest()) return;
      setError(getParsedApiError(err));
    } finally {
      if (isLatestRequest()) setLoading(false);
    }
  }, [marketFilter, issueFilter]);

  useEffect(() => {
    void load(1);
  }, [load]);

  const refresh = () => void load();

  const marketOptions = [
    { value: 'all', label: t('settings.dataQualityMarketAll') },
    { value: 'cn', label: t('settings.dataQualityMarketCn') },
    { value: 'us', label: t('settings.dataQualityMarketUs') },
    { value: 'hk', label: t('settings.dataQualityMarketHk') },
  ];
  const issueOptions = [
    { value: 'all', label: t('settings.dataQualityIssueAll') },
    { value: 'price_discrepancy', label: t('settings.dataQualityIssuePrice') },
    { value: 'date_mismatch', label: t('settings.dataQualityIssueDate') },
    { value: 'field_missing', label: t('settings.dataQualityIssueField') },
  ];

  const issueText = (issue: string): string => {
    const key = dataQualityIssueLabelKey(issue);
    return key ? t(key) : issue;
  };
  const pct = (v: number | null): string => (v == null ? '—' : `${v.toFixed(2)}%`);

  return (
    <SettingsSectionCard
      title={t('settings.dataQualityTitle')}
      description={t('settings.dataQualityDescription')}
      actions={(
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={disabled || loading}
          isLoading={loading}
          loadingText={t('settings.dataQualityRefreshing')}
          onClick={refresh}
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only">{t('settings.dataQualityRefresh')}</span>
        </Button>
      )}
    >
      <div data-testid="data-quality-card" className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Select
            label={t('settings.dataQualityMarket')}
            value={marketFilter}
            options={marketOptions}
            onChange={(value) => setMarketFilter(value)}
            disabled={disabled}
            className="w-44"
          />
          <Select
            label={t('settings.dataQualityIssue')}
            value={issueFilter}
            options={issueOptions}
            onChange={(value) => setIssueFilter(value as DataQualityIssueFilter)}
            disabled={disabled}
            className="w-44"
          />
        </div>

        {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}
        {loading ? <Loading label={t('settings.dataQualityLoading')} /> : null}
        {!loading && items.length === 0 ? (
          <EmptyState
            icon={<AlertTriangle className="h-6 w-6" />}
            title={t('settings.dataQualityEmptyTitle')}
            description={t('settings.dataQualityEmptyDescription')}
            className="flex flex-col items-center justify-center"
          />
        ) : null}
        {!loading && items.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[760px] text-left text-sm">
                <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
                  <tr>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColMarket')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColStock')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColIssue')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColSources')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColDiff')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.dataQualityColTime')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="px-3 py-3">{item.market}</td>
                      <td className="px-3 py-3">{item.stockCode}</td>
                      <td className="px-3 py-3">{issueText(item.issueType)}</td>
                      <td className="px-3 py-3">{item.primarySource} ↔ {item.secondarySource}</td>
                      <td className="px-3 py-3">{pct(item.priceDiffPct)}</td>
                      <td className="px-3 py-3">{item.createdAt}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Pagination
              currentPage={page}
              totalPages={Math.max(1, Math.ceil(total / DATA_QUALITY_PAGE_SIZE))}
              onPageChange={(next) => void load(next)}
              className="mt-5"
            />
          </>
        ) : null}
      </div>
    </SettingsSectionCard>
  );
};
