import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PaperAccountCard } from '../PaperAccountCard';

const { mockGetAccount, mockReset } = vi.hoisted(() => ({
  mockGetAccount: vi.fn(),
  mockReset: vi.fn(),
}));

vi.mock('../../../api/paper', () => ({
  paperApi: {
    getAccount: mockGetAccount,
    reset: mockReset,
  },
}));

const mockAccount = {
  accountId: 1,
  name: '模拟账户',
  initialCapital: 1000000,
  cash: 1000000,
  status: 'active',
  snapshot: {
    accountId: 1,
    cash: 1000000,
    marketValue: 0,
    netValue: 1000000,
    returnPct: 0,
    initialCapital: 1000000,
    openPositionCount: 0,
  },
};

describe('PaperAccountCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGetAccount.mockResolvedValue(mockAccount);
    mockReset.mockResolvedValue({ ...mockAccount, accountId: 2, initialCapital: 250000, cash: 250000 });
  });

  it('shows the current account summary', async () => {
    render(<PaperAccountCard />);

    expect(await screen.findByText('#1')).toBeInTheDocument();
    // 初始资金 / 现金 / 总资产当前都是 1,000,000。
    expect(screen.getAllByText('1,000,000').length).toBe(3);
  });

  it('confirms before resetting and passes the entered capital through', async () => {
    render(<PaperAccountCard />);

    const openButton = await screen.findByRole('button', { name: '重置账户' });
    openButton.click();

    // 弹窗里金额默认为空：不填就交给服务端配置值决定，不该先跑一次注定失败的请求。
    const input = await screen.findByPlaceholderText('例如 500000');
    expect(mockReset).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: '500000' } });
    screen.getByRole('button', { name: '确认重置' }).click();

    await waitFor(() => expect(mockReset).toHaveBeenCalledWith(500000));
    // 卡片用返回的新账户刷新摘要，并给出成功提示。
    expect(await screen.findByText('已重置，新账户初始资金 250,000')).toBeInTheDocument();
    expect(await screen.findByText('#2')).toBeInTheDocument();
  });

  it('blocks an invalid capital before calling the API', async () => {
    render(<PaperAccountCard />);

    (await screen.findByRole('button', { name: '重置账户' })).click();
    const input = await screen.findByPlaceholderText('例如 500000');

    fireEvent.change(input, { target: { value: '-5' } });

    expect(screen.getByText('请输入大于 0 的数字')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '确认重置' })).toBeDisabled();
    expect(mockReset).not.toHaveBeenCalled();
  });

  it('surfaces a load failure instead of an empty summary', async () => {
    mockGetAccount.mockRejectedValue({ message: '网络异常' });

    render(<PaperAccountCard />);

    expect(await screen.findByText('账户信息加载失败')).toBeInTheDocument();
  });
});
