import React, { useEffect, useRef, useState } from 'react';
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Card, Loading } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import { cn } from '../../utils/cn';
import { getReportText, normalizeReportLanguage } from '../../utils/reportLanguage';
import type { ReportLanguage } from '../../types/analysis';
import { stocksApi, type KLineItem } from '../../api/stocks';

interface StockPriceChartProps {
  stockCode?: string;
  language?: ReportLanguage;
  className?: string;
}

/**
 * 个股报告 K 线 + 成交量卡片。
 *
 * 数据源为现成的 `GET /api/v1/stocks/{code}/history?period=daily&days=N`（KLineData），
 * 不需要给报告 payload 新增 OHLC 字段。复用 PortfolioPage 的 ref + ResizeObserver 测量
 * 包装（避开 recharts ResponsiveContainer 首帧 null 的“弹开”闪烁），`isAnimationActive={false}`。
 * 涨绿跌红用项目语义 token：`hsl(var(--success))` / `hsl(var(--danger))`。
 *
 * 加载态由 `state.code` 是否等于当前 `stockCode` 派生，而不是靠 effect 里同步 setState。
 */

/** 蜡烛图实体：recharts 按 dataKey=close 与 y 轴底边（domainMin）计算 rect，再用两点锚定映射全区间。 */
function renderCandle(domainMin: number) {
  return (props: {
    x?: number;
    y?: number;
    width?: number;
    height?: number;
    payload?: KLineItem;
  }) => {
    const { x, y, width, height, payload } = props;
    if (payload == null || x == null || y == null || width == null || height == null) {
      return null;
    }
    const { open, high, low, close } = payload;
    const up = close >= open;
    const color = up ? 'hsl(var(--success))' : 'hsl(var(--danger))';
    const cx = x + width / 2;
    const bodyW = Math.max(width * 0.6, 1);
    const bodyX = cx - bodyW / 2;
    // rect 从 close 画到 y 轴最小值（domainMin）：像素比例 = height / (close - domainMin)。
    const scale = close - domainMin > 0 ? height / (close - domainMin) : 0;
    const toY = (v: number) => y + (close - v) * scale;
    const yHigh = toY(high);
    const yLow = toY(low);
    const yOpen = toY(open);
    const yClose = toY(close);
    const bodyTop = Math.min(yOpen, yClose);
    const bodyH = Math.max(Math.abs(yOpen - yClose), 1);
    return (
      <g data-testid="stock-candle">
        <line x1={cx} x2={cx} y1={yHigh} y2={yLow} stroke={color} strokeWidth={1} />
        <rect x={bodyX} y={bodyTop} width={bodyW} height={bodyH} fill={color} />
      </g>
    );
  };
}

const formatDateTick = (value?: string): string => value?.slice(5) ?? '';

export const StockPriceChart: React.FC<StockPriceChartProps> = ({
  stockCode,
  language = 'zh',
  className,
}) => {
  const reportLanguage = normalizeReportLanguage(language);
  const text = getReportText(reportLanguage);
  const containerRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [state, setState] = useState<{ code?: string; items: KLineItem[] }>({ items: [] });

  useEffect(() => {
    if (typeof ResizeObserver !== 'undefined' && containerRef.current) {
      const el = containerRef.current;
      const measure = () => {
        const rect = el.getBoundingClientRect();
        const width = Math.round(rect.width);
        const height = Math.round(rect.height);
        setSize((prev) => (prev.width === width && prev.height === height ? prev : { width, height }));
      };
      const observer = new ResizeObserver(measure);
      observer.observe(el);
      measure();
      return () => observer.disconnect();
    }
  }, []);

  useEffect(() => {
    if (!stockCode) return;
    let active = true;
    const run = async () => {
      try {
        const history = await stocksApi.getStockHistory(stockCode, 60);
        if (active) setState({ code: stockCode, items: history.data });
      } catch {
        if (active) setState({ code: stockCode, items: [] });
      }
    };
    void run();
    return () => {
      active = false;
    };
  }, [stockCode]);

  const ready = state.code === stockCode;
  const isEmpty = ready && state.items.length === 0;

  const renderChart = () => {
    const lowPrices = state.items.map((item) => item.low);
    const highPrices = state.items.map((item) => item.high);
    const minLow = Math.min(...lowPrices);
    const maxHigh = Math.max(...highPrices);
    const pad = (maxHigh - minLow) * 0.05 || 1;
    const domainMin = Math.max(minLow - pad, 0);
    const domainMax = maxHigh + pad;
    const maxVolume = Math.max(...state.items.map((item) => item.volume ?? 0), 1);
    const candleShape = renderCandle(domainMin);

    const priceHeight = Math.max(Math.round(size.height * 0.72), 120);
    const volumeHeight = Math.max(size.height - priceHeight - 10, 40);

    return (
      <div className="flex flex-col gap-2" data-testid="stock-price-chart">
        <ComposedChart
          data={state.items}
          width={size.width}
          height={priceHeight}
          margin={{ top: 6, right: 8, bottom: 0, left: 0 }}
        >
          <CartesianGrid stroke="rgba(148, 163, 184, 0.18)" vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatDateTick}
            tick={{ fontSize: 10, fill: 'currentColor' }}
            tickLine={false}
            axisLine={false}
            minTickGap={28}
          />
          <YAxis
            yAxisId="price"
            domain={[domainMin, domainMax]}
            width={52}
            tick={{ fontSize: 10, fill: 'currentColor' }}
            tickLine={false}
            axisLine={false}
          />
          <Tooltip cursor={{ stroke: 'rgba(148, 163, 184, 0.35)', strokeDasharray: '3 3' }} />
          <Bar yAxisId="price" dataKey="close" shape={candleShape} isAnimationActive={false} />
        </ComposedChart>

        <ComposedChart
          data={state.items}
          width={size.width}
          height={volumeHeight}
          margin={{ top: 0, right: 8, bottom: 0, left: 52 }}
        >
          <XAxis
            dataKey="date"
            tickFormatter={formatDateTick}
            tick={{ fontSize: 10, fill: 'currentColor' }}
            tickLine={false}
            axisLine={false}
            minTickGap={28}
          />
          <YAxis yAxisId="vol" domain={[0, maxVolume]} width={52} hide />
          <Bar
            yAxisId="vol"
            dataKey="volume"
            fill="hsl(var(--primary))"
            opacity={0.25}
            isAnimationActive={false}
          />
        </ComposedChart>
      </div>
    );
  };

  const hasSize = size.width > 0 && size.height > 0;

  if (!stockCode) {
    return null;
  }

  return (
    <Card variant="bordered" padding="md" className={cn('home-panel-card', className)}>
      <DashboardPanelHeader
        eyebrow={text.transparency}
        title={text.priceChart}
        className="mb-2"
      />
      <div ref={containerRef} className="h-80 w-full">
        {!ready || !hasSize ? (
          <div data-testid="stock-price-chart-loading" className="flex h-full items-center justify-center">
            <Loading className="h-20" />
          </div>
        ) : isEmpty ? (
          <div data-testid="stock-price-chart-empty" className="flex h-full items-center justify-center px-3 text-center text-xs text-secondary-text">
            {text.noPriceData}
          </div>
        ) : (
          renderChart()
        )}
      </div>
    </Card>
  );
};

export default StockPriceChart;
