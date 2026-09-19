import type React from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { EquityPoint } from '../../types/paper';
import type { UiLanguage } from '../../i18n/uiText';
import { PAPER_TRADING_TEXT } from '../../locales/featureText';

type Props = {
  points: EquityPoint[];
  language: UiLanguage;
};

function formatDate(value: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  return m ? `${m[2]}/${m[3]}` : value;
}

export const EquityCurveChart: React.FC<Props> = ({ points, language }) => {
  const text = PAPER_TRADING_TEXT[language];
  if (!points || points.length === 0) {
    return (
      <div className="flex h-64 flex-col items-center justify-center text-sm text-secondary-text">
        {text.noCurveTitle}
      </div>
    );
  }

  return (
    <div className="h-64 min-h-64 w-full">
      {/* 高度由布局固定给出（h-64 = 256px），宽度要等 ResizeObserver 测量后才知道，
          因此宽度传 0 表示「尺寸未知」——效果与 recharts 自己的默认值 -1 一样（测到之前
          不渲染图表），但不会每次挂载都往控制台打一条 dev-only 的 width(-1) 告警。 */}
      <ResponsiveContainer width="100%" height="100%" initialDimension={{ width: 0, height: 256 }}>
        <AreaChart data={points} margin={{ top: 12, right: 12, bottom: 8, left: 4 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.25} />
              <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="rgba(148, 163, 184, 0.18)" vertical={false} />
          <XAxis
            dataKey="tradeDate"
            tickFormatter={formatDate}
            tick={{ fontSize: 11, fill: 'currentColor' }}
            minTickGap={24}
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fontSize: 11, fill: 'currentColor' }}
            width={70}
          />
          <ChartTooltip
            formatter={(value) => [Number(value ?? 0).toLocaleString('zh-CN'), text.totalAssets]}
            labelFormatter={(label) => formatDate(String(label))}
          />
          <Area
            type="monotone"
            dataKey="netValue"
            stroke="hsl(var(--primary))"
            strokeWidth={2}
            fill="url(#equityFill)"
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
};
