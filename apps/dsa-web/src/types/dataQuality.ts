/** 跨源数据质量异常（camelCase，由 api/dataQuality 的 snake->camel mapper 构建）。 */
export interface DataQualityDiscrepancyItem {
  id: number;
  market: string;
  stockCode: string;
  issueType: string;
  primarySource: string | null;
  secondarySource: string | null;
  primaryPrice: number | null;
  secondaryPrice: number | null;
  priceDiffPct: number | null;
  primaryTs: string | null;
  secondaryTs: string | null;
  detail: string | null;
  createdAt: string;
}

export interface DataQualityDiscrepancyListResponse {
  items: DataQualityDiscrepancyItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface DataQualityDiscrepancyListQuery {
  market?: string;
  stockCode?: string;
  issueType?: string;
  page?: number;
  pageSize?: number;
}
