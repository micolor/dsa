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
