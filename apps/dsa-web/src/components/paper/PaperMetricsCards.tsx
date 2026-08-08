import type React from 'react';
import { Card } from '../common/Card';
import type { PaperSnapshot } from '../../types/paper';
import type { UiLanguage } from '../../i18n/uiText';
import { PAPER_TRADING_TEXT } from '../../locales/featureText';

type Props = {
  snapshot: PaperSnapshot | null;
  language: UiLanguage;
};

function fmt(value: number): string {
  if (value == null || Number.isNaN(value)) return '--';
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function fmtPct(value: number): string {
  if (value == null || Number.isNaN(value)) return '--';
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(2)}%`;
}

export const PaperMetricsCards: React.FC<Props> = ({ snapshot, language }) => {
  const text = PAPER_TRADING_TEXT[language];
  const items = [
    { label: text.totalAssets, value: snapshot ? fmt(snapshot.netValue) : '--' },
    { label: text.cash, value: snapshot ? fmt(snapshot.cash) : '--' },
    { label: text.marketValue, value: snapshot ? fmt(snapshot.marketValue) : '--' },
    { label: text.returnRate, value: snapshot ? fmtPct(snapshot.returnPct) : '--' },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      {items.map((item) => (
        <Card key={item.label} variant="gradient" className="p-4">
          <p className="text-xs text-secondary-text">{item.label}</p>
          <p className="mt-2 truncate text-lg font-semibold text-foreground">{item.value}</p>
        </Card>
      ))}
    </div>
  );
};
