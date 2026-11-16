import type React from 'react';
import { Suspense, useEffect } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { Button } from '../common';
import { PageLoadingFallback } from './RouteBoundary';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import type { UiTextKey } from '../../i18n/uiText';

type ResearchTab = {
  key: 'screening' | 'signals' | 'backtest';
  to: string;
  labelKey: UiTextKey;
};

const RESEARCH_TABS: ResearchTab[] = [
  { key: 'screening', to: '/research/screening', labelKey: 'research.tabs.screening' },
  { key: 'signals', to: '/research/signals', labelKey: 'research.tabs.signals' },
  { key: 'backtest', to: '/research/backtest', labelKey: 'research.tabs.backtest' },
];

/**
 * Shared shell for the research workspace. The actual pages keep owning their
 * data, tables and filters; this layout only adds one stable tab bar and an
 * assistant entry so users do not have to discover three separate nav items.
 */
export const ResearchLayout: React.FC = () => {
  const { t } = useUiLanguage();
  const location = useLocation();
  const activeTab = RESEARCH_TABS.find((tab) => location.pathname.startsWith(tab.to)) ?? RESEARCH_TABS[0];

  useEffect(() => {
    document.title = `${t(activeTab.labelKey)} - DSA`;
  }, [activeTab.labelKey, t]);

  const askAssistant = () => {
    const prompt = t('research.askAssistantPrompt', { tab: t(activeTab.labelKey) });
    window.location.assign(`/chat?prompt=${encodeURIComponent(prompt)}`);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 pt-3 md:px-4">
        <div
          role="tablist"
          aria-label={t('layout.route.research.title')}
          className="inline-flex items-center gap-1 rounded-xl border border-border/60 bg-elevated p-0.5"
        >
          {RESEARCH_TABS.map((tab) => (
            <NavLink
              key={tab.key}
              to={tab.to}
              role="tab"
              aria-selected={activeTab.key === tab.key}
              className={cn(
                'rounded-lg px-3 py-1.5 text-sm transition-colors',
                activeTab.key === tab.key
                  ? 'bg-primary/15 font-medium text-primary'
                  : 'text-secondary-text hover:bg-hover hover:text-foreground',
              )}
            >
              {t(tab.labelKey)}
            </NavLink>
          ))}
        </div>
        <Button type="button" variant="secondary" size="sm" onClick={askAssistant}>
          {t('research.askAssistant')}
        </Button>
      </div>
      <div className="min-h-0 flex-1">
        <Suspense fallback={<PageLoadingFallback fullPage={false} />}>
          <Outlet />
        </Suspense>
      </div>
    </div>
  );
};

export default ResearchLayout;
