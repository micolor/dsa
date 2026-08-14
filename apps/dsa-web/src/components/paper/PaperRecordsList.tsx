import type React from 'react';
import { useState } from 'react';
import { Badge, EmptyState, Pagination } from '../common';
import type { PaperSignalRecord, PaperTrade } from '../../types/paper';
import type { DecisionAction } from '../../types/analysis';
import type { UiLanguage } from '../../i18n/uiText';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { PAPER_TRADING_TEXT } from '../../locales/featureText';
import {
  buildDecisionActionLabelMap,
  getDecisionActionLabel,
  getDecisionActionTone,
} from '../../utils/decisionAction';

type Props = {
  signals: PaperSignalRecord[];
  trades: PaperTrade[];
  signalTotal: number;
  tradeTotal: number;
  page: number;
  pageSize: number;
  onPageChange: (page: number) => void;
  language: UiLanguage;
};

type MetaBadgeVariant = 'success' | 'danger' | 'warning' | 'info' | 'default';

export const PaperRecordsList: React.FC<Props> = ({
  signals,
  trades,
  signalTotal,
  tradeTotal,
  page,
  pageSize,
  onPageChange,
  language,
}) => {
  const text = PAPER_TRADING_TEXT[language];
  const { t } = useUiLanguage();
  const actionLabels = buildDecisionActionLabelMap(t);
  const dispositionMeta: Record<string, { label: string; variant: MetaBadgeVariant }> = {
    opened: { label: text.dispOpened, variant: 'success' },
    added: { label: text.dispAdded, variant: 'success' },
    closed: { label: text.dispClosed, variant: 'danger' },
    reduced: { label: text.dispReduced, variant: 'warning' },
    hold: { label: text.dispHold, variant: 'default' },
    ignored: { label: text.dispIgnored, variant: 'default' },
  };
  const [tab, setTab] = useState<'signal' | 'trade'>('signal');

  const totalPages = Math.max(1, Math.ceil((tab === 'signal' ? signalTotal : tradeTotal) / pageSize));
  const empty = tab === 'signal' ? signals.length === 0 : trades.length === 0;

  return (
    <div>
      <div className="mb-3 flex items-center gap-1">
        {(['signal', 'trade'] as const).map((key) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
              tab === key ? 'bg-[var(--nav-active-bg)] text-[hsl(var(--primary))]' : 'text-secondary-text hover:text-foreground'
            }`}
          >
            {key === 'signal' ? text.signalTab : text.tradeTab}
          </button>
        ))}
      </div>

      {empty ? (
        <EmptyState title={text.noRecordsTitle} description={text.noRecordsDescription} />
      ) : tab === 'signal' ? (
        <div className="space-y-2">
          {signals.map((record) => {
            const signalAction = record.action as DecisionAction;
            const actionLabel = getDecisionActionLabel(signalAction, null, null, record.action, actionLabels)
              ?? record.action;
            const actionVariant = getDecisionActionTone(signalAction) as MetaBadgeVariant;
            const disposition = dispositionMeta[record.disposition];
            return (
            <div key={record.signalId} className="home-subpanel flex items-center justify-between gap-2 p-3">
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <Badge variant={actionVariant}>{actionLabel}</Badge>
                <Badge variant={disposition?.variant ?? 'default'}>
                  {disposition?.label ?? record.disposition}
                </Badge>
                {record.stockCode ? (
                  <span className="min-w-0 truncate text-sm font-medium text-foreground">
                    {record.stockName || record.stockCode}
                    <span className="ml-1.5 font-mono text-xs text-secondary-text">{record.stockCode}</span>
                  </span>
                ) : null}
              </div>
              <span className="shrink-0 text-xs text-secondary-text">{record.processedAt}</span>
            </div>
          );
          })}
        </div>
      ) : (
        <div className="space-y-2">
          {trades.map((trade, index) => (
            <div key={`${trade.tradeDate}-${index}`} className="home-subpanel flex items-center justify-between p-3">
              <div className="flex items-center gap-2">
                <Badge variant={trade.side === 'buy' ? 'success' : 'danger'}>
                  {trade.side === 'buy' ? text.buy : text.sell}
                </Badge>
                <span className="text-sm font-medium text-foreground">{trade.stockCode}</span>
                <span className="text-xs text-secondary-text">
                  {trade.quantity} @ {trade.price}
                </span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-secondary-text">{trade.reason}</span>
                <span className="text-xs text-secondary-text">{trade.tradeDate}</span>
              </div>
            </div>
          ))}
        </div>
      )}

      {!empty && totalPages > 1 && (
        <div className="mt-4 flex justify-end">
          <Pagination
            currentPage={page}
            totalPages={totalPages}
            onPageChange={onPageChange}
          />
        </div>
      )}
    </div>
  );
};
