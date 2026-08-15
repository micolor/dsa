import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { UI_LANGUAGE_STORAGE_KEY } from '../../../utils/uiLanguage';
import { AlertRuleForm } from '../AlertRuleForm';

const { getAccounts } = vi.hoisted(() => ({
  getAccounts: vi.fn(),
}));

vi.mock('../../../api/portfolio', () => ({
  portfolioApi: {
    getAccounts,
  },
}));

describe('AlertRuleForm', () => {
  const onSubmit = vi.fn();

  beforeEach(() => {
    onSubmit.mockReset();
    onSubmit.mockResolvedValue(undefined);
    getAccounts.mockReset();
    window.localStorage.clear();
    getAccounts.mockResolvedValue({ accounts: [{ id: 9, name: 'Main', market: 'us', baseCurrency: 'USD', isActive: true }] });
  });

  function renderEnglishForm() {
    window.localStorage.setItem(UI_LANGUAGE_STORAGE_KEY, 'en');
    render(
      <UiLanguageProvider>
        <AlertRuleForm onSubmit={onSubmit} />
      </UiLanguageProvider>,
    );
  }

  /** 自定义 Select 组件交互：点击触发按钮展开，等待选项经 requestAnimationFrame 异步渲染后，再点击 data-value 匹配的选项 */
  async function selectByValue(label: string, value: string) {
    fireEvent.click(screen.getByLabelText(label));
    const option = await waitFor(() => {
      const el = screen.getAllByRole('option').find((o) => o.getAttribute('data-value') === value);
      if (!el) {
        throw new Error(`Select "${label}" has no option with value "${value}"`);
      }
      return el;
    });
    fireEvent.click(option);
  }

  /** 自定义 Select 组件交互：断言当前选中值（读取触发按钮的 data-value） */
  function expectSelectValue(label: string, value: string) {
    expect(screen.getByLabelText(label)).toHaveAttribute('data-value', value);
  }

  it('submits a price_cross rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('规则名称'), { target: { value: '茅台价格突破' } });
    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: '600519' } });
    fireEvent.change(screen.getByLabelText('价格阈值'), { target: { value: '1800' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith({
        name: '茅台价格突破',
        targetScope: 'single_symbol',
        target: '600519',
        alertType: 'price_cross',
        parameters: { direction: 'above', price: 1800 },
        severity: 'warning',
        enabled: true,
      });
    });
  });

  it('submits a price_change_percent rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: 'aapl' } });
    await selectByValue('规则类型', 'price_change_percent');
    await selectByValue('方向', 'down');
    fireEvent.change(screen.getByLabelText('涨跌幅阈值（%）'), { target: { value: '3.5' } });
    await selectByValue('严重级别', 'critical');
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        target: 'AAPL',
        alertType: 'price_change_percent',
        parameters: { direction: 'down', changePct: 3.5 },
        severity: 'critical',
      }));
    });
  });

  it('submits a volume_spike rule payload and supports disabled creation', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: 'msft' } });
    await selectByValue('规则类型', 'volume_spike');
    fireEvent.change(screen.getByLabelText('成交量放大倍数'), { target: { value: '2.5' } });
    fireEvent.click(screen.getByLabelText('创建后立即启用'));
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        target: 'MSFT',
        alertType: 'volume_spike',
        parameters: { multiplier: 2.5 },
        enabled: false,
      }));
    });
  });

  it('submits technical indicator rule payloads', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: '600519' } });
    await selectByValue('规则类型', 'macd_cross');
    await selectByValue('交叉方向', 'bearish_cross');
    fireEvent.change(screen.getByLabelText('快线周期'), { target: { value: '6' } });
    fireEvent.change(screen.getByLabelText('慢线周期'), { target: { value: '13' } });
    fireEvent.change(screen.getByLabelText('信号周期'), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        target: '600519',
        alertType: 'macd_cross',
        parameters: {
          direction: 'bearish_cross',
          fastPeriod: 6,
          slowPeriod: 13,
          signalPeriod: 5,
        },
      }));
    });
  });

  it('rejects invalid technical indicator boundaries before submit', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: '600519' } });
    await selectByValue('规则类型', 'rsi_threshold');
    fireEvent.change(screen.getByLabelText('RSI 阈值'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    expect(screen.getByRole('alert')).toHaveTextContent('RSI 阈值必须在 0 到 100 之间');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects indicator period combinations that exceed fetchable history', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: '600519' } });
    await selectByValue('规则类型', 'macd_cross');
    fireEvent.change(screen.getByLabelText('快线周期'), { target: { value: '2' } });
    fireEvent.change(screen.getByLabelText('慢线周期'), { target: { value: '250' } });
    fireEvent.change(screen.getByLabelText('信号周期'), { target: { value: '250' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    expect(screen.getByRole('alert')).toHaveTextContent('MACD 周期组合需要 501 根日线，最多支持 365 根');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects empty required technical indicator thresholds before submit', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: '600519' } });
    await selectByValue('规则类型', 'rsi_threshold');
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    expect(screen.getByRole('alert')).toHaveTextContent('RSI 阈值不能为空');
    expect(onSubmit).not.toHaveBeenCalled();

    await selectByValue('规则类型', 'cci_threshold');
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    expect(screen.getByRole('alert')).toHaveTextContent('CCI 阈值不能为空');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects invalid numeric thresholds before submit', () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: '600519' } });
    fireEvent.change(screen.getByLabelText('价格阈值'), { target: { value: '0' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    expect(screen.getByRole('alert')).toHaveTextContent('价格阈值必须是大于 0 的数字');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('rejects invalid stock code format before submit', () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: 'aapl-2026' } });
    fireEvent.change(screen.getByLabelText('价格阈值'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    expect(screen.getByRole('alert')).toHaveTextContent('股票代码格式不正确');
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('filters alert types and submits a watchlist rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    await selectByValue('目标范围', 'watchlist');
    expect(screen.queryByText('组合止损')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('价格阈值'), { target: { value: '10' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'watchlist',
        target: 'default',
        alertType: 'price_cross',
        parameters: { direction: 'above', price: 10 },
      }));
    });
  });

  it('loads accounts and submits portfolio stop-loss mode', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    await selectByValue('目标范围', 'portfolio_account');
    await waitFor(() => expect(getAccounts).toHaveBeenCalledWith(false));
    expect(screen.queryByText('价格突破')).not.toBeInTheDocument();
    await selectByValue('账户', '9');
    await selectByValue('止损模式', 'breach');
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'portfolio_account',
        target: '9',
        alertType: 'portfolio_stop_loss',
        parameters: { mode: 'breach' },
      }));
    });
  });

  it('renders portfolio alert type options in English UI mode', async () => {
    renderEnglishForm();

    await selectByValue('Target scope', 'portfolio_account');

    await waitFor(() => expect(getAccounts).toHaveBeenCalledWith(false));
    fireEvent.click(screen.getByLabelText('Rule type'));
    expect(await screen.findByRole('option', { name: 'Portfolio drawdown' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'Portfolio stop loss' })).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Severity'));
    expect(await screen.findByRole('option', { name: 'Info' })).toBeInTheDocument();
    expect(screen.queryByText('组合回撤')).not.toBeInTheDocument();
  });

  it('shows market region options for market scope in Chinese UI mode', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    await selectByValue('目标范围', 'market');
    fireEvent.click(screen.getByLabelText('市场区域'));

    expect(await screen.findByRole('option', { name: 'A 股（cn）' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: '港股（hk）' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: '美股（us）' })).toBeInTheDocument();
    // 告警市场区域仅支持 A 股/港股/美股，日韩不在候选内
    expect(screen.queryByRole('option', { name: '日股（jp）' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: '韩股（kr）' })).not.toBeInTheDocument();
  });

  it('submits a market light status rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    await selectByValue('目标范围', 'market');
    fireEvent.click(screen.getByLabelText('市场区域'));
    expect(await screen.findByRole('option', { name: 'A 股（cn）' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: '港股（hk）' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: '美股（us）' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: '日股（jp）' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: '韩股（kr）' })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole('option', { name: '港股（hk）' }));
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'market',
        target: 'hk',
        alertType: 'market_light_status',
        parameters: { statuses: ['red', 'yellow'] },
      }));
    });
  });

  it('keeps JP/KR out of market light options in English UI mode', async () => {
    renderEnglishForm();

    await selectByValue('Target scope', 'market');
    fireEvent.click(screen.getByLabelText('Market region'));

    expect(await screen.findByRole('option', { name: 'A-shares (cn)' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'Hong Kong (hk)' })).toBeInTheDocument();
    expect(await screen.findByRole('option', { name: 'US (us)' })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Japan (jp)' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'Korea (kr)' })).not.toBeInTheDocument();
  });

  it('submits a market light score-drop rule payload', async () => {
    render(<AlertRuleForm onSubmit={onSubmit} />);

    await selectByValue('目标范围', 'market');
    await selectByValue('市场区域', 'us');
    await selectByValue('规则类型', 'market_light_score_drop');
    fireEvent.change(screen.getByLabelText('Score 下降阈值'), { target: { value: '12' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
        targetScope: 'market',
        target: 'us',
        alertType: 'market_light_score_drop',
        parameters: { minDrop: 12 },
      }));
    });
  });

  it('keeps all account option when account loading fails', async () => {
    getAccounts.mockRejectedValueOnce(new Error('boom'));
    render(<AlertRuleForm onSubmit={onSubmit} />);

    await selectByValue('目标范围', 'portfolio_holdings');
    expect(await screen.findByRole('alert')).toHaveTextContent('boom');
    expectSelectValue('账户', 'all');
  });

  it('keeps form values when submit reports failure', async () => {
    onSubmit.mockResolvedValueOnce(false);
    render(<AlertRuleForm onSubmit={onSubmit} />);

    fireEvent.change(screen.getByLabelText('标的代码'), { target: { value: 'aapl' } });
    fireEvent.change(screen.getByLabelText('价格阈值'), { target: { value: '200' } });
    fireEvent.click(screen.getByRole('button', { name: '创建规则' }));

    await waitFor(() => expect(onSubmit).toHaveBeenCalled());
    expect(screen.getByLabelText('标的代码')).toHaveValue('aapl');
    expect(screen.getByLabelText('价格阈值')).toHaveValue(200);
  });
});
