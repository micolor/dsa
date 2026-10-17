export type FundNavReviewHorizon = '1m' | '3m' | '6m' | '1y';

export type FundNavReviewStatus = 'pending' | 'evaluated' | 'unable';

export type FundNavReviewAnchorSource = 'pipeline' | 'backfill';

export interface FundNavReviewItem {
  id: number;
  fundCode: string;
  fundName: string;
  horizon: string;
  engineVersion: string;
  anchorDate: string;
  anchorNav: number;
  dueDate: string;
  evalStatus: string;
  unableReason?: string | null;
  evalDate?: string | null;
  evalNav?: number | null;
  navReturnPct?: number | null;
  maxDrawdownPct?: number | null;
  evalWindowRecords?: number | null;
  analysisHistoryId?: number | null;
  anchorSource: string;
  attempts: number;
  lastError?: string | null;
  checkedAt?: string | null;
  evaluatedAt?: string | null;
  createdAt?: string | null;
  updatedAt?: string | null;
}

export interface FundNavReviewListParams {
  fundCode?: string;
  horizon?: FundNavReviewHorizon;
  evalStatus?: FundNavReviewStatus;
  anchorFrom?: string;
  anchorTo?: string;
  page?: number;
  pageSize?: number;
}

export interface FundNavReviewListResponse {
  items: FundNavReviewItem[];
  total: number;
  page: number;
  pageSize: number;
}

export interface FundNavReviewRunRequest {
  fundCode?: string;
  horizons?: FundNavReviewHorizon[];
  force?: boolean;
  limit?: number;
}

export interface FundNavReviewRunResponse {
  items: FundNavReviewItem[];
  evaluated: number;
  pending: number;
  unable: number;
  skipped: number;
  engineVersion: string;
}

export interface FundNavReviewBackfillRequest {
  fundCode?: string;
  since?: string;
  until?: string;
  dryRun?: boolean;
  resolveMissing?: boolean;
  limit?: number;
}

export interface FundNavReviewBackfillResponse {
  scanned: number;
  created: number;
  skipped: number;
  unmatched: number;
  funds: number;
  dryRun: boolean;
  engineVersion: string;
}

export interface FundNavReviewStatsBucket {
  dimension: string;
  value: string;
  total: number;
  anchorCount: number;
  pending: number;
  evaluated: number;
  unable: number;
  positiveRatePct?: number | null;
  avgNavReturnPct?: number | null;
  avgMaxDrawdownPct?: number | null;
  unableReasons: Record<string, number>;
}

export interface FundNavReviewStatsResponse {
  engineVersion: string;
  horizons: string[];
  total: number;
  anchorCount: number;
  pending: number;
  evaluated: number;
  unable: number;
  positiveRatePct?: number | null;
  avgNavReturnPct?: number | null;
  avgMaxDrawdownPct?: number | null;
  unableReasons: Record<string, number>;
  breakdowns: {
    horizon: FundNavReviewStatsBucket[];
    month: FundNavReviewStatsBucket[];
  };
}
