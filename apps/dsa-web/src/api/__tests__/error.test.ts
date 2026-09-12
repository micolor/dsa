import { describe, expect, it } from 'vitest';
import { getParsedApiError, normalizeBlobErrorBody } from '../error';

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

describe('parseApiError - 502/503 归因', () => {
  function buildStatusError(status: number, data: unknown, statusText = 'Service Unavailable') {
    const error = new Error(`Request failed with status code ${status}`);
    Object.assign(error, { response: { status, data, statusText } });
    return error;
  }

  it('服务端给出结构化原因时原样透出，不覆盖为代理/DNS 文案', () => {
    // 真实案例：分享图片缺少 wkhtmltoimage，本地服务返回 503 + {error, message}。
    const parsed = getParsedApiError(buildStatusError(503, {
      error: 'share_image_unavailable',
      message: '分享图片生成失败，请检查 wkhtmltoimage 转图工具是否已安装并可用',
    }));

    expect(parsed.message).toContain('wkhtmltoimage');
    expect(parsed.message).not.toContain('DNS');
    expect(parsed.category).not.toBe('upstream_network');
  });

  it('503 且响应体是网关 HTML 时仍归因为出网/代理问题', () => {
    const parsed = getParsedApiError(buildStatusError(503, '<html><body>503 Service Temporarily Unavailable</body></html>'));

    expect(parsed.category).toBe('upstream_network');
    expect(parsed.title).toBe('服务端无法访问外部依赖');
  });

  it('503 且无响应体时仍归因为出网/代理问题', () => {
    const parsed = getParsedApiError(buildStatusError(503, undefined));

    expect(parsed.category).toBe('upstream_network');
  });

  it('错误文本自带 proxy/DNS 线索时保持上游网络归因', () => {
    const parsed = getParsedApiError(buildStatusError(500, {
      message: 'HTTPSConnectionPool: ProxyError Unable to connect to proxy',
    }));

    expect(parsed.category).toBe('upstream_network');
  });

  it('blob 请求的 503 错误体还原后不再被通用文案覆盖', async () => {
    // 真实案例：`getShareImage` 用 responseType: 'blob'，axios 把失败响应也解析成 Blob。
    const error = buildStatusError(503, new Blob(
      [JSON.stringify({
        error: 'share_image_unavailable',
        message: '分享图片生成失败，请检查 wkhtmltoimage 转图工具是否已安装并可用',
      })],
      { type: 'application/json' },
    ));

    expect(getParsedApiError(error).message).toContain('DNS');

    await normalizeBlobErrorBody(error);

    const parsed = getParsedApiError(error);
    expect(parsed.message).toContain('wkhtmltoimage');
    expect(parsed.category).not.toBe('upstream_network');
  });

  it('blob 错误体不是 JSON 时保留纯文本，仍按网关失败归因', async () => {
    const error = buildStatusError(503, new Blob(
      ['<html><body>503 Service Temporarily Unavailable</body></html>'],
      { type: 'text/html' },
    ));

    await normalizeBlobErrorBody(error);

    expect(getParsedApiError(error).category).toBe('upstream_network');
  });

  it('非 Blob 错误体不受影响', async () => {
    const error = buildStatusError(503, { message: 'wkhtmltoimage 未安装' });

    await normalizeBlobErrorBody(error);

    expect((error as unknown as { response: { data: unknown } }).response.data)
      .toEqual({ message: 'wkhtmltoimage 未安装' });
  });
});
