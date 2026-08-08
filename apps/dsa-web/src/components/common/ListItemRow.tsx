import type React from 'react';
import { Trash2 } from 'lucide-react';
import { Button } from './Button';

interface ListItemRowProps {
  /** 外层容器类名（含 row 表面样式与 group/item），如 home-history-item / home-subpanel */
  wrapperClassName?: string;
  /** 外层容器 data-testid（如 watchlist-row-{code}） */
  wrapperTestId?: string;
  /** 可点击按钮类名（含选中态与布局） */
  buttonClassName?: string;
  /** 可选 aria-pressed（仅自选条目需要） */
  pressed?: boolean;
  ariaLabel: string;
  onClick: () => void;
  /** 内容前的强调位（色条 / 状态图标） */
  leading?: React.ReactNode;
  /** 标题行左侧（名称，可带 tooltip / 状态图标） */
  title: React.ReactNode;
  /** 右上徽章区（ScoreBadge / 情绪徽章），删除按钮由本组件按需渲染 */
  trailing?: React.ReactNode;
  onDelete?: () => void;
  deleteAriaLabel?: string;
  deleteDisabled?: boolean;
  /** meta 行（代码·时间·阶段） */
  meta?: React.ReactNode;
  metaTestId?: string;
  actionsTestId?: string;
  /** 附加行（自选任务状态） */
  footer?: React.ReactNode;
}

/**
 * 列表条目共享展示骨架：一个可点击行，含 leading 强调位、标题行（标题 + 右上操作）、
 * meta 行与可选 footer 行。不包含任何业务数据派生，由各条目组件（自选 / 个股栏 / 历史）
 * 映射各自数据模型后使用。
 */
export const ListItemRow: React.FC<ListItemRowProps> = ({
  wrapperClassName = '',
  wrapperTestId,
  buttonClassName = 'w-full min-w-0 flex-1 text-left',
  pressed,
  ariaLabel,
  onClick,
  leading,
  title,
  trailing,
  onDelete,
  deleteAriaLabel,
  deleteDisabled,
  meta,
  metaTestId,
  actionsTestId,
  footer,
}) => (
  <div className={`group/item ${wrapperClassName}`.trim()} data-testid={wrapperTestId}>
    {/*
      Row is a div[role=button], not a <button>, so the per-row delete button below
      isn't nested inside another <button> (invalid HTML, hydration warnings, and
      unreliable click/focus handling for assistive tech). Keyboard activation is
      re-added below; key events only fire when the row itself is focused (target
      guard keeps the delete button's own keys from bubbling up to it).
    */}
    <div
      role="button"
      tabIndex={0}
      aria-pressed={pressed}
      aria-label={ariaLabel}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick();
        }
      }}
      className={`cursor-pointer ${buttonClassName}`.trim()}
    >
      <div className="relative z-10 flex items-center gap-2.5">
        {leading}
        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">{title}</div>
            <div className="flex shrink-0 items-center gap-1" data-testid={actionsTestId}>
              {trailing}
              {onDelete ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="xsm"
                  className="reveal-on-hover flex h-6 w-6 shrink-0 items-center justify-center p-0"
                  disabled={deleteDisabled}
                  aria-label={deleteAriaLabel}
                  onClick={(e) => {
                    e.stopPropagation();
                    onDelete();
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5 text-danger" aria-hidden="true" />
                </Button>
              ) : null}
            </div>
          </div>
          {meta ? (
            <div className="mt-1 flex flex-wrap items-center gap-2" data-testid={metaTestId}>
              {meta}
            </div>
          ) : null}
          {footer}
        </div>
      </div>
    </div>
  </div>
);
