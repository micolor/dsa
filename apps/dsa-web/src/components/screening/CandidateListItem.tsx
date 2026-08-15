import { memo } from 'react';
import type { ScreeningCandidate } from '../../api/screening';
import { Badge, ListItemRow } from '../common';
import {
  FACTOR_LABELS,
  formatAmount,
  formatEnrichmentSummary,
  formatNumber,
  formatPercent,
  formatScore,
  getCandidateReason,
  getFactorEntries,
  getRiskLabel,
  getSignal,
  hasLlmInsight,
} from './candidateFormat';

interface CandidateListItemProps {
  item: ScreeningCandidate;
  rank: number;
  /** true when the run used deterministic factor ranking (否则展示 LLM 分) */
  factorRanking: boolean;
  expanded: boolean;
  onToggle: () => void;
  onAnalyze: (candidate: ScreeningCandidate) => void;
}

/** 评分映射到语义色（高分→success，中→primary，低→warning），与首页评分徽章同思路。 */
const getScoreColor = (score: number | string | null | undefined): string => {
  if (score == null || Number.isNaN(Number(score))) {
    return 'hsl(0 0% 60%)';
  }
  const value = Number(score);
  if (value >= 85) return 'hsl(152 69% 40%)';
  if (value >= 70) return 'hsl(247 84% 62%)';
  return 'hsl(37 92% 50%)';
};

/** 涨跌幅着色：正→danger（红），负→success（绿），零→次级文本。 */
const getChangeClass = (value: number): string => {
  if (value > 0) return 'text-danger';
  if (value < 0) return 'text-success';
  return 'text-secondary-text';
};

const SectionLabel: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <p className="text-xs font-semibold text-secondary-text">{children}</p>
);

/** 规格表行：标签固定左列对齐，值右列对齐（对齐首页 MetricLine 的 7rem/1fr 模式）。 */
const FactRow: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid items-start gap-3 text-sm sm:grid-cols-[6.5rem_1fr]">
    <span className="text-xs text-secondary-text">{label}</span>
    <div className="min-w-0 break-words text-foreground">{children}</div>
  </div>
);

/** 因子横向条：标签 + 进度条 + 数值，比小卡更可扫读。 */
const FactorBar: React.FC<{ label: string; value: number; color: string }> = ({ label, value, color }) => (
  <div className="grid items-center gap-2 sm:grid-cols-[6.5rem_1fr_2.5rem]">
    <span className="text-xs text-secondary-text">{label}</span>
    <div className="h-1.5 overflow-hidden rounded-full bg-surface">
      <div
        className="h-full rounded-full transition-all"
        style={{ width: `${Math.min(100, Math.max(0, value))}%`, backgroundColor: color }}
      />
    </div>
    <span className="text-right text-xs font-semibold tabular-nums text-foreground">
      {formatNumber(value)}
    </span>
  </div>
);

const CandidateDetail: React.FC<{ item: ScreeningCandidate; onAnalyze: (c: ScreeningCandidate) => void }> = ({
  item,
  onAnalyze,
}) => {
  const factors = getFactorEntries(item);
  const llmInsightAvailable = hasLlmInsight(item);
  const dsaWarnings = item.dsaContext?.warnings || [];
  const dsaNews = item.dsaNews || [];
  const dsaEvents = item.dsaEvents || [];
  const riskFlags = [...(item.riskFlags || []), ...(item.llmRisks || [])];
  const factorColor = getScoreColor(item.score);

  return (
    <div className="px-4 pb-4">
      {/* 结论：整宽段落，详情最突出的一块 */}
      <div className="rounded-xl bg-card/50 p-4 shadow-[inset_0_1px_0_hsl(0_0%_100%_/_0.06)]">
        <SectionLabel>分析结论</SectionLabel>
        <p className="mt-1.5 text-sm leading-6 text-foreground">{getCandidateReason(item)}</p>
        {item.dsaAnalysisSummary ? (
          <div className="mt-2 border-t border-subtle pt-2">
            <SectionLabel>增强摘要</SectionLabel>
            <p className="mt-1 text-[13px] leading-6 text-secondary-text">
              {formatEnrichmentSummary(item.dsaAnalysisSummary)}
            </p>
          </div>
        ) : null}
      </div>

      {/* 关键事实：左对齐规格表 */}
      <div className="mt-4 space-y-2.5">
        <FactRow label="操作信号">{getSignal(item)}</FactRow>
        <FactRow label="成交额">{formatAmount(item.amount)}</FactRow>
        <FactRow label="风险">
          {riskFlags.length ? (
            <span className="flex flex-wrap gap-1.5">
              {riskFlags.map((flag, index) => (
                <Badge key={`${item.code}-risk-${index}`} size="sm" variant="danger" className="text-[11px]">
                  {flag}
                </Badge>
              ))}
            </span>
          ) : (
            '无'
          )}
        </FactRow>
        {llmInsightAvailable && (item.llmSector || item.llmTheme) ? (
          <FactRow label="板块 · 主题">
            {item.llmSector || '-'} · {item.llmTheme || '-'}
          </FactRow>
        ) : null}
        {item.llmConfidence != null ? (
          <FactRow label="置信度">{formatPercent(item.llmConfidence)}</FactRow>
        ) : null}
        {item.llmWatchItems?.length ? (
          <FactRow label="智能关注项">{item.llmWatchItems.join('，')}</FactRow>
        ) : null}
        {item.llmCatalysts?.length ? (
          <FactRow label="催化因素">{item.llmCatalysts.join('，')}</FactRow>
        ) : null}
      </div>

      {/* 主要因子：横向条形 */}
      {factors.length > 0 ? (
        <div className="mt-4">
          <SectionLabel>主要因子</SectionLabel>
          <div className="mt-2.5 space-y-2.5">
            {factors.map(([key, value]) => (
              <FactorBar
                key={key}
                label={FACTOR_LABELS[key] || key}
                value={typeof value === 'number' ? value : Number(value) || 0}
                color={factorColor}
              />
            ))}
          </div>
        </div>
      ) : null}

      {/* 资讯：相关新闻 | 公告与事件 */}
      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <div>
          <SectionLabel>相关新闻</SectionLabel>
          {dsaNews.length > 0 ? (
            <ul className="mt-1 space-y-1 text-sm leading-6 text-foreground">
              {dsaNews.slice(0, 3).map((newsItem, newsIndex) => (
                <li key={`${item.code}-dsa-news-${newsIndex}`}>
                  {newsItem.title || newsItem.snippet || '-'}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-secondary-text">无</p>
          )}
        </div>
        <div>
          <SectionLabel>公告与事件</SectionLabel>
          {dsaEvents.length > 0 ? (
            <ul className="mt-1 space-y-1 text-sm leading-6 text-foreground">
              {dsaEvents.slice(0, 3).map((eventItem, eventIndex) => (
                <li key={`${item.code}-dsa-event-${eventIndex}`}>
                  {eventItem.title || eventItem.snippet || '-'}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-sm text-secondary-text">无</p>
          )}
        </div>
      </div>

      {dsaWarnings.length > 0 ? (
        <div className="mt-4">
          <SectionLabel>数据补充提示</SectionLabel>
          <p className="mt-1 text-sm text-secondary-text">{dsaWarnings.join('，')}</p>
        </div>
      ) : null}

      {/* CTA */}
      <button
        className="mt-4 w-full rounded-xl border border-cyan/40 px-4 py-2.5 text-sm font-semibold text-cyan transition-colors hover:bg-cyan/10"
        type="button"
        onClick={() => onAnalyze(item)}
      >
        进一步深度分析
      </button>
    </div>
  );
};

const CandidateListItemInner: React.FC<CandidateListItemProps> = ({
  item,
  rank,
  factorRanking,
  expanded,
  onToggle,
  onAnalyze,
}) => {
  const stockName = item.name || item.code || '-';
  const scoreColor = getScoreColor(item.score);
  const rankingBasis = factorRanking ? '因子排序' : formatScore(item.llmScore);
  const changeValue = Number(item.changePct);
  const changeText = `${changeValue > 0 ? '+' : ''}${formatNumber(item.changePct)}%`;

  const leading = (
    <div className="flex shrink-0 flex-col items-center gap-1 self-stretch">
      <span className="text-[11px] font-semibold leading-none text-secondary-text">{rank}</span>
      <div
        className="w-1 flex-1 rounded-full"
        style={{ backgroundColor: scoreColor, boxShadow: `0 0 10px ${scoreColor}40` }}
      />
    </div>
  );

  const trailing = (
    <>
      <Badge
        variant="default"
        size="sm"
        className="shrink-0 shadow-none text-[11px] font-semibold leading-none"
        style={{ color: scoreColor, borderColor: `${scoreColor}30`, backgroundColor: `${scoreColor}10` }}
      >
        {formatScore(item.score)}
      </Badge>
      <Badge
        variant={item.riskLevel === 'high' ? 'danger' : item.riskLevel === 'medium' ? 'warning' : item.riskLevel === 'low' ? 'success' : 'default'}
        size="sm"
        className="shrink-0 shadow-none"
      >
        {getRiskLabel(item.riskLevel)}
      </Badge>
    </>
  );

  const meta = (
    <>
      <span className="font-mono text-[11px] text-secondary-text">{item.code}</span>
      <span className="w-1 h-1 rounded-full bg-subtle-hover" />
      {item.industry ? (
        <>
          <span className="text-[11px] text-secondary-text">{item.industry}</span>
          <span className="w-1 h-1 rounded-full bg-subtle-hover" />
        </>
      ) : null}
      <span className="text-[11px] text-secondary-text">价格 {formatNumber(item.price)}</span>
      <span className="w-1 h-1 rounded-full bg-subtle-hover" />
      <span className="text-[11px] text-secondary-text">涨跌幅 </span>
      <span className={`text-[11px] font-semibold ${getChangeClass(changeValue)}`}>{changeText}</span>
      <span className="w-1 h-1 rounded-full bg-subtle-hover" />
      <span className="text-[11px] text-secondary-text">排序依据</span>
      <span className="text-[11px] font-semibold text-cyan">{rankingBasis}</span>
    </>
  );

  return (
    <div
      className={`glass-card !border-transparent overflow-hidden transition-all duration-200 ${
        expanded ? 'ring-1 ring-cyan/30' : 'hover:bg-card/55'
      }`}
    >
      <ListItemRow
        wrapperClassName="w-full min-w-0 flex-1"
        buttonClassName="w-full min-w-0 flex-1 text-left p-3"
        ariaLabel={`${stockName} ${item.code}，展开查看详情`}
        onClick={onToggle}
        leading={leading}
        title={(
          <span className="block w-full truncate text-sm font-semibold text-foreground tracking-tight">
            {stockName}
          </span>
        )}
        trailing={trailing}
        meta={meta}
        metaTestId="candidate-card-meta"
        actionsTestId="candidate-card-actions"
      />
      {expanded ? <CandidateDetail item={item} onAnalyze={onAnalyze} /> : null}
    </div>
  );
};

export const CandidateListItem = memo(CandidateListItemInner);
CandidateListItem.displayName = 'CandidateListItem';
