import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  BackfillResult,
  EquityPoint,
  PaperAccount,
  PaperPosition,
  PaperSignalList,
  PaperSnapshot,
  PaperTradeList,
  PaperValuation,
} from '../types/paper';

export const paperApi = {
  getAccount: async (): Promise<PaperAccount> => {
    const response = await apiClient.get('/api/v1/paper/account');
    return toCamelCase<PaperAccount>(response.data);
  },

  getSnapshot: async (): Promise<PaperSnapshot> => {
    const response = await apiClient.get('/api/v1/paper/snapshot');
    return toCamelCase<PaperSnapshot>(response.data);
  },

  getEquityCurve: async (start?: string, end?: string): Promise<EquityPoint[]> => {
    const queryParams: Record<string, string> = {};
    if (start) queryParams.start = start;
    if (end) queryParams.end = end;
    const response = await apiClient.get('/api/v1/paper/equity-curve', { params: queryParams });
    return (response.data || []).map((point: unknown) => toCamelCase<EquityPoint>(point));
  },

  getPositions: async (): Promise<PaperPosition[]> => {
    const response = await apiClient.get('/api/v1/paper/positions');
    return (response.data || []).map((item: unknown) => toCamelCase<PaperPosition>(item));
  },

  getTrades: async (page = 1, limit = 50): Promise<PaperTradeList> => {
    const response = await apiClient.get('/api/v1/paper/trades', { params: { page, limit } });
    return toCamelCase<PaperTradeList>(response.data);
  },

  getSignals: async (page = 1, limit = 50): Promise<PaperSignalList> => {
    const response = await apiClient.get('/api/v1/paper/signals', { params: { page, limit } });
    return toCamelCase<PaperSignalList>(response.data);
  },

  refresh: async (): Promise<PaperValuation> => {
    const response = await apiClient.post('/api/v1/paper/refresh');
    return toCamelCase<PaperValuation>(response.data);
  },

  backfill: async (fromDate: string, toDate?: string): Promise<BackfillResult> => {
    const requestData: Record<string, unknown> = { from_date: fromDate };
    if (toDate) requestData.to_date = toDate;
    const response = await apiClient.post('/api/v1/paper/backfill', requestData);
    return toCamelCase<BackfillResult>(response.data);
  },
};
