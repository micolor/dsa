import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BellRing, RefreshCw } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../../api/error';
import { notificationsApi } from '../../api/notifications';
import { ApiErrorAlert, Button, EmptyState, Loading, Pagination, Select } from '../common';
import { SettingsSectionCard } from '../settings/SettingsSectionCard';
import { NOTIFICATION_CHANNEL_OPTIONS } from '../settings/notificationChannels';
import { ALERT_CHANNEL_LABELS, ALERT_NOTIFICATION_STATUS_LABELS } from '../../locales/featureText';
import { formatDateTime } from '../../utils/format';
import type { NotificationDeliveryItem } from '../../types/notifications';
import type { UiLanguage, UiTextKey } from '../../i18n/uiText';

const DELIVERY_PAGE_SIZE = 20;

type DeliveryRouteFilter = 'all' | 'report' | 'system_error' | 'default';
type DeliveryStatusFilter = 'all' | 'success' | 'failed';

export type NotificationDeliveryCardProps = {
  t: (key: UiTextKey, params?: Record<string, string | number>) => string;
  language: UiLanguage;
  disabled: boolean;
};

/** 渠道中文名优先取 NOTIFICATION_CHANNEL_OPTIONS（真实渠道），伪渠道（冷却/降噪等）回退 ALERT_CHANNEL_LABELS。 */
function buildDeliveryChannelLabels(language: UiLanguage): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const option of NOTIFICATION_CHANNEL_OPTIONS) {
    labels[option.value] = language === 'en' ? option.labelEn : option.labelZh;
  }
  Object.assign(labels, ALERT_CHANNEL_LABELS[language]);
  return labels;
}

function deliveryRouteText(
  routeType: string,
  t: (key: UiTextKey, params?: Record<string, string | number>) => string,
): string {
  if (routeType === 'report') return t('settings.deliveryRouteReport');
  if (routeType === 'system_error') return t('settings.deliveryRouteSystemError');
  if (routeType === 'default') return t('settings.deliveryRouteDefault');
  return routeType;
}

export const NotificationDeliveryCard: React.FC<NotificationDeliveryCardProps> = ({ t, language, disabled }) => {
  const channelLabels = buildDeliveryChannelLabels(language);
  const statusLabels = ALERT_NOTIFICATION_STATUS_LABELS[language];
  const [items, setItems] = useState<NotificationDeliveryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [routeFilter, setRouteFilter] = useState<DeliveryRouteFilter>('all');
  const [statusFilter, setStatusFilter] = useState<DeliveryStatusFilter>('all');
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
      const response = await notificationsApi.getNotificationDeliveries({
        routeType: routeFilter === 'all' ? undefined : routeFilter,
        success: statusFilter === 'all' ? undefined : statusFilter === 'success',
        page: requestedPage,
        pageSize: DELIVERY_PAGE_SIZE,
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
  }, [routeFilter, statusFilter]);

  useEffect(() => {
    void load(1);
  }, [load]);

  const refresh = () => void load();

  const routeOptions = [
    { value: 'all', label: t('settings.deliveryRouteAll') },
    { value: 'report', label: t('settings.deliveryRouteReport') },
    { value: 'system_error', label: t('settings.deliveryRouteSystemError') },
    { value: 'default', label: t('settings.deliveryRouteDefault') },
  ];
  const statusOptions = [
    { value: 'all', label: t('settings.deliveryStatusAll') },
    { value: 'success', label: t('settings.deliveryStatusSuccess') },
    { value: 'failed', label: t('settings.deliveryStatusFailed') },
  ];

  const formatChannel = (channel: string): string => channelLabels[channel] ?? channel;
  const formatStatus = (item: NotificationDeliveryItem): string => {
    if (item.success) return statusLabels.success ?? t('settings.deliveryStatusSuccess');
    if (item.errorCode) return statusLabels[item.errorCode] ?? statusLabels.failed ?? t('settings.deliveryStatusFailed');
    return statusLabels.failed ?? t('settings.deliveryStatusFailed');
  };

  return (
    <SettingsSectionCard
      title={t('settings.deliveryTitle')}
      description={t('settings.deliveryDescription')}
      actions={(
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={disabled || loading}
          isLoading={loading}
          loadingText={t('settings.deliveryRefreshing')}
          onClick={refresh}
        >
          <RefreshCw className="h-4 w-4" aria-hidden="true" />
          <span className="sr-only">{t('settings.deliveryRefresh')}</span>
        </Button>
      )}
    >
      <div data-testid="notification-delivery-card" className="space-y-4">
        <div className="flex flex-wrap items-end gap-3">
          <Select
            label={t('settings.deliveryRouteType')}
            value={routeFilter}
            options={routeOptions}
            onChange={(value) => setRouteFilter(value as DeliveryRouteFilter)}
            disabled={disabled}
            className="w-44"
          />
          <Select
            label={t('settings.deliveryStatus')}
            value={statusFilter}
            options={statusOptions}
            onChange={(value) => setStatusFilter(value as DeliveryStatusFilter)}
            disabled={disabled}
            className="w-40"
          />
        </div>

        {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}
        {loading ? <Loading label={t('settings.deliveryLoading')} /> : null}
        {!loading && items.length === 0 ? (
          <EmptyState
            icon={<BellRing className="h-6 w-6" />}
            title={t('settings.deliveryEmptyTitle')}
            description={t('settings.deliveryEmptyDescription')}
            className="flex flex-col items-center justify-center"
          />
        ) : null}
        {!loading && items.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-left text-sm">
                <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
                  <tr>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColChannel')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColStatus')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColRouteType')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColErrorCode')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColLatency')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColAttempts')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColTime')}</th>
                    <th className="px-3 py-2 font-medium">{t('settings.deliveryColDiagnostics')}</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/40">
                  {items.map((item) => (
                    <tr key={item.id}>
                      <td className="px-3 py-3">{formatChannel(item.channel)}</td>
                      <td className="px-3 py-3">
                        <span>{formatStatus(item)}</span>
                        {!item.success ? (
                          <span className={`ml-1 text-xs ${item.retryable ? 'text-warning' : 'text-muted-text'}`}>
                            ({item.retryable ? t('settings.deliveryRetryable') : t('settings.deliveryNotRetryable')})
                          </span>
                        ) : null}
                      </td>
                      <td className="px-3 py-3">{deliveryRouteText(item.routeType, t)}</td>
                      <td className="px-3 py-3">{item.errorCode ?? '—'}</td>
                      <td className="px-3 py-3">{item.latencyMs == null ? '—' : `${item.latencyMs}ms`}</td>
                      <td className="px-3 py-3">{item.attempt}</td>
                      <td className="px-3 py-3">{formatDateTime(item.createdAt)}</td>
                      <td className="px-3 py-3">{item.diagnostics ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-muted-text">{t('settings.deliveryTotal', { total })}</p>
            <Pagination
              currentPage={page}
              totalPages={Math.max(1, Math.ceil(total / DELIVERY_PAGE_SIZE))}
              onPageChange={(next) => void load(next)}
              className="mt-5"
            />
          </>
        ) : null}
      </div>
    </SettingsSectionCard>
  );
};

