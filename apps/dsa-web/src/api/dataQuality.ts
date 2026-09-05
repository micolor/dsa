import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  DataQualityDiscrepancyItem,
  DataQualityDiscrepancyListQuery,
  DataQualityDiscrepancyListResponse,
} from '../types/dataQuality';

function toDiscrepancyParams(query: DataQualityDiscrepancyListQuery = {}): Record<string, string | number | boolean> {
  const params: Record<string, string | number | boolean> = {};
  if (query.market) params.market = query.market;
  if (query.stockCode) params.stock_code = query.stockCode;
  if (query.issueType) params.issue_type = query.issueType;
  if (query.page !== undefined) params.page = query.page;
  if (query.pageSize !== undefined) params.page_size = query.pageSize;
  return params;
}

function mapDiscrepancyItem(data: Record<string, unknown>): DataQualityDiscrepancyItem {
  // 复用既有 snake->camel 工具：stock_code->stockCode / issue_type->issueType / primary_source->primarySource /
  // price_diff_pct->priceDiffPct / primary_ts->primaryTs / created_at->createdAt。对已是 camel 的 key 是幂等 no-op。
  const item = toCamelCase<DataQualityDiscrepancyItem>(data);
  item.id = Number(item.id ?? 0);
  item.market = String(item.market ?? '');
  item.stockCode = String(item.stockCode ?? '');
  item.issueType = String(item.issueType ?? '');
  item.primarySource = (item.primarySource ?? null) as string | null;
  item.secondarySource = (item.secondarySource ?? null) as string | null;
  item.primaryPrice = item.primaryPrice == null ? null : Number(item.primaryPrice);
  item.secondaryPrice = item.secondaryPrice == null ? null : Number(item.secondaryPrice);
  item.priceDiffPct = item.priceDiffPct == null ? null : Number(item.priceDiffPct);
  item.primaryTs = (item.primaryTs ?? null) as string | null;
  item.secondaryTs = (item.secondaryTs ?? null) as string | null;
  item.detail = (item.detail ?? null) as string | null;
  item.createdAt = String(item.createdAt ?? '');
  return item;
}

function mapDiscrepancyListResponse(data: Record<string, unknown>): DataQualityDiscrepancyListResponse {
  const rawItems = Array.isArray(data.items) ? data.items : [];
  const items = rawItems.map((it) => mapDiscrepancyItem((it ?? {}) as Record<string, unknown>));
  return {
    items,
    total: Number(data.total ?? 0),
    page: Number(data.page ?? 1),
    pageSize: Number(data.page_size ?? data.pageSize ?? 20),
  };
}

export const dataQualityApi = {
  async getDiscrepancies(query: DataQualityDiscrepancyListQuery = {}): Promise<DataQualityDiscrepancyListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/data-quality/discrepancies', {
      params: toDiscrepancyParams(query),
    });
    return mapDiscrepancyListResponse(response.data);
  },
};
