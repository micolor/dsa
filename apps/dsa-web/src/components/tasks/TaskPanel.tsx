import type React from 'react';
import { useId, useState } from 'react';
import { ChevronDown, RefreshCw, Workflow } from 'lucide-react';
import { Badge, Button, Card, StatusDot, Tooltip } from '../common';
import { DashboardPanelHeader } from '../dashboard';
import type { TaskInfo } from '../../types/analysis';
import { getRequestedPhaseLabel } from '../../utils/marketPhase';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

/**
 * 任务项组件属性
 */
interface TaskItemProps {
  task: TaskInfo;
  onOpenRunFlow?: (task: TaskInfo) => void;
}

/**
 * 单个任务项
 */
const TaskItem: React.FC<TaskItemProps> = ({ task, onOpenRunFlow }) => {
  const { language, t } = useUiLanguage();
  const isPending = task.status === 'pending';
  const isProcessing = task.status === 'processing';
  const isCancelRequested = task.status === 'cancel_requested';
  const isCancelled = task.status === 'cancelled';
  const statusLabel = isCancelRequested
    ? t('taskPanel.cancelRequested')
    : isCancelled
      ? t('taskPanel.cancelled')
      : isProcessing ? t('taskPanel.processing') : t('taskPanel.pending');
  const statusVariant = isCancelRequested ? 'warning' : isProcessing ? 'info' : 'default';
  const statusTone = isCancelRequested ? 'warning' : isProcessing ? 'info' : 'neutral';
  const progress = Math.max(0, Math.min(100, task.progress || 0));
  const requestedPhaseLabel = getRequestedPhaseLabel(task.analysisPhase, language);
  const requestedPhaseVariant = task.analysisPhase === 'auto' ? 'default' : 'info';

  return (
    <div className="home-subpanel grid min-w-0 gap-2.5 px-3 py-2.5" data-testid="task-panel-item">
      <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-start gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <div className="shrink-0">
            {isProcessing ? (
              <StatusDot tone="info" pulse className="h-2.5 w-2.5" aria-label={t('taskPanel.processingAria')} />
            ) : isCancelRequested ? (
              <StatusDot tone="warning" pulse className="h-2.5 w-2.5" aria-label={t('taskPanel.cancelRequestedAria')} />
            ) : isPending ? (
              <StatusDot tone="neutral" className="h-2.5 w-2.5" aria-label={t('taskPanel.pendingAria')} />
            ) : null}
          </div>

          <div className="min-w-0">
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="max-w-full truncate text-sm font-medium text-foreground">
                {task.stockName || task.stockCode}
              </span>
              <span className="shrink-0 text-xs text-muted-text">
                {task.stockCode}
              </span>
            </div>
          </div>
        </div>

        <div className="relative z-10 flex shrink-0 items-center gap-1.5">
          {requestedPhaseLabel ? (
            <Badge
              variant={requestedPhaseVariant}
              className="shrink-0 shadow-none"
              aria-label={requestedPhaseLabel}
            >
              {requestedPhaseLabel}
            </Badge>
          ) : null}
          <Badge
            variant={statusVariant}
            className="min-w-[4.75rem] max-w-[7rem] justify-center gap-1.5 whitespace-nowrap shadow-none"
            aria-label={t('taskPanel.statusAria', { status: statusLabel })}
          >
            <StatusDot tone={statusTone} pulse={isProcessing || isCancelRequested} className="h-1.5 w-1.5 shrink-0" />
            <span className="min-w-0 truncate">{statusLabel}</span>
          </Badge>
          {onOpenRunFlow ? (
            <Tooltip content={t('taskPanel.openRunFlow')}>
              <span className="inline-flex">
                <Button
                  type="button"
                  variant="ghost"
                  size="xsm"
                  className="h-8 w-8 px-0"
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpenRunFlow(task);
                  }}
                  aria-label={t('taskPanel.openRunFlowAria', {
                    stock: task.stockName || task.stockCode,
                  })}
                >
                  <Workflow className="h-4 w-4" aria-hidden="true" />
                </Button>
              </span>
            </Tooltip>
          ) : null}
        </div>
      </div>

      {task.message ? (
        <p className="min-w-0 truncate text-xs text-secondary-text">
          {task.message}
        </p>
      ) : null}

      <div className="flex min-w-0 items-center gap-2">
        <div className="h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white/8">
          <div
            className="h-full rounded-full bg-cyan transition-[width] duration-300 ease-out"
            style={{ width: `${progress}%` }}
          />
        </div>
        <span className="shrink-0 text-[11px] text-muted-text tabular-nums">
          {progress}%
        </span>
      </div>

    </div>
  );
};

/**
 * 任务面板属性
 */
interface TaskPanelProps {
  /** 任务列表 */
  tasks: TaskInfo[];
  /** 是否显示 */
  visible?: boolean;
  /** 标题 */
  title?: string;
  /** 自定义类名 */
  className?: string;
  /** 打开运行流面板 */
  onOpenRunFlow?: (task: TaskInfo) => void;
  /** 是否折叠 */
  collapsed?: boolean;
  /** 折叠状态变化 */
  onCollapsedChange?: (collapsed: boolean) => void;
}

/**
 * 任务面板组件
 * 显示进行中的分析任务列表
 */
export const TaskPanel: React.FC<TaskPanelProps> = ({
  tasks,
  visible = true,
  title,
  className = '',
  onOpenRunFlow,
  collapsed,
  onCollapsedChange,
}) => {
  const { t } = useUiLanguage();
  const contentId = useId();
  const [internalCollapsed, setInternalCollapsed] = useState(false);
  // 筛选活跃任务（pending / processing / cancel requested）
  const activeTasks = tasks.filter(
    (t) => t.status === 'pending' || t.status === 'processing' || t.status === 'cancel_requested'
  );
  const isCollapsed = collapsed ?? internalCollapsed;

  // 无任务或不可见时不渲染
  if (!visible || activeTasks.length === 0) {
    return null;
  }

  const pendingCount = activeTasks.filter((t) => t.status === 'pending').length;
  const processingCount = activeTasks.filter((t) => t.status === 'processing').length;
  const cancelRequestedCount = activeTasks.filter((t) => t.status === 'cancel_requested').length;
  const averageProgress = processingCount > 0
    ? Math.round(
      activeTasks
        .filter((t) => t.status === 'processing')
        .reduce((total, task) => total + Math.max(0, Math.min(100, task.progress || 0)), 0) / processingCount,
    )
    : 0;

  const handleCollapsedChange = () => {
    const nextCollapsed = !isCollapsed;
    if (collapsed === undefined) {
      setInternalCollapsed(nextCollapsed);
    }
    onCollapsedChange?.(nextCollapsed);
  };

  return (
    <Card
      variant="bordered"
      padding="none"
      className={`home-panel-card glass-surface shrink-0 overflow-hidden ${className}`}
    >
      <div className="px-3 py-3">
        <DashboardPanelHeader
          className="mb-0"
          title={title ?? t('taskPanel.title')}
          titleClassName="text-sm font-medium"
          leading={(
            <RefreshCw className="h-4 w-4 text-cyan" aria-hidden="true" />
          )}
          headingClassName="items-center"
          actions={(
            <div className="flex items-center gap-1.5">
              <div className="hidden items-center gap-2 text-xs text-muted-text sm:flex">
                {processingCount > 0 && (
                  <span className="flex items-center gap-1">
                    <StatusDot tone="info" pulse className="h-1.5 w-1.5" />
                    {t('taskPanel.processingTasks', { count: processingCount })}
                  </span>
                )}
                {pendingCount > 0 ? (
                  <span className="flex items-center gap-1">
                    <StatusDot tone="neutral" className="h-1.5 w-1.5" />
                    {t('taskPanel.pendingTasks', { count: pendingCount })}
                  </span>
                ) : null}
              </div>
              <Button
                type="button"
                variant="ghost"
                size="xsm"
                className="h-7 w-7 px-0"
                aria-expanded={!isCollapsed}
                aria-controls={contentId}
                aria-label={isCollapsed ? t('taskPanel.expand') : t('taskPanel.collapse')}
                onClick={handleCollapsedChange}
              >
                <ChevronDown
                  className={`h-4 w-4 transition-transform ${isCollapsed ? '-rotate-90' : ''}`}
                  aria-hidden="true"
                />
              </Button>
            </div>
          )}
        />
      </div>

      {isCollapsed ? (
        <div
          id={contentId}
          className="border-t border-subtle px-3 py-2.5 text-xs text-muted-text"
          data-testid="task-panel-collapsed-summary"
        >
          <div className="flex flex-wrap items-center gap-2">
            {processingCount > 0 ? (
              <span>{t('taskPanel.processingTasks', { count: processingCount })}</span>
            ) : null}
            {pendingCount > 0 ? (
              <span>{t('taskPanel.pendingTasks', { count: pendingCount })}</span>
            ) : null}
            {cancelRequestedCount > 0 ? (
              <span>{t('taskPanel.cancelRequestedTasks', { count: cancelRequestedCount })}</span>
            ) : null}
            {processingCount > 0 ? (
              <span>{t('taskPanel.averageProgress', { progress: averageProgress })}</span>
            ) : null}
          </div>
        </div>
      ) : (
        <div id={contentId} className="max-h-64 overflow-y-auto border-t border-subtle p-2">
          <div className="space-y-2">
            {activeTasks.map((task) => (
              <TaskItem key={task.taskId} task={task} onOpenRunFlow={onOpenRunFlow} />
            ))}
          </div>
        </div>
      )}
    </Card>
  );
};

export default TaskPanel;
