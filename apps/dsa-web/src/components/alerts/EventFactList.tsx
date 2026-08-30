import type React from 'react';
import { Zap } from 'lucide-react';
import { Badge, EmptyState, Loading } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { ALERT_PAGE_TEXT, EVENT_FACT_SOURCE_LABELS } from '../../locales/featureText';
import type { AlertTriggerItem } from '../../types/alerts';
import { formatDateTime } from '../../utils/format';
import { formatMoney, parseEventDiagnostics } from './eventFacts';

interface EventFactListProps {
  triggers: AlertTriggerItem[];
  isLoading?: boolean;
}

export const EventFactList: React.FC<EventFactListProps> = ({ triggers, isLoading = false }) => {
  const { language } = useUiLanguage();
  const text = ALERT_PAGE_TEXT[language];
  const sourceLabels = EVENT_FACT_SOURCE_LABELS[language];

  return (
    <section className="flex flex-1 flex-col glass-card !border-transparent p-4 md:p-5">
      <DashboardPanelHeader
        className="mb-3"
        eyebrow={text.eventEyebrow}
        title={text.eventTitle}
        titleClassName="text-base font-semibold"
      />
      {isLoading ? <Loading label={text.eventLoading} /> : null}
      {!isLoading && triggers.length === 0 ? (
        <EmptyState
          icon={<Zap className="h-6 w-6" />}
          title={text.eventEmptyTitle}
          description={text.eventEmptyDescription}
          className="flex flex-1 flex-col items-center justify-center"
        />
      ) : null}
      {!isLoading && triggers.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {triggers.map((trigger) => {
            const diagnostics = parseEventDiagnostics(trigger.diagnostics);
            const sourceLabel = sourceLabels[trigger.dataSource ?? ''] ?? trigger.dataSource ?? text.dash;
            return (
              <li key={trigger.id} className="rounded-xl border border-border/60 bg-base/30 p-3">
                <div className="mb-1 flex flex-wrap items-center gap-2">
                  <Badge variant="default">{sourceLabel}</Badge>
                  <span className="font-mono text-secondary-text">{trigger.target}</span>
                  <Badge variant={trigger.status === 'triggered' ? 'success' : trigger.status === 'failed' ? 'danger' : 'warning'}>
                    {trigger.status}
                  </Badge>
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-secondary-text">
                  <span>
                    {text.eventColObserved} <span className="font-mono">{trigger.observedValue ?? '--'}</span>
                  </span>
                  <span>
                    {text.eventColThreshold} <span className="font-mono">{trigger.threshold ?? '--'}</span>
                  </span>
                  <span className="text-xs">{formatDateTime(trigger.dataTimestamp ?? trigger.triggeredAt)}</span>
                </div>
                {diagnostics.event ? (
                  <div className="mt-1 text-xs text-muted-text">
                    {String(diagnostics.event)}
                    {diagnostics.recent_count !== undefined ? ` · ${text.eventColObserved}: ${String(diagnostics.recent_count)}` : ''}
                    {diagnostics.count !== undefined ? ` · count: ${String(diagnostics.count)}` : ''}
                    {diagnostics.main_net_inflow !== undefined
                      ? ` · inflow: ${formatMoney(diagnostics.main_net_inflow)}`
                      : ''}
                  </div>
                ) : null}
                {trigger.reason ? <div className="mt-1 text-sm text-secondary-text">{trigger.reason}</div> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
};
