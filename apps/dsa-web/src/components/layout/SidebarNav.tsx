import React, { useEffect, useState } from 'react';
import { Activity, BarChart3, Bell, BriefcaseBusiness, CalendarClock, Home, Inbox, LogOut, MessageSquareQuote, Search, Settings2, TrendingUp } from 'lucide-react';
import { NavLink } from 'react-router-dom';
import { INBOX_OPEN_ALL_EVENT, INBOX_UPDATED_EVENT, inboxApi } from '../../api/inbox';
import { SCREENING_CONFIG_CHANGED_EVENT, SYSTEM_CONFIG_CHANGED_EVENT, screeningApi } from '../../api/screening';
import { useAuth } from '../../contexts/AuthContext';
import { useAgentChatStore } from '../../stores/agentChatStore';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';
import { ConfirmDialog } from '../common/ConfirmDialog';
import { StatusDot } from '../common/StatusDot';

type SidebarNavProps = {
  collapsed?: boolean;
  onNavigate?: () => void;
  variant?: 'default' | 'rail';
};

/** 选股导航项启用状态的本地缓存 key（避免每次打开菜单都动态插入该导航项） */
const SCREENING_NAV_ENABLED_KEY = 'dsa.screeningNav.enabled';

const readScreeningNavEnabled = (): boolean => {
  try {
    return window.localStorage.getItem(SCREENING_NAV_ENABLED_KEY) === '1';
  } catch {
    return false;
  }
};

type NavItem = {
  key: string;
  labelKey: UiTextKey;
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  exact?: boolean;
  badge?: 'completion' | 'unread';
  /** Non-route entry: triggers an app-level action instead of navigation. */
  action?: 'inbox';
};

type NavGroup = {
  key: string;
  labelKey: UiTextKey;
  items: NavItem[];
};

/**
 * 侧栏按「工作台 → 研究 → 交易 → 自动化 → 系统」分组，而不是平铺 11 个入口。
 * 路由本身不合并；分组只降低首屏的认知负担，后续再按实际使用情况合并页面。
 */
const NAV_GROUPS: NavGroup[] = [
  {
    key: 'workspace',
    labelKey: 'layout.navGroup.workspace',
    items: [
      { key: 'home', labelKey: 'layout.nav.home', to: '/', icon: Home, exact: true },
      { key: 'chat', labelKey: 'layout.nav.chat', to: '/chat', icon: MessageSquareQuote, badge: 'completion' },
    ],
  },
  {
    key: 'research',
    labelKey: 'layout.navGroup.research',
    items: [
      { key: 'screening', labelKey: 'layout.nav.screening', to: '/screening', icon: Search },
      { key: 'decision-signals', labelKey: 'layout.nav.decisionSignals', to: '/decision-signals', icon: Activity },
    ],
  },
  {
    key: 'trading',
    labelKey: 'layout.navGroup.trading',
    items: [
      { key: 'portfolio', labelKey: 'layout.nav.portfolio', to: '/portfolio', icon: BriefcaseBusiness },
      { key: 'paper', labelKey: 'layout.nav.paperTrading', to: '/paper', icon: TrendingUp },
    ],
  },
  {
    key: 'automation',
    labelKey: 'layout.navGroup.automation',
    items: [
      { key: 'inbox', labelKey: 'layout.nav.inbox', to: '', icon: Inbox, action: 'inbox' },
      { key: 'alerts', labelKey: 'layout.nav.alerts', to: '/alerts', icon: Bell },
      { key: 'scheduled-tasks', labelKey: 'layout.nav.scheduledTasks', to: '/scheduled-tasks', icon: CalendarClock },
    ],
  },
  {
    key: 'system',
    labelKey: 'layout.navGroup.system',
    items: [
      { key: 'settings', labelKey: 'layout.nav.settings', to: '/settings', icon: Settings2 },
    ],
  },
];

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed = false, onNavigate, variant = 'default' }) => {
  const { authEnabled, logout } = useAuth();
  const { t } = useUiLanguage();
  const completionBadge = useAgentChatStore((state) => state.completionBadge);
  const [showLogoutConfirm, setShowLogoutConfirm] = useState(false);
  // 优先从本地缓存初始化，避免打开菜单时「选股」项在接口返回后才插入的闪变
  const [showScreeningNav, setShowScreeningNav] = useState<boolean>(readScreeningNavEnabled);
  const [inboxUnread, setInboxUnread] = useState(0);

  // 侧栏只在菜单展开时挂载，这里的轮询生命周期很短；复用铃铛的更新事件，
  // 让「提醒」入口也能看到未读数，而不是只能看顶栏徽标。
  useEffect(() => {
    let active = true;
    const refreshUnread = async () => {
      try {
        const result = await inboxApi.list({ pageSize: 1 });
        if (active) setInboxUnread(result.unreadCount);
      } catch {
        // keep the previous count
      }
    };
    void refreshUnread();
    window.addEventListener(INBOX_UPDATED_EVENT, refreshUnread);
    const timer = window.setInterval(refreshUnread, 60_000);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.removeEventListener(INBOX_UPDATED_EVENT, refreshUnread);
    };
  }, []);

  useEffect(() => {
    let active = true;

    const refreshScreeningStatus = async () => {
      try {
        const status = await screeningApi.getStatus();
        const enabled = Boolean(status.enabled);
        if (active) {
          setShowScreeningNav(enabled);
        }
        try {
          window.localStorage.setItem(SCREENING_NAV_ENABLED_KEY, enabled ? '1' : '0');
        } catch {
          // localStorage 不可用时静默降级
        }
      } catch {
        if (active) {
          // 拉取失败时回退到缓存值，避免菜单项闪变
          setShowScreeningNav(readScreeningNavEnabled());
        }
      }
    };

    void refreshScreeningStatus();
    window.addEventListener(SCREENING_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
    window.addEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshScreeningStatus);

    return () => {
      active = false;
      window.removeEventListener(SCREENING_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
      window.removeEventListener(SYSTEM_CONFIG_CHANGED_EVENT, refreshScreeningStatus);
    };
  }, []);

  const navGroups = NAV_GROUPS
    .map((group) => ({
      ...group,
      items: showScreeningNav
        ? group.items
        : group.items.filter((item) => item.key !== 'screening'),
    }))
    .filter((group) => group.items.length > 0);
  const isRail = variant === 'rail';
  const itemBaseClass = cn(
    'group relative flex h-[var(--nav-item-height)] w-full items-center overflow-hidden rounded-xl border border-transparent text-sm leading-none text-secondary-text transition-colors',
    isRail
      ? 'justify-center gap-2.5 px-2'
      : collapsed
        ? 'justify-center px-0'
        : 'gap-3 px-[var(--nav-item-padding-x)]'
  );
  const itemInteractiveClass = cn(
    itemBaseClass,
    'hover:bg-[var(--nav-hover-bg)] hover:text-foreground'
  );
  const itemActiveClass = 'bg-[var(--nav-active-bg)] font-medium text-[hsl(var(--primary))]';
  const itemIconClass = cn(isRail ? 'h-[18px] w-[18px]' : 'h-4 w-4', 'shrink-0');
  const itemLabelClass = cn('truncate', isRail ? 'text-center' : '');

  return (
    <div className="flex h-full flex-col">
      <div
        className={cn(
          'flex items-center',
          isRail ? 'mb-5 justify-center gap-2 pt-1' : 'mb-4 gap-2 px-1',
          collapsed || isRail ? 'justify-center' : ''
        )}
      >
        <div
          className={cn(
            'flex items-center justify-center bg-primary-gradient text-[hsl(var(--primary-foreground))] shadow-soft-card',
            isRail ? 'h-9 w-9 rounded-[1rem]' : 'h-10 w-10 rounded-2xl'
          )}
        >
          <BarChart3 className={cn(isRail ? 'h-[19px] w-[19px]' : 'h-4 w-4')} />
        </div>
        {!collapsed ? (
          <p className={cn('min-w-0 truncate font-semibold text-foreground', isRail ? 'text-[0.95rem] leading-none' : 'text-sm')}>DSA</p>
        ) : null}
      </div>

      <nav className={cn('flex flex-col gap-1', isRail ? '' : 'flex-1')} aria-label={t('layout.mainNav')}>
        {navGroups.map((group, groupIndex) => (
          <div
            key={group.key}
            className={cn(
              'flex flex-col gap-1',
              groupIndex > 0 && (collapsed || isRail
                ? 'mt-2 border-t border-border/40 pt-2'
                : 'mt-1'),
            )}
          >
            {!collapsed && !isRail ? (
              <p className="px-2 pb-1 pt-2 text-[11px] font-medium uppercase tracking-wide text-muted-text">
                {t(group.labelKey)}
              </p>
            ) : null}
            {group.items.map(({ key, labelKey, to, icon: Icon, exact, badge, action }) => {
              const label = t(labelKey);
              if (action === 'inbox') {
                return (
                  <button
                    key={key}
                    type="button"
                    aria-label={label}
                    onClick={() => {
                      window.dispatchEvent(new Event(INBOX_OPEN_ALL_EVENT));
                      onNavigate?.();
                    }}
                    className={cn(itemInteractiveClass, 'bg-transparent text-left')}
                  >
                    <Icon className={itemIconClass} />
                    {!collapsed ? <span className={itemLabelClass}>{label}</span> : null}
                    {!collapsed && inboxUnread > 0 ? (
                      <span
                        aria-hidden="true"
                        className="ml-auto rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-medium tabular-nums text-primary"
                      >
                        {inboxUnread > 99 ? '99+' : inboxUnread}
                      </span>
                    ) : null}
                  </button>
                );
              }
              return (
                <NavLink
                  key={key}
                  to={to}
                  end={exact}
                  onClick={onNavigate}
                  aria-label={label}
                  className={({ isActive }) =>
                    cn(
                      itemInteractiveClass,
                      isActive ? itemActiveClass : ''
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <Icon className={cn(itemIconClass, isActive ? 'text-[var(--nav-icon-active)]' : 'text-current')} />
                      {!collapsed ? <span className={itemLabelClass}>{label}</span> : null}
                      {badge === 'completion' && completionBadge ? (
                        <StatusDot
                          tone="info"
                          data-testid="chat-completion-badge"
                          className={cn(
                            'absolute right-3 border-2 border-background shadow-[0_0_10px_var(--nav-indicator-shadow)]',
                            collapsed ? 'right-2 top-2' : ''
                          )}
                          aria-label={t('layout.newChatMessage')}
                        />
                      ) : null}
                    </>
                  )}
                </NavLink>
              );
            })}
          </div>
        ))}
      </nav>

      {authEnabled ? (
        <button
          type="button"
          onClick={() => setShowLogoutConfirm(true)}
          className={cn(
            itemInteractiveClass,
            isRail ? 'mt-1.5' : 'mt-5'
          )}
        >
          <LogOut className={itemIconClass} />
          {!collapsed ? <span className={itemLabelClass}>{t('layout.logout')}</span> : null}
        </button>
      ) : null}

      <ConfirmDialog
        isOpen={showLogoutConfirm}
        title={t('layout.logoutTitle')}
        message={t('layout.logoutMessage')}
        confirmText={t('layout.logoutConfirm')}
        cancelText={t('common.cancel')}
        isDanger
        onConfirm={() => {
          setShowLogoutConfirm(false);
          onNavigate?.();
          void logout();
        }}
        onCancel={() => setShowLogoutConfirm(false)}
      />
    </div>
  );
};
