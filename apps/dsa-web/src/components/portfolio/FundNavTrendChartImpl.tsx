import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from 'recharts';

export type FundNavTrendBasis = 'adjusted' | 'unit' | 'acc';

export interface FundNavTrendPoint {
  date: string;
  unit: number;
  acc: number;
  adjusted: number | null;
}

interface Props {
  points: FundNavTrendPoint[];
  basis: FundNavTrendBasis;
  height: number;
}

const formatDateTick = (value?: string): string => value?.slice(5) ?? '';

const formatNav = (value: unknown): string => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toFixed(4) : '--';
};

/**
 * recharts 渲染层，由 `FundNavTrendChart` 懒加载。
 *
 * 单独成文件是为了让 recharts 不进首屏 bundle：父组件负责请求、口径切换与
 * 错误/空态，这里只把已就绪的数据画成折线。
 */
export default function FundNavTrendChartImpl({ points, basis, height }: Props) {
  return (
    <div className="w-full" style={{ height }}>
      <ResponsiveContainer
        width="100%"
        height="100%"
        initialDimension={{ width: 0, height }}
      >
        <LineChart data={points} margin={{ top: 12, right: 16, bottom: 8, left: 4 }}>
          <CartesianGrid stroke="rgba(148, 163, 184, 0.18)" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDateTick}
            tick={{ fontSize: 11, fill: 'currentColor' }}
            minTickGap={24}
          />
          <YAxis
            domain={['auto', 'auto']}
            tick={{ fontSize: 11, fill: 'currentColor' }}
            tickFormatter={formatNav}
            width={64}
          />
          <ChartTooltip
            formatter={(value) => [formatNav(value), '']}
            labelFormatter={(label) => String(label ?? '')}
          />
          <Line
            type="monotone"
            dataKey={basis}
            dot={false}
            isAnimationActive={false}
            connectNulls={false}
            stroke="hsl(var(--primary))"
            strokeWidth={2}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
