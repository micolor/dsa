import type React from 'react';
import { ScrollArea } from '../common';
import { DashboardPanelHeader, DashboardStateBlock } from '../dashboard';
import { StockBarItemComponent } from './StockBarItem';
import type { StockBarItem as StockBarItemType } from '../../types/analysis';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface StockBarProps {
  items: StockBarItemType[];
  isLoading: boolean;
  selectedStockCode?: string;
  selectedRecordId?: number;
  onItemClick: (recordId: number) => void;
  onDeleteStock?: (stockCode: string) => Promise<void> | void;
  isDeleting?: boolean;
  className?: string;
}

/**
 * 个股栏组件：以股票维度展示历史分析记录，每只股票只显示一条。
 * 大盘复盘可作为 MARKET 项参与展示，并按最近分析时间排序。
 * 单只删除通过每个条目的删除按钮进行，不做批量删除。
 */
export const StockBar: React.FC<StockBarProps> = ({
  items,
  isLoading,
  selectedStockCode,
  selectedRecordId,
  onItemClick,
  onDeleteStock,
  isDeleting = false,
  className = '',
}) => {
  const { t } = useUiLanguage();
  const isMarketReview = (code: string) => code === 'MARKET';

  return (
    <aside className={`glass-card overflow-hidden flex flex-col ${className}`}>
      <ScrollArea
        viewportClassName="p-4"
        testId="home-stock-bar-scroll"
      >
        <div className="mb-4 space-y-3">
          <DashboardPanelHeader
            className="mb-1"
            title={t('stockBar.title')}
            titleClassName="text-sm font-medium"
            leading={(
              <svg className="h-4 w-4 text-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z" />
              </svg>
            )}
            headingClassName="items-center"
            actions={
              items.length > 0 ? (
                <span className="text-[11px] text-muted-text">{t('common.itemsCount', { count: items.length })}</span>
              ) : undefined
            }
          />
        </div>

        {isLoading ? (
          <DashboardStateBlock
            loading
            compact
            title={t('stockBar.loading')}
          />
        ) : items.length === 0 ? (
          <DashboardStateBlock
            title={t('stockBar.emptyTitle')}
            description={t('stockBar.emptyDescription')}
            icon={(
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
            )}
          />
        ) : (
          <div className="space-y-1.5">
            {items.map((item) => {
              const code = item.stockCode || '';
              const isMarket = isMarketReview(code);
              const isSelected = selectedRecordId === item.id || selectedStockCode === code;

              return (
                <StockBarItemComponent
                  key={`${code}-${item.id}`}
                  item={item}
                  isViewing={isSelected}
                  onClick={onItemClick}
                  onDelete={onDeleteStock}
                  isDeleting={isDeleting}
                  isMarketReview={isMarket}
                />
              );
            })}
          </div>
        )}
      </ScrollArea>
    </aside>
  );
};

export default StockBar;
