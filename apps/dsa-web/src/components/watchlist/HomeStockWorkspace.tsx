import type React from 'react';
import { memo, useCallback, useMemo, useState } from 'react';
import { useShallow } from 'zustand/react/shallow';
import {
  ArrowDownWideNarrow,
  CalendarDays,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Loader2,
  Play,
  Plus,
  RefreshCw,
  Star,
} from 'lucide-react';
import { Badge, Button, InlineAlert, Input, ListItemRow, ScrollArea, SentimentBadge, StatusDot, Tooltip } from '../common';
import { DashboardPanelHeader, DashboardStateBlock } from '../dashboard';
import { StockBar } from '../history';
import { useStockPoolStore } from '../../stores';
import type { StockBarItem, TaskInfo } from '../../types/analysis';
import { getSentimentColor } from '../../types/analysis';
import { buildDecisionActionLabelMap, getDecisionActionLabel } from '../../utils/decisionAction';
import { formatDateTime } from '../../utils/format';
import { areStockCodesEquivalent, normalizeStockCode } from '../../utils/stockCode';
import { truncateStockName } from '../../utils/stockName';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey, UiTextParams } from '../../i18n/uiText';

/** 归一化股票代码键，与 HomePage 的自选/任务 key 保持一致。 */
function getStockCodeKey(code?: string | null): string {
  const trimmed = (code ?? '').trim();
  return trimmed ? normalizeStockCode(trimmed).toUpperCase() : '';
}

/** 由活动任务推导「代码 → 运行中任务」映射（排除大盘复盘与已结束状态）。 */
function buildActiveTaskByCode(tasks: TaskInfo[]): Map<string, TaskInfo> {
  const tasksByCode = new Map<string, TaskInfo>();
  for (const task of tasks) {
    if (!['pending', 'processing', 'cancel_requested'].includes(task.status)) {
      continue;
    }
    if (task.reportType === 'market_review') {
      continue;
    }
    const key = getStockCodeKey(task.stockCode);
    if (key) {
      tasksByCode.set(key, task);
    }
  }
  return tasksByCode;
}

export type HomeWorkspaceTab = 'watchlist' | 'today' | 'history';
export type WatchlistAnalyzeMode = 'all' | 'pending';

export interface HomeWatchlistRow {
  code: string;
  latestItem?: StockBarItem;
  analyzedToday: boolean;
  isTodayStatusLoading?: boolean;
  isTodayStatusUnknown?: boolean;
  activeTask?: TaskInfo;
}

interface BatchStatus {
  variant: 'success' | 'warning' | 'danger';
  message: string;
}

interface HomeStockWorkspaceProps {
  activeTab: HomeWorkspaceTab;
  onTabChange: (tab: HomeWorkspaceTab) => void;
  watchlistRows: HomeWatchlistRow[];
  watchlistLoading: boolean;
  watchlistActioning: boolean;
  watchlistMessage: string | null;
  onAddToWatchlist: (code: string) => Promise<void>;
  onRemoveFromWatchlist: (code: string) => Promise<void>;
  onRefreshWatchlist: () => Promise<void>;
  onAnalyzeWatchlist: (mode: WatchlistAnalyzeMode) => Promise<void>;
  isBatchAnalyzing: boolean;
  batchStatus: BatchStatus | null;
  todayItems: StockBarItem[];
  isLoadingTodayItems: boolean;
  todayLoadError: boolean;
  watchlistAnalyzedTodayCount: number;
  historyItems: StockBarItem[];
  isLoadingHistory: boolean;
  selectedStockCode?: string;
  selectedRecordId?: number;
  onHistoryItemClick: (recordId: number) => void;
  onDeleteStock?: (stockCode: string) => Promise<void> | void;
  isDeleting?: boolean;
  className?: string;
}

function getTaskStatusLabel(task: TaskInfo | undefined, t: (key: UiTextKey, params?: UiTextParams) => string) {
  if (!task) return '';
  if (task.status === 'processing') return t('taskPanel.processing');
  if (task.status === 'pending') return t('taskPanel.pending');
  if (task.status === 'cancel_requested') return t('taskPanel.cancelRequested');
  return task.status;
}

const ScoreBadge: React.FC<{ item?: StockBarItem }> = ({ item }) => {
  const { t } = useUiLanguage();
  const score = typeof item?.sentimentScore === 'number' ? item.sentimentScore : null;
  const color = score !== null ? getSentimentColor(score) : null;
  if (score === null || !color) {
    return <span className="text-[11px] text-muted-text">{t('common.noData')}</span>;
  }

  const actionLabels = buildDecisionActionLabelMap(t);
  const operationLabel = getDecisionActionLabel(
    item?.action,
    item?.actionLabel,
    item?.operationAdvice,
    t('history.sentiment'),
    actionLabels,
  );

  return <SentimentBadge color={color} operationLabel={operationLabel} score={score} />;
};

const WatchlistRowItemInner: React.FC<{
  row: HomeWatchlistRow;
  activeTask?: TaskInfo;
  onRemove: (code: string) => Promise<void>;
  onOpenDetail: (row: HomeWatchlistRow) => void;
  disabled: boolean;
  selected: boolean;
}> = ({ row, activeTask, onRemove, onOpenDetail, disabled, selected }) => {
  const { t } = useUiLanguage();
  const taskLabel = getTaskStatusLabel(activeTask, t);
  const isLatestDetailLoading = Boolean(row.isTodayStatusLoading);
  const isLatestDetailUnavailable = !isLatestDetailLoading && Boolean(row.isTodayStatusUnknown);
  // Keep showing the last-known detail during a refresh (so the row doesn't blank
  // out and jitter); loading only blocks opening it, preventing stale-detail clicks.
  const item = row.latestItem;
  const stockName = item?.stockName || row.code;
  const canOpenDetail = !isLatestDetailLoading && !isLatestDetailUnavailable && typeof item?.id === 'number';

  const handleOpenDetail = () => {
    onOpenDetail(row);
  };

  const score = typeof item?.sentimentScore === 'number' ? item.sentimentScore : null;
  const color = score !== null ? getSentimentColor(score) : null;
  const leading = color ? (
    <div
      className="w-1 h-8 rounded-full flex-shrink-0"
      style={{ backgroundColor: color, boxShadow: `0 0 10px ${color}40` }}
    />
  ) : (
    <div className="w-1 h-8 rounded-full flex-shrink-0 bg-subtle" />
  );

  return (
    <ListItemRow
      wrapperClassName="home-history-item w-full min-w-0 flex-1"
      wrapperTestId={`watchlist-row-${row.code}`}
      buttonClassName={`w-full min-w-0 flex-1 text-left p-2.5 ${selected ? 'home-history-item-selected' : ''}`}
      pressed={selected}
      leading={leading}
      ariaLabel={canOpenDetail
        ? t('watchlist.openLatestDetailAria', { code: row.code })
        : isLatestDetailLoading
          ? t('watchlist.latestDetailLoadingAria', { code: row.code })
          : isLatestDetailUnavailable
            ? t('watchlist.latestDetailUnavailableAria', { code: row.code })
          : t('watchlist.noLatestDetailAria', { code: row.code })}
      onClick={handleOpenDetail}
      title={(
        <div className="flex min-w-0 items-center gap-2">
          <Tooltip
            content={canOpenDetail ? t('common.details') : undefined}
            className="min-w-0"
          >
            <span className="truncate text-sm font-semibold text-foreground">
              {truncateStockName(stockName)}
            </span>
          </Tooltip>
          {row.isTodayStatusLoading ? (
            <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-muted-text" aria-label={t('watchlist.todayStatusLoading')} />
          ) : row.isTodayStatusUnknown ? (
            <CircleAlert className="h-3.5 w-3.5 shrink-0 text-warning" aria-label={t('watchlist.todayStatusUnavailable')} />
          ) : row.analyzedToday ? (
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" aria-label={t('watchlist.analyzedToday')} />
          ) : (
            <Clock3 className="h-3.5 w-3.5 shrink-0 text-muted-text" aria-label={t('watchlist.notAnalyzedToday')} />
          )}
        </div>
      )}
      trailing={<ScoreBadge item={item} />}
      onDelete={() => void onRemove(row.code)}
      deleteAriaLabel={t('watchlist.removeAria', { code: row.code })}
      deleteDisabled={disabled}
      meta={(
        <>
          <span className="font-mono text-[11px] text-secondary-text">{row.code}</span>
          {item?.lastAnalysisTime ? (
            <>
              <span className="h-1 w-1 rounded-full bg-subtle-hover" />
              <span className="text-[11px] text-muted-text">{formatDateTime(item.lastAnalysisTime)}</span>
            </>
          ) : null}
        </>
      )}
      actionsTestId="watchlist-row-actions"
      footer={activeTask ? (
        <div className="flex min-w-0 items-center gap-2 text-[11px] text-muted-text">
          <StatusDot
            tone={activeTask.status === 'processing' ? 'info' : 'neutral'}
            pulse={activeTask.status === 'processing'}
            className="h-1.5 w-1.5"
          />
          <span className="truncate">{t('watchlist.taskRunning', { status: taskLabel })}</span>
        </div>
      ) : undefined}
    />
  );
};

const WatchlistRowItem = memo(WatchlistRowItemInner);

const TodayItemInner: React.FC<{ item: StockBarItem; onClick: (recordId: number) => void; selected: boolean }> = ({ item, onClick, selected }) => {
  const { t } = useUiLanguage();
  const stockName = item.stockName || item.stockCode;
  const score = typeof item.sentimentScore === 'number' ? item.sentimentScore : null;
  const color = score !== null ? getSentimentColor(score) : null;

  const leading = color ? (
    <div
      className="w-1 h-8 rounded-full flex-shrink-0"
      style={{ backgroundColor: color, boxShadow: `0 0 10px ${color}40` }}
    />
  ) : (
    <div className="w-1 h-8 rounded-full flex-shrink-0 bg-subtle" />
  );

  return (
    <ListItemRow
      wrapperClassName="home-history-item w-full min-w-0 flex-1"
      buttonClassName={`w-full min-w-0 flex-1 text-left p-2.5 ${selected ? 'home-history-item-selected' : ''}`}
      ariaLabel={t('history.itemAria', { name: stockName, code: item.stockCode })}
      onClick={() => onClick(item.id)}
      leading={leading}
      title={(
        <span className="block w-full truncate text-sm font-semibold text-foreground tracking-tight">
          {truncateStockName(stockName)}
        </span>
      )}
      trailing={<ScoreBadge item={item} />}
      meta={(
        <>
          <span className="text-[11px] text-secondary-text font-mono">{item.stockCode}</span>
          {item.lastAnalysisTime ? (
            <>
              <span className="w-1 h-1 rounded-full bg-subtle-hover" />
              <span className="text-[11px] text-muted-text">{formatDateTime(item.lastAnalysisTime)}</span>
            </>
          ) : null}
        </>
      )}
    />
  );
};

const TodayItem = memo(TodayItemInner);

export const HomeStockWorkspace: React.FC<HomeStockWorkspaceProps> = ({
  activeTab,
  onTabChange,
  watchlistRows,
  watchlistLoading,
  watchlistActioning,
  watchlistMessage,
  onAddToWatchlist,
  onRemoveFromWatchlist,
  onRefreshWatchlist,
  onAnalyzeWatchlist,
  isBatchAnalyzing,
  batchStatus,
  todayItems,
  isLoadingTodayItems,
  todayLoadError,
  watchlistAnalyzedTodayCount,
  historyItems,
  isLoadingHistory,
  selectedStockCode,
  selectedRecordId,
  onHistoryItemClick,
  onDeleteStock,
  isDeleting = false,
  className = '',
}) => {
  const { t } = useUiLanguage();
  const activeTasks = useStockPoolStore(useShallow((state) => state.activeTasks));
  const activeTaskByCode = useMemo(() => buildActiveTaskByCode(activeTasks), [activeTasks]);
  const [draftCode, setDraftCode] = useState('');
  const [workspaceNoticeCode, setWorkspaceNoticeCode] = useState<string | null>(null);
  const pendingWatchlistCount = watchlistRows
    .filter((row) => !row.analyzedToday && !row.isTodayStatusLoading && !row.isTodayStatusUnknown)
    .length;
  const isTodayStatusUnavailable = watchlistRows.some((row) => row.isTodayStatusLoading || row.isTodayStatusUnknown);
  const topTodayItem = todayItems[0];
  const tabs: Array<{ key: HomeWorkspaceTab; label: string }> = [
    { key: 'history', label: t('watchlist.tabHistory') },
    { key: 'watchlist', label: t('watchlist.tabWatchlist') },
    { key: 'today', label: t('watchlist.tabToday') },
  ];

  const statusClassName = useMemo(() => {
    if (!batchStatus) return '';
    if (batchStatus.variant === 'danger') return 'border-danger/30 bg-danger/10 text-danger';
    if (batchStatus.variant === 'warning') return 'border-warning/30 bg-warning/10 text-warning';
    return 'border-success/30 bg-success/10 text-success';
  }, [batchStatus]);

  const visibleWorkspaceNotice = useMemo(() => {
    if (!workspaceNoticeCode) return null;
    const row = watchlistRows.find((item) => areStockCodesEquivalent(item.code, workspaceNoticeCode));
    if (!row) return null;
    if (row.isTodayStatusLoading) {
      return { message: t('watchlist.latestDetailLoading') };
    }
    if (row.isTodayStatusUnknown) {
      return { message: t('watchlist.latestDetailUnavailable') };
    }
    if (row.latestItem) return null;
    return { message: t('watchlist.noLatestDetail') };
  }, [t, watchlistRows, workspaceNoticeCode]);

  const handleAddSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const code = draftCode.trim();
    if (!code) return;
    setWorkspaceNoticeCode(null);
    void onAddToWatchlist(code).then(() => setDraftCode(''));
  };

  const handleWatchlistRowOpen = useCallback((row: HomeWatchlistRow) => {
    if (row.isTodayStatusLoading || row.isTodayStatusUnknown) {
      setWorkspaceNoticeCode(row.code);
      return;
    }
    const recordId = row.latestItem?.id;
    if (typeof recordId === 'number') {
      setWorkspaceNoticeCode(null);
      onHistoryItemClick(recordId);
      return;
    }
    setWorkspaceNoticeCode(row.code);
  }, [onHistoryItemClick]);

  // 稳定引用，配合 WatchlistRowItem 的 memo 让行在父重渲染时跳过。
  const handleRemoveFromWatchlist = useCallback(async (code: string) => {
    setWorkspaceNoticeCode(null);
    await onRemoveFromWatchlist(code);
  }, [onRemoveFromWatchlist]);

  const renderTabs = (
    <div className="grid grid-cols-3 gap-1 rounded-xl border border-subtle bg-base/40 p-1">
      {tabs.map((tab) => {
        const selected = activeTab === tab.key;
        return (
          <button
            key={tab.key}
            type="button"
            aria-pressed={selected}
            className={`h-8 rounded-lg px-2 text-xs font-medium transition-colors ${
              selected ? 'bg-primary/15 text-primary shadow-inner' : 'text-secondary-text hover:bg-hover hover:text-foreground'
            }`}
            onClick={() => {
              setWorkspaceNoticeCode(null);
              onTabChange(tab.key);
            }}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );

  return (
    <div className={`flex min-h-0 flex-1 flex-col gap-2 ${className}`}>
      {renderTabs}
      {activeTab === 'history' ? (
        <StockBar
          items={historyItems}
          isLoading={isLoadingHistory}
          selectedStockCode={selectedStockCode}
          selectedRecordId={selectedRecordId}
          onItemClick={onHistoryItemClick}
          onDeleteStock={onDeleteStock}
          isDeleting={isDeleting}
          className="flex-1 overflow-hidden"
        />
      ) : (
        <aside className="glass-card flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="space-y-2 border-b border-subtle px-3 py-3 sm:px-4">
            {activeTab === 'watchlist' ? (
          <>
            <DashboardPanelHeader
              className="mb-0"
              title={t('watchlist.title')}
              titleClassName="text-sm font-medium"
              headingClassName="items-center"
              leading={<Star className="h-4 w-4 text-primary" aria-hidden="true" />}
              actions={(
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] text-muted-text">{t('common.itemsCount', { count: watchlistRows.length })}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xsm"
                    className="h-7 w-7 px-0"
                    disabled={watchlistLoading}
                    onClick={() => {
                      setWorkspaceNoticeCode(null);
                      void onRefreshWatchlist();
                    }}
                    aria-label={t('watchlist.refreshAria')}
                  >
                    <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                </div>
              )}
            />
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-muted-text">
              <span>
                {t('watchlist.todayCoverage')}{' '}
                <span className="font-medium text-secondary-text">{watchlistAnalyzedTodayCount}/{watchlistRows.length}</span>
              </span>
              <span className="h-1 w-1 rounded-full bg-subtle-hover" aria-hidden="true" />
              <span>
                {t('watchlist.pendingToday')}{' '}
                <span className="font-medium text-secondary-text">{pendingWatchlistCount}</span>
              </span>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="home-action-ai"
                className="h-8 flex-1 whitespace-nowrap px-2 text-xs sm:flex-none"
                disabled={watchlistRows.length === 0 || isBatchAnalyzing}
                isLoading={isBatchAnalyzing}
                loadingText={t('watchlist.submitting')}
                onClick={() => void onAnalyzeWatchlist('all')}
              >
                <Play className="h-4 w-4" aria-hidden="true" />
                {t('watchlist.analyzeAll')}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="home-action-report"
                className="h-8 flex-1 whitespace-nowrap px-2 text-xs sm:flex-none"
                disabled={pendingWatchlistCount === 0 || isTodayStatusUnavailable || isBatchAnalyzing}
                onClick={() => void onAnalyzeWatchlist('pending')}
              >
                <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                {t('watchlist.analyzePending')}
              </Button>
            </div>
            <form className="grid grid-cols-[minmax(0,1fr)_auto] gap-2" onSubmit={handleAddSubmit}>
              <Input
                value={draftCode}
                onChange={(event) => setDraftCode(event.target.value)}
                placeholder={t('watchlist.addPlaceholder')}
                className="h-8 rounded-lg px-3 text-xs"
                disabled={watchlistActioning}
                aria-label={t('watchlist.addPlaceholder')}
              />
              <Button
                type="submit"
                size="sm"
                variant="secondary"
                className="h-8 w-8 px-0"
                disabled={!draftCode.trim() || watchlistActioning}
                isLoading={watchlistActioning}
                aria-label={t('watchlist.add')}
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
              </Button>
            </form>
            {batchStatus ? (
              <div className={`rounded-xl border px-3 py-2 text-xs ${statusClassName}`}>
                {batchStatus.message}
              </div>
            ) : null}
            {watchlistMessage ? (
              <div className="rounded-xl border border-subtle bg-base/35 px-3 py-2 text-xs text-secondary-text">
                {watchlistMessage}
              </div>
            ) : null}
            {visibleWorkspaceNotice ? (
              <InlineAlert
                variant="warning"
                message={visibleWorkspaceNotice.message}
                className="rounded-xl px-3 py-2 text-xs shadow-none"
              />
            ) : null}
          </>
        ) : (
          <>
            <DashboardPanelHeader
              className="mb-0"
              title={t('watchlist.todayTitle')}
              titleClassName="text-sm font-medium"
              headingClassName="items-center"
              leading={<CalendarDays className="h-4 w-4 text-primary" aria-hidden="true" />}
              actions={(
                <div className="flex items-center gap-1.5">
                  <span className="text-[11px] text-muted-text">{t('common.itemsCount', { count: todayItems.length })}</span>
                  <Button
                    type="button"
                    variant="ghost"
                    size="xsm"
                    className="h-7 w-7 px-0"
                    disabled={watchlistLoading || isLoadingTodayItems}
                    onClick={() => {
                      setWorkspaceNoticeCode(null);
                      void onRefreshWatchlist();
                    }}
                    aria-label={t('watchlist.refreshAria')}
                  >
                    <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
                  </Button>
                </div>
              )}
            />
            <div className="flex flex-wrap gap-1.5">
              <Badge variant="default" className="gap-1 shadow-none text-[11px]">
                {t('watchlist.watchlistCoverage')} {watchlistAnalyzedTodayCount}/{watchlistRows.length}
              </Badge>
              <Badge variant="default" className="gap-1 shadow-none text-[11px]">
                {t('watchlist.topScore')} {topTodayItem?.sentimentScore ?? '-'}
              </Badge>
            </div>
          </>
        )}
      </div>

      <ScrollArea viewportClassName="px-3 py-3 sm:px-4" className="min-h-0 flex-1">
        {activeTab === 'watchlist' ? (
          watchlistLoading ? (
            <DashboardStateBlock loading compact title={t('watchlist.loading')} />
          ) : watchlistRows.length === 0 ? (
            <DashboardStateBlock
              compact
              title={t('watchlist.emptyTitle')}
              description={t('watchlist.emptyDescription')}
            />
          ) : (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2 text-[11px] text-muted-text">
                <ArrowDownWideNarrow className="h-3.5 w-3.5" aria-hidden="true" />
                {t('watchlist.listHint')}
              </div>
              {watchlistRows.map((row) => (
                <WatchlistRowItem
                  key={row.code}
                  row={row}
                  activeTask={activeTaskByCode.get(getStockCodeKey(row.code))}
                  onRemove={handleRemoveFromWatchlist}
                  onOpenDetail={handleWatchlistRowOpen}
                  disabled={watchlistActioning}
                  selected={
                    (typeof selectedRecordId === 'number' && selectedRecordId === row.latestItem?.id)
                    || (
                      Boolean(selectedStockCode)
                      && (
                        areStockCodesEquivalent(selectedStockCode ?? '', row.code)
                        || areStockCodesEquivalent(selectedStockCode ?? '', row.latestItem?.stockCode ?? '')
                      )
                    )
                  }
                />
              ))}
            </div>
          )
        ) : isLoadingTodayItems ? (
          <DashboardStateBlock loading compact title={t('watchlist.loading')} />
        ) : todayLoadError ? (
          <DashboardStateBlock
            compact
            title={t('watchlist.todayLoadErrorTitle')}
            description={t('watchlist.todayLoadErrorDescription')}
          />
        ) : todayItems.length === 0 ? (
          <DashboardStateBlock
            compact
            title={t('watchlist.todayEmptyTitle')}
            description={t('watchlist.todayEmptyDescription')}
          />
        ) : (
          <div className="space-y-1.5">
            <div className="flex items-center gap-2 text-[11px] text-muted-text">
              <ArrowDownWideNarrow className="h-3.5 w-3.5" aria-hidden="true" />
              {t('watchlist.todaySortHint')}
            </div>
            {todayItems.map((item) => (
              <TodayItem
                key={`${item.stockCode}-${item.id}`}
                item={item}
                onClick={onHistoryItemClick}
                selected={
                  (typeof selectedRecordId === 'number' && selectedRecordId === item.id)
                  || (
                    Boolean(selectedStockCode)
                    && areStockCodesEquivalent(selectedStockCode ?? '', item.stockCode)
                  )
                }
              />
            ))}
          </div>
            )}
          </ScrollArea>
        </aside>
      )}
    </div>
  );
};

export default HomeStockWorkspace;
