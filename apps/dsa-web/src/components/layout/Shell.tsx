import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Menu, X } from 'lucide-react';
import { Outlet } from 'react-router-dom';
import { SidebarNav } from './SidebarNav';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

const HOVER_CLOSE_DELAY = 150;

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const { t } = useUiLanguage();
  const closeTimerRef = useRef<number | undefined>(undefined);

  const closeMenu = useCallback(() => {
    window.clearTimeout(closeTimerRef.current);
    setMenuOpen(false);
  }, []);

  const openMenu = useCallback(() => {
    window.clearTimeout(closeTimerRef.current);
    setMenuOpen(true);
  }, []);

  const scheduleClose = useCallback(() => {
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = window.setTimeout(() => setMenuOpen(false), HOVER_CLOSE_DELAY);
  }, []);

  useEffect(() => () => window.clearTimeout(closeTimerRef.current), []);

  return (
    <div className="min-h-screen bg-background text-foreground">
      <div
        className="fixed left-3 top-3 z-[100]"
        onMouseEnter={openMenu}
        onMouseLeave={scheduleClose}
      >
        <button
          type="button"
          onClick={() => setMenuOpen((o) => !o)}
          className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border/70 bg-card/85 text-secondary-text shadow-soft-card backdrop-blur-md transition-colors hover:bg-hover hover:text-foreground"
          aria-label={menuOpen ? t('layout.closeNav') : t('layout.openNav')}
          aria-expanded={menuOpen}
        >
          {menuOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>

        {menuOpen ? (
          <div className="mt-2 w-64 max-h-[calc(100vh-4rem)] overflow-y-auto rounded-2xl border border-border/70 bg-card/95 p-2.5 shadow-soft-card backdrop-blur-sm">
            <SidebarNav onNavigate={closeMenu} />
          </div>
        ) : null}
      </div>

      <div className="mx-auto flex min-h-screen w-full max-w-[1680px] px-3 py-3 sm:px-4 sm:py-4 lg:px-5">
        <main className="min-h-0 min-w-0 flex-1 pt-14 lg:pt-0 touch-pan-y">
          {children ?? <Outlet />}
        </main>
      </div>
    </div>
  );
};
