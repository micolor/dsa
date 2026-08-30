import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BellRing, X } from 'lucide-react';
import { alertsApi } from '../api/alerts';
import type { ParsedApiError } from '../api/error';
import { getParsedApiError } from '../api/error';
import { AlertRuleForm } from '../components/alerts/AlertRuleForm';
import {
  AlertRuleList,
  type AlertRuleBusyState,
  type AlertRuleEnabledFilter,
  type AlertTypeFilter,
} from '../components/alerts/AlertRuleList';
import { AlertTriggerHistory } from '../components/alerts/AlertTriggerHistory';
import { EventFactList } from '../components/alerts/EventFactList';
import { filterEventTriggers } from '../components/alerts/eventFacts';
import { ApiErrorAlert, AppPage, Dialog, EmptyState, InlineAlert, Loading, Pagination, ToastViewport } from '../components/common';
import { DashboardPanelHeader } from '../components/dashboard';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText } from '../i18n/uiText';
import {
  ALERT_PAGE_TEXT,
  ALERT_DRY_RUN_STATUS_LABELS,
  ALERT_CHANNEL_LABELS,
  ALERT_NOTIFICATION_STATUS_LABELS,
  ALERT_TRIGGER_STATUS_LABELS,
} from '../locales/featureText';
import type {
  AlertDryRunStatus,
  AlertNotificationItem,
  AlertRuleCreateRequest,
  AlertRuleItem,
  AlertRuleTestResponse,
  AlertTriggerItem,
  AlertTriggerStatus,
  AlertType,
} from '../types/alerts';
import { formatDateTime } from '../utils/format';

const PAGE_SIZE = 20;

function enabledFilterToQuery(value: AlertRuleEnabledFilter): boolean | undefined {
  if (value === 'enabled') return true;
  if (value === 'disabled') return false;
  return undefined;
}

function alertTypeFilterToQuery(value: AlertTypeFilter): AlertType | undefined {
  return value === 'all' ? undefined : value;
}

function testVariant(result: AlertRuleTestResponse): 'success' | 'warning' | 'danger' {
  if (result.status === 'evaluation_error') return 'danger';
  return result.triggered ? 'success' : 'warning';
}

/** 试跑整体状态码 → 标签（not_triggered 只是「未触发」，不代表数据完整）。 */
function formatDryRunStatus(status: AlertDryRunStatus, labels: Record<string, string>): string {
  return labels[status] ?? status;
}

/** 目标级记录状态码 → 标签（skipped = 缺数据跳过，不是求值失败）。 */
function formatRecordStatus(status: AlertTriggerStatus | null | undefined, labels: Record<string, string>): string {
  if (!status) return '';
  return labels[status] ?? status;
}

function renderTestResultMessage(
  result: AlertRuleTestResponse,
  text: (typeof ALERT_PAGE_TEXT)[keyof typeof ALERT_PAGE_TEXT],
  dryRunLabels: Record<string, string>,
  recordLabels: Record<string, string>,
): React.ReactNode {
  const targetResults = result.targetResults ?? [];
  const hasCounts = typeof result.evaluatedCount === 'number';
  const skippedCount = result.skippedCount ?? 0;
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="font-medium">{formatDryRunStatus(result.status, dryRunLabels)}</span>
        <span className="text-secondary-text">{formatUiText(text.testTriggered, { value: result.triggered ? text.testYes : text.testNo })}</span>
        <span className="text-secondary-text">{formatUiText(text.testObserved, { value: result.observedValue == null ? text.dash : String(result.observedValue) })}</span>
      </div>
      {hasCounts ? (
        <div className="text-xs text-secondary-text">
          {formatUiText(text.testEvaluated, {
            count: result.evaluatedCount ?? 0,
            triggered: result.triggeredCount ?? 0,
            degraded: result.degradedCount ?? 0,
            skipped: skippedCount,
          })}
        </div>
      ) : null}
      {skippedCount > 0 ? (
        <div className="rounded-lg border border-warning/20 bg-warning/10 px-2.5 py-1.5 text-xs text-warning">
          {formatUiText(text.testSkippedHint, { count: skippedCount })}
        </div>
      ) : null}
      {targetResults.length > 1 ? (
        <div className="grid gap-1 text-xs">
          {targetResults.slice(0, 20).map((item) => (
            <div key={`${item.target}-${item.status}`} className="flex flex-wrap justify-between gap-2">
              <span>{item.displayTarget ?? item.target}</span>
              <span>
                {formatDryRunStatus(item.status, dryRunLabels)}
                {item.recordStatus ? ` / ${formatRecordStatus(item.recordStatus, recordLabels)}` : ''}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function formatNotificationChannel(channel: string, labels: Record<string, string>): string {
  return labels[channel] ?? channel;
}

function formatNotificationStatus(notification: AlertNotificationItem, labels: Record<string, string>): string {
  if (notification.success) return labels.success ?? 'Success';
  if (notification.errorCode) return labels[notification.errorCode] ?? labels.failed ?? 'Failed';
  return labels.failed ?? 'Failed';
}

type AlertsTabKey = 'rules' | 'history' | 'events' | 'notifications';

const AlertsPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = ALERT_PAGE_TEXT[language];
  const dryRunLabels = ALERT_DRY_RUN_STATUS_LABELS[language];
  const recordLabels = ALERT_TRIGGER_STATUS_LABELS[language];
  const channelLabels = ALERT_CHANNEL_LABELS[language];
  const notificationLabels = ALERT_NOTIFICATION_STATUS_LABELS[language];

  const tabs: Array<{ key: AlertsTabKey; label: string }> = [
    { key: 'rules', label: text.tabsRules },
    { key: 'history', label: text.tabsHistory },
    { key: 'events', label: text.tabsEvents },
    { key: 'notifications', label: text.tabsNotifications },
  ];

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const [rules, setRules] = useState<AlertRuleItem[]>([]);
  const [rulesTotal, setRulesTotal] = useState(0);
  const [rulesPage, setRulesPage] = useState(1);
  const [enabledFilter, setEnabledFilter] = useState<AlertRuleEnabledFilter>('all');
  const [alertTypeFilter, setAlertTypeFilter] = useState<AlertTypeFilter>('all');
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesError, setRulesError] = useState<ParsedApiError | null>(null);

  const [triggers, setTriggers] = useState<AlertTriggerItem[]>([]);
  const [triggersTotal, setTriggersTotal] = useState(0);
  const [triggersPage, setTriggersPage] = useState(1);
  const [triggersLoading, setTriggersLoading] = useState(false);
  const [triggersError, setTriggersError] = useState<ParsedApiError | null>(null);

  const [notifications, setNotifications] = useState<AlertNotificationItem[]>([]);
  const [notificationsTotal, setNotificationsTotal] = useState(0);
  const [notificationsPage, setNotificationsPage] = useState(1);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState<ParsedApiError | null>(null);

  const [events, setEvents] = useState<AlertTriggerItem[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<ParsedApiError | null>(null);

  const [createLoading, setCreateLoading] = useState(false);
  const [createError, setCreateError] = useState<ParsedApiError | null>(null);
  const [createSuccess, setCreateSuccess] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [editingRule, setEditingRule] = useState<AlertRuleItem | null>(null);
  const [busyRule, setBusyRule] = useState<AlertRuleBusyState | null>(null);
  const [testResult, setTestResult] = useState<AlertRuleTestResponse | null>(null);
  const [activeTab, setActiveTab] = useState<AlertsTabKey>('rules');
  const rulesRequestIdRef = useRef(0);
  const triggersRequestIdRef = useRef(0);
  const notificationsRequestIdRef = useRef(0);
  const triggersLoadedRef = useRef(false);
  const notificationsLoadedRef = useRef(false);
  const eventsLoadedRef = useRef(false);
  const mountedRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // 测试结果为右上角 toast，几秒后自动消失。
  useEffect(() => {
    if (!testResult) return;
    const timer = window.setTimeout(() => setTestResult(null), 5000);
    return () => window.clearTimeout(timer);
  }, [testResult]);

  const loadRules = useCallback(async (pageOverride?: number) => {
    const requestId = rulesRequestIdRef.current + 1;
    rulesRequestIdRef.current = requestId;
    const isLatestRequest = () => rulesRequestIdRef.current === requestId;
    const requestedPage = pageOverride ?? rulesPage;
    const baseQuery = {
      enabled: enabledFilterToQuery(enabledFilter),
      alertType: alertTypeFilterToQuery(alertTypeFilter),
      pageSize: PAGE_SIZE,
    };
    setRulesLoading(true);
    try {
      let response = await alertsApi.listRules({ ...baseQuery, page: requestedPage });
      if (!mountedRef.current || !isLatestRequest()) return null;
      const lastPage = Math.max(1, Math.ceil(response.total / PAGE_SIZE));
      if (response.items.length === 0 && response.total > 0 && requestedPage > lastPage) {
        setRulesPage(lastPage);
        response = await alertsApi.listRules({ ...baseQuery, page: lastPage });
        if (!mountedRef.current || !isLatestRequest()) return null;
      } else if (pageOverride !== undefined && pageOverride !== rulesPage) {
        setRulesPage(pageOverride);
      }
      setRules(response.items);
      setRulesTotal(response.total);
      setRulesError(null);
      return response;
    } catch (error) {
      if (!mountedRef.current || !isLatestRequest()) return null;
      setRulesError(getParsedApiError(error));
      return null;
    } finally {
      if (mountedRef.current && isLatestRequest()) {
        setRulesLoading(false);
      }
    }
  }, [alertTypeFilter, enabledFilter, rulesPage]);

  const loadTriggers = useCallback(async (page?: number) => {
    const requestedPage = page ?? triggersPage;
    const requestId = triggersRequestIdRef.current + 1;
    triggersRequestIdRef.current = requestId;
    const isLatestRequest = () => triggersRequestIdRef.current === requestId;
    setTriggersLoading(true);
    try {
      const response = await alertsApi.listTriggers({ page: requestedPage, pageSize: PAGE_SIZE });
      if (!mountedRef.current || !isLatestRequest()) return;
      setTriggers(response.items);
      setTriggersTotal(response.total);
      setTriggersPage(requestedPage);
      setTriggersError(null);
    } catch (error) {
      if (!mountedRef.current || !isLatestRequest()) return;
      setTriggersError(getParsedApiError(error));
    } finally {
      if (mountedRef.current && isLatestRequest()) {
        setTriggersLoading(false);
      }
    }
  }, [triggersPage]);

  const loadEvents = useCallback(async () => {
    const requestId = triggersRequestIdRef.current + 1;
    triggersRequestIdRef.current = requestId;
    const isLatestRequest = () => triggersRequestIdRef.current === requestId;
    setEventsLoading(true);
    try {
      // 事件较稀疏，拉取最新一页（接口 page_size 上限 100）后在客户端折叠出事件项，
      // 避免为低频事件新增 data_source 过滤参数改动 repo/service/endpoint/schema 契约。
      const response = await alertsApi.listTriggers({ page: 1, pageSize: 100 });
      if (!mountedRef.current || !isLatestRequest()) return;
      setEvents(filterEventTriggers(response.items));
      setEventsError(null);
    } catch (error) {
      if (!mountedRef.current || !isLatestRequest()) return;
      setEventsError(getParsedApiError(error));
    } finally {
      if (mountedRef.current && isLatestRequest()) {
        setEventsLoading(false);
      }
    }
  }, []);

  const loadNotifications = useCallback(async (page?: number) => {
    const requestedPage = page ?? notificationsPage;
    const requestId = notificationsRequestIdRef.current + 1;
    notificationsRequestIdRef.current = requestId;
    const isLatestRequest = () => notificationsRequestIdRef.current === requestId;
    setNotificationsLoading(true);
    try {
      const response = await alertsApi.listNotifications({ page: requestedPage, pageSize: PAGE_SIZE });
      if (!mountedRef.current || !isLatestRequest()) return;
      setNotifications(response.items);
      setNotificationsTotal(response.total);
      setNotificationsPage(requestedPage);
      setNotificationsError(null);
    } catch (error) {
      if (!mountedRef.current || !isLatestRequest()) return;
      setNotificationsError(getParsedApiError(error));
    } finally {
      if (mountedRef.current && isLatestRequest()) {
        setNotificationsLoading(false);
      }
    }
  }, [notificationsPage]);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  // 各 Tab 首次激活时才加载对应列表，避免进入页面即三份全量预载。
  useEffect(() => {
    if (activeTab !== 'history' || triggersLoadedRef.current) return;
    triggersLoadedRef.current = true;
    void loadTriggers(1);
  }, [activeTab, loadTriggers]);

  useEffect(() => {
    if (activeTab !== 'notifications' || notificationsLoadedRef.current) return;
    notificationsLoadedRef.current = true;
    void loadNotifications(1);
  }, [activeTab, loadNotifications]);

  useEffect(() => {
    if (activeTab !== 'events' || eventsLoadedRef.current) return;
    eventsLoadedRef.current = true;
    void loadEvents();
  }, [activeTab, loadEvents]);

  const handleCreateRule = async (payload: AlertRuleCreateRequest) => {
    setCreateLoading(true);
    setCreateError(null);
    setCreateSuccess(null);
    try {
      if (editingRule) {
        const updated = await alertsApi.updateRule(editingRule.id, payload);
        setCreateSuccess(formatUiText(text.updated, { name: updated.name }));
      } else {
        const created = await alertsApi.createRule(payload);
        setCreateSuccess(formatUiText(text.created, { name: created.name }));
      }
      await loadRules(1);
      return true;
    } catch (error) {
      setCreateError(getParsedApiError(error));
      return false;
    } finally {
      setCreateLoading(false);
    }
  };

  const handleEditRule = (rule: AlertRuleItem) => {
    setEditingRule(rule);
    setCreateError(null);
    setCreateOpen(true);
  };

  const handleDeleteRule = async (rule: AlertRuleItem) => {
    setBusyRule({ id: rule.id, action: 'delete' });
    try {
      await alertsApi.deleteRule(rule.id);
      await loadRules();
    } catch (error) {
      setRulesError(getParsedApiError(error));
    } finally {
      setBusyRule(null);
    }
  };

  const handleTestRule = async (rule: AlertRuleItem) => {
    setBusyRule({ id: rule.id, action: 'test' });
    setTestResult(null);
    try {
      const result = await alertsApi.testRule(rule.id);
      setTestResult(result);
    } catch (error) {
      setRulesError(getParsedApiError(error));
    } finally {
      setBusyRule(null);
    }
  };

  return (
    <AppPage className="space-y-5">
      {createSuccess ? (
        <InlineAlert
          elevated
          title={text.createSuccess}
          message={createSuccess}
          variant="success"
          action={(
            <button
              type="button"
              onClick={() => setCreateSuccess(null)}
              className="self-start p-1 text-muted-text transition-colors hover:text-foreground"
              aria-label={text.close}
            >
              <X className="h-4 w-4" aria-hidden="true" />
            </button>
          )}
        />
      ) : null}
      {testResult ? (
        <ToastViewport>
          <InlineAlert
            elevated
            title={text.testResult}
            variant={testVariant(testResult)}
            message={renderTestResultMessage(testResult, text, dryRunLabels, recordLabels)}
            className="pointer-events-auto"
            action={(
              <button
                type="button"
                onClick={() => setTestResult(null)}
                className="self-start p-1 text-muted-text transition-colors hover:text-foreground"
                aria-label={text.close}
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            )}
          />
        </ToastViewport>
      ) : null}
      <div className="flex min-h-full flex-col gap-4">
        <div className="grid grid-cols-4 gap-1 rounded-xl border border-subtle bg-base/40 p-1">
          {tabs.map((tab) => {
            const selected = activeTab === tab.key;
            return (
              <button
                key={tab.key}
                type="button"
                aria-pressed={selected}
                className={`h-8 rounded-lg px-2 text-xs font-medium transition-colors ${
                  selected ? 'bg-primary/15 text-primary shadow-inner' : 'text-secondary-text hover:bg-hover hover:text-foreground'
                }`}
                onClick={() => setActiveTab(tab.key)}
              >
                {tab.label}
              </button>
            );
          })}
        </div>

        {activeTab === 'rules' ? (
          <>
            {rulesError ? <ApiErrorAlert error={rulesError} onDismiss={() => setRulesError(null)} /> : null}
            <AlertRuleList
              className="flex min-h-0 flex-1 flex-col"
              rules={rules}
              total={rulesTotal}
              page={rulesPage}
              pageSize={PAGE_SIZE}
              isLoading={rulesLoading}
              enabledFilter={enabledFilter}
              alertTypeFilter={alertTypeFilter}
              onEnabledFilterChange={(value) => {
                setEnabledFilter(value);
                setRulesPage(1);
              }}
              onAlertTypeFilterChange={(value) => {
                setAlertTypeFilter(value);
                setRulesPage(1);
              }}
              onPageChange={setRulesPage}
              onDelete={(rule) => void handleDeleteRule(rule)}
              onTest={(rule) => void handleTestRule(rule)}
              onEdit={handleEditRule}
              onCreate={() => {
                setEditingRule(null);
                setCreateOpen(true);
              }}
              busyRule={busyRule}
            />
          </>
        ) : null}

        {activeTab === 'history' ? (
          <>
            {triggersError ? <ApiErrorAlert error={triggersError} onDismiss={() => setTriggersError(null)} /> : null}
            <AlertTriggerHistory
              triggers={triggers}
              isLoading={triggersLoading}
              page={triggersPage}
              total={triggersTotal}
              pageSize={PAGE_SIZE}
              onPageChange={loadTriggers}
            />
          </>
        ) : null}

        {activeTab === 'events' ? (
          <>
            {eventsError ? <ApiErrorAlert error={eventsError} onDismiss={() => setEventsError(null)} /> : null}
            <EventFactList triggers={events} isLoading={eventsLoading} />
          </>
        ) : null}

        {activeTab === 'notifications' ? (
          <>
            {notificationsError ? <ApiErrorAlert error={notificationsError} onDismiss={() => setNotificationsError(null)} /> : null}
            <section className="flex flex-1 flex-col glass-card !border-transparent p-4 md:p-5">
              <DashboardPanelHeader
                className="mb-3"
                eyebrow={text.notificationEyebrow}
                title={text.notificationTitle}
                titleClassName="text-base font-semibold"
              />
              {notificationsLoading ? <Loading label={text.notificationLoading} /> : null}
              {!notificationsLoading && notifications.length === 0 ? (
                <EmptyState
                  icon={<BellRing className="h-6 w-6" />}
                  title={text.notificationEmptyTitle}
                  description={text.notificationEmptyDescription}
                  className="flex-1 flex flex-col items-center justify-center"
                />
              ) : null}
              {!notificationsLoading && notifications.length > 0 ? (
                <>
                  <div className="overflow-x-auto">
                    <table className="w-full min-w-[680px] text-left text-sm">
                      <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
                        <tr>
                          <th className="px-3 py-2 font-medium">{text.channelCol}</th>
                          <th className="px-3 py-2 font-medium">{text.statusCol}</th>
                          <th className="px-3 py-2 font-medium">{text.errorCodeCol}</th>
                          <th className="px-3 py-2 font-medium">{text.latencyCol}</th>
                          <th className="px-3 py-2 font-medium">{text.timeCol}</th>
                          <th className="px-3 py-2 font-medium">{text.diagnosticsCol}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-border/40">
                        {notifications.map((notification) => (
                          <tr key={notification.id}>
                            <td className="px-3 py-3">{formatNotificationChannel(notification.channel, channelLabels)}</td>
                            <td className="px-3 py-3">{formatNotificationStatus(notification, notificationLabels)}</td>
                            <td className="px-3 py-3">{notification.errorCode ?? text.dash}</td>
                            <td className="px-3 py-3">{notification.latencyMs == null ? text.dash : `${notification.latencyMs}ms`}</td>
                            <td className="px-3 py-3">{formatDateTime(notification.createdAt)}</td>
                            <td className="px-3 py-3">{notification.diagnostics ?? text.dash}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <Pagination
                    currentPage={notificationsPage}
                    totalPages={Math.max(1, Math.ceil(notificationsTotal / PAGE_SIZE))}
                    onPageChange={loadNotifications}
                    className="mt-5"
                  />
                </>
              ) : null}
            </section>
          </>
        ) : null}
      </div>

      <Dialog
        isOpen={createOpen}
        onClose={() => {
          setCreateOpen(false);
          setEditingRule(null);
        }}
        title={editingRule ? text.editRule : text.createRule}
        eyebrow={text.eyebrow}
        ariaLabel={editingRule ? text.editRule : text.createRule}
      >
        {createError ? (
          <div className="mb-4">
            <ApiErrorAlert error={createError} onDismiss={() => setCreateError(null)} />
          </div>
        ) : null}
        <AlertRuleForm
          key={editingRule ? `edit-${editingRule.id}` : 'create'}
          onSubmit={handleCreateRule}
          isSubmitting={createLoading}
          bare
          editingRule={editingRule}
          onSuccess={() => {
            setCreateOpen(false);
            setEditingRule(null);
          }}
        />
      </Dialog>
    </AppPage>
  );
};

export default AlertsPage;
