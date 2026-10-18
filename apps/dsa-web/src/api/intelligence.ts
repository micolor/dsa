import apiClient from './index';
import { toCamelCase, toSnakeCase } from './utils';

export interface IntelligenceSource {
  id: number;
  name: string;
  sourceType: string;
  enabled: boolean;
  [key: string]: unknown;
}

export interface IntelligenceListParams {
  scopeType?: string;
  scopeValue?: string;
  market?: string;
  query?: string;
  days?: number;
  page?: number;
  pageSize?: number;
}

/** 情报源 / 情报条目接口，对齐 `api/v1/endpoints/intelligence.py`。 */
export const intelligenceApi = {
  async listSources(): Promise<IntelligenceSource[]> {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/intelligence/sources',
    );
    const items = Array.isArray(response.data.items) ? response.data.items : [];
    return items.map((item) => toCamelCase<IntelligenceSource>(item));
  },

  async createSource(payload: Record<string, unknown>): Promise<IntelligenceSource> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/intelligence/sources',
      toSnakeCase(payload),
    );
    return toCamelCase<IntelligenceSource>(response.data);
  },

  async createFromTemplate(
    templateId: string,
    overrides: Record<string, unknown> = {},
  ): Promise<IntelligenceSource> {
    const response = await apiClient.post<Record<string, unknown>>(
      `/api/v1/intelligence/sources/templates/${encodeURIComponent(templateId)}`,
      toSnakeCase(overrides),
    );
    return toCamelCase<IntelligenceSource>(response.data);
  },

  async createDefaults(overrides: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/intelligence/sources/defaults',
      toSnakeCase(overrides),
    );
    return response.data;
  },

  async listItems(params: IntelligenceListParams = {}): Promise<Record<string, unknown>> {
    const response = await apiClient.get<Record<string, unknown>>(
      '/api/v1/intelligence/items',
      { params: toSnakeCase(params) as Record<string, unknown> },
    );
    return response.data;
  },
};
