import type React from 'react';
import { useState } from 'react';
import { ApiErrorAlert, EmptyState } from '../common';
import { Collapsible } from '../common/Collapsible';
import { Activity } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import type { DecisionSignalItem } from '../../types/decisionSignals';
import type { ParsedApiError } from '../../api/error';
import { DecisionSignalCard } from './DecisionSignalDisplay';
import {
  DECISION_SIGNAL_QUEUE_ORDER,
  groupDecisionSignalsByQueue,
  type DecisionSignalQueueGroup,
} from '../../utils/decisionSignalQueue';

export type DecisionSignalActionQueueProps = {
  items: readonly DecisionSignalItem[];
  loading: boolean;
  error: ParsedApiError | null;
  onRetry: () => void;
  onSelect: (item: DecisionSignalItem) => void;
  selectedId: number | null;
};

const GROUP_TITLE_KEYS: Record<DecisionSignalQueueGroup, UiTextKey> = {
  actionable: 'decisionSignals.queueActionable',
  needs_review: 'decisionSignals.queueNeedsReview',
  watch: 'decisionSignals.queueWatch',
};

const GROUP_HINT_KEYS: Record<DecisionSignalQueueGroup, UiTextKey> = {
  actionable: 'decisionSignals.queueActionableHint',
  needs_review: 'decisionSignals.queueNeedsReviewHint',
  watch: 'decisionSignals.queueWatchHint',
};

export const DecisionSignalActionQueue: React.FC<DecisionSignalActionQueueProps> = ({
  items,
  loading,
  error,
  onRetry,
  onSelect,
  selectedId,
}) => {
  const { t } = useUiLanguage();
  // 「观察」档的展开状态由本组件持有：受控模式下未展开时子内容不挂载，
  // 收起的内容因此不会留在 Tab 顺序里（见下方 Collapsible 的注释）。
  const [watchExpanded, setWatchExpanded] = useState(false);

  if (error) {
    return (
      <ApiErrorAlert
        error={{ ...error, title: t('decisionSignals.errorTitle') }}
        actionLabel={t('common.retry')}
        onAction={onRetry}
      />
    );
  }

  if (loading) {
    return <p className="text-sm text-secondary-text">{t('common.loading')}...</p>;
  }

  if (items.length === 0) {
    return (
      <EmptyState
        title={t('decisionSignals.queueEmptyTitle')}
        description={t('decisionSignals.queueEmptyDescription')}
        icon={<Activity className="h-7 w-7" />}
      />
    );
  }

  const grouped = groupDecisionSignalsByQueue(items);

  return (
    <div className="space-y-3">
      {DECISION_SIGNAL_QUEUE_ORDER.map((group) => {
        const groupItems = grouped[group];
        const title = t(GROUP_TITLE_KEYS[group]);
        // 观察档默认收起：它不需要立即动作，不该占据首屏。
        const collapsible = group === 'watch';

        const body =
          groupItems.length === 0 ? (
            <p className="text-sm text-secondary-text">{t('decisionSignals.queueGroupEmpty')}</p>
          ) : (
            <div className="grid gap-3 xl:grid-cols-2">
              {groupItems.map((item) => (
                <DecisionSignalCard
                  key={item.id}
                  item={item}
                  onSelect={onSelect}
                  selected={selectedId === item.id}
                />
              ))}
            </div>
          );

        const heading = (
          <span className="flex items-center gap-2">
            <span>{title}</span>
            <span className="text-xs font-normal text-secondary-text">
              {t('decisionSignals.queueCount', { count: groupItems.length })}
            </span>
          </span>
        );

        return (
          <section key={group} aria-labelledby={`queue-group-${group}`}>
            <h3 id={`queue-group-${group}`} className="sr-only">
              {title}
            </h3>
            <p className="mb-2 text-xs text-secondary-text">{t(GROUP_HINT_KEYS[group])}</p>
            {collapsible ? (
              // Collapsible 的 title 是 string，折叠档只能把条数拼进标题里；
              // spec §4 要求「观察」分组标题带条数。
              //
              // 用受控模式 + 条件挂载：非受控模式只把面板压成 max-h-0 / opacity-0，
              // 收起的内容仍然留在 DOM 和 Tab 顺序里，键盘用户会 Tab 进看不见的卡片、
              // 焦点「消失」。子内容不挂载同时让长列表不再被 2000px 裁掉，因此这里
              // 也用 scrollable 给面板一个真正的滚动容器。
              <Collapsible
                title={`${title} · ${t('decisionSignals.queueCount', { count: groupItems.length })}`}
                open={watchExpanded}
                onOpenChange={setWatchExpanded}
                scrollable
              >
                {watchExpanded ? body : null}
              </Collapsible>
            ) : (
              <div className="rounded-2xl border border-border/70 bg-card/60 p-3">
                <div className="mb-3 font-medium text-foreground">{heading}</div>
                {body}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
};
