import { fireEvent, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { renderWithToastHost as render } from '../../testUtils/renderWithToastHost';
import ScheduledTasksPage from '../ScheduledTasksPage';

const { listTasks, calendar } = vi.hoisted(() => ({
  listTasks: vi.fn(),
  calendar: vi.fn(),
}));

vi.mock('../../api/scheduledTasks', () => ({
  scheduledTasksApi: {
    list: listTasks,
    calendar,
    create: vi.fn(),
    update: vi.fn(),
    remove: vi.fn(),
    setEnabled: vi.fn(),
    runNow: vi.fn(),
    runs: vi.fn(),
  },
}));

const task = {
  id: 1,
  name: '每日盘前自选',
  taskType: 'stock_analysis' as const,
  payload: { stocks: [], reportType: 'detailed' },
  weekdays: [0, 1, 2, 3, 4],
  timeOfDay: '08:30',
  enabled: true,
  tradingDayOnly: true,
  createdAt: '2026-11-09T08:00:00',
  updatedAt: null,
  lastRunAt: null,
  lastStatus: null,
  lastMessage: null,
};

function calendarPayload() {
  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  return {
    start: iso,
    end: iso,
    generatedAt: `${iso}T08:00:00`,
    occurrences: [
      {
        task,
        scheduledFor: `${iso}T08:30`,
        status: 'scheduled' as const,
        message: null,
      },
    ],
  };
}

describe('ScheduledTasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listTasks.mockResolvedValue([task]);
    calendar.mockResolvedValue(calendarPayload());
  });

  it('renders the calendar, selected-day tasks and the task list', async () => {
    render(<ScheduledTasksPage />);

    await waitFor(() => expect(listTasks).toHaveBeenCalled());
    expect((await screen.findAllByText(/每日盘前自选/)).length).toBeGreaterThan(0);
    expect(screen.getByText('任务日历')).toBeInTheDocument();
    fireEvent.click(screen.getAllByText(/每日盘前自选/)[0]);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getAllByText(/每日盘前自选/).length).toBeGreaterThan(1);
  });

  it('shows an empty-state hint with a create action when no tasks exist', async () => {
    listTasks.mockResolvedValue([]);
    calendar.mockResolvedValue({ ...calendarPayload(), occurrences: [] });

    render(<ScheduledTasksPage />);

    expect(await screen.findByText('还没有定时任务')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '新建任务' }).length).toBeGreaterThanOrEqual(2);
  });

  it('opens the create dialog and shows type-specific fields', async () => {
    render(<ScheduledTasksPage />);

    fireEvent.click(await screen.findByRole('button', { name: '新建任务' }));

    expect(await screen.findByLabelText('任务名称')).toBeInTheDocument();
    expect(screen.getByLabelText('任务类型')).toBeInTheDocument();
    expect(screen.getByText('股票代码')).toBeInTheDocument();
  });

  it('shows the lookback field for paper auto trade tasks', async () => {
    render(<ScheduledTasksPage />);

    fireEvent.click(await screen.findByRole('button', { name: '新建任务' }));
    fireEvent.click(await screen.findByLabelText('任务类型'));
    fireEvent.click(await screen.findByRole('option', { name: '模拟盘自动交易' }));

    expect(await screen.findByText('信号回看天数')).toBeInTheDocument();
    expect(screen.getByText(/只处理最近 N 天内创建且仍有效的信号/)).toBeInTheDocument();
  });

  it('shows screening fields and the hotspot hint for data refresh tasks', async () => {
    render(<ScheduledTasksPage />);

    fireEvent.click(await screen.findByRole('button', { name: '新建任务' }));
    fireEvent.click(await screen.findByLabelText('任务类型'));
    fireEvent.click(await screen.findByRole('option', { name: '数据刷新（选股 + 热点）' }));

    expect(await screen.findByText('选股策略')).toBeInTheDocument();
    expect(screen.getByText(/执行选股并刷新热点缓存/)).toBeInTheDocument();
  });
});
