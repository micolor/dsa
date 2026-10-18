import { describe, expect, it } from 'vitest';
import {
  parseActionExecutedEvent,
  parseActionTaskEvent,
} from '../agentAction';

describe('agentAction', () => {
  it('parses an executed event', () => {
    expect(parseActionExecutedEvent({
      type: 'action_executed',
      kind: 'fx_refresh',
      summary: '已刷新汇率',
      result: { updated: 3 },
      undo_hint: '再次刷新即可覆盖',
    })).toEqual({
      kind: 'fx_refresh',
      summary: '已刷新汇率',
      result: { updated: 3 },
      undoHint: '再次刷新即可覆盖',
    });
  });

  it('parses a task event', () => {
    expect(parseActionTaskEvent({
      type: 'action_task',
      kind: 'stock_analysis',
      summary: '已提交分析',
      task_id: 'abc',
      status_endpoint: '/api/v1/analysis/status/abc',
      poll_interval_ms: 2000,
    })).toEqual({
      kind: 'stock_analysis',
      summary: '已提交分析',
      taskId: 'abc',
      statusEndpoint: '/api/v1/analysis/status/abc',
      pollIntervalMs: 2000,
    });
  });

  it('rejects unknown kinds and blank summaries', () => {
    expect(parseActionExecutedEvent({ kind: 'nope', summary: 'x', result: {}, undo_hint: 'x' }))
      .toBeUndefined();
    expect(parseActionTaskEvent({
      kind: 'stock_analysis', summary: '  ', task_id: 'a',
      status_endpoint: '/x', poll_interval_ms: 2000,
    })).toBeUndefined();
  });
});
