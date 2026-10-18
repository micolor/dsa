import type React from 'react';
import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Maximize2, Sparkles, X } from 'lucide-react';
import { cn } from '../../utils/cn';
import { useEscTopLayer } from '../../hooks/useEscTopLayer';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

// 与 /chat 路由共用同一个懒加载模块：Vite 按模块 id 去重，不会额外打一份包。
const ChatPage = lazy(() => import('../../pages/ChatPage'));

/**
 * 右下角悬浮问股助手。
 *
 * 站内操作类请求（加自选、记一笔交易、跑一次复盘）的价值在于「人在哪页就操作哪页」，
 * 强制跳到 /chat 再回来会把一次操作拆成三次导航。这里复用 ChatPage 的 `dock` 形态：
 * 同一份消息渲染、同一个 store，因此会话、流式回复、操作确认卡片都与整页完全一致。
 *
 * 离开 /chat 后仍在跑的回复由 store 继续接收，结束时置 `completionBadge`，入口按钮上出红点。
 */
export const AssistantDock: React.FC = () => {
  const { t } = useUiLanguage();
  const location = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const { modalRef, isCovered } = useEscTopLayer();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const clearCompletionBadge = useAgentChatStore((state) => state.clearCompletionBadge);

  // /chat 本身已经有完整页面，登录页不在 Shell 里但也一并排除，避免两处同时挂载同一会话。
  const hidden = location.pathname === '/chat' || location.pathname === '/login';

  // 路由切到整页时直接收起：渲染期重置（而不是在 effect 里 setState）避免多跑一轮渲染，
  // 也避免离开 /chat 后带着旧状态重新弹开。
  if (hidden && open) {
    setOpen(false);
  }

  // Esc 只归最上面那层：面板里的策略浮层、确认框打开时先关它们，再关面板。
  useEffect(() => {
    if (!open) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || isCovered()) return;
      event.preventDefault();
      setOpen(false);
    };
    document.addEventListener('keydown', handleKeyDown);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [open, isCovered]);

  // 回复在面板展开期间完成时，用户已经看到了内容，不该在收起后还留一个未读红点。
  useEffect(() => {
    if (open && completionBadge) {
      clearCompletionBadge();
    }
  }, [open, completionBadge, clearCompletionBadge]);

  const toggle = useCallback(() => {
    setOpen((prev) => {
      if (!prev) {
        clearCompletionBadge();
      }
      return !prev;
    });
  }, [clearCompletionBadge]);

  const openFullPage = useCallback(() => {
    setOpen(false);
    navigate('/chat');
  }, [navigate]);

  if (hidden) return null;

  const toggleLabel = open
    ? t('assistant.closePanel')
    : completionBadge
      ? t('assistant.openWithReply')
      : t('assistant.openPanel');

  return (
    <>
      <button
        type="button"
        onClick={toggle}
        aria-label={toggleLabel}
        aria-expanded={open}
        aria-controls={open ? 'assistant-dock-panel' : undefined}
        className={cn(
          'group fixed bottom-5 right-5 z-30 inline-flex h-12 w-12 items-center justify-center rounded-full',
          'transition-[transform,box-shadow,background-color] duration-200 ease-out',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/70 focus-visible:ring-offset-2 focus-visible:ring-offset-background',
          open
            ? 'border border-border/70 bg-elevated text-secondary-text shadow-soft-card hover:text-foreground active:scale-95'
            : [
                // 品牌色实心圆 + 一圈同色投影：悬浮入口需要在任何页面底色上都能被认出来，
                // 又不能用描边抢走页面的注意力（Intercom / Crisp 的入口都是这一档强度）。
                'border border-primary/30 bg-primary text-white',
                'shadow-[0_6px_20px_-4px_hsl(var(--primary)/0.5),0_2px_6px_hsl(var(--primary)/0.25)]',
                'hover:scale-105 hover:bg-primary/90 hover:shadow-[0_10px_28px_-6px_hsl(var(--primary)/0.6)] active:scale-95',
              ].join(' '),
        )}
      >
        {!open && (
          // 桌面端悬停提示：入口只放一个图标，第一次访问的用户不一定知道它是问股助手。
          // 触屏没有 hover，移动端不渲染，靠 aria-label 播报。
          <span
            className={cn(
              'pointer-events-none absolute right-full mr-3 hidden whitespace-nowrap rounded-full border border-border/60 bg-elevated/95 px-3 py-1.5',
              'text-xs font-medium text-foreground shadow-soft-card backdrop-blur-sm',
              'opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100 sm:block',
            )}
            aria-hidden="true"
          >
            {t('assistant.launcherLabel')}
          </span>
        )}
        {open
          ? <X className="h-5 w-5" aria-hidden="true" />
          : <Sparkles className="h-5 w-5" aria-hidden="true" />}
        {!open && completionBadge && (
          <span className="absolute -right-0.5 -top-0.5 flex h-3.5 w-3.5" aria-hidden="true">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-danger/60 motion-reduce:animate-none" />
            <span className="relative inline-flex h-3.5 w-3.5 rounded-full border-2 border-card bg-danger" />
          </span>
        )}
      </button>

      {open && (
        <section
          id="assistant-dock-panel"
          ref={modalRef}
          data-esc-layer="true"
          aria-label={t('assistant.panelTitle')}
          className={cn(
            'fixed z-30 flex flex-col overflow-hidden border border-border/60 bg-card/95 backdrop-blur-2xl',
            'shadow-soft-card-strong',
            // 从右下角展开：位移收敛到 10px、缩放只到 0.98，像从入口按钮长出来，
            // 而不是整块卡片从屏幕外飞入。窄屏从底部展开，原点随之下移。
            'assistant-dock-panel origin-bottom sm:origin-bottom-right',
            // 窄屏铺满：400px 的浮层在手机上会把输入框和消息区都挤得没法用。
            // 顶部让出 36px 的 sticky 顶栏（z-40）：面板是 z-30，铺到 top-0 会把
            // 自己的标题栏和关闭按钮压在顶栏下面。
            'inset-x-0 bottom-0 top-9 sm:inset-auto sm:bottom-20 sm:right-5 sm:h-[min(760px,calc(100vh-6rem))] sm:w-[min(460px,calc(100vw-2.5rem))] sm:rounded-3xl',
            'max-sm:pb-[env(safe-area-inset-bottom)]',
          )}
        >
          <div className="flex h-14 flex-shrink-0 items-center justify-between gap-2 border-b border-border/50 bg-gradient-to-b from-white/[0.04] to-transparent px-3.5">
            <div className="flex min-w-0 items-center gap-2.5">
              <span className="flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary to-primary/70 text-white shadow-[0_4px_12px_-2px_hsl(var(--primary)/0.5)] ring-1 ring-white/15">
                <Sparkles className="h-4 w-4" aria-hidden="true" />
              </span>
              <div className="min-w-0">
                <h2 className="truncate text-sm font-semibold leading-tight text-foreground">
                  {t('assistant.panelTitle')}
                </h2>
                <p className="mt-0.5 flex items-center gap-1.5 truncate text-[11px] leading-tight text-secondary-text">
                  <span className="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-success" aria-hidden="true" />
                  {t('assistant.panelSubtitle')}
                </p>
              </div>
            </div>
            <div className="flex flex-shrink-0 items-center gap-0.5">
              <button
                type="button"
                onClick={openFullPage}
                aria-label={t('assistant.openFullPage')}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-secondary-text transition-colors hover:bg-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
              >
                <Maximize2 className="h-4 w-4" aria-hidden="true" />
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label={t('assistant.closePanel')}
                className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-secondary-text transition-colors hover:bg-hover hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60"
              >
                <X className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>

          <div className="min-h-0 flex-1">
            <Suspense
              fallback={(
                <div className="flex h-full items-center justify-center" role="status" aria-live="polite">
                  <div className="h-5 w-5 animate-spin rounded-full border-2 border-cyan/20 border-t-cyan" />
                </div>
              )}
            >
              <ChatPage variant="dock" />
            </Suspense>
          </div>
        </section>
      )}
    </>
  );
};
