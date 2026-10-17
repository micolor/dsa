import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  FundNavReviewBackfillRequest,
  FundNavReviewBackfillResponse,
  FundNavReviewListParams,
  FundNavReviewListResponse,
  FundNavReviewRunRequest,
  FundNavReviewRunResponse,
  FundNavReviewStatsResponse,
} from '../types/fundNavReviews';

function omitUndefined(input: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(input).filter(([, value]) => value !== undefined),
  );
}

function serializeRepeatedQueryParams(params: Record<string, unknown>): string {
  const searchParams = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const values = Array.isArray(value) ? value : [value];
    for (const item of values) {
      if (item === undefined || item === null || item === '') continue;
      searchParams.append(key, String(item));
    }
  }
  return searchParams.toString();
}

function toListParams(params: FundNavReviewListParams): Record<string, unknown> {
  return omitUndefined({
    fund_code: params.fundCode,
    horizon: params.horizon,
    eval_status: params.evalStatus,
    anchor_from: params.anchorFrom,
    anchor_to: params.anchorTo,
    page: params.page,
    page_size: params.pageSize,
  });
}

function toRunPayload(payload: FundNavReviewRunRequest): Record<string, unknown> {
  return omitUndefined({
    fund_code: payload.fundCode,
    horizons: payload.horizons,
    force: payload.force,
    limit: payload.limit,
  });
}

function toBackfillPayload(payload: FundNavReviewBackfillRequest): Record<string, unknown> {
  return omitUndefined({
    fund_code: payload.fundCode,
    since: payload.since,
    until: payload.until,
    dry_run: payload.dryRun,
    resolve_missing: payload.resolveMissing,
    limit: payload.limit,
  });
}

export const fundNavReviewsApi = {
  async list(params: FundNavReviewListParams = {}): Promise<FundNavReviewListResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/fund-nav-reviews', {
      params: toListParams(params),
    });
    return toCamelCase<FundNavReviewListResponse>(response.data);
  },

  async getStats(horizons?: string[]): Promise<FundNavReviewStatsResponse> {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/fund-nav-reviews/stats', {
      params: horizons && horizons.length > 0 ? { horizons } : {},
      paramsSerializer: {
        serialize: serializeRepeatedQueryParams,
      },
    });
    return toCamelCase<FundNavReviewStatsResponse>(response.data);
  },

  async run(payload: FundNavReviewRunRequest = {}): Promise<FundNavReviewRunResponse> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/fund-nav-reviews/run',
      toRunPayload(payload),
    );
    return toCamelCase<FundNavReviewRunResponse>(response.data);
  },

  async backfill(
    payload: FundNavReviewBackfillRequest = {},
  ): Promise<FundNavReviewBackfillResponse> {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/fund-nav-reviews/backfill/run',
      toBackfillPayload(payload),
    );
    return toCamelCase<FundNavReviewBackfillResponse>(response.data);
  },
};
