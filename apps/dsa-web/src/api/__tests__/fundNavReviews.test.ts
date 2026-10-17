import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fundNavReviewsApi } from '../fundNavReviews';

const { get, post } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../index', () => ({
  default: {
    get,
    post,
  },
}));

describe('fundNavReviewsApi', () => {
  beforeEach(() => {
    get.mockReset();
    post.mockReset();
  });

  it('lists reviews with snake_case filters and camel-cases the response', async () => {
    get.mockResolvedValueOnce({
      data: {
        items: [
          {
            id: 1,
            fund_code: '003095',
            fund_name: '中欧医疗健康混合A',
            horizon: '1m',
            engine_version: 'fund-nav-v1',
            anchor_date: '2024-01-02',
            anchor_nav: 1.2345,
            due_date: '2024-02-06',
            eval_status: 'evaluated',
            nav_return_pct: 2.5,
            max_drawdown_pct: -1.2,
            eval_window_records: 22,
            anchor_source: 'pipeline',
            attempts: 1,
          },
        ],
        total: 1,
        page: 2,
        page_size: 10,
      },
    });

    const response = await fundNavReviewsApi.list({
      fundCode: 'fund:003095',
      horizon: '1m',
      evalStatus: 'evaluated',
      anchorFrom: '2024-01-01',
      anchorTo: '2024-01-31',
      page: 2,
      pageSize: 10,
    });

    expect(get).toHaveBeenCalledWith('/api/v1/fund-nav-reviews', {
      params: {
        fund_code: 'fund:003095',
        horizon: '1m',
        eval_status: 'evaluated',
        anchor_from: '2024-01-01',
        anchor_to: '2024-01-31',
        page: 2,
        page_size: 10,
      },
    });
    expect(response.items[0]).toMatchObject({
      fundCode: '003095',
      fundName: '中欧医疗健康混合A',
      anchorDate: '2024-01-02',
      anchorNav: 1.2345,
      evalStatus: 'evaluated',
      navReturnPct: 2.5,
      maxDrawdownPct: -1.2,
      evalWindowRecords: 22,
      anchorSource: 'pipeline',
    });
    expect(response.pageSize).toBe(10);
  });

  it('omits undefined filters instead of sending empty values', async () => {
    get.mockResolvedValueOnce({ data: { items: [], total: 0, page: 1, page_size: 20 } });

    await fundNavReviewsApi.list({});

    expect(get).toHaveBeenCalledWith('/api/v1/fund-nav-reviews', { params: {} });
  });

  it('repeats horizon query params for stats', async () => {
    get.mockResolvedValueOnce({
      data: {
        engine_version: 'fund-nav-v1',
        horizons: ['1m'],
        total: 0,
        anchor_count: 0,
        pending: 0,
        evaluated: 0,
        unable: 0,
        positive_rate_pct: null,
        avg_nav_return_pct: null,
        avg_max_drawdown_pct: null,
        unable_reasons: {},
        breakdowns: {
          horizon: [
            {
              dimension: 'horizon',
              value: '1m',
              total: 0,
              anchor_count: 0,
              pending: 0,
              evaluated: 0,
              unable: 0,
              positive_rate_pct: null,
              avg_nav_return_pct: null,
              avg_max_drawdown_pct: null,
              unable_reasons: {},
            },
          ],
          month: [],
        },
      },
    });

    const response = await fundNavReviewsApi.getStats(['1m', '1y']);

    const [, options] = get.mock.calls[0];
    expect(options.params).toEqual({ horizons: ['1m', '1y'] });
    expect(options.paramsSerializer.serialize({ horizons: ['1m', '1y'] })).toBe('horizons=1m&horizons=1y');
    expect(response.breakdowns.horizon[0].value).toBe('1m');
    expect(response.positiveRatePct).toBeNull();
  });

  it('serializes nav range params and camelizes the response', async () => {
    get.mockResolvedValueOnce({
      data: {
        fund_code: '003095',
        start_date: '2025-01-01',
        end_date: '2025-01-31',
        adjustment_status: 'available',
        truncated: false,
        events: { dividends: 1, splits: 0 },
        items: [
          {
            date: '2025-01-02',
            unit_nav: 1,
            acc_nav: 1.1,
            adjusted_nav: 1,
            change_pct: 0,
          },
        ],
      },
    });

    const result = await fundNavReviewsApi.getNavRange({
      fund_code: '003095',
      start_date: '2025-01-01',
      end_date: '2025-01-31',
    });

    expect(get).toHaveBeenCalledWith('/api/v1/fund-nav-reviews/nav-range', {
      params: {
        fund_code: '003095',
        start_date: '2025-01-01',
        end_date: '2025-01-31',
      },
    });
    expect(result.adjustmentStatus).toBe('available');
    expect(result.events).toEqual({ dividends: 1, splits: 0 });
    expect(result.items[0].adjustedNav).toBe(1);
    expect(result.items[0].accNav).toBe(1.1);
  });

  it('sends the run payload with snake_case keys', async () => {
    post.mockResolvedValueOnce({
      data: {
        items: [],
        evaluated: 2,
        pending: 1,
        unable: 0,
        skipped: 3,
        engine_version: 'fund-nav-v1',
      },
    });

    const response = await fundNavReviewsApi.run({
      fundCode: 'fund:003095',
      horizons: ['1m'],
      force: true,
      limit: 20,
    });

    expect(post).toHaveBeenCalledWith('/api/v1/fund-nav-reviews/run', {
      fund_code: 'fund:003095',
      horizons: ['1m'],
      force: true,
      limit: 20,
    });
    expect(response.engineVersion).toBe('fund-nav-v1');
    expect(response.evaluated).toBe(2);
  });

  it('maps the backfill payload, including dryRun and resolveMissing', async () => {
    post.mockResolvedValueOnce({
      data: {
        scanned: 3,
        created: 1,
        skipped: 0,
        unmatched: 2,
        funds: 1,
        dry_run: false,
        engine_version: 'fund-nav-v1',
      },
    });

    const response = await fundNavReviewsApi.backfill({
      fundCode: '003095',
      since: '2024-01-01',
      until: '2024-03-01',
      dryRun: false,
      resolveMissing: true,
      limit: 50,
    });

    expect(post).toHaveBeenCalledWith('/api/v1/fund-nav-reviews/backfill/run', {
      fund_code: '003095',
      since: '2024-01-01',
      until: '2024-03-01',
      dry_run: false,
      resolve_missing: true,
      limit: 50,
    });
    expect(response.dryRun).toBe(false);
    expect(response.unmatched).toBe(2);
  });
});
