import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AlertTriggerHistory } from '../AlertTriggerHistory';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import type { AlertTriggerItem } from '../../../types/alerts';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';

const triggers: AlertTriggerItem[] = [
  {
    id: 1,
    ruleId: 1,
    target: '600519',
    status: 'triggered',
    observedValue: 1850,
    threshold: 1800,
    dataSource: 'sina',
    dataTimestamp: '2026-05-18T10:30:00',
    reason: '价格突破上阈值',
    marketPhaseSummary: { phase: 'intraday', warnings: [] },
    analysisContextPackOverview: {
      packVersion: '1',
      subject: { code: '600519' },
      blocks: [],
      counts: {
        available: 0, missing: 0, notSupported: 0, fallback: 0,
        stale: 0, estimated: 0, partial: 0, fetchFailed: 0,
      },
      dataQuality: { level: 'good', blockScores: {}, limitations: ['少量缺失'] },
      warnings: [],
      metadata: {},
    },
  },
  {
    id: 2,
    ruleId: 1,
    target: '300750',
    status: 'skipped',
    observedValue: null,
    threshold: null,
    reason: null,
  },
  {
    id: 3,
    ruleId: 1,
    target: '000001',
    status: 'failed',
    observedValue: null,
    threshold: null,
    reason: '数据源无响应',
  },
];

describe('AlertTriggerHistory', () => {
  it('renders trigger rows with phase, quality, and status labels', () => {
    render(<AlertTriggerHistory triggers={triggers} />);

    expect(screen.getByText('触发历史')).toBeInTheDocument();
    expect(screen.getByText('状态')).toBeInTheDocument();
    expect(screen.getByText('阶段 / 质量')).toBeInTheDocument();
    expect(screen.getByText('已触发')).toBeInTheDocument();
    expect(screen.getByText('已跳过')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
    expect(screen.getByText('质量：good')).toBeInTheDocument();
    expect(screen.getByText('600519')).toBeInTheDocument();
  });

  it('shows an empty state when there are no triggers', () => {
    render(<AlertTriggerHistory triggers={[]} />);
    expect(screen.getByText('暂无触发历史')).toBeInTheDocument();
  });

  it('renders in English UI mode', () => {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    render(
      <UiLanguageProvider>
        <AlertTriggerHistory triggers={triggers} />
      </UiLanguageProvider>,
    );

    expect(screen.getByText('Trigger history')).toBeInTheDocument();
    expect(screen.getByText('Status')).toBeInTheDocument();
    expect(screen.getByText('Triggered')).toBeInTheDocument();
    expect(screen.getByText('Skipped')).toBeInTheDocument();
    expect(screen.getByText('Failed')).toBeInTheDocument();
    expect(screen.getByText('Quality: good')).toBeInTheDocument();
    expect(screen.queryByText('已触发')).not.toBeInTheDocument();
    expect(screen.queryByText('触发历史')).not.toBeInTheDocument();
  });
});
