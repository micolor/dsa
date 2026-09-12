import type { TaskInfo } from '../types/analysis';

/**
 * 大盘复盘任务在任务队列里的 `stock_code`。
 *
 * 对应后端 `api/v1/endpoints/analysis.py` 的 `trigger_market_review`，它调用
 * `submit_background_task(stock_code="market_review", ...)`，**没有**传 report_type，
 * 于是 report_type 取默认值 `"detailed"`。因此判断「这是不是大盘复盘任务」只能看
 * stock_code，`task.reportType === 'market_review'` 对这类任务永远不成立
 * （`market_review` 是报告类型，不是这里的任务类型）。
 */
export const MARKET_REVIEW_TASK_STOCK_CODE = 'market_review';

/** 是否为大盘复盘任务。同时兼容未来显式传入 report_type 的情况。 */
export function isMarketReviewTask(task: Pick<TaskInfo, 'stockCode' | 'reportType'>): boolean {
  return task.stockCode === MARKET_REVIEW_TASK_STOCK_CODE || task.reportType === 'market_review';
}

/** 任务是否仍在队列中（尚未落到终态）。 */
export function isTaskInFlight(task: Pick<TaskInfo, 'status'>): boolean {
  return task.status === 'pending' || task.status === 'processing';
}
