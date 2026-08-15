import { describe, expect, it } from 'vitest';
import { getParsedApiError } from '../error';

function buildError(rawMessage: string, status = 400) {
  const error = new Error(rawMessage);
  Object.assign(error, {
    response: { status, data: { detail: rawMessage }, statusText: 'Bad Request' },
  });
  return error;
}

describe('parseApiError - LLM 配置错误识别', () => {
  it('识别 "LLM Provider NOT provided"（无 provider 前缀）', () => {
    const parsed = getParsedApiError(buildError(
      'All LLM models failed (tried 1 model(s)). Last error: BadRequestError: '
      + 'litellm.BadRequestError: LLM Provider NOT provided. '
      + 'Pass in the LLM provider you are trying to call. You passed model=Qwen/Qwen3-235B-A22B-Thinking-2507',
    ));
    expect(parsed.category).toBe('llm_not_configured');
  });

  it('识别 "No API key provided"', () => {
    const parsed = getParsedApiError(buildError(
      'Authentication Error: No API key provided.',
    ));
    expect(parsed.category).toBe('llm_not_configured');
  });

  it('保持原有 "All LLM models failed ... last error: none" 分支', () => {
    const parsed = getParsedApiError(buildError(
      'All LLM models failed (tried 1 model(s)). Last error: None',
    ));
    expect(parsed.category).toBe('llm_not_configured');
  });

  it('"All LLM models failed" + 真实 provider 错误（非 none、无凭据提示）不归类 llm_not_configured', () => {
    const parsed = getParsedApiError(buildError(
      'All LLM models failed (tried 1 model(s)). Last error: RateLimitError: 429 - Rate limit reached.',
    ));
    expect(parsed.category).not.toBe('llm_not_configured');
  });

  it('不影响其他错误分类（上游超时）', () => {
    const parsed = getParsedApiError(buildError(
      'Service timed out after 30s',
    ));
    expect(parsed.category).toBe('upstream_timeout');
  });
});

describe('parseApiError - FastAPI 422 / 429', () => {
  it('把 422 的 detail 数组渲染成可读的字段错误，而非原始 JSON', () => {
    const error = new Error('Request failed with status code 422');
    Object.assign(error, {
      response: {
        status: 422,
        data: {
          detail: [
            { loc: ['query', 'cost_method'], msg: "Input should be 'fifo' or 'avg'", type: 'literal_error' },
            { loc: ['query', 'account_id'], msg: 'Input should be a valid integer', type: 'int_parsing' },
          ],
        },
        statusText: 'Unprocessable Entity',
      },
    });
    const parsed = getParsedApiError(error);
    expect(parsed.category).toBe('http_error');
    expect(parsed.status).toBe(422);
    expect(parsed.title).toBe('请求参数校验失败');
    expect(parsed.message).toContain('cost_method');
    expect(parsed.message).toContain('fifo');
    expect(parsed.message).not.toContain('[{');
  });

  it('429 归类为请求过于频繁', () => {
    const parsed = getParsedApiError(buildError('rate limit', 429));
    expect(parsed.category).toBe('http_error');
    expect(parsed.title).toBe('请求过于频繁');
  });
});
