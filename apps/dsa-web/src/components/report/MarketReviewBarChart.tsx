import type React from 'react';
import { cn } from '../../utils/cn';

/**
 * 首页大盘复盘"结构化大盘数据"卡的轻量可视化条形组件。
 *
 * 三组图表均用纯 CSS 宽度条实现（无 recharts / 无 ResponsiveContainer）：
 * - 无需 JS 测量尺寸，天然自适应容器，规避 recharts 首帧 null 导致的"弹开"闪烁；
 * - 小尺寸、多实例场景下比引入整套图表库更轻、更稳。
 *
 * 颜色统一使用项目语义 token：涨=`text-success`、跌=`text-danger`。
 */

export interface MarketBarRow {
  name: string;
  /** 用于归一化的数值（如 changePct），可能缺失 */
  value: number | null;
  /** 预格式化的展示文本（如 `+4.20%`），由调用方传入保持一致 */
  label: string;
}

const clampPercent = (value: number): number => Math.min(Math.max(value, 0), 100);

/**
 * 上涨 / 下跌家数对比双段占比条。
 * 左段绿色（上涨）、右段红色（下跌），长度按两者之和归一化。
 */
export const BreadthBar: React.FC<{
  upCount: number;
  downCount: number;
  ariaLabel: string;
  className?: string;
}> = ({ upCount, downCount, ariaLabel, className }) => {
  const total = Math.max(upCount + downCount, 1);
  const upPercent = clampPercent((upCount / total) * 100);

  return (
    <div
      role="img"
      aria-label={ariaLabel}
      data-testid="market-breadth-bar"
      className={cn('flex h-2.5 w-full overflow-hidden rounded-full', className)}
    >
      <div
        data-direction="up"
        className="h-full bg-success transition-[width] duration-500"
        style={{ width: `${upPercent}%` }}
      />
      <div
        data-direction="down"
        className="h-full bg-danger transition-[width] duration-500"
        style={{ width: `${clampPercent(100 - upPercent)}%` }}
      />
    </div>
  );
};

/**
 * 指数涨跌幅迷你横向条（内嵌到表格"涨跌幅"单元格使用）。
 * 以中线 0 为原点：正向绿、负向红，长度按给定最大绝对值归一化。
 * 紧凑条形，不重复渲染名称 / 百分比文本（由调用方表格已有文本承载，避免重复节点）。
 */
export const MiniChangeBar: React.FC<{
  value: number | null;
  maxAbs: number;
  className?: string;
}> = ({ value, maxAbs, className }) => {
  const scale = maxAbs > 0 ? maxAbs : 1;
  const width = value === null ? 0 : (Math.abs(value) / scale) * 50;
  const isNegative = value !== null && value < 0;

  return (
    <span className={cn('inline-flex h-2 min-w-10 overflow-hidden rounded-full bg-subtle align-middle', className)}>
      <span className="relative h-full w-full" data-testid="market-index-bar">
        <span className="absolute inset-y-0 left-1/2 w-px bg-border" />
        {value !== null && (
          <span
            data-direction={isNegative ? 'down' : 'up'}
            className={cn('absolute inset-y-0 bg-success', isNegative && 'bg-danger')}
            style={
              isNegative
                ? { right: '50%', width: `${Math.max(width, 2)}%` }
                : { left: '50%', width: `${Math.max(width, 2)}%` }
            }
          />
        )}
      </span>
    </span>
  );
};

/**
 * 板块 / 概念 Top5 横向条组。
 * 每行：名称 + 按 `|changePct|` 归一化的条 + 涨跌幅文本。
 * `direction` 决定整组配色（top 组绿、bottom 组红），长度组内归一化。
 */
export const SectorBarList: React.FC<{
  rows: MarketBarRow[];
  direction: 'up' | 'down';
  className?: string;
}> = ({ rows, direction, className }) => {
  const maxAbs = rows.reduce(
    (max, row) => Math.max(max, row.value === null ? 0 : Math.abs(row.value)),
    0,
  );
  const scale = maxAbs > 0 ? maxAbs : 1;

  return (
    <div className={cn('space-y-1.5', className)}>
      {rows.map((row) => {
        const width = row.value === null ? 0 : (Math.abs(row.value) / scale) * 100;
        return (
          <div
            key={row.name}
            data-testid="market-sector-bar"
            className="flex items-center gap-2 text-sm"
          >
            <span className="min-w-0 flex-1 truncate text-foreground">{row.name}</span>
            <div className="relative h-2 w-14 shrink-0 overflow-hidden rounded-full bg-subtle">
              <div
                data-direction={direction}
                className={cn(
                  'absolute inset-y-0 left-0 bg-success transition-[width] duration-500',
                  direction === 'down' && 'bg-danger',
                )}
                style={{ width: `${width}%` }}
              />
            </div>
            <span
              className={cn(
                'w-16 shrink-0 text-right tabular-nums text-secondary-text',
                direction === 'up' ? 'text-success' : 'text-danger',
              )}
            >
              {row.label}
            </span>
          </div>
        );
      })}
    </div>
  );
};
