import { beforeEach, describe, expect, it, vi } from 'vitest';
import { intelligenceApi } from '../intelligence';
import apiClient from '../index';

vi.mock('../index', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

beforeEach(() => {
  vi.clearAllMocks();
});

describe('intelligenceApi', () => {
  it('creates a source with a snake_case payload', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 1, source_type: 'rss', name: 'n' } });
    const source = await intelligenceApi.createSource(
      { name: 'n', sourceType: 'rss', url: 'https://x', scopeValue: null } as never,
    );

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/intelligence/sources',
      expect.objectContaining({ source_type: 'rss', scope_value: null }),
    );
    expect(source).toEqual({ id: 1, sourceType: 'rss', name: 'n' });
  });

  it('lists sources from the items envelope', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({
      data: { items: [{ id: 1, name: 'n', source_type: 'rss', enabled: true }], total: 1, page: 1, page_size: 50 },
    });

    await expect(intelligenceApi.listSources()).resolves.toEqual([
      { id: 1, name: 'n', sourceType: 'rss', enabled: true },
    ]);
    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/intelligence/sources');
  });

  it('creates a source from a template with snake_case overrides', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { id: 2 } });

    await intelligenceApi.createFromTemplate('rss-default', { scopeType: 'symbol', scopeValue: '600519' });

    expect(apiClient.post).toHaveBeenCalledWith(
      '/api/v1/intelligence/sources/templates/rss-default',
      { scope_type: 'symbol', scope_value: '600519' },
    );
  });

  it('creates default sources', async () => {
    vi.mocked(apiClient.post).mockResolvedValue({ data: { created_count: 3, items: [], total: 3 } });

    await expect(intelligenceApi.createDefaults()).resolves.toEqual({ created_count: 3, items: [], total: 3 });
    expect(apiClient.post).toHaveBeenCalledWith('/api/v1/intelligence/sources/defaults', {});
  });

  it('lists items with the endpoint query parameters', async () => {
    vi.mocked(apiClient.get).mockResolvedValue({ data: { items: [], total: 0, page: 1, page_size: 50 } });

    await intelligenceApi.listItems({ scopeType: 'symbol', scopeValue: '600519', pageSize: 20 });

    expect(apiClient.get).toHaveBeenCalledWith('/api/v1/intelligence/items', {
      params: { scope_type: 'symbol', scope_value: '600519', page_size: 20 },
    });
  });
});
