import { alertsApi } from '../api/alerts';
import { decisionSignalsApi } from '../api/decisionSignals';
import { historyApi } from '../api/history';
import { intelligenceApi } from '../api/intelligence';
import { paperApi } from '../api/paper';
import { portfolioApi } from '../api/portfolio';
import { systemConfigApi } from '../api/systemConfig';
import { toCamelCase } from '../api/utils';
import type { AlertRuleCreateRequest } from '../types/alerts';
import type {
  DecisionSignalCreateRequest,
  DecisionSignalFeedbackRequest,
  DecisionSignalReassessRequest,
  DecisionSignalStatusUpdateRequest,
} from '../types/decisionSignals';
import type {
  PortfolioAccountCreateRequest,
  PortfolioCashLedgerCreateRequest,
  PortfolioCorporateActionCreateRequest,
  PortfolioTradeCreateRequest,
} from '../types/portfolio';
import type {
  TestNotificationChannelRequest,
  UpdateSystemConfigRequest,
} from '../types/systemConfig';
import type { ActionProposal } from '../types/actionProposal';

/**
 * 把 signal_create 的后端 snake_case 顶层字段映射成客户端请求类型。
 *
 * 不能对整份 payload 做递归 camelCase：`evidence` / `metadata` /
 * `watch_conditions` / `data_quality_summary` 是 opaque JSON，内部键必须原样透传
 * （`decisionSignalsApi.create` 也不会再转换它们）。因此这里只逐个映射 schema
 * 的顶层字段，嵌套值直接引用。
 */
function toSignalCreateRequest(payload: unknown): DecisionSignalCreateRequest {
  const source = (payload ?? {}) as Record<string, unknown>;
  return {
    stockCode: source.stock_code as string,
    stockName: source.stock_name as DecisionSignalCreateRequest['stockName'],
    market: source.market as DecisionSignalCreateRequest['market'],
    sourceType: source.source_type as DecisionSignalCreateRequest['sourceType'],
    sourceAgent: source.source_agent as DecisionSignalCreateRequest['sourceAgent'],
    sourceReportId: source.source_report_id as DecisionSignalCreateRequest['sourceReportId'],
    traceId: source.trace_id as DecisionSignalCreateRequest['traceId'],
    decisionProfile: source.decision_profile as DecisionSignalCreateRequest['decisionProfile'],
    marketPhase: source.market_phase as DecisionSignalCreateRequest['marketPhase'],
    triggerSource: source.trigger_source as string,
    action: source.action as DecisionSignalCreateRequest['action'],
    actionLabel: source.action_label as DecisionSignalCreateRequest['actionLabel'],
    confidence: source.confidence as DecisionSignalCreateRequest['confidence'],
    score: source.score as DecisionSignalCreateRequest['score'],
    horizon: source.horizon as DecisionSignalCreateRequest['horizon'],
    entryLow: source.entry_low as DecisionSignalCreateRequest['entryLow'],
    entryHigh: source.entry_high as DecisionSignalCreateRequest['entryHigh'],
    stopLoss: source.stop_loss as DecisionSignalCreateRequest['stopLoss'],
    targetPrice: source.target_price as DecisionSignalCreateRequest['targetPrice'],
    // 以下字段是 opaque JSON，保持原始对象/数组，不递归转换键名。
    invalidation: source.invalidation,
    watchConditions: source.watch_conditions,
    reason: source.reason,
    riskSummary: source.risk_summary,
    catalystSummary: source.catalyst_summary,
    evidence: source.evidence as DecisionSignalCreateRequest['evidence'],
    dataQualitySummary: source.data_quality_summary as DecisionSignalCreateRequest['dataQualitySummary'],
    planQuality: source.plan_quality as DecisionSignalCreateRequest['planQuality'],
    status: source.status as DecisionSignalCreateRequest['status'],
    expiresAt: source.expires_at as DecisionSignalCreateRequest['expiresAt'],
    metadata: source.metadata as DecisionSignalCreateRequest['metadata'],
    reportLanguage: source.report_language as DecisionSignalCreateRequest['reportLanguage'],
  };
}

/**
 * 执行一条经用户确认的提案。
 *
 * 写入发生在浏览器会话下、走既有 REST 接口，AI 从不持有写权限；这里只负责把后端
 * 原样的 snake_case 请求体转成各客户端的入参形状并调用。每个客户端再负责把 camelCase
 * 入参序列化回后端 schema 的 snake_case，因此不存在两套手写字段映射。
 *
 * kind 不在白名单时抛错（不会静默什么都不做），调用方可据此包 try/catch 提示用户。
 * 末尾的 `never` 断言是穷尽性检查：新增 kind 但忘了补分发时，`tsc` 会直接报错。
 */
export async function applyActionProposal(proposal: ActionProposal): Promise<void> {
  switch (proposal.kind) {
    case 'alert_create':
      await alertsApi.createRule(toCamelCase<AlertRuleCreateRequest>(proposal.proposal));
      return;
    case 'alert_update': {
      const { ruleId, ...rest } = toCamelCase<AlertRuleCreateRequest & { ruleId: number }>(
        proposal.proposal,
      );
      await alertsApi.updateRule(ruleId, rest);
      return;
    }
    case 'alert_delete':
      await alertsApi.deleteRule(Number((proposal.proposal as { rule_id: number }).rule_id));
      return;
    case 'portfolio_trade':
      await portfolioApi.createTrade(
        toCamelCase<PortfolioTradeCreateRequest>(proposal.proposal),
      );
      return;
    case 'portfolio_cash_create':
      await portfolioApi.createCashLedger(
        toCamelCase<PortfolioCashLedgerCreateRequest>(proposal.proposal),
      );
      return;
    case 'portfolio_cash_delete':
      await portfolioApi.deleteCashLedger(Number((proposal.proposal as { entry_id: number }).entry_id));
      return;
    case 'portfolio_corporate_create':
      await portfolioApi.createCorporateAction(
        toCamelCase<PortfolioCorporateActionCreateRequest>(proposal.proposal),
      );
      return;
    case 'portfolio_corporate_delete':
      await portfolioApi.deleteCorporateAction(
        Number((proposal.proposal as { action_id: number }).action_id),
      );
      return;
    case 'portfolio_account_create':
      await portfolioApi.createAccount(
        toCamelCase<PortfolioAccountCreateRequest>(proposal.proposal),
      );
      return;
    case 'portfolio_account_update': {
      const { accountId, ...rest } = toCamelCase<
        Partial<PortfolioAccountCreateRequest> & { accountId: number; isActive?: boolean }
      >(proposal.proposal);
      await portfolioApi.updateAccount(accountId, rest);
      return;
    }
    case 'portfolio_account_deactivate':
      await portfolioApi.deleteAccount(
        Number((proposal.proposal as { account_id: number }).account_id),
      );
      return;
    case 'signal_create':
      await decisionSignalsApi.create(toSignalCreateRequest(proposal.proposal));
      return;
    case 'signal_status': {
      const source = (proposal.proposal ?? {}) as Record<string, unknown>;
      const request: DecisionSignalStatusUpdateRequest = {
        status: source.status as DecisionSignalStatusUpdateRequest['status'],
        // metadata 是 opaque JSON，原样透传，不能递归 camelCase。
        metadata: source.metadata as DecisionSignalStatusUpdateRequest['metadata'],
      };
      await decisionSignalsApi.updateStatus(Number(source.signal_id), request);
      return;
    }
    case 'signal_feedback': {
      const source = (proposal.proposal ?? {}) as Record<string, unknown>;
      const request: DecisionSignalFeedbackRequest = {
        feedbackValue: source.feedback_value as DecisionSignalFeedbackRequest['feedbackValue'],
        reasonCode: source.reason_code as string | undefined,
        note: source.note as string | undefined,
      };
      await decisionSignalsApi.putFeedback(Number(source.signal_id), request);
      return;
    }
    case 'signal_reassess': {
      const source = (proposal.proposal ?? {}) as Record<string, unknown>;
      const request: DecisionSignalReassessRequest = {
        sourceReportId: Number(source.source_report_id),
        decisionProfile: source.decision_profile as DecisionSignalReassessRequest['decisionProfile'],
        persist: source.persist as boolean | undefined,
      };
      await decisionSignalsApi.reassess(request);
      return;
    }
    case 'paper_reset': {
      const payload = toCamelCase<{ initialCapital?: number }>(proposal.proposal);
      await paperApi.reset(payload.initialCapital);
      return;
    }
    case 'history_delete_records':
      await historyApi.deleteRecords(
        (proposal.proposal as { record_ids: number[] }).record_ids,
      );
      return;
    case 'history_delete_by_code':
      await historyApi.deleteByCode((proposal.proposal as { stock_code: string }).stock_code);
      return;
    case 'intelligence_source_create':
      await intelligenceApi.createSource(toCamelCase<Record<string, unknown>>(proposal.proposal));
      return;
    case 'intelligence_defaults_create':
      await intelligenceApi.createDefaults();
      return;
    case 'config_update': {
      const result = await systemConfigApi.update(
        toCamelCase<UpdateSystemConfigRequest>(proposal.proposal),
      );
      // 该端点用 200 表示「请求已处理」，写入失败通过 success=false 表达；
      // 不检查的话卡片会显示「已提交」，把真实失败藏起来。
      if (!result.success) {
        throw new Error(result.warnings?.join('；') || '系统配置更新失败');
      }
      return;
    }
    case 'scheduler_run':
      // run-now 在未接受时返回 409（scheduler_busy），2xx 一定是已接受。
      await systemConfigApi.runSchedulerNow();
      return;
    case 'notification_test': {
      const result = await systemConfigApi.testNotificationChannel(
        toCamelCase<TestNotificationChannelRequest>(proposal.proposal),
      );
      // 通知发送失败同样以 2xx + success=false 返回；必须抛出，让卡片进入失败态
      // 并展示渠道返回的失败原因（例如 webhook 拒绝、凭据无效）。
      if (!result.success) {
        throw new Error(result.message?.trim() || result.errorCode || '通知渠道测试失败');
      }
      return;
    }
    default: {
      const exhaustive: never = proposal.kind;
      throw new Error(`unsupported action proposal kind: ${String(exhaustive)}`);
    }
  }
}
