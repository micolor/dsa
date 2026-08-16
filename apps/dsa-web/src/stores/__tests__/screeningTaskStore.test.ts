import { describe, expect, it, beforeEach } from 'vitest';
import {
  toScreenTaskInfo,
  useScreeningTaskStore,
  type ScreenProgressTask,
} from '../screeningTaskStore';

const sampleTask: ScreenProgressTask = {
  taskId: 'task-1',
  traceId: 'trace-abc',
  title: '双低 选股',
  progress: 42,
  message: '正在执行因子评分',
  status: 'processing',
};

describe('useScreeningTaskStore', () => {
  beforeEach(() => {
    useScreeningTaskStore.setState({ activeScreenTask: null });
  });

  it('starts empty', () => {
    expect(useScreeningTaskStore.getState().activeScreenTask).toBeNull();
  });

  it('setScreenTask publishes the active task', () => {
    useScreeningTaskStore.getState().setScreenTask(sampleTask);
    expect(useScreeningTaskStore.getState().activeScreenTask).toEqual(sampleTask);
  });

  it('clearScreenTask removes the active task', () => {
    useScreeningTaskStore.getState().setScreenTask(sampleTask);
    useScreeningTaskStore.getState().clearScreenTask();
    expect(useScreeningTaskStore.getState().activeScreenTask).toBeNull();
  });
});

describe('toScreenTaskInfo', () => {
  it('maps a screening task onto the TaskInfo contract passing the real taskId', () => {
    const info = toScreenTaskInfo(sampleTask);

    expect(info.taskId).toBe('task-1');
    expect(info.status).toBe('processing');
    expect(info.progress).toBe(42);
    expect(info.message).toBe('正在执行因子评分');
    expect(info.traceId).toBe('trace-abc');
    expect(info.stockName).toBe('双低 选股');
    expect(info.stockCode).toBe('选股');
    expect(info.reportType).toBe('screening');
    expect(info.analysisPhase).toBeUndefined();
  });

  it('passes the real screening taskId through for a clickable run flow', () => {
    const info = toScreenTaskInfo({ ...sampleTask, taskId: 'abc-123' });
    expect(info.taskId).toBe('abc-123');
  });

  it('omits traceId when absent and falls back the title', () => {
    const info = toScreenTaskInfo({ ...sampleTask, traceId: undefined, title: '' });
    expect(info.traceId).toBeUndefined();
    expect(info.stockName).toBe('选股');
  });
});
