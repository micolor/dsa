import type React from 'react';
import { Menu, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { useLocation } from 'react-router-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { UiLanguageToggle } from '../i18n/UiLanguageToggle';
import { ThemeToggle } from '../theme/ThemeToggle';
import { ROUTE_TITLES } from './routeTitles';

type ShellHeaderProps = {
  collapsed: boolean;
  onToggleSidebar: () => void;
  onOpenMobileNav: () => void;
};

export const ShellHeader: React.FC<ShellHeaderProps> = ({
  collapsed,
  onToggleSidebar,
  onOpenMobileNav,
}) => {
  const location = useLocation();
  const { t } = useUiLanguage();
  const current = ROUTE_TITLES[location.pathname];

  return (
    <header className="sticky top-0 z-30 border-b border-border/60 bg-background/84 backdrop-blur-xl">
      <div className="mx-auto flex h-16 w-full max-w-[1680px] items-center gap-3 px-4 sm:px-6 lg:px-8">
        <button
          type="button"
          onClick={onOpenMobileNav}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border/70 bg-card/70 text-secondary-text transition-colors hover:bg-hover hover:text-foreground lg:hidden"
          aria-label={t('layout.openNav')}
        >
          <Menu className="h-5 w-5" />
        </button>

        <button
          type="button"
          onClick={onToggleSidebar}
          className="hidden h-10 w-10 items-center justify-center rounded-xl border border-border/70 bg-card/70 text-secondary-text transition-colors hover:bg-hover hover:text-foreground lg:inline-flex"
          aria-label={collapsed ? t('layout.expandSidebar') : t('layout.collapseSidebar')}
        >
          {collapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-5 w-5" />}
        </button>

        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold text-foreground">{current ? t(current.title) : t('layout.appFallbackTitle')}</p>
          <p className="truncate text-xs text-secondary-text">{current ? t(current.description) : t('layout.appFallbackDescription')}</p>
        </div>

        <UiLanguageToggle />
        <ThemeToggle />
      </div>
    </header>
  );
};
