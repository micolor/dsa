import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
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
import { ApiErrorAlert, AppPage, EmptyState, InlineAlert, Loading } from '../components/common';
import { DashboardPanelHeader } from '../components/dashboard';
import type {
  AlertNotificationItem,
  AlertRuleCreateRequest,
  AlertRuleItem,
  AlertRuleTestResponse,
  AlertTriggerItem,
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

function renderTestResultMessage(result: AlertRuleTestResponse): React.ReactNode {
  const targetResults = result.targetResults ?? [];
  return (
    <div className="space-y-2">
      <div>
        {result.message}
        {' · 状态：'}
        {result.status}
        {' · 触发：'}
        {result.triggered ? '是' : '否'}
        {' · 观察值：'}
        {result.observedValue == null ? '--' : String(result.observedValue)}
      </div>
      {result.evaluatedCount != null && result.evaluatedCount > 1 ? (
        <div className="text-xs">
          评估 {result.evaluatedCount} · 触发 {result.triggeredCount ?? 0} · 降级 {result.degradedCount ?? 0} · 跳过 {result.skippedCount ?? 0}
        </div>
      ) : null}
      {targetResults.length > 1 ? (
        <div className="grid gap-1 text-xs">
          {targetResults.slice(0, 20).map((item) => (
            <div key={`${item.target}-${item.status}`} className="flex flex-wrap justify-between gap-2">
              <span>{item.displayTarget ?? item.target}</span>
              <span>
                {item.status}
                {item.recordStatus ? ` / ${item.recordStatus}` : ''}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const notificationChannelLabel: Record<string, string> = {
  __cooldown__: '业务冷却',
  __cooldown_read_failed__: '冷却读取失败',
  __noise_suppressed__: '通知降噪',
  __no_channel__: '无可用渠道',
  __dispatch__: '通知调度',
  __context__: '会话渠道',
};

function formatNotificationChannel(channel: string): string {
  return notificationChannelLabel[channel] ?? channel;
}

function formatNotificationStatus(notification: AlertNotificationItem): string {
  if (notification.success) return '成功';
  if (notification.errorCode === 'cooldown_active') return '冷却抑制';
  if (notification.errorCode === 'cooldown_read_failed') return '冷却读取失败';
  if (notification.errorCode === 'noise_suppressed') return '降噪抑制';
  if (notification.errorCode === 'no_channel') return '无渠道';
  return '失败';
}

const AlertsPage: React.FC = () => {
  useEffect(() => {
    document.title = '告警中心 - DSA';
  }, []);

  const [rules, setRules] = useState<AlertRuleItem[]>([]);
  const [rulesTotal, setRulesTotal] = useState(0);
  const [rulesPage, setRulesPage] = useState(1);
  const [enabledFilter, setEnabledFilter] = useState<AlertRuleEnabledFilter>('all');
  const [alertTypeFilter, setAlertTypeFilter] = useState<AlertTypeFilter>('all');
  const [rulesLoading, setRulesLoading] = useState(false);
  const [rulesError, setRulesError] = useState<ParsedApiError | null>(null);
  const [rulesLoaded, setRulesLoaded] = useState(false);

  const [triggers, setTriggers] = useState<AlertTriggerItem[]>([]);
  const [triggersLoading, setTriggersLoading] = useState(false);
  const [triggersError, setTriggersError] = useState<ParsedApiError | null>(null);

  const [notifications, setNotifications] = useState<AlertNotificationItem[]>([]);
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationsError, setNotificationsError] = useState<ParsedApiError | null>(null);

  const [createLoading, setCreateLoading] = useState(false);
  const [createError, setCreateError] = useState<ParsedApiError | null>(null);
  const [createSuccess, setCreateSuccess] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [busyRule, setBusyRule] = useState<AlertRuleBusyState | null>(null);
  const [testResult, setTestResult] = useState<AlertRuleTestResponse | null>(null);
  const rulesRequestIdRef = useRef(0);

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
      if (!isLatestRequest()) return null;
      const lastPage = Math.max(1, Math.ceil(response.total / PAGE_SIZE));
      if (response.items.length === 0 && response.total > 0 && requestedPage > lastPage) {
        setRulesPage(lastPage);
        response = await alertsApi.listRules({ ...baseQuery, page: lastPage });
        if (!isLatestRequest()) return null;
      } else if (pageOverride !== undefined && pageOverride !== rulesPage) {
        setRulesPage(pageOverride);
      }
      setRules(response.items);
      setRulesTotal(response.total);
      setRulesError(null);
      setRulesLoaded(true);
      return response;
    } catch (error) {
      if (!isLatestRequest()) return null;
      setRulesError(getParsedApiError(error));
      return null;
    } finally {
      if (isLatestRequest()) {
        setRulesLoading(false);
      }
    }
  }, [alertTypeFilter, enabledFilter, rulesPage]);

  const loadTriggers = useCallback(async () => {
    setTriggersLoading(true);
    try {
      const response = await alertsApi.listTriggers({ page: 1, pageSize: PAGE_SIZE });
      setTriggers(response.items);
      setTriggersError(null);
    } catch (error) {
      setTriggersError(getParsedApiError(error));
    } finally {
      setTriggersLoading(false);
    }
  }, []);

  const loadNotifications = useCallback(async () => {
    setNotificationsLoading(true);
    try {
      const response = await alertsApi.listNotifications({ page: 1, pageSize: PAGE_SIZE });
      setNotifications(response.items);
      setNotificationsError(null);
    } catch (error) {
      setNotificationsError(getParsedApiError(error));
    } finally {
      setNotificationsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRules();
  }, [loadRules]);

  useEffect(() => {
    if (!rulesLoaded) return;
    void loadTriggers();
    void loadNotifications();
  }, [loadNotifications, loadTriggers, rulesLoaded]);

  useEffect(() => {
    if (!createOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setCreateOpen(false);
      }
    };
    document.addEventListener('keydown', onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [createOpen]);

  const handleCreateRule = async (payload: AlertRuleCreateRequest) => {
    setCreateLoading(true);
    setCreateError(null);
    setCreateSuccess(null);
    try {
      const created = await alertsApi.createRule(payload);
      setCreateSuccess(`已创建告警规则「${created.name}」`);
      await loadRules(1);
      return true;
    } catch (error) {
      setCreateError(getParsedApiError(error));
      return false;
    } finally {
      setCreateLoading(false);
    }
  };

  const handleToggleEnabled = async (rule: AlertRuleItem) => {
    setBusyRule({ id: rule.id, action: 'toggle' });
    try {
      if (rule.enabled) {
        await alertsApi.disableRule(rule.id);
      } else {
        await alertsApi.enableRule(rule.id);
      }
      await loadRules();
    } catch (error) {
      setRulesError(getParsedApiError(error));
    } finally {
      setBusyRule(null);
    }
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
          title="创建成功"
          message={createSuccess}
          variant="success"
          action={(
            <button type="button" className="text-sm underline" onClick={() => setCreateSuccess(null)}>
              关闭
            </button>
          )}
        />
      ) : null}
      {rulesError ? <ApiErrorAlert error={rulesError} onDismiss={() => setRulesError(null)} /> : null}

      <div className="flex min-h-0 flex-col gap-4">
        <AlertRuleList
          className="flex min-h-0 flex-col"
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
          onToggleEnabled={(rule) => void handleToggleEnabled(rule)}
          onDelete={(rule) => void handleDeleteRule(rule)}
          onTest={(rule) => void handleTestRule(rule)}
          onCreate={() => setCreateOpen(true)}
          busyRule={busyRule}
        />
        {testResult ? (
          <InlineAlert
            title="测试结果"
            variant={testVariant(testResult)}
            message={renderTestResultMessage(testResult)}
          />
        ) : null}
      </div>

      {triggersError ? <ApiErrorAlert error={triggersError} onDismiss={() => setTriggersError(null)} /> : null}
      <AlertTriggerHistory triggers={triggers} isLoading={triggersLoading} />

      {notificationsError ? <ApiErrorAlert error={notificationsError} onDismiss={() => setNotificationsError(null)} /> : null}
      <section className="glass-card !border-transparent p-4 md:p-5">
        <DashboardPanelHeader
          className="mb-3"
          eyebrow="通知结果"
          title="通知尝试记录"
          titleClassName="text-base font-semibold"
        />
        {notificationsLoading ? <Loading label="正在加载通知尝试记录" /> : null}
        {!notificationsLoading && notifications.length === 0 ? (
          <EmptyState
            icon={<BellRing className="h-6 w-6" />}
            title="暂无通知尝试记录"
            description="当前没有可展示的通知尝试明细；告警触发仍会按已配置通知渠道发送。"
          />
        ) : null}
        {!notificationsLoading && notifications.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[680px] text-left text-sm">
              <thead className="border-b border-border/60 text-xs uppercase text-muted-text">
                <tr>
                  <th className="px-3 py-2 font-medium">渠道</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 font-medium">错误码</th>
                  <th className="px-3 py-2 font-medium">耗时</th>
                  <th className="px-3 py-2 font-medium">时间</th>
                  <th className="px-3 py-2 font-medium">诊断</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border/40">
                {notifications.map((notification) => (
                  <tr key={notification.id}>
                    <td className="px-3 py-3">{formatNotificationChannel(notification.channel)}</td>
                    <td className="px-3 py-3">{formatNotificationStatus(notification)}</td>
                    <td className="px-3 py-3">{notification.errorCode ?? '--'}</td>
                    <td className="px-3 py-3">{notification.latencyMs == null ? '--' : `${notification.latencyMs}ms`}</td>
                    <td className="px-3 py-3">{formatDateTime(notification.createdAt)}</td>
                    <td className="px-3 py-3">{notification.diagnostics ?? '--'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      {createOpen ? createPortal(
        <div
          className="fixed inset-0 z-[140] flex items-end bg-background/25 backdrop-blur-sm sm:items-center sm:justify-center"
          onClick={() => setCreateOpen(false)}
        >
          <div
            role="dialog"
            aria-modal="true"
            aria-label="新建告警规则"
            onClick={(e) => e.stopPropagation()}
            className="relative flex max-h-[88vh] w-full flex-col overflow-hidden rounded-t-2xl border border-border/80 bg-card shadow-soft-card-strong sm:max-w-2xl sm:rounded-2xl"
          >
            <div className="flex items-start justify-between gap-4 border-b border-border/60 px-5 py-4">
              <div className="min-w-0">
                <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-text">
                  告警中心
                </p>
                <h2 className="mt-1 text-lg font-semibold text-foreground">新建告警规则</h2>
              </div>
              <button
                type="button"
                onClick={() => setCreateOpen(false)}
                className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-border/70 bg-card/80 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
                aria-label="关闭"
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </button>
            </div>

            <div className="overflow-y-auto px-5 py-5">
              {createError ? (
                <div className="mb-4">
                  <ApiErrorAlert error={createError} onDismiss={() => setCreateError(null)} />
                </div>
              ) : null}
              <AlertRuleForm
                onSubmit={handleCreateRule}
                isSubmitting={createLoading}
                bare
                onSuccess={() => setCreateOpen(false)}
              />
            </div>
          </div>
        </div>,
        document.body,
      ) : null}
    </AppPage>
  );
};

export default AlertsPage;
