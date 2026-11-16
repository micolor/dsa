/** Paper-trading (simulated account) types. */

export interface PaperSnapshot {
  accountId: number;
  cash: number;
  marketValue: number;
  netValue: number;
  returnPct: number;
  initialCapital: number;
  openPositionCount: number;
}

export interface PaperAccount {
  accountId: number;
  name: string;
  initialCapital: number;
  cash: number;
  status: string;
  snapshot: PaperSnapshot;
}

export interface EquityPoint {
  tradeDate: string;
  netValue: number;
  returnPct: number | null;
}

export interface PaperPosition {
  stockCode: string;
  stockName: string | null;
  market: string | null;
  quantity: number;
  avgCost: number | null;
  currentPrice: number | null;
  marketValue: number | null;
  entryDate: string | null;
  stopLoss: number | null;
  targetPrice: number | null;
  status: string;
}

export interface PaperTrade {
  stockCode: string;
  stockName: string | null;
  side: string;
  quantity: number;
  price: number | null;
  amount: number | null;
  /** 佣金+印花税+过户费；滑点已含在 price 里。旧记录为 0。 */
  fee: number;
  tradeDate: string;
  reason: string | null;
}

export interface PaperTradeList {
  items: PaperTrade[];
  total: number;
}

export interface PaperSignalRecord {
  signalId: number;
  action: string;
  disposition: string;
  processedAt: string;
  stockCode?: string | null;
  stockName?: string | null;
}

export interface PaperSignalList {
  items: PaperSignalRecord[];
  total: number;
}

export interface PaperValuation {
  accountId: number;
  tradeDate: string;
  cash: number;
  marketValue: number;
  netValue: number;
  returnPct: number;
  /** `data_unavailable` means a quote was missing, so no snapshot was written. */
  status?: string;
  missingSymbols?: string[];
  snapshotWritten?: boolean;
}

export interface PaperAutoTradeResult {
  status: string;
  accountId: number;
  lookbackDays: number;
  signalsSeen: number;
  processed: number;
  skipped: number;
  unavailable: number;
  results: Array<{
    signalId: number;
    stockCode: string;
    action: string;
    status: string;
    disposition?: string | null;
  }>;
}

export interface BackfillResult {
  accountId: number;
  fromDate: string;
  toDate: string;
  signalsReplayed: number;
  signalsUnavailable: number;
  snapshot: PaperSnapshot;
}
