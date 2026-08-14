import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { paperApi } from '../api/paper';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { ApiErrorAlert, Badge, DatePicker, EmptyState, InlineAlert } from '../components/common';
import { EquityCurveChart } from '../components/paper/EquityCurveChart';
import { PaperMetricsCards } from '../components/paper/PaperMetricsCards';
import { PaperRecordsList } from '../components/paper/PaperRecordsList';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { formatUiText } from '../i18n/uiText';
import { PAPER_TRADING_TEXT } from '../locales/featureText';
import type { PaperAccount, PaperPosition, PaperSignalRecord, PaperSnapshot, PaperTrade } from '../types/paper';

const PAGE_SIZE = 50;

export const PaperTradingPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = PAPER_TRADING_TEXT[language];

  const [snapshot, setSnapshot] = useState<PaperSnapshot | null>(null);
  const [account, setAccount] = useState<PaperAccount | null>(null);
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
  const [activeSection, setActiveSection] = useState<'positions' | 'records'>('positions');

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [snap, pos, curveData, signalData, tradeData, acct] = await Promise.all([
        paperApi.getSnapshot(),
        paperApi.getPositions(),
        paperApi.getEquityCurve(),
        paperApi.getSignals(page, PAGE_SIZE),
        paperApi.getTrades(page, PAGE_SIZE),
        paperApi.getAccount(),
      ]);
      setSnapshot(snap);
      setAccount(acct);
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
  const closedPositions = positions.filter((p) => p.status === 'closed');
  const closedStats = (() => {
    const items = closedPositions.map((p) => {
      const cost = p.avgCost;
      const close = p.currentPrice;
      const qty = p.quantity;
      const pnl = cost != null && close != null ? (close - cost) * qty : null;
      const pnlPct = cost != null && close != null && cost !== 0
        ? ((close - cost) / cost) * 100
        : null;
      return { ...p, pnl, pnlPct };
    });
    const wins = items.filter((i) => i.pnl != null && i.pnl > 0).length;
    const totalRealized = items.reduce((sum, i) => sum + (i.pnl ?? 0), 0);
    return { items, wins, totalRealized, count: items.length };
  })();

  return (
    <div className="flex h-[calc(100vh-5rem)] w-full flex-col overflow-hidden px-4 pb-6 pt-4 sm:h-[calc(100vh-5.5rem)] md:px-6 lg:h-[calc(100vh-2rem)]">
      <div className="flex-1 min-h-0 space-y-4 overflow-y-auto">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs leading-5 text-muted-text">{text.description}</p>
          <ul className="mt-1.5 space-y-1 text-[11px] leading-4 text-muted-text/80">
            {text.mechanism.map((line) => (
              <li key={line} className="flex gap-1.5">
                <span className="mt-[5px] h-1 w-1 shrink-0 rounded-full bg-current/60" aria-hidden="true" />
                <span>{line}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <DatePicker
            value={backfillFrom}
            onChange={setBackfillFrom}
            placeholder={text.backfill}
            className="h-9 w-44"
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
            className="btn-primary inline-flex h-9 items-center gap-1.5 rounded-lg px-3"
          >
            <RefreshCw className={`h-4 w-4 ${isActioning ? 'animate-spin' : ''}`} />
            <span>{text.refresh}</span>
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
          {account ? (
            <div className="home-subpanel flex flex-wrap items-center gap-x-5 gap-y-1 px-4 py-2.5 text-xs text-secondary-text">
              <span>
                {text.account}：<span className="font-medium text-foreground">{account.name}</span>
              </span>
              <span>
                {text.initialCapital}：<span className="font-medium tabular-nums text-foreground">{account.initialCapital.toLocaleString('zh-CN')}</span>
              </span>
              <span className="inline-flex items-center gap-1.5">
                {text.accountStatus}：
                <Badge variant={account.status === 'active' ? 'success' : 'default'}>{account.status}</Badge>
              </span>
            </div>
          ) : null}

          <PaperMetricsCards snapshot={snapshot} language={language} />

          <div className="home-subpanel p-4">
            <h2 className="mb-3 text-sm font-semibold text-foreground">{text.equityCurve}</h2>
            <EquityCurveChart points={curve} language={language} />
          </div>

          <div className="flex w-fit items-center gap-1 rounded-xl border border-subtle bg-base/40 p-1">
            {(['positions', 'records'] as const).map((key) => (
              <button
                key={key}
                type="button"
                onClick={() => setActiveSection(key)}
                className={`rounded-lg px-3 py-1.5 text-xs transition-colors ${
                  activeSection === key
                    ? 'bg-[var(--nav-active-bg)] text-[hsl(var(--primary))]'
                    : 'text-secondary-text hover:text-foreground'
                }`}
              >
                {key === 'positions' ? text.positionsTitle : text.recordsTitle}
              </button>
            ))}
          </div>

          {activeSection === 'positions' ? (
          <>
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
                      <th className="py-2 pr-3 font-medium">{text.unrealizedPnl}</th>
                      <th className="py-2 pr-3 font-medium">{text.pnlPct}</th>
                      <th className="py-2 pr-3 font-medium">{text.stopLoss}</th>
                      <th className="py-2 pr-3 font-medium">{text.toStopLoss}</th>
                      <th className="py-2 pr-3 font-medium">{text.targetPrice}</th>
                      <th className="py-2 pr-3 font-medium">{text.toTarget}</th>
                      <th className="py-2 pr-3 font-medium">{text.status}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {openPositions.map((p) => {
                      const cost = p.avgCost;
                      const price = p.currentPrice;
                      const qty = p.quantity;
                      const pnl = cost != null && price != null ? (price - cost) * qty : null;
                      const pnlPct = cost != null && price != null && cost !== 0
                        ? ((price - cost) / cost) * 100
                        : null;
                      const toStop = p.stopLoss != null && price != null && price !== 0
                        ? ((price - p.stopLoss) / price) * 100
                        : null;
                      const toTarget = p.targetPrice != null && price != null && price !== 0
                        ? ((p.targetPrice - price) / price) * 100
                        : null;
                      const pnlTone = pnl == null || pnl === 0
                        ? 'text-secondary-text'
                        : pnl > 0 ? 'text-success' : 'text-danger';
                      const signedPnl = pnl != null
                        ? `${pnl > 0 ? '+' : ''}${pnl.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
                        : '-';
                      const signedPct = pnlPct != null
                        ? `${pnlPct > 0 ? '+' : ''}${pnlPct.toFixed(2)}%`
                        : '-';
                      const stopText = toStop != null ? `${toStop.toFixed(1)}%` : '-';
                      const targetText = toTarget != null ? `${toTarget.toFixed(1)}%` : '-';
                      return (
                        <tr key={p.stockCode} className="border-t border-border/60">
                          <td className="py-2 pr-3 font-medium text-foreground">{p.stockCode}</td>
                          <td className="py-2 pr-3 text-secondary-text">{qty}</td>
                          <td className="py-2 pr-3 text-secondary-text">{cost?.toFixed(2)}</td>
                          <td className="py-2 pr-3 text-secondary-text">{price?.toFixed(2)}</td>
                          <td className="py-2 pr-3 text-secondary-text">{p.marketValue?.toLocaleString('zh-CN')}</td>
                          <td className={`py-2 pr-3 tabular-nums ${pnlTone}`}>{signedPnl}</td>
                          <td className={`py-2 pr-3 tabular-nums ${pnlTone}`}>{signedPct}</td>
                          <td className="py-2 pr-3 text-secondary-text">{p.stopLoss?.toFixed(2)}</td>
                          <td className="py-2 pr-3 text-secondary-text">{stopText}</td>
                          <td className="py-2 pr-3 text-secondary-text">{p.targetPrice?.toFixed(2)}</td>
                          <td className="py-2 pr-3 text-secondary-text">{targetText}</td>
                          <td className="py-2 pr-3"><Badge variant="success">{text.open}</Badge></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {closedPositions.length > 0 ? (
            <div className="home-subpanel p-4">
              <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1">
                <h2 className="text-sm font-semibold text-foreground">{text.closedPositions} ({closedStats.count})</h2>
                <span className="text-xs text-secondary-text">
                  {text.winRate}：
                  <span className="font-medium text-foreground">
                    {closedStats.count > 0 ? `${((closedStats.wins / closedStats.count) * 100).toFixed(1)}%` : '-'}
                  </span>
                </span>
                <span className="text-xs text-secondary-text">
                  {text.totalRealized}：
                  <span className={`font-medium tabular-nums ${closedStats.totalRealized > 0 ? 'text-success' : closedStats.totalRealized < 0 ? 'text-danger' : 'text-foreground'}`}>
                    {closedStats.totalRealized > 0 ? '+' : ''}{closedStats.totalRealized.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}
                  </span>
                </span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-xs text-secondary-text">
                      <th className="py-2 pr-3 font-medium">{text.stock}</th>
                      <th className="py-2 pr-3 font-medium">{text.quantity}</th>
                      <th className="py-2 pr-3 font-medium">{text.avgCost}</th>
                      <th className="py-2 pr-3 font-medium">{text.closePrice}</th>
                      <th className="py-2 pr-3 font-medium">{text.realizedPnl}</th>
                      <th className="py-2 pr-3 font-medium">{text.pnlPct}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {closedStats.items.map((p) => {
                      const tone = p.pnl == null || p.pnl === 0
                        ? 'text-secondary-text'
                        : p.pnl > 0 ? 'text-success' : 'text-danger';
                      const signedPnl = p.pnl != null
                        ? `${p.pnl > 0 ? '+' : ''}${p.pnl.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
                        : '-';
                      const signedPct = p.pnlPct != null
                        ? `${p.pnlPct > 0 ? '+' : ''}${p.pnlPct.toFixed(2)}%`
                        : '-';
                      return (
                        <tr key={p.stockCode} className="border-t border-border/60">
                          <td className="py-2 pr-3 font-medium text-foreground">
                            {p.stockName || p.stockCode}
                            <span className="ml-1.5 font-mono text-xs text-secondary-text">{p.stockCode}</span>
                          </td>
                          <td className="py-2 pr-3 text-secondary-text">{p.quantity}</td>
                          <td className="py-2 pr-3 text-secondary-text">{p.avgCost?.toFixed(2)}</td>
                          <td className="py-2 pr-3 text-secondary-text">{p.currentPrice?.toFixed(2)}</td>
                          <td className={`py-2 pr-3 tabular-nums ${tone}`}>{signedPnl}</td>
                          <td className={`py-2 pr-3 tabular-nums ${tone}`}>{signedPct}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
          </>
          ) : (
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
          )}
        </>
      )}
      </div>
    </div>
  );
};

export default PaperTradingPage;
