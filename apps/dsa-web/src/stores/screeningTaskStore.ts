import { create } from 'zustand';
import type { TaskInfo } from '../types/analysis';

/**
 * 选股任务的活跃态（仅 pending / processing 会上报）。
 *
 * 选股任务与分析任务使用不同的后端任务系统：前者在 /api/v1/screening/screen/tasks，
 * 页面本地 2s 轮询；后者由 SSE + 全局 stockPoolStore 承载。为了让选股任务的进度也能
 * 在左侧全局任务图标（FloatingTaskPanel）中展示，这里提供一个极小的共享 store，
 * 由选股页在提交 / 轮询 / 结束时写入，GlobalTaskCenter 统一读取并归一化到 TaskInfo。
 */
export type ScreenProgressTask = {
  taskId: string;
  traceId?: string;
  /** 面板里展示的可读标题，如「双低 选股」 */
  title: string;
  progress: number;
  message: string;
  status: 'pending' | 'processing';
};

type ScreeningTaskState = {
  activeScreenTask: ScreenProgressTask | null;
  setScreenTask: (task: ScreenProgressTask) => void;
  clearScreenTask: () => void;
};

export const useScreeningTaskStore = create<ScreeningTaskState>((set) => ({
  activeScreenTask: null,
  setScreenTask: (task) => set({ activeScreenTask: task }),
  clearScreenTask: () => set({ activeScreenTask: null }),
}));

/**
 * 把选股任务的活跃态归一化到 TaskInfo，复用 FloatingTaskPanel / TaskPanel 的现有契约。
 * - taskId 透传真实 taskId（不加前缀），保证点击可正常打开执行详情；面板聚合时由 GlobalTaskCenter
 *   剔除同一选股任务经 SSE 混入分析任务列表的重复条目，避免同一任务重复展示；
 * - analysisPhase 不设 → TaskItem 的 phase badge 不渲染（getRequestedPhaseLabel 对空值返回 null）；
 * - status 仅透传活跃态（pending / processing），完成/失败由选股页直接 clear。
 */
export const toScreenTaskInfo = (task: ScreenProgressTask): TaskInfo => ({
  taskId: task.taskId,
  stockCode: '选股',
  stockName: task.title || '选股',
  status: task.status,
  progress: task.progress,
  message: task.message,
  reportType: 'screening',
  createdAt: '',
  ...(task.traceId ? { traceId: task.traceId } : {}),
});
