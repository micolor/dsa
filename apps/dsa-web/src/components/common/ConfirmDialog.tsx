import type React from 'react';
import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { useEscTopLayer } from '../../hooks/useEscTopLayer';

interface ConfirmDialogProps {
  isOpen: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  confirmDisabled?: boolean;
  cancelDisabled?: boolean;
  isDanger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  /** Optional extra controls (e.g. an input) rendered between message and buttons. */
  children?: React.ReactNode;
}

/**
 * Generic confirmation dialog component.
 * Style is consistent with ChatPage.
 */
export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  isOpen,
  title,
  message,
  confirmText,
  cancelText,
  confirmDisabled = false,
  cancelDisabled = false,
  isDanger = false,
  onConfirm,
  onCancel,
  children,
}) => {
  const { t } = useUiLanguage();
  const { modalRef, isCovered } = useEscTopLayer();

  /**
   * Esc = 点背景：这是全站唯一一个漏了键盘关闭的模态浮层 —— Drawer、Dialog、DatePicker、
   * MarketReviewRegionSelector、SettingsHelpButton 都认 Esc，只有它挂着 `aria-modal="true"`
   * 却只能鼠标点掉。销毁类确认（删自选、删会话、删告警规则）尤其容易踩到。
   *
   * 取消被禁用时不响应，与点背景一致：`cancelDisabled` 表达的是「这次确认不允许反悔」，
   * 不能给它留一条绕过去的暗路。
   */
  useEffect(() => {
    if (!isOpen || cancelDisabled) {
      return;
    }
    const onKeyDown = (event: KeyboardEvent) => {
      // 让位判断今天恒为 false（确认框总是最上面那层），写上是为了和 Drawer / Dialog 同一套规则：
      // 哪天确认框再叠一层确认框，这里不必回头补。
      if (event.key === 'Escape' && !isCovered()) {
        onCancel();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [isOpen, cancelDisabled, onCancel, isCovered]);

  if (!isOpen) return null;

  const dialog = (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm transition-colors"
      role="presentation"
      onClick={() => {
        if (!cancelDisabled) {
          onCancel();
        }
      }}
    >
      <div
        ref={modalRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="mx-4 w-full max-w-sm rounded-xl overflow-hidden glass-surface-strong p-6 shadow-2xl animate-in fade-in zoom-in duration-200"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-lg font-medium text-foreground">{title}</h3>
        <p className="text-sm text-secondary-text mb-6 leading-relaxed">
          {message}
        </p>
        {children ? <div className="mb-6">{children}</div> : null}
        <div className="flex justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            disabled={cancelDisabled}
            className="rounded-lg border border-border/70 px-4 py-2 text-sm font-medium text-secondary-text transition-colors hover:bg-hover hover:text-foreground disabled:cursor-not-allowed disabled:opacity-60"
          >
            {cancelText ?? t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={confirmDisabled}
            className={`rounded-lg px-4 py-2 text-sm font-medium text-foreground transition-colors ${
              isDanger
                ? 'bg-red-500/80 hover:bg-red-500 shadow-lg shadow-red-500/20'
                : 'bg-cyan/80 hover:bg-cyan shadow-lg shadow-cyan/20'
            } disabled:cursor-not-allowed disabled:opacity-60`}
          >
            {confirmText ?? t('common.confirm')}
          </button>
        </div>
      </div>
    </div>
  );

  return createPortal(dialog, document.body);
};
