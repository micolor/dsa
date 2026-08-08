import type { UiTextKey } from '../../i18n/uiText';

/**
 * Route -> module title/description i18n keys. Single source of truth for the
 * persistent module indicator shown in the top bar (Shell / ShellHeader).
 */
export const ROUTE_TITLES: Record<string, { title: UiTextKey; description: UiTextKey }> = {
  '/': { title: 'layout.route.home.title', description: 'layout.route.home.description' },
  '/chat': { title: 'layout.route.chat.title', description: 'layout.route.chat.description' },
  '/portfolio': { title: 'layout.route.portfolio.title', description: 'layout.route.portfolio.description' },
  '/decision-signals': { title: 'layout.route.decisionSignals.title', description: 'layout.route.decisionSignals.description' },
  '/screening': { title: 'layout.route.screening.title', description: 'layout.route.screening.description' },
  '/backtest': { title: 'layout.route.backtest.title', description: 'layout.route.backtest.description' },
  '/alerts': { title: 'layout.route.alerts.title', description: 'layout.route.alerts.description' },
  '/paper': { title: 'layout.route.paper.title', description: 'layout.route.paper.description' },
  '/usage': { title: 'layout.route.usage.title', description: 'layout.route.usage.description' },
  '/settings': { title: 'layout.route.settings.title', description: 'layout.route.settings.description' },
};
