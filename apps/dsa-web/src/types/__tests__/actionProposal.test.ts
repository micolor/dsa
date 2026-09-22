import { describe, expect, it } from 'vitest';
import { parseActionProposalEvent } from '../actionProposal';

/**
 * 表驱动：每一条非法形状单独钉一行。
 *
 * 走流式 harness 时，`proposal` 与 `summary` 两条守卫被证明是「变异可存活」的
 * （删掉它们测试仍然全绿），所以这里直接对着纯函数钉，比走 SSE 便宜得多。
 */
describe('parseActionProposalEvent', () => {
  const validEvent = {
    type: 'action_proposal',
    kind: 'alert',
    summary: '「600519」价格上穿 ¥1800',
    proposal: { target: '600519' },
  };

  it('parses a valid event, keeping the proposal body untouched', () => {
    expect(parseActionProposalEvent(validEvent)).toEqual({
      kind: 'alert',
      summary: '「600519」价格上穿 ¥1800',
      proposal: { target: '600519' },
    });
  });

  const invalidCases: Array<[string, unknown]> = [
    ['kind 缺失', { summary: 'x', proposal: { a: 1 } }],
    ['kind 不是字符串', { kind: 42, summary: 'x', proposal: { a: 1 } }],
    ['kind 不在白名单（但确实是字符串）', { kind: 'drop_table', summary: 'x', proposal: { a: 1 } }],
    ['summary 缺失', { kind: 'alert', proposal: { a: 1 } }],
    ['summary 不是字符串', { kind: 'alert', summary: 42, proposal: { a: 1 } }],
    ['proposal 缺失', { kind: 'alert', summary: 'x' }],
    ['proposal 不是对象', { kind: 'alert', summary: 'x', proposal: 'nope' }],
  ];

  it.each(invalidCases)('returns undefined when %s', (_label, event) => {
    expect(parseActionProposalEvent(event)).toBeUndefined();
  });

  it('returns undefined for a null or non-object event', () => {
    expect(parseActionProposalEvent(null)).toBeUndefined();
    expect(parseActionProposalEvent(undefined)).toBeUndefined();
    expect(parseActionProposalEvent('action_proposal')).toBeUndefined();
  });
});
