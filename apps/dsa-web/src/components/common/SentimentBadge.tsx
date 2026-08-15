import type React from 'react';
import { Badge } from './Badge';
import type { MarketPhaseSummary, ReportLanguage } from '../../types/analysis';
import { getMarketPhaseBadgeLabel } from '../../utils/marketPhase';

interface SentimentBadgeProps {
  color: string;
  operationLabel?: string | null;
  score?: string | number | null;
  /**
   * 历史/持仓列表追加 `home-history-sentiment-badge` 与透明度过渡类；
   * 首页评分徽章不传，保持无过渡的紧凑样式。
   */
  transition?: boolean;
}

/**
 * 统一的操作信号评分徽章。
 * StockBarItem / HistoryListItem / HomeStockWorkspace.ScoreBadge 共用，
 * 颜色、边框、底色与字号保持一致，避免三处重复实现漂移。
 */
export const SentimentBadge: React.FC<SentimentBadgeProps> = ({
  color,
  operationLabel,
  score,
  transition = false,
}) => (
  <Badge
    variant="default"
    size="sm"
    className={`shrink-0 shadow-none text-[11px] font-semibold leading-none${
      transition ? ' home-history-sentiment-badge transition-opacity duration-200' : ''
    }`}
    style={{
      color,
      borderColor: `${color}30`,
      backgroundColor: `${color}10`,
    }}
  >
    {operationLabel} {score}
  </Badge>
);

interface MarketPhaseBadgeProps {
  summary?: MarketPhaseSummary | null;
  language?: ReportLanguage | null;
}

/**
 * 市场阶段徽章（带前导分隔点），与 StockBarItem / HistoryListItem 的 meta 一致。
 * 无 summary 时返回 null，调用方可直接内联使用。
 */
export const MarketPhaseBadge: React.FC<MarketPhaseBadgeProps> = ({ summary, language }) => {
  const label = getMarketPhaseBadgeLabel(summary, language);
  if (!label) {
    return null;
  }
  return (
    <>
      <span className="w-1 h-1 rounded-full bg-subtle-hover" />
      <Badge variant="default" size="sm" className="shrink-0 shadow-none text-[10px] leading-none">
        {label}
      </Badge>
    </>
  );
};
