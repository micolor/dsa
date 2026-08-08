import type React from 'react';
import { Badge, ListItemRow } from '../common';
import type { StockBarItem as StockBarItemType } from '../../types/analysis';
import { getSentimentColor } from '../../types/analysis';
import { buildDecisionActionLabelMap, getDecisionActionLabel } from '../../utils/decisionAction';
import { formatDateTime } from '../../utils/format';
import { getMarketPhaseSummaryLabel } from '../../utils/marketPhase';
import { truncateStockName } from '../../utils/stockName';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface StockBarItemProps {
  item: StockBarItemType;
  isViewing: boolean;
  onClick: (recordId: number) => void;
  onDelete?: (stockCode: string) => void;
  isDeleting?: boolean;
  isMarketReview?: boolean;
}

export const StockBarItemComponent: React.FC<StockBarItemProps> = ({
  item,
  isViewing,
  onClick,
  onDelete,
  isDeleting = false,
  isMarketReview = false,
}) => {
  const { language, t } = useUiLanguage();
  const sentimentScore = typeof item.sentimentScore === 'number' ? item.sentimentScore : null;
  const sentimentColor = sentimentScore !== null ? getSentimentColor(sentimentScore) : null;
  const stockName = item.stockName || item.stockCode;
  const actionLabels = buildDecisionActionLabelMap(t);
  const operationLabel = getDecisionActionLabel(
    item.action,
    item.actionLabel,
    item.operationAdvice,
    t('history.sentiment'),
    actionLabels,
  );
  const phaseLabel = getMarketPhaseSummaryLabel(item.marketPhaseSummary, language)
    ?.replace('市场阶段: ', '')
    .replace('市场阶段：', '')
    .replace('Market phase: ', '');

  const leading = isMarketReview ? (
    <div className="w-1 h-8 rounded-full flex-shrink-0 bg-primary" style={{ boxShadow: '0 0 10px hsl(247 84% 58% / 0.4)' }} />
  ) : sentimentColor ? (
    <div
      className="w-1 h-8 rounded-full flex-shrink-0"
      style={{
        backgroundColor: sentimentColor,
        boxShadow: `0 0 10px ${sentimentColor}40`,
      }}
    />
  ) : (
    <div className="w-1 h-8 rounded-full flex-shrink-0 bg-subtle" />
  );

  const trailing = (
    <>
      {isMarketReview ? (
        <Badge
          variant="default"
          size="sm"
          className="shrink-0 shadow-none text-[10px] font-semibold leading-none"
          style={{
            color: 'hsl(247 84% 62%)',
            borderColor: 'hsl(247 84% 58% / 0.32)',
            backgroundColor: 'hsl(247 84% 58% / 0.1)',
          }}
        >
          {t('stockBar.market')}
        </Badge>
      ) : sentimentColor ? (
        <Badge
          variant="default"
          size="sm"
          className="home-history-sentiment-badge shrink-0 shadow-none text-[11px] font-semibold leading-none transition-opacity duration-200"
          style={{
            color: sentimentColor,
            borderColor: `${sentimentColor}30`,
            backgroundColor: `${sentimentColor}10`,
          }}
        >
          {operationLabel} {sentimentScore}
        </Badge>
      ) : null}
    </>
  );

  const meta = (
    <>
      <span className="text-[11px] text-secondary-text font-mono">
        {item.stockCode}
      </span>
      {item.lastAnalysisTime && (
        <>
          <span className="w-1 h-1 rounded-full bg-subtle-hover" />
          <span className="text-[11px] text-muted-text">
            {formatDateTime(item.lastAnalysisTime)}
          </span>
        </>
      )}
      {item.analysisCount > 1 && (
        <>
          <span className="w-1 h-1 rounded-full bg-subtle-hover" />
          <span className="text-[10px] text-muted-text">
            {t('history.analysisCount', { count: item.analysisCount })}
          </span>
        </>
      )}
      {phaseLabel ? (
        <>
          <span className="w-1 h-1 rounded-full bg-subtle-hover" />
          <Badge variant="default" size="sm" className="shrink-0 shadow-none text-[10px] leading-none">
            {phaseLabel}
          </Badge>
        </>
      ) : null}
    </>
  );

  return (
    <ListItemRow
      wrapperClassName="home-history-item w-full min-w-0 flex-1"
      buttonClassName={`w-full min-w-0 flex-1 text-left p-2.5 ${
        isViewing ? 'home-history-item-selected' : ''
      }`}
      ariaLabel={t('history.itemAria', { name: stockName, code: item.stockCode })}
      onClick={() => onClick(item.id)}
      leading={leading}
      title={(
        <span className="block w-full truncate text-sm font-semibold text-foreground tracking-tight">
          {truncateStockName(stockName)}
        </span>
      )}
      trailing={trailing}
      onDelete={onDelete ? () => onDelete(item.stockCode) : undefined}
      deleteAriaLabel={t('history.deleteRecord', { name: item.stockName || item.stockCode })}
      deleteDisabled={isDeleting}
      meta={meta}
      metaTestId="history-card-meta"
      actionsTestId="history-card-actions"
    />
  );
};
