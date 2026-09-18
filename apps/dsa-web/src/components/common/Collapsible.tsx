import React, { useState } from 'react';
import { cn } from '../../utils/cn';

interface CollapsibleProps {
  title: string;
  children: React.ReactNode;
  defaultOpen?: boolean;
  /** 传入即为受控模式；展开状态由父组件持有。 */
  open?: boolean;
  /** 受控模式下点击标题栏时回调期望的新状态。 */
  onOpenChange?: (open: boolean) => void;
  icon?: React.ReactNode;
  className?: string;
  /**
   * 展开后不设高度上限，并把面板变成真正的滚动容器（内容超过视口高度时在面板内滚动）。
   *
   * 默认 `false`：既有消费方（如 MarketReviewReportView）仍走 `max-h-[2000px]` 的行为，
   * 不因为本次改动改变观感。只有在这之上放不确定高度内容（例如待处理区的「观察」档、
   * 统计区的 skill × 窗口表）的调用方才需要打开它，否则超出 2000px 的内容会被
   * `overflow-hidden` 静默裁掉且无法滚动到。
   *
   * 代价：`max-h-none` 不可插值，展开/收起不再有 max-height 过渡。
   */
  scrollable?: boolean;
}

/**
 * Collapsible panel with animated expand and collapse behavior.
 *
 * 同时支持受控与非受控：不传 `open` 时用内部 state（原行为），传了 `open` 时
 * 完全由父组件决定展开状态，本组件只通过 `onOpenChange` 上报点击意图。
 * 父组件需要「未展开不挂载子内容」时用受控模式，非受控模式的子节点始终挂载。
 * 展开后内容可能超过 2000px 的调用方需要显式打开 `scrollable`（见该 prop 的说明）。
 */
export const Collapsible: React.FC<CollapsibleProps> = ({
  title,
  children,
  defaultOpen = false,
  open,
  onOpenChange,
  icon,
  className = '',
  scrollable = false,
}) => {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const isControlled = open !== undefined;
  const isOpen = isControlled ? open : uncontrolledOpen;

  const handleToggle = () => {
    if (!isControlled) {
      setUncontrolledOpen(!isOpen);
    }
    onOpenChange?.(!isOpen);
  };

  // 展开态的高度类：默认沿用既有的 2000px 上限（不动既有消费方的观感），
  // `scrollable` 时去掉上限并改由内层滚动容器限制高度。
  const expandedPanelClass = scrollable ? 'max-h-none opacity-100' : 'max-h-[2000px] opacity-100';

  return (
    <div
      className={cn(
        'overflow-hidden rounded-2xl border border-subtle bg-card/70 shadow-soft-card transition-[background-color,box-shadow] duration-300',
        'hover:border-accent',
        className,
      )}
    >
      <button
        type="button"
        onClick={handleToggle}
        // 展开态此前只由箭头 icon 的旋转表达，读屏用户拿不到状态；
        // 仓库其他折叠组件（RunFlowNodeDetails / RunFlowGraph / TaskPanel）都带该属性。
        aria-expanded={isOpen}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-hover"
      >
        <div className="flex items-center gap-3">
          {icon && <span className="text-cyan">{icon}</span>}
          <span className="font-medium text-foreground">{title}</span>
        </div>
        <svg
          className={cn('h-5 w-5 text-secondary-text transition-transform duration-300', isOpen && 'rotate-180')}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      <div
        data-testid="collapsible-panel"
        className={cn(
          'overflow-hidden transition-[max-height,opacity] duration-300 ease-in-out',
          isOpen ? expandedPanelClass : 'max-h-0 opacity-0',
        )}
      >
        <div
          data-testid="collapsible-panel-body"
          className={cn(
            'border-t border-subtle px-4 pb-4 pt-2',
            // 真正的滚动容器：没有它时 `overflow-hidden` 会把超长内容裁掉且无法滚动到。
            scrollable && 'max-h-[70vh] overflow-y-auto',
          )}
        >
          {children}
        </div>
      </div>
    </div>
  );
};
