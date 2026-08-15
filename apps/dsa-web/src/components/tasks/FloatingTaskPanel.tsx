import React, { useEffect, useRef, useState } from 'react';
import { ListChecks } from 'lucide-react';
import { TaskPanel } from './TaskPanel';
import type { TaskInfo } from '../../types/analysis';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

const ACTIVE_STATUSES = new Set(['pending', 'processing', 'cancel_requested']);

interface FloatingTaskPanelProps {
  /** 任务列表 */
  tasks: TaskInfo[];
  /** 打开运行流面板 */
  onOpenRunFlow?: (task: TaskInfo) => void;
}

/**
 * 悬浮任务面板
 * 有进行中任务时，在左上角导航菜单图标正下方显示一个任务图标按钮；hover 展开任务明细。
 * 按钮尺寸、左边距与样式和导航菜单按钮保持一致；交互同样沿用 mouseenter 展开 / mouseleave 延迟收起。
 */
export const FloatingTaskPanel: React.FC<FloatingTaskPanelProps> = ({ tasks, onOpenRunFlow }) => {
  const { t } = useUiLanguage();
  const [open, setOpen] = useState(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const activeTasks = tasks.filter((task) => ACTIVE_STATUSES.has(task.status));
  const activeCount = activeTasks.length;
  const processingCount = activeTasks.filter((task) => task.status === 'processing').length;

  const clearCloseTimer = () => {
    if (closeTimerRef.current !== null) {
      window.clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  };

  const openPanel = () => {
    clearCloseTimer();
    setOpen(true);
  };

  const scheduleClose = () => {
    clearCloseTimer();
    closeTimerRef.current = window.setTimeout(() => setOpen(false), 200);
  };

  useEffect(() => clearCloseTimer, []);

  // 任务短暂清空时收起面板，避免新任务出现时面板突然弹回造成闪动（渲染期状态调整）。
  const [prevActiveCount, setPrevActiveCount] = useState(activeCount);
  if (prevActiveCount !== activeCount) {
    setPrevActiveCount(activeCount);
    if (activeCount === 0 && open) {
      setOpen(false);
    }
  }

  if (activeCount === 0) {
    return null;
  }

  return (
    <div
      className="fixed bottom-4 left-0.5 z-[100] flex flex-col-reverse items-start gap-2"
      onMouseEnter={openPanel}
      onMouseLeave={scheduleClose}
    >
      <button
        type="button"
        onClick={() => setOpen((next) => !next)}
        className="relative inline-flex h-9 w-9 items-center justify-center rounded-xl border border-border/60 bg-card/60 text-secondary-text shadow-soft-card backdrop-blur-xl transition-colors hover:bg-hover hover:text-foreground"
        aria-label={t('taskPanel.openTasks', { count: activeCount })}
        aria-expanded={open}
        data-testid="floating-task-panel-button"
      >
        {processingCount > 0 ? (
          <span
            className="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 animate-pulse rounded-full bg-cyan"
            aria-hidden="true"
          />
        ) : null}
        <ListChecks className="h-4 w-4" aria-hidden="true" />
        {activeCount > 1 ? (
          <span
            className="absolute -bottom-1 -right-1 inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-primary px-1 text-[10px] font-semibold text-primary-foreground"
            aria-hidden="true"
          >
            {activeCount}
          </span>
        ) : null}
      </button>

      {open ? (
        <div className="w-[34rem] max-h-[min(24rem,calc(100vh-12rem))] overflow-hidden rounded-2xl">
          <TaskPanel tasks={tasks} onOpenRunFlow={onOpenRunFlow} />
        </div>
      ) : null}
    </div>
  );
};

export default FloatingTaskPanel;
