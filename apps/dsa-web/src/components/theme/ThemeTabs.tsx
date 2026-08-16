import type React from 'react';
import { Monitor, Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import type { UiTextKey } from '../../i18n/uiText';
import { cn } from '../../utils/cn';

type ThemeOption = 'light' | 'dark' | 'system';

const THEME_OPTIONS: Array<{
  value: ThemeOption;
  labelKey: UiTextKey;
  icon: typeof Sun;
}> = [
  { value: 'light', labelKey: 'theme.light', icon: Sun },
  { value: 'dark', labelKey: 'theme.dark', icon: Moon },
  { value: 'system', labelKey: 'theme.system', icon: Monitor },
];

interface ThemeTabsProps {
  className?: string;
}

/**
 * Horizontal segmented tab control for the interface theme.
 *
 * Renders 浅色 / 深色 / 跟随系统 as an expanded inline tab group (rather than a
 * collapsed dropdown), matching the app's pill/segmented pattern. Used in the
 * Settings page preferences card.
 */
export const ThemeTabs: React.FC<ThemeTabsProps> = ({ className }) => {
  const { theme, setTheme } = useTheme();
  const { t } = useUiLanguage();
  const activeTheme = (theme as ThemeOption | undefined) ?? 'system';

  return (
    <div
      role="tablist"
      aria-label={t('settings.theme')}
      className={cn(
        'inline-flex flex-wrap items-center gap-1 rounded-xl border border-border/70 bg-elevated/50 p-1 shadow-soft-card',
        className,
      )}
    >
      {THEME_OPTIONS.map(({ value, labelKey, icon: Icon }) => {
        const isActive = activeTheme === value;
        return (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={isActive}
            onClick={() => setTheme(value)}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm transition-colors',
              isActive
                ? 'border border-[hsl(var(--primary)/0.36)] bg-[hsl(var(--primary)/0.12)] font-medium text-foreground'
                : 'border border-transparent text-secondary-text hover:bg-hover hover:text-foreground',
            )}
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
            {t(labelKey)}
          </button>
        );
      })}
    </div>
  );
};
