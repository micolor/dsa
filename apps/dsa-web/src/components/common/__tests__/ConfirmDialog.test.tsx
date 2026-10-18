import { fireEvent, render, screen } from '@testing-library/react';
import type React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { ConfirmDialog } from '../ConfirmDialog';
import { Dialog } from '../Dialog';

function renderDialog(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const onConfirm = vi.fn();
  const onCancel = vi.fn();
  const result = render(
    <UiLanguageProvider>
      <ConfirmDialog
        isOpen
        title="确认操作"
        message="确认继续吗？"
        confirmText="确定"
        cancelText="取消"
        onConfirm={onConfirm}
        onCancel={onCancel}
        {...overrides}
      />
    </UiLanguageProvider>,
  );
  return { onConfirm, onCancel, ...result };
}

describe('ConfirmDialog', () => {
  it('disables confirm and cancel actions independently', () => {
    const { onConfirm, onCancel } = renderDialog({
      confirmDisabled: true,
      cancelDisabled: true,
    });

    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    fireEvent.click(document.body.lastElementChild as HTMLElement);

    expect(screen.getByRole('button', { name: '确定' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '取消' })).toBeDisabled();
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('keeps the default confirm and cancel behavior when not disabled', () => {
    const { onConfirm, onCancel } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    fireEvent.click(screen.getByRole('button', { name: '取消' }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  // 全站其它模态浮层（Drawer / Dialog / DatePicker / 区域选择器 / 帮助面板）都认 Esc，
  // 这个对话框是唯一的例外。它是 aria-modal 的中心模态，只能鼠标点掉说不过去。
  it('takes Escape as a cancel', () => {
    const { onConfirm, onCancel } = renderDialog();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });

  // 与点背景一致：取消被禁用时 Esc 也不该是绕过它的一条暗路。
  it('ignores Escape while cancel is disabled, matching the backdrop', () => {
    const { onConfirm, onCancel } = renderDialog({ cancelDisabled: true });

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onCancel).not.toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
  });

  it('does not answer Escape while closed', () => {
    const { onCancel } = renderDialog({ isOpen: false });

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(onCancel).not.toHaveBeenCalled();
  });

  // 确认框经常从别的模态里发起（事件记录里删流水、抽屉里删会话）。两者都 portal 到 body，
  // 谁的层级高谁可见；层级一旦配反，确认框会渲染在底层弹窗后面，用户只能看到一张模糊的卡片。
  it('stacks above an open Dialog', () => {
    const layerZ = (dialog: HTMLElement) => {
      const matched = dialog.parentElement?.className.match(/z-\[(\d+)\]/);
      return matched ? Number(matched[1]) : 0;
    };

    render(
      <UiLanguageProvider>
        <Dialog isOpen onClose={() => {}} title="事件记录" ariaLabel="事件记录">
          内容
        </Dialog>
        <ConfirmDialog isOpen title="确认操作" message="确认继续吗？" onConfirm={() => {}} onCancel={() => {}} />
      </UiLanguageProvider>,
    );

    const [hostDialog, confirmDialog] = screen.getAllByRole('dialog');
    expect(layerZ(confirmDialog)).toBeGreaterThan(layerZ(hostDialog));
  });
});
