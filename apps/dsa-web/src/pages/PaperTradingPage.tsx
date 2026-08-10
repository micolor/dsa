import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { paperApi } from '../api/paper';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, Badge, EmptyState, InlineAlert } from '../components/common';
import { EquityCurveChart } from '../components/paper/EquityCurveChart';
import { PaperMetricsCards } from '../components/paper/PaperMetricsCards';
import { PaperRecordsList } from '../components/paper/PaperRecordsList';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText } from '../i18n/uiText';
import { PAPER_TRADING_TEXT } from '../locales/featureText';
import type { PaperPosition, PaperSignalRecord, PaperSnapshot, PaperTrade } from '../types/paper';

const PAGE_SIZE = 50;

export const PaperTradingPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = PAPER_TRADING_TEXT[language];

  const [snapshot, setSnapshot] = useState<PaperSnapshot | null>(null);
  const [positions, setPositions] = useState<PaperPosition[]>([]);
  const [curve, setCurve] = useState<Array<{ tradeDate: string; netValue: number; returnPct: number | null }>>([]);
  const [signals, setSignals] = useState<PaperSignalRecord[]>([]);
  const [signalTotal, setSignalTotal] = useState(0);
  const [trades, setTrades] = useState<PaperTrade[]>([]);
  const [tradeTotal, setTradeTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isActioning, setIsActioning] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [backfillFrom, setBackfillFrom] = useState('');

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [snap, pos, curveData, signalData, tradeData] = await Promise.all([
        paperApi.getSnapshot(),
        paperApi.getPositions(),
        paperApi.getEquityCurve(),
        paperApi.getSignals(page, PAGE_SIZE),
        paperApi.getTrades(page, PAGE_SIZE),
      ]);
      setSnapshot(snap);
      setPositions(pos);
      setCurve(curveData);
      setSignals(signalData.items);
      setSignalTotal(signalData.total);
      setTrades(tradeData.items);
      setTradeTotal(tradeData.total);
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsLoading(false);
    }
  }, [page]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRefresh = useCallback(async () => {
    setIsActioning(true);
    setError(null);
    try {
      await paperApi.refresh();
      setNotice(text.refreshDone);
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsActioning(false);
    }
  }, [load, text.refreshDone]);

  const handleBackfill = useCallback(async () => {
    if (!backfillFrom) return;
    setIsActioning(true);
    setError(null);
    try {
      const result = await paperApi.backfill(backfillFrom);
      setNotice(formatUiText(text.backfillDone, { count: result.signalsReplayed }));
      setBackfillFrom('');
      await load();
    } catch (err) {
      setError(getParsedApiError(err));
    } finally {
      setIsActioning(false);
    }
  }, [backfillFrom, load, text.backfillDone]);

  const openPositions = positions.filter((p) => p.status === 'open');

  return (
    <div className="flex h-[calc(100vh-5rem)] w-full flex-col overflow-hidden px-4 pb-6 pt-4 sm:h-[calc(100vh-5.5rem)] md:px-6 lg:h-[calc(100vh-2rem)]">
      <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
      <div className="flex justify-end">
        <div className="flex items-center gap-2">
          <input
            type="date"
            value={backfillFrom}
            onChange={(e) => setBackfillFrom(e.target.value)}
            aria-label={text.backfill}
            className="input-surface h-9 rounded-lg border border-border/70 bg-card/70 px-3 text-xs text-foreground"
          />
          <button
            type="button"
            onClick={handleBackfill}
            disabled={isActioning || !backfillFrom}
            className="home-surface-button h-9 rounded-lg px-3 text-xs text-secondary-text hover:text-foreground disabled:opacity-50"
          >
            {isActioning ? text.backfilling : text.backfill}
          </button>
          <button
            type="button"
            onClick={handleRefresh}
            disabled={isActioning}
            className="btn-primary h-9 w-9 rounded-lg p-0 inline-flex items-center justify-center"
          >
            <RefreshCw className={`h-4 w-4 ${isActioning ? 'animate-spin' : ''}`} />
            <span className="sr-only">{isActioning ? text.refreshing : text.refresh}</span>
          </button>
        </div>
      </div>

      {notice ? (
        <InlineAlert
          variant="success"
          message={notice}
          action={(
            <button
              type="button"
              onClick={() => setNotice(null)}
              className="shrink-0 text-xs opacity-80 transition-opacity hover:opacity-100"
              aria-label={text.retry}
            >
              ×
            </button>
          )}
        />
      ) : null}
      {error ? <ApiErrorAlert error={error} /> : null}

      {isLoading ? (
        <div className="flex h-64 items-center justify-center">
          <div className="home-spinner h-10 w-10 animate-spin border-[3px]" />
        </div>
      ) : (
        <>
          <PaperMetricsCards snapshot={snapshot} language={language} />

          <div className="home-subpanel p-4">
            <h2 className="mb-3 text-sm font-semibold text-foreground">{text.equityCurve}</h2>
            <EquityCurveChart points={curve} language={language} />
          </div>

          <div className="home-subpanel p-4">
            <h2 className="mb-3 text-sm font-semibold text-foreground">{text.positionsTitle}</h2>
            {openPositions.length === 0 ? (
              <EmptyState title={text.noPositionsTitle} description={text.noPositionsDescription} />
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-secondary-text">
                      <th className="py-2 pr-3 font-medium">{text.stock}</th>
                      <th className="py-2 pr-3 font-medium">{text.quantity}</th>
                      <th className="py-2 pr-3 font-medium">{text.avgCost}</th>
                      <th className="py-2 pr-3 font-medium">{text.currentPrice}</th>
                      <th className="py-2 pr-3 font-medium">{text.marketValue}</th>
                      <th className="py-2 pr-3 font-medium">{text.stopLoss}</th>
                      <th className="py-2 pr-3 font-medium">{text.targetPrice}</th>
                      <th className="py-2 pr-3 font-medium">{text.status}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openPositions.map((p) => (
                      <tr key={p.stockCode} className="border-t border-border/60">
                        <td className="py-2 pr-3 font-medium text-foreground">{p.stockCode}</td>
                        <td className="py-2 pr-3 text-secondary-text">{p.quantity}</td>
                        <td className="py-2 pr-3 text-secondary-text">{p.avgCost?.toFixed(2)}</td>
                        <td className="py-2 pr-3 text-secondary-text">{p.currentPrice?.toFixed(2)}</td>
                        <td className="py-2 pr-3 text-secondary-text">{p.marketValue?.toLocaleString('zh-CN')}</td>
                        <td className="py-2 pr-3 text-secondary-text">{p.stopLoss?.toFixed(2)}</td>
                        <td className="py-2 pr-3 text-secondary-text">{p.targetPrice?.toFixed(2)}</td>
                        <td className="py-2 pr-3"><Badge variant="success">{text.open}</Badge></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="home-subpanel p-4">
            <h2 className="mb-3 text-sm font-semibold text-foreground">{text.recordsTitle}</h2>
            <PaperRecordsList
              signals={signals}
              trades={trades}
              signalTotal={signalTotal}
              tradeTotal={tradeTotal}
              page={page}
              pageSize={PAGE_SIZE}
              onPageChange={setPage}
              language={language}
            />
          </div>
        </>
      )}
      </div>
    </div>
  );
};

export default PaperTradingPage;
