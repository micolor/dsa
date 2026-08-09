import { useEffect, useMemo } from 'react';
import { useShallow } from 'zustand/react/shallow';
import { FloatingTaskPanel } from './FloatingTaskPanel';
import { screeningApi } from '../../api/screening';
import { useTaskStream } from '../../hooks/useTaskStream';
import { useStockPoolStore } from '../../stores/stockPoolStore';
import {
  toScreenTaskInfo,
  useScreeningTaskStore,
} from '../../stores/screeningTaskStore';
import type { TaskInfo } from '../../types/analysis';

/** 与分析任务共享的轮询间隔：补齐在途任务、修剪已完成任务。 */
const ACTIVE_TASK_POLL_INTERVAL_MS = 30_000;
/** 选股任务轮询：页面卸载后仍保持图标进度准确，完成/失败时清除。 */
const SCREEN_TASK_POLL_INTERVAL_MS = 3_000;

/**
 * 全局任务中心（常驻 Shell）。
 *
 * 把「分析任务（SSE + stockPoolStore）」与「选股任务（选股页写入 screeningTaskStore）」
 * 聚合到左侧任务图标（FloatingTaskPanel），让任何页面都能通过图标查看任务执行进度。
 * 它只负责任务侧：SSE 同步、定时补齐/修剪、聚合渲染；history/stockBar 等首页数据刷新仍由
 * HomePage 的 useDashboardLifecycle 负责（两者共享同一个 SSE 单例，store 同步按 taskId 幂等）。
 */
export const GlobalTaskCenter: React.FC = () => {
  const activeTasks = useStockPoolStore(
    useShallow((state) => state.activeTasks),
  );
  const activeScreenTask = useScreeningTaskStore((state) => state.activeScreenTask);
  const screenTaskId = activeScreenTask?.taskId ?? null;

  // 选股任务轮询：即使选股页已卸载，也保持图标进度准确，并在完成/失败时清除。
  useEffect(() => {
    if (!screenTaskId) {
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const task = await screeningApi.getScreenTask(screenTaskId);
        if (!active) {
          return;
        }
        if (task.status === 'completed' || task.status === 'failed') {
          useScreeningTaskStore.getState().clearScreenTask();
          return;
        }
        if (task.status === 'pending' || task.status === 'processing') {
          const current = useScreeningTaskStore.getState().activeScreenTask;
          const nextProgress = Number(task.progress ?? 0);
          useScreeningTaskStore.getState().setScreenTask({
            taskId: screenTaskId,
            title: current?.title ?? '选股任务',
            progress: Number.isFinite(nextProgress) ? nextProgress : (current?.progress ?? 0),
            message: task.message || current?.message || '',
            status: task.status,
          });
        }
      } catch {
        // 轮询暂时不可达：保持当前进度，稍后重试。
      } finally {
        if (active) {
          timer = window.setTimeout(poll, SCREEN_TASK_POLL_INTERVAL_MS);
        }
      }
    };
    void poll();
    return () => {
      active = false;
      if (timer) {
        window.clearTimeout(timer);
      }
    };
  }, [screenTaskId]);

  // SSE：让分析任务在任何路由实时流入 stockPoolStore。
  useTaskStream({
    onTaskCreated: (task) => useStockPoolStore.getState().syncTaskCreated(task),
    onTaskStarted: (task) => useStockPoolStore.getState().syncTaskUpdated(task),
    onTaskProgress: (task) => useStockPoolStore.getState().syncTaskUpdated(task),
    onTaskCompleted: (task) => useStockPoolStore.getState().syncTaskUpdated(task),
    onTaskFailed: (task) => useStockPoolStore.getState().syncTaskFailed(task),
    onConnected: () => {
      void useStockPoolStore.getState().refreshActiveTasks();
    },
  });

  // 挂载 + 定期 + 页面重新可见时补齐在途任务、修剪已完成任务。
  useEffect(() => {
    void useStockPoolStore.getState().refreshActiveTasks();

    const intervalId = window.setInterval(() => {
      void useStockPoolStore.getState().refreshActiveTasks();
    }, ACTIVE_TASK_POLL_INTERVAL_MS);

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void useStockPoolStore.getState().refreshActiveTasks();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  const mergedTasks = useMemo<TaskInfo[]>(() => {
    if (!activeScreenTask) {
      return activeTasks;
    }
    return [...activeTasks, toScreenTaskInfo(activeScreenTask)];
  }, [activeScreenTask, activeTasks]);

  return <FloatingTaskPanel tasks={mergedTasks} />;
};

export default GlobalTaskCenter;
