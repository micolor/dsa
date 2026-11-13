import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  ScheduledTask,
  ScheduledTaskCalendar,
  ScheduledTaskInput,
  ScheduledTaskRun,
} from '../types/scheduledTasks';

function toSnakeInput(input: Partial<ScheduledTaskInput>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  if (input.name !== undefined) payload.name = input.name;
  if (input.taskType !== undefined) payload.task_type = input.taskType;
  if (input.payload !== undefined) payload.payload = input.payload;
  if (input.weekdays !== undefined) payload.weekdays = input.weekdays;
  if (input.timeOfDay !== undefined) payload.time_of_day = input.timeOfDay;
  if (input.enabled !== undefined) payload.enabled = input.enabled;
  if (input.tradingDayOnly !== undefined) payload.trading_day_only = input.tradingDayOnly;
  return payload;
}

export const scheduledTasksApi = {
  async list(enabledOnly = false): Promise<ScheduledTask[]> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/scheduled-tasks', {
      params: { enabled_only: enabledOnly },
    });
    return toCamelCase<{ items: ScheduledTask[] }>(response.data).items ?? [];
  },

  async create(input: ScheduledTaskInput): Promise<ScheduledTask> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/scheduled-tasks',
      toSnakeInput(input),
    );
    return toCamelCase<{ task: ScheduledTask }>(response.data).task;
  },

  async update(taskId: number, input: Partial<ScheduledTaskInput>): Promise<ScheduledTask> {
    const response = await apiClient.patch<Record<string, unknown>>(
      `/api/v1/scheduled-tasks/${taskId}`,
      toSnakeInput(input),
    );
    return toCamelCase<{ task: ScheduledTask }>(response.data).task;
  },

  async remove(taskId: number): Promise<void> {
    await apiClient.delete(`/api/v1/scheduled-tasks/${taskId}`);
  },

  async setEnabled(taskId: number, enabled: boolean): Promise<ScheduledTask> {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/scheduled-tasks/${taskId}/enabled`,
      { enabled },
    );
    return toCamelCase<{ task: ScheduledTask }>(response.data).task;
  },

  async runNow(taskId: number): Promise<void> {
    await apiClient.post(`/api/v1/scheduled-tasks/${taskId}/run`);
  },

  async calendar(start: string, end: string): Promise<ScheduledTaskCalendar> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/scheduled-tasks/calendar', {
      params: { start, end },
    });
    return toCamelCase<ScheduledTaskCalendar>(response.data);
  },

  async runs(taskId: number, limit = 20): Promise<ScheduledTaskRun[]> {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/scheduled-tasks/${taskId}/runs`,
      { params: { limit } },
    );
    return toCamelCase<{ items: ScheduledTaskRun[] }>(response.data).items ?? [];
  },
};
