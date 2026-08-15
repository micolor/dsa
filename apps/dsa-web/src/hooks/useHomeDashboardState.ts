import { useShallow } from 'zustand/react/shallow';
import { useStockPoolStore } from '../stores';

/**
 * Keep HomePage focused on local UI state while the store owns dashboard business state.
 * This preserves the current visual contract and only centralizes state selection.
 *
 * The selector intentionally picks only the fields HomePage consumes. Each subscribed
 * field forces a re-render of HomePage when it changes, so unused fields are trimmed
 * to avoid unrelated store updates triggering whole-page re-renders.
 */
export function useHomeDashboardState() {
  return useStockPoolStore(
    useShallow((state) => ({
      query: state.query,
      inputError: state.inputError,
      duplicateError: state.duplicateError,
      error: state.error,
      isAnalyzing: state.isAnalyzing,
      marketReviewHistoryItems: state.marketReviewHistoryItems,
      selectedReport: state.selectedReport,
      isLoadingReport: state.isLoadingReport,
      isHistoryTrendOpen: state.isHistoryTrendOpen,
      stockHistoryItems: state.stockHistoryItems,
      stockHistoryTotal: state.stockHistoryTotal,
      stockHistoryHasMore: state.stockHistoryHasMore,
      isLoadingStockHistory: state.isLoadingStockHistory,
      isLoadingMoreStockHistory: state.isLoadingMoreStockHistory,
      stockHistoryError: state.stockHistoryError,
      stockHistoryFilters: state.stockHistoryFilters,
      markdownDrawerOpen: state.markdownDrawerOpen,
      notify: state.notify,
      setQuery: state.setQuery,
      setNotify: state.setNotify,
      clearError: state.clearError,
      loadInitialHistory: state.loadInitialHistory,
      refreshHistory: state.refreshHistory,
      refreshHistoryForCompletedTask: state.refreshHistoryForCompletedTask,
      loadMarketReviewHistory: state.loadMarketReviewHistory,
      refreshMarketReviewHistory: state.refreshMarketReviewHistory,
      selectHistoryItem: state.selectHistoryItem,
      submitAnalysis: state.submitAnalysis,
      syncTaskCreated: state.syncTaskCreated,
      syncTaskUpdated: state.syncTaskUpdated,
      syncTaskFailed: state.syncTaskFailed,
      refreshActiveTasks: state.refreshActiveTasks,
      removeTask: state.removeTask,
      openMarkdownDrawer: state.openMarkdownDrawer,
      closeMarkdownDrawer: state.closeMarkdownDrawer,
      openHistoryTrend: state.openHistoryTrend,
      closeHistoryTrend: state.closeHistoryTrend,
      setStockHistoryRange: state.setStockHistoryRange,
      loadMoreStockHistory: state.loadMoreStockHistory,
      stockBarItems: state.stockBarItems,
      isLoadingStockBar: state.isLoadingStockBar,
      stockBarRefreshFailed: state.stockBarRefreshFailed,
      loadStockBar: state.loadStockBar,
      refreshStockBar: state.refreshStockBar,
    })),
  );
}

export default useHomeDashboardState;
