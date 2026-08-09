import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { GlobalTaskCenter } from '../GlobalTaskCenter';
import { useStockPoolStore } from '../../../stores/stockPoolStore';
import { useScreeningTaskStore } from '../../../stores/screeningTaskStore';
import type { TaskInfo } from '../../../types/analysis';

const { getTasks, getScreenTask } = vi.hoisted(() => ({
  getTasks: vi.fn(),
  getScreenTask: vi.fn(),
}));

vi.mock('../../../hooks/useTaskStream', () => ({
  useTaskStream: vi.fn(() => ({})),
}));

vi.mock('../../../api/analysis', () => ({
  analysisApi: { getTasks },
}));

vi.mock('../../../api/screening', () => ({
  screeningApi: { getScreenTask },
}));

const analysisTask: TaskInfo = {
  taskId: 'analysis-1',
  stockCode: '600519',
  stockName: '贵州茅台',
  status: 'processing',
  progress: 35,
  message: '分析中',
  reportType: 'detailed',
  createdAt: '2026-06-08T08:00:00Z',
};

describe('GlobalTaskCenter', () => {
  beforeEach(() => {
    useStockPoolStore.setState({ activeTasks: [] });
    useScreeningTaskStore.setState({ activeScreenTask: null });
    getTasks.mockReset();
    getScreenTask.mockReset();
  });

  it('merges analysis and screening tasks into one floating panel', async () => {
    getTasks.mockResolvedValue({
      total: 1,
      pending: 0,
      processing: 1,
      tasks: [analysisTask],
    });
    useStockPoolStore.setState({ activeTasks: [analysisTask] });
    useScreeningTaskStore.getState().setScreenTask({
      taskId: 'screen-1',
      title: '双低 选股',
      progress: 50,
      message: '正在执行因子评分',
      status: 'processing',
    });
    getScreenTask.mockResolvedValue({
      taskId: 'screen-1',
      status: 'processing',
      progress: 50,
      message: '正在执行因子评分',
      result: null,
    });

    render(<GlobalTaskCenter />);

    const button = await screen.findByTestId('floating-task-panel-button');
    fireEvent.mouseOver(button);

    const items = await screen.findAllByTestId('task-panel-item');
    expect(items).toHaveLength(2);
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('双低 选股')).toBeInTheDocument();
  });

  it('hides the icon when there are no active tasks', async () => {
    getTasks.mockResolvedValue({ total: 0, pending: 0, processing: 0, tasks: [] });
    getScreenTask.mockResolvedValue({ taskId: 'screen-1', status: 'completed' });

    render(<GlobalTaskCenter />);

    await waitFor(() => expect(getTasks).toHaveBeenCalled());
    expect(screen.queryByTestId('floating-task-panel-button')).not.toBeInTheDocument();
  });

  it('clears a screening task from the icon once its poll reports completion', async () => {
    getTasks.mockResolvedValue({ total: 0, pending: 0, processing: 0, tasks: [] });
    useScreeningTaskStore.getState().setScreenTask({
      taskId: 'screen-1',
      title: '双低 选股',
      progress: 100,
      message: '执行中',
      status: 'processing',
    });
    getScreenTask.mockResolvedValue({
      taskId: 'screen-1',
      status: 'completed',
      progress: 100,
      message: '任务执行完成',
      result: null,
    });

    render(<GlobalTaskCenter />);

    await waitFor(() => {
      expect(screen.queryByTestId('floating-task-panel-button')).not.toBeInTheDocument();
    });
    expect(useScreeningTaskStore.getState().activeScreenTask).toBeNull();
  });
});
