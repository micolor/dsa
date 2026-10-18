import { beforeEach, describe, expect, it, vi } from 'vitest';
import { applyActionProposal } from '../actionProposal';
import type { ActionProposal } from '../../types/actionProposal';

vi.mock('../../api/alerts', () => ({
  alertsApi: { createRule: vi.fn(), updateRule: vi.fn(), deleteRule: vi.fn() },
}));
vi.mock('../../api/portfolio', () => ({
  portfolioApi: {
    createTrade: vi.fn(),
    createCashLedger: vi.fn(),
    deleteCashLedger: vi.fn(),
    createCorporateAction: vi.fn(),
    deleteCorporateAction: vi.fn(),
    createAccount: vi.fn(),
    updateAccount: vi.fn(),
    deleteAccount: vi.fn(),
  },
}));
vi.mock('../../api/decisionSignals', () => ({
  decisionSignalsApi: {
    create: vi.fn(),
    updateStatus: vi.fn(),
    putFeedback: vi.fn(),
    reassess: vi.fn(),
  },
}));
vi.mock('../../api/paper', () => ({ paperApi: { reset: vi.fn() } }));
vi.mock('../../api/history', () => ({
  historyApi: { deleteRecords: vi.fn(), deleteByCode: vi.fn() },
}));
vi.mock('../../api/intelligence', () => ({
  intelligenceApi: { createSource: vi.fn(), createDefaults: vi.fn() },
}));
vi.mock('../../api/systemConfig', () => ({
  systemConfigApi: {
    update: vi.fn(),
    runSchedulerNow: vi.fn(),
    testNotificationChannel: vi.fn(),
  },
}));

const { alertsApi } = await import('../../api/alerts');
const { portfolioApi } = await import('../../api/portfolio');
const { decisionSignalsApi } = await import('../../api/decisionSignals');
const { paperApi } = await import('../../api/paper');
const { historyApi } = await import('../../api/history');
const { intelligenceApi } = await import('../../api/intelligence');
const { systemConfigApi } = await import('../../api/systemConfig');

beforeEach(() => {
  vi.clearAllMocks();
});

describe('applyActionProposal', () => {
  it('creates an alert rule from a snake_case proposal', async () => {
    const proposal: ActionProposal = {
      kind: 'alert_create',
      summary: '「600519」价格上穿 ¥1800',
      proposal: {
        name: '600519 price above 1800',
        target_scope: 'single_symbol',
        target: '600519',
        alert_type: 'price_cross',
        parameters: { direction: 'above', price: 1800 },
        severity: 'info',
      },
    };

    await applyActionProposal(proposal);

    expect(alertsApi.createRule).toHaveBeenCalledWith({
      name: '600519 price above 1800',
      targetScope: 'single_symbol',
      target: '600519',
      alertType: 'price_cross',
      parameters: { direction: 'above', price: 1800 },
      severity: 'info',
    });
  });

  it('dispatches alert_update to updateRule without leaking rule_id into the body', async () => {
    await applyActionProposal({
      kind: 'alert_update',
      summary: 'x',
      proposal: {
        rule_id: 7,
        name: 'n',
        target_scope: 'single_symbol',
        target: '600519',
        alert_type: 'price_above',
        parameters: { price: 1 },
        severity: 'info',
      },
    });

    expect(portfolioApi.createTrade).not.toHaveBeenCalled();
    expect(alertsApi.updateRule).toHaveBeenCalledWith(7, expect.objectContaining({ name: 'n' }));
    expect(alertsApi.updateRule).toHaveBeenCalledWith(
      7,
      expect.not.objectContaining({ ruleId: expect.anything() }),
    );
  });

  it('dispatches alert_delete to deleteRule', async () => {
    await applyActionProposal({ kind: 'alert_delete', summary: 'x', proposal: { rule_id: 9 } });

    expect(alertsApi.deleteRule).toHaveBeenCalledWith(9);
  });

  it('records a portfolio trade from a snake_case proposal', async () => {
    const proposal: ActionProposal = {
      kind: 'portfolio_trade',
      summary: '在「A股主账户」记一笔买入 005827 25900 @ CNY 1.6706（2026-09-22，约支出 CNY 43268.54）',
      proposal: {
        account_id: 1,
        symbol: '005827',
        trade_date: '2026-09-22',
        side: 'buy',
        quantity: 25900,
        price: 1.6706,
        fee: 0,
        tax: 0,
      },
    };

    await applyActionProposal(proposal);

    expect(portfolioApi.createTrade).toHaveBeenCalledWith({
      accountId: 1,
      symbol: '005827',
      tradeDate: '2026-09-22',
      side: 'buy',
      quantity: 25900,
      price: 1.6706,
      fee: 0,
      tax: 0,
    });
  });

  it('dispatches portfolio ledger and account kinds', async () => {
    await applyActionProposal({
      kind: 'portfolio_cash_create',
      summary: 'x',
      proposal: {
        account_id: 1,
        event_date: '2026-09-22',
        direction: 'in',
        amount: 100,
        currency: 'CNY',
        note: null,
      },
    });
    await applyActionProposal({
      kind: 'portfolio_cash_delete',
      summary: 'x',
      proposal: { entry_id: 3 },
    });
    await applyActionProposal({
      kind: 'portfolio_corporate_create',
      summary: 'x',
      proposal: {
        account_id: 1,
        symbol: '600519',
        effective_date: '2026-09-22',
        action_type: 'cash_dividend',
        market: 'cn',
        currency: 'CNY',
        cash_dividend_per_share: 1.2,
        split_ratio: null,
        note: null,
      },
    });
    await applyActionProposal({
      kind: 'portfolio_corporate_delete',
      summary: 'x',
      proposal: { action_id: 4 },
    });
    await applyActionProposal({
      kind: 'portfolio_account_create',
      summary: 'x',
      proposal: {
        name: 'n',
        broker: null,
        market: 'cn',
        base_currency: 'CNY',
        owner_id: null,
      },
    });
    await applyActionProposal({
      kind: 'portfolio_account_update',
      summary: 'x',
      proposal: { account_id: 2, name: 'new', is_active: false },
    });
    await applyActionProposal({
      kind: 'portfolio_account_deactivate',
      summary: 'x',
      proposal: { account_id: 2 },
    });

    expect(portfolioApi.createCashLedger).toHaveBeenCalledWith(expect.objectContaining({
      accountId: 1,
      eventDate: '2026-09-22',
      direction: 'in',
      amount: 100,
      currency: 'CNY',
    }));
    expect(portfolioApi.deleteCashLedger).toHaveBeenCalledWith(3);
    expect(portfolioApi.createCorporateAction).toHaveBeenCalledWith(expect.objectContaining({
      accountId: 1,
      effectiveDate: '2026-09-22',
      actionType: 'cash_dividend',
      cashDividendPerShare: 1.2,
    }));
    expect(portfolioApi.deleteCorporateAction).toHaveBeenCalledWith(4);
    expect(portfolioApi.createAccount).toHaveBeenCalledWith(expect.objectContaining({
      name: 'n',
      baseCurrency: 'CNY',
    }));
    expect(portfolioApi.updateAccount).toHaveBeenCalledWith(2, expect.objectContaining({
      name: 'new',
      isActive: false,
    }));
    expect(portfolioApi.deleteAccount).toHaveBeenCalledWith(2);
  });

  it('dispatches signal kinds to decisionSignalsApi', async () => {
    await applyActionProposal({
      kind: 'signal_create',
      summary: 'x',
      proposal: {
        stock_code: '600519',
        market: 'cn',
        source_type: 'manual',
        trigger_source: 'agent',
        action: 'buy',
      },
    });
    await applyActionProposal({
      kind: 'signal_status',
      summary: 'x',
      proposal: { signal_id: 11, status: 'expired' },
    });
    await applyActionProposal({
      kind: 'signal_feedback',
      summary: 'x',
      proposal: { signal_id: 11, feedback_value: 'helpful', reason_code: 'clear' },
    });
    await applyActionProposal({
      kind: 'signal_reassess',
      summary: 'x',
      proposal: { source_report_id: 5, decision_profile: 'balanced', persist: true },
    });

    expect(decisionSignalsApi.create).toHaveBeenCalledWith(expect.objectContaining({
      stockCode: '600519',
      sourceType: 'manual',
      triggerSource: 'agent',
    }));
    expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(11, { status: 'expired', metadata: undefined });
    expect(decisionSignalsApi.putFeedback).toHaveBeenCalledWith(11, {
      feedbackValue: 'helpful',
      reasonCode: 'clear',
      note: undefined,
    });
    expect(decisionSignalsApi.reassess).toHaveBeenCalledWith({
      sourceReportId: 5,
      decisionProfile: 'balanced',
      persist: true,
    });
  });

  it('preserves opaque nested signal JSON while converting only top-level schema fields', async () => {
    const evidence = {
      price_levels: { entry_low: 1, stop_loss: 2 },
      nested_list: [{ change_pct: 3 }],
    };
    const metadata = {
      source_tags: ['manual'],
      raw_payload: { source_report_id: 42, decision_profile: 'balanced' },
    };
    const watchConditions = { invalidate_if: { below: 1 } };
    const dataQualitySummary = { missing_fields: ['pe_ratio'] };

    await applyActionProposal({
      kind: 'signal_create',
      summary: 'x',
      proposal: {
        stock_code: '600519',
        stock_name: '贵州茅台',
        market: 'cn',
        source_type: 'manual',
        source_agent: 'agent',
        source_report_id: 42,
        decision_profile: 'balanced',
        trigger_source: 'agent',
        action: 'buy',
        entry_low: 1.5,
        evidence,
        metadata,
        watch_conditions: watchConditions,
        data_quality_summary: dataQualitySummary,
      },
    });

    const request = vi.mocked(decisionSignalsApi.create).mock.calls[0][0];
    expect(request).toEqual(expect.objectContaining({
      stockCode: '600519',
      stockName: '贵州茅台',
      sourceType: 'manual',
      sourceReportId: 42,
      decisionProfile: 'balanced',
      triggerSource: 'agent',
      entryLow: 1.5,
    }));
    // opaque JSON 必须逐键原样保留（snake_case 不能被递归改成 camelCase）。
    expect(request.evidence).toEqual(evidence);
    expect(request.metadata).toEqual(metadata);
    expect(request.watchConditions).toEqual(watchConditions);
    expect(request.dataQualitySummary).toEqual(dataQualitySummary);
    expect((request.metadata as Record<string, unknown>).raw_payload).toEqual({
      source_report_id: 42,
      decision_profile: 'balanced',
    });
  });

  it('preserves nested signal status metadata verbatim', async () => {
    const metadata = { raw_payload: { source_report_id: 7 } };
    await applyActionProposal({
      kind: 'signal_status',
      summary: 'x',
      proposal: { signal_id: 11, status: 'expired', metadata },
    });

    expect(decisionSignalsApi.updateStatus).toHaveBeenCalledWith(11, {
      status: 'expired',
      metadata,
    });
  });

  it('dispatches paper_reset to paperApi.reset', async () => {
    await applyActionProposal({ kind: 'paper_reset', summary: 'x', proposal: { initial_capital: 100000 } });
    expect(paperApi.reset).toHaveBeenCalledWith(100000);
  });

  it('dispatches history deletion kinds', async () => {
    await applyActionProposal({ kind: 'history_delete_records', summary: 'x', proposal: { record_ids: [1, 2] } });
    await applyActionProposal({ kind: 'history_delete_by_code', summary: 'x', proposal: { stock_code: '600519' } });

    expect(historyApi.deleteRecords).toHaveBeenCalledWith([1, 2]);
    expect(historyApi.deleteByCode).toHaveBeenCalledWith('600519');
  });

  it('dispatches intelligence source kinds', async () => {
    await applyActionProposal({
      kind: 'intelligence_source_create',
      summary: 'x',
      proposal: {
        name: 'n',
        url: 'https://example.com/feed',
        source_type: 'rss',
        enabled: true,
        scope_type: 'market',
        scope_value: null,
        market: 'cn',
      },
    });
    await applyActionProposal({ kind: 'intelligence_defaults_create', summary: 'x', proposal: {} });

    expect(intelligenceApi.createSource).toHaveBeenCalledWith(expect.objectContaining({
      name: 'n',
      sourceType: 'rss',
      scopeType: 'market',
      scopeValue: null,
    }));
    expect(intelligenceApi.createDefaults).toHaveBeenCalledWith();
  });

  it('dispatches config and scheduler kinds to systemConfigApi', async () => {
    vi.mocked(systemConfigApi.update).mockResolvedValue({ success: true } as never);
    vi.mocked(systemConfigApi.testNotificationChannel).mockResolvedValue({
      success: true,
      message: 'feishu 通知测试成功',
    } as never);

    await applyActionProposal({
      kind: 'config_update',
      summary: 'x',
      proposal: { config_version: 'v1', items: [{ key: 'STOCK_LIST', value: '600519' }] },
    });
    await applyActionProposal({ kind: 'scheduler_run', summary: 'x', proposal: {} });
    await applyActionProposal({
      kind: 'notification_test',
      summary: 'x',
      proposal: { channel: 'feishu', title: 't' },
    });

    expect(systemConfigApi.update).toHaveBeenCalledWith({
      configVersion: 'v1',
      items: [{ key: 'STOCK_LIST', value: '600519' }],
    });
    expect(systemConfigApi.runSchedulerNow).toHaveBeenCalledWith();
    expect(systemConfigApi.testNotificationChannel).toHaveBeenCalledWith({
      channel: 'feishu',
      title: 't',
    });
  });

  it('surfaces a failed notification channel test instead of reporting success', async () => {
    vi.mocked(systemConfigApi.testNotificationChannel).mockResolvedValue({
      success: false,
      message: 'feishu 通知测试失败',
      errorCode: 'send_failed',
      retryable: false,
    } as never);

    await expect(applyActionProposal({
      kind: 'notification_test',
      summary: 'x',
      proposal: { channel: 'feishu' },
    })).rejects.toThrow('feishu 通知测试失败');
  });

  it('surfaces a failed config update instead of reporting success', async () => {
    vi.mocked(systemConfigApi.update).mockResolvedValue({
      success: false,
      configVersion: 'v1',
      appliedCount: 0,
      skippedMaskedCount: 0,
      reloadTriggered: false,
      updatedKeys: [],
      warnings: ['配置写入失败'],
    });

    await expect(applyActionProposal({
      kind: 'config_update',
      summary: 'x',
      proposal: { config_version: 'v1', items: [{ key: 'STOCK_LIST', value: '600519' }] },
    })).rejects.toThrow('配置写入失败');
  });

  it('rejects an unknown kind instead of silently doing nothing', async () => {
    await expect(
      applyActionProposal({
        kind: 'drop_table' as never,
        summary: 'x',
        proposal: {},
      }),
    ).rejects.toThrow(/drop_table/);
  });
});
