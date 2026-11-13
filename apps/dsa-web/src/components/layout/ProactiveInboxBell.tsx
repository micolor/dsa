import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Bell, CheckCheck, Inbox, X } from 'lucide-react';
import { INBOX_OPEN_ALL_EVENT, INBOX_UPDATED_EVENT, inboxApi, type InboxItem, type InboxType } from '../../api/inbox';
import { systemConfigApi } from '../../api/systemConfig';
import { Button } from '../common';
import { Drawer } from '../common';
import { ReportMarkdownBody } from '../report/ReportMarkdownBody';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';

const TYPE_TONE: Record<InboxItem['type'], string> = {
  daily_brief: 'border-primary/25 bg-primary/10 text-primary',
  event: 'border-warning/30 bg-warning/10 text-warning',
  weekly: 'border-cyan/25 bg-cyan/10 text-cyan',
  opportunity: 'border-success/25 bg-success/10 text-success',
};

const DROPDOWN_UNREAD_LIMIT = 8;
const ALL_PAGE_SIZE = 20;

type InboxFilter = InboxType | 'all';

function formatTime(value: string | null, language: 'zh' | 'en'): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(language === 'en' ? 'en-US' : 'zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

export const ProactiveInboxBell: React.FC = () => {
  const { language, t } = useUiLanguage();
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();
  const [open, setOpen] = useState(false);
  const [unreadItems, setUnreadItems] = useState<InboxItem[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loadingUnread, setLoadingUnread] = useState(false);
  const [error, setError] = useState('');
  const [allOpen, setAllOpen] = useState(false);
  const [allItems, setAllItems] = useState<InboxItem[]>([]);
  const [allFilter, setAllFilter] = useState<InboxFilter>('all');
  const [allPage, setAllPage] = useState(1);
  const [allHasMore, setAllHasMore] = useState(false);
  const [allLoading, setAllLoading] = useState(false);
  const [allError, setAllError] = useState('');
  const [selected, setSelected] = useState<InboxItem | null>(null);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);

  const loadUnread = useCallback(async () => {
    setLoadingUnread(true);
    setError('');
    try {
      const result = await inboxApi.list({ unreadOnly: true, pageSize: DROPDOWN_UNREAD_LIMIT });
      setUnreadItems(result.items);
      setUnreadCount(result.unreadCount);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : t('inbox.errorTitle'));
    } finally {
      setLoadingUnread(false);
    }
  }, [t]);

  const loadAll = useCallback(async (targetPage = 1, append = false) => {
    setAllLoading(true);
    setAllError('');
    try {
      const result = await inboxApi.list({
        type: allFilter === 'all' ? undefined : allFilter,
        page: targetPage,
        pageSize: ALL_PAGE_SIZE,
      });
      setAllItems((current) => (append ? [...current, ...result.items] : result.items));
      setAllPage(result.page ?? targetPage);
      setAllHasMore(Boolean(result.hasMore));
      setUnreadCount(result.unreadCount);
    } catch (loadError) {
      setAllError(loadError instanceof Error ? loadError.message : t('inbox.errorTitle'));
    } finally {
      setAllLoading(false);
    }
  }, [allFilter, t]);

  useEffect(() => {
    let active = true;
    const refreshUnread = async () => {
      if (open) {
        // 下拉打开时直接刷新新消息列表，而不只是改徽标。
        if (active) await loadUnread();
        return;
      }
      try {
        const result = await inboxApi.list({ pageSize: 1 });
        if (active) setUnreadCount(result.unreadCount);
      } catch {
        // keep the previous badge
      }
    };
    if (!open) void refreshUnread();
    const timer = window.setInterval(refreshUnread, 60_000);
    window.addEventListener(INBOX_UPDATED_EVENT, refreshUnread);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener(INBOX_UPDATED_EVENT, refreshUnread);
    };
  }, [loadUnread, open]);

  useEffect(() => {
    if (!open) return undefined;
    void loadUnread();
    const handlePointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [loadUnread, open]);

  useEffect(() => {
    if (!allOpen) return;
    void loadAll(1, false);
  }, [allOpen, loadAll]);

  // 侧栏「提醒」入口复用铃铛的全量抽屉，不新增页面或路由。
  useEffect(() => {
    const openAll = () => {
      setOpen(false);
      setAllOpen(true);
    };
    window.addEventListener(INBOX_OPEN_ALL_EVENT, openAll);
    return () => window.removeEventListener(INBOX_OPEN_ALL_EVENT, openAll);
  }, []);

  const markItemRead = useCallback(async (item: InboxItem): Promise<InboxItem> => {
    if (item.read) return item;
    try {
      await inboxApi.markRead([item.id]);
      setUnreadItems((current) => current.filter((entry) => entry.id !== item.id));
      setAllItems((current) => current.map((entry) => (
        entry.id === item.id ? { ...entry, read: true } : entry
      )));
      setUnreadCount((current) => Math.max(0, current - 1));
      return { ...item, read: true };
    } catch {
      // The item remains visible so the user can retry.
      return item;
    }
  }, []);

  const openItem = useCallback(async (item: InboxItem) => {
    setSelected(item);
    setAdded(false);
    setOpen(false);
    const updated = await markItemRead(item);
    setSelected((current) => (current && current.id === item.id ? updated : current));
  }, [markItemRead]);

  useEffect(() => {
    const raw = searchParams.get('inbox');
    if (!raw) return;
    const next = new URLSearchParams(searchParams);
    next.delete('inbox');
    setSearchParams(next, { replace: true });
    const messageId = Number(raw);
    if (!Number.isFinite(messageId) || messageId <= 0) {
      setOpen(true);
      return;
    }
    void (async () => {
      try {
        const result = await inboxApi.list({ id: messageId, pageSize: 1 });
        const item = result.items[0];
        if (item) {
          await openItem(item);
        } else {
          setOpen(true);
        }
      } catch {
        setOpen(true);
      }
    })();
  }, [openItem, searchParams, setSearchParams]);

  const markAllRead = useCallback(async () => {
    try {
      await inboxApi.markAllRead();
      setUnreadItems([]);
      setAllItems((current) => current.map((entry) => ({ ...entry, read: true })));
      setUnreadCount(0);
      setSelected((current) => (current ? { ...current, read: true } : current));
    } catch {
      // keep state; the next open retries
    }
  }, []);

  const markItemUnread = useCallback(async (item: InboxItem): Promise<InboxItem> => {
    try {
      await inboxApi.markUnread([item.id]);
      setUnreadItems((current) => (
        current.some((entry) => entry.id === item.id)
          ? current
          : [{ ...item, read: false }, ...current]
      ));
      setAllItems((current) => current.map((entry) => (
        entry.id === item.id ? { ...entry, read: false } : entry
      )));
      setUnreadCount((current) => current + 1);
      return { ...item, read: false };
    } catch {
      // keep state
      return item;
    }
  }, []);

  const markUnread = useCallback(async () => {
    if (!selected) return;
    setSelected(await markItemUnread(selected));
  }, [markItemUnread, selected]);

  const addToWatchlist = useCallback(async () => {
    if (!selected?.stockCode || added) return;
    setAdding(true);
    try {
      await systemConfigApi.addToWatchlist(selected.stockCode);
      setAdded(true);
    } catch {
      // leave the button available for retry
    } finally {
      setAdding(false);
    }
  }, [added, selected]);


  const renderItem = (item: InboxItem) => (
    <button
      key={item.id}
      type="button"
      onClick={() => void openItem(item)}
      className={cn(
        'flex w-full items-start gap-2 rounded-xl border p-2.5 text-left transition-colors',
        item.read
          ? 'border-transparent bg-transparent hover:bg-hover/60'
          : 'border-primary/20 bg-primary/5 hover:bg-primary/10',
      )}
    >
      <span className={cn('mt-1.5 h-1.5 w-1.5 flex-shrink-0 rounded-full', item.read ? 'bg-subtle' : 'bg-danger')} aria-hidden="true" />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5">
          <span className={cn('rounded-full border px-1.5 py-0.5 text-[10px]', TYPE_TONE[item.type])}>
            {item.typeLabel}
          </span>
          <span className="truncate text-xs font-semibold text-foreground">{item.title}</span>
        </span>
        <span className="mt-0.5 line-clamp-2 block text-[11px] leading-relaxed text-secondary-text">
          {item.preview}
        </span>
        <span className="mt-1 block text-[10px] text-muted-text">
          {formatTime(item.createdAt, language)}
        </span>
      </span>
    </button>
  );

  const renderAllRow = (item: InboxItem) => (
    <button
      key={item.id}
      type="button"
      onClick={() => void openItem(item)}
      className="flex w-full items-start gap-3 border-b border-border/40 px-4 py-3 text-left transition-colors last:border-b-0 hover:bg-hover"
    >
      <span className={cn('mt-2 h-1.5 w-1.5 flex-shrink-0 rounded-full', item.read ? 'bg-subtle' : 'bg-danger')} aria-hidden="true" />
      <span className={cn('mt-0.5 hidden shrink-0 rounded-full border px-2 py-0.5 text-[11px] sm:inline-flex', TYPE_TONE[item.type])}>
        {item.typeLabel}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className={cn('shrink-0 rounded-full border px-1.5 py-0.5 text-[10px] sm:hidden', TYPE_TONE[item.type])}>
            {item.typeLabel}
          </span>
          <span className="truncate text-sm font-semibold text-foreground">{item.title}</span>
        </span>
        <span className="mt-1 line-clamp-1 text-xs leading-relaxed text-secondary-text">
          {item.preview}
        </span>
      </span>
      <span className="mt-0.5 shrink-0 text-[11px] text-muted-text">
        {formatTime(item.createdAt, language)}
      </span>
    </button>
  );

  const filterOptions: Array<{ value: InboxFilter; label: string }> = [
    { value: 'all', label: t('inbox.filter.all') },
    { value: 'daily_brief', label: t('inbox.filter.dailyBrief') },
    { value: 'event', label: t('inbox.filter.event') },
    { value: 'weekly', label: t('inbox.filter.weekly') },
    { value: 'opportunity', label: t('inbox.filter.opportunity') },
  ];

  return (
    <div ref={rootRef} className="relative ml-auto">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={t('inbox.title')}
        aria-expanded={open}
        className="relative inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border/60 bg-card/60 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
      >
        <Bell className="h-3.5 w-3.5" />
        {unreadCount > 0 ? (
          <span
            data-testid="inbox-unread-badge"
            className="absolute -right-1 -top-1 flex min-w-[1rem] items-center justify-center rounded-full bg-danger px-0.5 text-[10px] font-semibold leading-4 text-white"
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          data-esc-layer="true"
          className="absolute right-0 top-full z-50 mt-2 w-[22rem] max-w-[calc(100vw-1.5rem)] overflow-hidden rounded-2xl border border-border/70 bg-card shadow-soft-card"
        >
          <div className="flex items-center justify-between gap-2 border-b border-border/50 px-3 py-2.5">
            <div className="flex items-center gap-2">
              <Inbox className="h-4 w-4 text-primary" />
              <span className="text-sm font-semibold text-foreground">{t('inbox.title')}</span>
              {unreadCount > 0 ? (
                <span className="text-[11px] text-muted-text">
                  {unreadCount} {t('inbox.unread')}
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => void markAllRead()}
                disabled={unreadCount === 0}
                className="rounded-md p-1 text-muted-text transition-colors hover:text-foreground disabled:opacity-40"
                aria-label={t('inbox.markAllRead')}
              >
                <CheckCheck className="h-4 w-4" />
              </button>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="rounded-md p-1 text-muted-text transition-colors hover:text-foreground"
                aria-label={t('common.close')}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>

          <div className="max-h-[55vh] overflow-y-auto p-2">
            {error ? <p className="px-2 py-3 text-xs text-danger">{error}</p> : null}
            {loadingUnread && unreadItems.length === 0 ? (
              <p className="px-2 py-6 text-center text-xs text-muted-text">{t('common.loading')}</p>
            ) : null}
            {!error && unreadItems.length === 0 && !loadingUnread ? (
              <p className="px-2 py-6 text-center text-xs text-muted-text">{t('inbox.noNew')}</p>
            ) : null}
            <div className="space-y-1.5">{unreadItems.map(renderItem)}</div>
          </div>

          <div className="border-t border-border/50 p-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="w-full justify-center"
              onClick={() => {
                setOpen(false);
                setAllOpen(true);
              }}
            >
              <Inbox className="h-3.5 w-3.5" aria-hidden="true" />
              {t('inbox.viewAll')}
            </Button>
          </div>
        </div>
      ) : null}

      <Drawer
        isOpen={allOpen}
        onClose={() => setAllOpen(false)}
        title={t('inbox.allTitle')}
        eyebrow={t('inbox.title')}
        width="max-w-none"
        panelClassName="border-l-0 bg-background"
      >
        <div className="mx-auto w-full max-w-[1600px] space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-1.5">
              {filterOptions.map((option) => {
                const active = allFilter === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={active}
                    onClick={() => setAllFilter(option.value)}
                    className={cn(
                      'rounded-lg border px-2.5 py-1 text-xs transition-colors',
                      active
                        ? 'border-primary/30 bg-primary/15 text-primary'
                        : 'border-border/60 text-secondary-text hover:bg-hover hover:text-foreground',
                    )}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
            <div className="flex items-center gap-2">
              {unreadCount > 0 ? (
                <span className="text-xs text-muted-text">
                  {unreadCount} {t('inbox.unread')}
                </span>
              ) : null}
              <Button
                variant="ghost"
                size="sm"
                disabled={unreadCount === 0}
                onClick={() => void markAllRead()}
              >
                <CheckCheck className="h-4 w-4" aria-hidden="true" />
                {t('inbox.markAllRead')}
              </Button>
            </div>
          </div>
          {allError ? <p className="text-xs text-danger">{allError}</p> : null}
          {!allError && allItems.length === 0 && !allLoading ? (
            <p className="py-10 text-center text-xs text-muted-text">{t('inbox.empty')}</p>
          ) : null}
          {allItems.length ? (
            <div className="overflow-hidden rounded-2xl border border-border/60 bg-card/40">
              {allItems.map(renderAllRow)}
            </div>
          ) : null}
          {allLoading ? <p className="py-2 text-center text-xs text-muted-text">{t('common.loading')}</p> : null}
          {allHasMore ? (
            <div className="flex justify-center pt-1">
              <Button
                variant="ghost"
                size="sm"
                isLoading={allLoading}
                onClick={() => void loadAll(allPage + 1, true)}
              >
                {t('inbox.loadMore')}
              </Button>
            </div>
          ) : null}
        </div>
      </Drawer>

      <Drawer
        isOpen={Boolean(selected)}
        onClose={() => setSelected(null)}
        title={selected?.title}
        width="max-w-3xl"
      >
        {selected ? (
          <div className="space-y-4">
            <ReportMarkdownBody content={selected.content} testId="inbox-detail-markdown" />
            <div className="flex flex-wrap justify-end gap-2 border-t border-border/50 pt-4">
              {selected.stockCode ? (
                <Button
                  variant="secondary"
                  size="sm"
                  isLoading={adding}
                  disabled={added}
                  onClick={() => void addToWatchlist()}
                >
                  {added ? t('inbox.addedToWatchlist') : t('inbox.addToWatchlist')}
                </Button>
              ) : null}
              {selected.read ? (
                <Button variant="secondary" size="sm" onClick={() => void markUnread()}>
                  {t('inbox.markUnread')}
                </Button>
              ) : null}
              <Button variant="secondary" size="sm" onClick={() => setSelected(null)}>
                {t('common.close')}
              </Button>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
};
