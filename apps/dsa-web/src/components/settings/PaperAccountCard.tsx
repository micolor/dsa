import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { paperApi } from '../../api/paper';
import { getParsedApiError } from '../../api/error';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { formatUiText } from '../../i18n/uiText';
import { PAPER_TRADING_TEXT } from '../../locales/featureText';
import type { PaperAccount } from '../../types/paper';
import { Badge, Button, ConfirmDialog, Input } from '../common';
import { SettingsAlert } from './SettingsAlert';
import { ToastPortal } from '../../contexts/ToastHostContext';
import { SettingsSectionCard } from './SettingsSectionCard';

// 与模拟盘页一致的数字口径。
function fmt(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '--';
  return value.toLocaleString('zh-CN', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

/**
 * 模拟盘账户的资金与重置入口。
 *
 * 重置是低频的破坏性操作，放在设置里而不是模拟盘页工具栏，避免和刷新 / 回填这类
 * 高频操作挤在一起误点。归档只改账户状态，旧账户的持仓、成交、净值仍按原
 * account_id 保留在库里。
 */
export const PaperAccountCard: React.FC = () => {
  const { language, t } = useUiLanguage();
  const text = PAPER_TRADING_TEXT[language];

  const [account, setAccount] = useState<PaperAccount | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isResetting, setIsResetting] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);
  const [isResetOpen, setIsResetOpen] = useState(false);
  const [resetCapital, setResetCapital] = useState('');

  const loadAccount = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      setAccount(await paperApi.getAccount());
    } catch (err) {
      setLoadError(getParsedApiError(err).message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAccount();
  }, [loadAccount]);

  // 金额留空表示交给服务端的 PAPER_INITIAL_CAPITAL 配置；填了就先在本地挡掉
  // 明显非法的输入，省一次注定 422 的往返。
  const resetCapitalValue = resetCapital.trim() === '' ? null : Number(resetCapital);
  const resetCapitalInvalid =
    resetCapitalValue != null && (!Number.isFinite(resetCapitalValue) || resetCapitalValue <= 0);

  const handleReset = async () => {
    setIsResetting(true);
    setActionError(null);
    setSuccessMessage(null);
    try {
      const next = await paperApi.reset(resetCapitalValue ?? undefined);
      setAccount(next);
      setSuccessMessage(formatUiText(text.resetDone, { amount: fmt(next.initialCapital) }));
      setIsResetOpen(false);
      setResetCapital('');
    } catch (err) {
      setActionError(getParsedApiError(err).message);
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <SettingsSectionCard
      title={text.title}
      description={text.resetCardDescription}
      actions={
        account ? (
          <Badge
            variant={account.status === 'active' ? 'success' : 'default'}
            size="sm"
            className={account.status === 'active' ? '' : 'border-border/60 bg-hover text-secondary-text'}
          >
            {account.status === 'active' ? text.open : account.status}
          </Badge>
        ) : null
      }
    >
      <div className="rounded-xl border border-border/60 bg-elevated p-4 shadow-soft-card">
        {isLoading ? (
          <p className="text-sm text-muted-text">{text.refreshing}</p>
        ) : loadError ? (
          <div className="space-y-3">
            <SettingsAlert title={text.resetCardLoadFailed} message={loadError} variant="error" />
            <Button type="button" variant="secondary" onClick={() => void loadAccount()}>
              {text.refresh}
            </Button>
          </div>
        ) : account ? (
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {[
              { key: text.account, value: `#${account.accountId}` },
              { key: text.initialCapital, value: fmt(account.initialCapital) },
              { key: text.cash, value: fmt(account.cash) },
              { key: text.totalAssets, value: fmt(account.snapshot?.netValue) },
            ].map((item) => (
              <div key={item.key} className="min-w-0">
                <dt className="text-xs leading-5 text-muted-text">{item.key}</dt>
                <dd className="mt-1 truncate text-sm font-semibold text-foreground">{item.value}</dd>
              </div>
            ))}
          </dl>
        ) : null}
      </div>

      <p className="text-xs leading-6 text-muted-text">{text.resetCardHint}</p>

      {/* 重置账户的结果送全局右上角容器；卡片自身的加载失败占位留在卡片里。 */}
      <ToastPortal>
        {actionError ? (
          <SettingsAlert presentation="toast" className="pointer-events-auto" title={text.resetFailed} message={actionError} variant="error" />
        ) : null}
        {successMessage ? (
          <SettingsAlert presentation="toast" className="pointer-events-auto" title={t('settings.actionSuccess')} message={successMessage} variant="success" />
        ) : null}
      </ToastPortal>

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="secondary"
          onClick={() => {
            setResetCapital('');
            setActionError(null);
            setIsResetOpen(true);
          }}
          disabled={isLoading || isResetting}
        >
          {text.reset}
        </Button>
        <Button
          type="button"
          variant="secondary"
          onClick={() => void loadAccount()}
          disabled={isLoading || isResetting}
        >
          {text.refresh}
        </Button>
      </div>

      <ConfirmDialog
        isOpen={isResetOpen}
        title={text.resetTitle}
        message={text.resetMessage}
        confirmText={isResetting ? text.resetting : text.resetConfirm}
        cancelText={text.cancel}
        confirmDisabled={isResetting || resetCapitalInvalid}
        cancelDisabled={isResetting}
        isDanger
        onConfirm={() => void handleReset()}
        onCancel={() => setIsResetOpen(false)}
      >
        <Input
          type="text"
          inputMode="decimal"
          value={resetCapital}
          onChange={(e) => setResetCapital(e.target.value)}
          placeholder={text.resetCapitalPlaceholder}
          label={text.resetCapitalLabel}
          error={resetCapitalInvalid ? text.resetCapitalInvalid : undefined}
        />
      </ConfirmDialog>
    </SettingsSectionCard>
  );
};
