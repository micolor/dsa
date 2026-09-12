import { afterEach, describe, expect, it } from 'vitest';
import apiClient from '../index';
import { getParsedApiError } from '../error';

/** 用自定义 adapter 直接产出 axios 的错误响应，绕开真实网络。 */
function rejectWithBlobResponse(status: number, body: string, contentType: string) {
  apiClient.defaults.adapter = async (config) => {
    const response = {
      data: new Blob([body], { type: contentType }),
      status,
      statusText: 'Service Unavailable',
      headers: {},
      config,
    };
    throw Object.assign(new Error(`Request failed with status code ${status}`), { response });
  };
}

describe('apiClient 响应拦截器', () => {
  afterEach(() => {
    delete (apiClient.defaults as { adapter?: unknown }).adapter;
  });

  it('blob 请求失败时按服务端结构化错误体归因，而不是退回代理/DNS 通用文案', async () => {
    // 真实案例：分享图片接口用 responseType: 'blob'，503 响应体是 JSON。
    // axios 会把失败响应也解析成 Blob，拦截器若不在 attachParsedApiError 之前还原，
    // 错误对象上就会缓存「代理、DNS 或出网配置」这一错误归因。
    rejectWithBlobResponse(503, JSON.stringify({
      error: 'share_image_unavailable',
      message: '分享图片生成失败，请检查 wkhtmltoimage 转图工具是否已安装并可用',
    }), 'application/json');

    const error = await apiClient.get('/api/v1/history/122/share-image', { responseType: 'blob' })
      .catch((err: unknown) => err);

    const parsed = getParsedApiError(error);
    expect(parsed.message).toContain('wkhtmltoimage');
    expect(parsed.category).not.toBe('upstream_network');
  });

  it('blob 请求失败但响应体是网关 HTML 时仍归因为出网/代理问题', async () => {
    rejectWithBlobResponse(503, '<html><body>503 Service Temporarily Unavailable</body></html>', 'text/html');

    const error = await apiClient.get('/api/v1/history/122/share-image', { responseType: 'blob' })
      .catch((err: unknown) => err);

    expect(getParsedApiError(error).category).toBe('upstream_network');
  });
});
