import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChangePasswordCard } from '../ChangePasswordCard';

const { changePassword, useAuthMock } = vi.hoisted(() => ({
  changePassword: vi.fn(),
  useAuthMock: vi.fn(),
}));

vi.mock('../../../hooks', () => ({
  useAuth: () => useAuthMock(),
}));

const SUCCESS_MESSAGE = '管理员密码已更新。';
const DELAY_MS = 4000;

async function submitSuccessfully() {
  changePassword.mockResolvedValue({ success: true });
  useAuthMock.mockReturnValue({ changePassword });

  render(<ChangePasswordCard />);

  fireEvent.change(screen.getByLabelText('当前密码'), { target: { value: 'old-pass' } });
  fireEvent.change(screen.getByLabelText('新密码'), { target: { value: 'new-pass' } });
  fireEvent.change(screen.getByLabelText('确认新密码'), { target: { value: 'new-pass' } });
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: '保存新密码' }));
  });
}

function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

describe('ChangePasswordCard', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('hides the success message once the auto-dismiss window elapses', async () => {
    await submitSuccessfully();

    expect(screen.getByText(SUCCESS_MESSAGE)).toBeInTheDocument();

    advance(DELAY_MS - 1);
    expect(screen.getByText(SUCCESS_MESSAGE)).toBeInTheDocument();

    advance(1);
    expect(screen.queryByText(SUCCESS_MESSAGE)).not.toBeInTheDocument();
  });

  it('keeps the success message while the pointer rests on it', async () => {
    await submitSuccessfully();

    fireEvent.mouseEnter(screen.getByText(SUCCESS_MESSAGE));
    advance(DELAY_MS * 4);

    expect(screen.getByText(SUCCESS_MESSAGE)).toBeInTheDocument();
  });

  it('restarts the full window after the pointer leaves', async () => {
    await submitSuccessfully();

    // 悬停前先耗掉大部分倒计时，确认移开后拿到的是完整窗口而不是剩余时间。
    advance(DELAY_MS - 100);
    fireEvent.mouseEnter(screen.getByText(SUCCESS_MESSAGE));
    advance(DELAY_MS * 2);
    fireEvent.mouseLeave(screen.getByText(SUCCESS_MESSAGE));

    advance(DELAY_MS - 1);
    expect(screen.getByText(SUCCESS_MESSAGE)).toBeInTheDocument();

    advance(1);
    expect(screen.queryByText(SUCCESS_MESSAGE)).not.toBeInTheDocument();
  });
});
