export type ScheduledTaskType =
  | 'stock_analysis'
  | 'market_review'
  | 'screening'
  | 'backtest'
  | 'agent_prompt';

export type ScheduledTaskRunStatus =
  | 'submitted'
  | 'success'
  | 'failed'
  | 'skipped'
  | 'missed'
  | 'scheduled'
  | 'running';

export interface ScheduledTask {
  id: number;
  name: string;
  taskType: ScheduledTaskType;
  payload: Record<string, unknown>;
  weekdays: number[];
  timeOfDay: string;
  enabled: boolean;
  tradingDayOnly: boolean;
  createdAt: string | null;
  updatedAt: string | null;
  lastRunAt: string | null;
  lastStatus: string | null;
  lastMessage: string | null;
}

export interface ScheduledTaskOccurrence {
  task: ScheduledTask;
  scheduledFor: string;
  status: ScheduledTaskRunStatus;
  message?: string | null;
}

export interface ScheduledTaskCalendar {
  start: string;
  end: string;
  generatedAt: string;
  occurrences: ScheduledTaskOccurrence[];
}

export interface ScheduledTaskRun {
  id: number;
  taskId: number;
  triggerSource: string;
  status: string;
  message: string | null;
  scheduledFor: string | null;
  createdAt: string | null;
  finishedAt: string | null;
}

export interface ScheduledTaskInput {
  name: string;
  taskType: ScheduledTaskType;
  payload: Record<string, unknown>;
  weekdays: number[];
  timeOfDay: string;
  enabled: boolean;
  tradingDayOnly: boolean;
}
