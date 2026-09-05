import type React from 'react';
import { lazy, memo, Suspense, useCallback, useMemo } from 'react';
import { BarChart3 } from 'lucide-react';
import { ApiErrorAlert, Button, EmptyState, InlineAlert } from '../common';
import { DashboardStateBlock } from '../dashboard';
import { StockHistoryTrendDrawer } from '../history';
import { MarketReviewReportView } from './MarketReviewReportView';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { ParsedApiError } from '../../api/error';
import { normalizeReportLanguage } from '../../utils/reportLanguage';
import type {
  AnalysisReport,
  HistoryItem,
  MarketReviewPayload,
  ReportLanguage,
  StockHistoryFilters,
  StockHistoryRange,
} from '../../types/analysis';

// ReportSummary 承载 chart 重子树（StockPriceChart -> recharts，vendor-charts 392K）。
// 它仅在某只个股报告被选中时渲染（首页裸空态不渲染），所以懒加载可把 recharts
// 从首屏同步 bundle 剥离：空态挂载时完全不拉该 chunk，选报告时才按需加载。
const ReportSummary = lazy(() =>
  import('./ReportSummary').then((m) => ({ default: m.ReportSummary })),
);

export type MarketReviewNotice = {
  variant: 'success' | 'warning' | 'danger';
  title: string;
  message: string;
} | null;

export interface HomeReportRegionProps {
  marketReviewNotice: MarketReviewNotice;
  marketReviewError: ParsedApiError | null;
  onDismissMarketReviewError: () => void;
  marketReviewReport: string | null;
  marketReviewPayload: MarketReviewPayload | null;
  error: ParsedApiError | null;
  onDismissError: () => void;
  isLoadingReport: boolean;
  selectedReport: AnalysisReport | null;
  isAnalyzing: boolean;
  isSubmittingMarketReview: boolean;
  onReanalyze: () => void;
  onAskFollowUp: () => void;
  onTriggerMarketReview: () => void;
  isHistoryTrendOpen: boolean;
  onToggleHistoryTrend: () => void;
  onOpenMarkdownDrawer: () => void;
  stockHistoryItems: HistoryItem[];
  stockHistoryTotal: number;
  stockHistoryHasMore: boolean;
  isLoadingStockHistory: boolean;
  isLoadingMoreStockHistory: boolean;
  stockHistoryError: ParsedApiError | null;
  stockHistoryFilters: StockHistoryFilters;
  onCloseHistoryTrend: () => void;
  onSetStockHistoryRange: (range: StockHistoryRange) => void;
  onLoadMoreStockHistory: () => void;
  onSelectStockHistoryRecord: (recordId: number) => void;
  onOpenHistoryTrend: () => void;
  isInWatchlist: (code: string) => boolean;
  onToggleWatchlist: (code: string) => Promise<void>;
  isActioning: boolean;
  actionMessage: string | null;
  onOpenRunFlow: (recordId: number) => void;
}

/**
 * 首页右侧报告区（大盘复盘状态/报告 + 个股报告 + 历史趋势 + 空态）。
 *
 * 该区域不依赖 `activeTasks`：任务进度 SSE 更新只会触发 HomePage 重渲染，
 * 而本组件以 `memo` 包裹且所有 props 在任务进度期间引用稳定，因此任务跑动时
 * 昂贵的报告子树不会随每次 progress 事件重建。
 */
const HomeReportRegionInner: React.FC<HomeReportRegionProps> = ({
  marketReviewNotice,
  marketReviewError,
  onDismissMarketReviewError,
  marketReviewReport,
  marketReviewPayload,
  error,
  onDismissError,
  isLoadingReport,
  selectedReport,
  isAnalyzing,
  isSubmittingMarketReview,
  onReanalyze,
  onAskFollowUp,
  onTriggerMarketReview,
  isHistoryTrendOpen,
  onToggleHistoryTrend,
  onOpenMarkdownDrawer,
  stockHistoryItems,
  stockHistoryTotal,
  stockHistoryHasMore,
  isLoadingStockHistory,
  isLoadingMoreStockHistory,
  stockHistoryError,
  stockHistoryFilters,
  onCloseHistoryTrend,
  onSetStockHistoryRange,
  onLoadMoreStockHistory,
  onSelectStockHistoryRecord,
  onOpenHistoryTrend,
  isInWatchlist,
  onToggleWatchlist,
  isActioning,
  actionMessage,
  onOpenRunFlow,
}) => {
  const { t } = useUiLanguage();
  const isMarketReviewHistoryReport = selectedReport?.meta.reportType === 'market_review';
  const isHistoryTrendUnavailable = !selectedReport || !selectedReport.meta.stockCode;
  const liveMarketReviewLanguage: ReportLanguage = normalizeReportLanguage(marketReviewPayload?.language);

  const handleRangeChange = useCallback(
    (range: StockHistoryRange) => {
      void onSetStockHistoryRange(range);
    },
    [onSetStockHistoryRange],
  );
  const handleLoadMore = useCallback(() => {
    void onLoadMoreStockHistory();
  }, [onLoadMoreStockHistory]);
  const handleSelectRecord = useCallback((recordId: number) => {
    void onSelectStockHistoryRecord(recordId);
  }, [onSelectStockHistoryRecord]);
  const handleRetry = useCallback(() => {
    void onOpenHistoryTrend();
  }, [onOpenHistoryTrend]);

  const watchlistBlock = useMemo(
    () => ({
      isInWatchlist,
      onToggle: onToggleWatchlist,
      isActioning,
      actionMessage,
    }),
    [actionMessage, isActioning, isInWatchlist, onToggleWatchlist],
  );

  return (
    <>
      {marketReviewNotice ? (
        <div className="mb-3">
          <InlineAlert
            variant={marketReviewNotice.variant}
            title={marketReviewNotice.title}
            message={marketReviewNotice.message}
            className="rounded-xl px-3 py-2 text-xs shadow-none"
          />
        </div>
      ) : null}

      {marketReviewError ? (
        <div className="mb-3">
          <ApiErrorAlert
            error={marketReviewError}
            className="mb-1"
            onDismiss={onDismissMarketReviewError}
          />
        </div>
      ) : null}

      {marketReviewReport ? (
        <MarketReviewReportView
          content={marketReviewReport}
          payload={marketReviewPayload}
          reportLanguage={liveMarketReviewLanguage}
          className="mb-3"
        />
      ) : null}

      {error ? (
        <ApiErrorAlert
          error={error}
          className="mb-3"
          onDismiss={onDismissError}
        />
      ) : null}
      {!marketReviewReport && isLoadingReport ? (
        <div className="flex h-full flex-col items-center justify-center">
          <DashboardStateBlock title={t('home.loadingReport')} loading />
        </div>
      ) : !marketReviewReport && selectedReport ? (
        <div className="space-y-4 pb-8">
          <div className="flex flex-wrap items-center justify-end gap-2">
            {!isMarketReviewHistoryReport ? (
              <>
                <Button
                  variant="home-action-ai"
                  size="sm"
                  disabled={isAnalyzing || selectedReport.meta.id === undefined}
                  onClick={onReanalyze}
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                  </svg>
                  {t('home.reanalyze')}
                </Button>
                <Button
                  variant="home-action-ai"
                  size="sm"
                  disabled={selectedReport.meta.id === undefined}
                  onClick={onAskFollowUp}
                >
                  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                  </svg>
                  {t('home.askAi')}
                </Button>
              </>
            ) : (
              <Button
                variant="home-action-ai"
                size="sm"
                disabled={isSubmittingMarketReview}
                isLoading={isSubmittingMarketReview}
                loadingText={t('home.submitMarketReview')}
                onClick={onTriggerMarketReview}
              >
                <BarChart3 className="h-4 w-4" />
                {t('home.rerunMarketReview')}
              </Button>
            )}
            <Button
              variant="home-action-ai"
              size="sm"
              disabled={selectedReport.meta.id === undefined || isHistoryTrendUnavailable}
              className={isHistoryTrendOpen ? 'border-primary/70 bg-primary/15 text-primary shadow-glow-cyan' : undefined}
              onClick={onToggleHistoryTrend}
            >
              <BarChart3 className="h-4 w-4" />
              {t('home.historyTrend')}
            </Button>
            <Button
              variant="home-action-ai"
              size="sm"
              disabled={selectedReport.meta.id === undefined}
              onClick={onOpenMarkdownDrawer}
            >
              <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              {t('home.fullReport')}
            </Button>
          </div>
          {isHistoryTrendOpen ? (
            <StockHistoryTrendDrawer
              key={`stock-history-${selectedReport.meta.id}`}
              report={selectedReport}
              items={stockHistoryItems}
              total={stockHistoryTotal}
              hasMore={stockHistoryHasMore}
              isLoading={isLoadingStockHistory}
              isLoadingMore={isLoadingMoreStockHistory}
              error={stockHistoryError}
              filters={stockHistoryFilters}
              onClose={onCloseHistoryTrend}
              onRangeChange={handleRangeChange}
              onLoadMore={handleLoadMore}
              onSelectRecord={handleSelectRecord}
              onRetry={handleRetry}
            />
          ) : (
            <Suspense
              fallback={(
                <div className="flex min-h-[16rem] items-center justify-center">
                  <DashboardStateBlock title={t('home.loadingReport')} loading />
                </div>
              )}
            >
              <ReportSummary
                data={selectedReport}
                isHistory
                onOpenRunFlow={onOpenRunFlow}
                watchlist={watchlistBlock}
              />
            </Suspense>
          )}
        </div>
      ) : !marketReviewReport ? (
        <div className="flex h-full items-center justify-center">
          <EmptyState
            title={t('home.startAnalysisTitle')}
            description={t('home.startAnalysisDescription')}
            className="max-w-xl border-dashed"
            icon={(
              <svg className="h-6 w-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            )}
          />
        </div>
      ) : null}
    </>
  );
};

export const HomeReportRegion = memo(HomeReportRegionInner);
HomeReportRegion.displayName = 'HomeReportRegion';
