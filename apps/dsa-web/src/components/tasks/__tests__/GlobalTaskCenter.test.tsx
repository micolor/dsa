import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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

    render(<MemoryRouter><GlobalTaskCenter /></MemoryRouter>);

    const button = await screen.findByTestId('floating-task-panel-button');
    fireEvent.mouseOver(button);

    const items = await screen.findAllByTestId('task-panel-item');
    expect(items).toHaveLength(2);
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
    expect(screen.getByText('双低 选股')).toBeInTheDocument();
  });

  it('shows a screening task once even when it also arrives via the analysis SSE list', async () => {
    getTasks.mockResolvedValue({ total: 0, pending: 0, processing: 0, tasks: [] });
    getScreenTask.mockResolvedValue({
      taskId: 'screen-1',
      status: 'processing',
      progress: 50,
      message: '正在执行因子评分',
      result: null,
    });
    // 同一选股任务既经 SSE 进入分析任务列表（英文标题、真实 taskId），也写入 screeningTaskStore（中文标题）。
    useStockPoolStore.setState({
      activeTasks: [{
        taskId: 'screen-1',
        stockCode: 'screening_screen',
        stockName: '双低 / 沪深A股',
        status: 'processing',
        progress: 50,
        message: '正在执行因子评分',
        reportType: 'screening_screen',
        createdAt: '2026-06-08T08:00:00Z',
      }],
    });
    useScreeningTaskStore.getState().setScreenTask({
      taskId: 'screen-1',
      title: '双低 选股',
      progress: 50,
      message: '正在执行因子评分',
      status: 'processing',
    });

    render(<MemoryRouter><GlobalTaskCenter /></MemoryRouter>);

    const button = await screen.findByTestId('floating-task-panel-button');
    fireEvent.mouseOver(button);

    const items = await screen.findAllByTestId('task-panel-item');
    // 只保留一条：中文标题的选股条目（透传真实 taskId，可点击），不再出现英文重复条目。
    expect(items).toHaveLength(1);
    expect(screen.getByText('双低 选股')).toBeInTheDocument();
    expect(screen.queryByText('双低 / 沪深A股')).not.toBeInTheDocument();
  });

  it('hides the icon when there are no active tasks', async () => {
    getTasks.mockResolvedValue({ total: 0, pending: 0, processing: 0, tasks: [] });
    getScreenTask.mockResolvedValue({ taskId: 'screen-1', status: 'completed' });

    render(<MemoryRouter><GlobalTaskCenter /></MemoryRouter>);

    await waitFor(() => expect(getTasks).toHaveBeenCalled());
    expect(screen.queryByTestId('floating-task-panel-button')).not.toBeInTheDocument();
  });

  // 后端重启后任务记录被清掉，轮询会一直拿到 404 `screening_screen_task_not_found`。
  // 这里此前是裸 `catch {}` + 无条件每 3s 重排：面板会永远停在旧进度上，
  // 而 FloatingTaskPanel 没有单条任务的清除入口，用户只能刷新页面才可能摆脱它。
  it('clears a screening task the server no longer knows about', async () => {
    vi.useFakeTimers();
    try {
      getTasks.mockResolvedValue({ total: 0, pending: 0, processing: 0, tasks: [] });
      useScreeningTaskStore.getState().setScreenTask({
        taskId: 'screen-1',
        title: '双低 选股',
        progress: 40,
        message: '正在执行因子评分',
        status: 'processing',
      });
      const notFound = {
        response: {
          status: 404,
          data: {
            error: 'screening_screen_task_not_found',
            message: '选股任务 screen-1 不存在或已过期',
          },
        },
      };
      getScreenTask.mockRejectedValue(notFound);

      render(<MemoryRouter><GlobalTaskCenter /></MemoryRouter>);

      // 首次轮询立即发出：等它落地。
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });

      expect(useScreeningTaskStore.getState().activeScreenTask).toBeNull();
      expect(screen.queryByTestId('floating-task-panel-button')).not.toBeInTheDocument();

      // 不可恢复：不得再继续重排轮询（原本每 3s 重试一次，永远停不下来）。
      const callsAfterClear = getScreenTask.mock.calls.length;
      await act(async () => {
        await vi.advanceTimersByTimeAsync(10_000);
      });
      expect(getScreenTask.mock.calls.length).toBe(callsAfterClear);
    } finally {
      vi.runOnlyPendingTimers();
      vi.useRealTimers();
    }
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

    render(<MemoryRouter><GlobalTaskCenter /></MemoryRouter>);

    await waitFor(() => {
      expect(screen.queryByTestId('floating-task-panel-button')).not.toBeInTheDocument();
    });
    expect(useScreeningTaskStore.getState().activeScreenTask).toBeNull();
  });
});
