import { useEffect, useRef } from 'react';
import type { TaskInfo } from '../types/analysis';
import { useTaskStream } from './useTaskStream';

type UseDashboardLifecycleOptions = {
  loadInitialHistory: () => Promise<void>;
  refreshHistory: (silent?: boolean) => Promise<void>;
  refreshHistoryForCompletedTask?: (task: TaskInfo) => Promise<void>;
  loadStockBar: () => Promise<void>;
  /** silent=true 用于后台刷新，不进入加载假态。 */
  refreshStockBar: (silent?: boolean) => Promise<void>;
  loadMarketReviewHistory?: () => Promise<void>;
  // 返回值由调用方按需使用（如取刚落库的大盘复盘记录 ID），生命周期本身不消费它。
  refreshMarketReviewHistory?: (silent?: boolean) => Promise<unknown>;
  syncTaskCreated: (task: TaskInfo) => void;
  syncTaskUpdated: (task: TaskInfo) => void;
  syncTaskFailed: (task: TaskInfo) => void;
  removeTask: (taskId: string) => void;
  onDashboardDataRefresh?: () => void;
  onCompletedTaskDataRefreshStarted?: (task: TaskInfo) => void;
  onCompletedTaskDataRefreshed?: (task: TaskInfo) => void;
  enabled?: boolean;
};

export function useDashboardLifecycle({
  loadInitialHistory,
  refreshHistory,
  refreshHistoryForCompletedTask,
  loadStockBar,
  refreshStockBar,
  loadMarketReviewHistory,
  refreshMarketReviewHistory,
  syncTaskCreated,
  syncTaskUpdated,
  syncTaskFailed,
  removeTask,
  onDashboardDataRefresh,
  onCompletedTaskDataRefreshStarted,
  onCompletedTaskDataRefreshed,
  enabled = true,
}: UseDashboardLifecycleOptions): void {
  const removalTimeoutsRef = useRef<number[]>([]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    void loadInitialHistory();
    void loadStockBar();
    void loadMarketReviewHistory?.();
  }, [enabled, loadInitialHistory, loadMarketReviewHistory, loadStockBar]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const intervalId = window.setInterval(() => {
      void refreshHistory(true);
      void refreshStockBar(true);
      void refreshMarketReviewHistory?.(true);
      onDashboardDataRefresh?.();
    }, 30_000);

    return () => window.clearInterval(intervalId);
  }, [enabled, onDashboardDataRefresh, refreshHistory, refreshMarketReviewHistory, refreshStockBar]);

  useEffect(() => {
    if (!enabled) {
      return;
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        void refreshHistory(true);
        void refreshStockBar(true);
        void refreshMarketReviewHistory?.(true);
        onDashboardDataRefresh?.();
      }
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [enabled, onDashboardDataRefresh, refreshHistory, refreshMarketReviewHistory, refreshStockBar]);

  useEffect(() => {
    return () => {
      removalTimeoutsRef.current.forEach((timeoutId) => window.clearTimeout(timeoutId));
      removalTimeoutsRef.current = [];
    };
  }, []);

  const scheduleTaskRemoval = (taskId: string, delayMs: number) => {
    const timeoutId = window.setTimeout(() => {
      removeTask(taskId);
      removalTimeoutsRef.current = removalTimeoutsRef.current.filter((item) => item !== timeoutId);
    }, delayMs);

    removalTimeoutsRef.current.push(timeoutId);
  };

  useTaskStream({
    onTaskCreated: syncTaskCreated,
    onTaskStarted: syncTaskUpdated,
    onTaskProgress: syncTaskUpdated,
    onTaskCompleted: (task) => {
      syncTaskUpdated(task);
      onCompletedTaskDataRefreshStarted?.(task);
      const historyRefresh = refreshHistoryForCompletedTask
        ? refreshHistoryForCompletedTask(task)
        : refreshHistory(true);
      const stockBarRefresh = refreshStockBar(true);
      void Promise.allSettled([historyRefresh, stockBarRefresh]).then(() => {
        onCompletedTaskDataRefreshed?.(task);
      });
      void refreshMarketReviewHistory?.(true);
      scheduleTaskRemoval(task.taskId, 2_000);
    },
    onTaskFailed: (task) => {
      syncTaskFailed(task);
      scheduleTaskRemoval(task.taskId, 5_000);
    },
    onError: (error) => {
      console.warn('SSE connection disconnected, reconnecting...', error);
    },
    enabled,
  });
}

export default useDashboardLifecycle;
