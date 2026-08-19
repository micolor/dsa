import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BarChart3, X } from 'lucide-react';
import { Outlet, useLocation } from 'react-router-dom';
import { SidebarNav } from './SidebarNav';
import { GlobalTaskCenter } from '../tasks/GlobalTaskCenter';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { ROUTE_TITLES } from './routeTitles';

const HOVER_CLOSE_DELAY = 150;

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const scrollRafRef = useRef<number | undefined>(undefined);
  const { t } = useUiLanguage();
  const location = useLocation();
  const closeTimerRef = useRef<number | undefined>(undefined);
  const current = ROUTE_TITLES[location.pathname];

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

  // Reveal the pinned top bar only once the active scroll container has moved.
  // Scroll doesn't bubble, so use a capture-phase listener to catch the
  // homepage's inner scroll container as well as the document itself.
  useEffect(() => {
    const isScrolledEl = (el: Element | Document | null): boolean => {
      if (!el) return false;
      if (el === document) {
        return (document.scrollingElement ?? document.documentElement).scrollTop > 2;
      }
      return el instanceof Element && el.scrollTop > 2;
    };

    // Initial state (e.g. restored scroll position on a back-nav).
    const initialRaf = requestAnimationFrame(() => {
      setScrolled(
        (document.scrollingElement ?? document.documentElement).scrollTop > 2 ||
          Array.from(document.querySelectorAll('main, main *')).some((el) => el.scrollTop > 2),
      );
    });

    const onScroll = (e: Event) => {
      const now = isScrolledEl(e.target as Element | Document);
      if (scrollRafRef.current == null) {
        scrollRafRef.current = requestAnimationFrame(() => {
          scrollRafRef.current = undefined;
          setScrolled((prev) => (prev === now ? prev : now));
        });
      }
    };

    window.addEventListener('scroll', onScroll, true);
    return () => {
      window.removeEventListener('scroll', onScroll, true);
      cancelAnimationFrame(initialRaf);
      if (scrollRafRef.current != null) cancelAnimationFrame(scrollRafRef.current);
    };
  }, []);

  return (
    <div className="min-h-screen bg-transparent text-foreground">
      <header
        className={
          'sticky top-1 z-40 glass-surface transition-[border-color] duration-200 ' +
          (scrolled
            ? 'border-b border-border/40 border-x-transparent border-t-transparent'
            : 'border-b border-transparent')
        }
      >
        <div
          className="absolute left-0 top-0 bottom-0 z-10 flex items-center"
          onMouseEnter={openMenu}
          onMouseLeave={scheduleClose}
        >
          <button
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            className="ml-2 inline-flex h-7 w-7 items-center justify-center rounded-lg border border-border/60 bg-card/60 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
            aria-label={menuOpen ? t('layout.closeNav') : t('layout.openNav')}
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X className="h-3.5 w-3.5" /> : <BarChart3 className="h-3.5 w-3.5 text-primary" />}
          </button>

          {menuOpen ? (
            <div className="absolute left-2 top-full mt-1.5 w-60 max-h-[calc(100vh-6rem)] overflow-y-auto rounded-2xl glass-surface-strong p-2.5 shadow-soft-card">
              <SidebarNav onNavigate={closeMenu} />
            </div>
          ) : null}
        </div>

        <div className="mx-auto flex h-9 w-full max-w-[1680px] items-center pl-12 pr-3 sm:pr-4 lg:pr-5">
          <div className="mt-0.5 flex min-w-0 flex-1 items-baseline gap-2 overflow-hidden">
            <span className="truncate text-lg font-semibold text-foreground">
              {current ? t(current.title) : t('layout.appFallbackTitle')}
            </span>
            {current ? (
              <>
                <span aria-hidden="true" className="hidden shrink-0 text-secondary-text/70 md:inline">·</span>
                <span className="hidden truncate text-xs text-secondary-text md:inline">
                  {t(current.description)}
                </span>
              </>
            ) : null}
          </div>
        </div>
      </header>

      <div className="mx-auto flex min-h-[calc(100vh-2.5rem)] w-full max-w-[1680px] px-3 py-3 sm:px-4 sm:py-4 lg:px-5">
        <main className="min-h-0 min-w-0 flex-1 touch-pan-y">
          {children ?? <Outlet />}
        </main>
      </div>

      {/* 全局任务中心：分析 + 选股任务的左侧任务图标，所有路由常驻 */}
      <GlobalTaskCenter />
    </div>
  );
};
