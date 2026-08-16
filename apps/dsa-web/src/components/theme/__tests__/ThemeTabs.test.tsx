import { fireEvent, render, screen } from '@testing-library/react';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../ThemeProvider';
import { ThemeTabs } from '../ThemeTabs';

beforeAll(() => {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation((query: string) => ({
      matches: query === '(prefers-color-scheme: dark)',
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
});

describe('ThemeTabs', () => {
  it('renders the theme options as an expanded horizontal tab group', () => {
    render(
      <ThemeProvider>
        <ThemeTabs />
      </ThemeProvider>,
    );

    expect(screen.getByRole('tablist')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '浅色' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '深色' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '跟随系统' })).toBeInTheDocument();
  });

  it('selects a tab on click', () => {
    render(
      <ThemeProvider>
        <ThemeTabs />
      </ThemeProvider>,
    );

    const darkTab = screen.getByRole('tab', { name: '深色' });
    const lightTab = screen.getByRole('tab', { name: '浅色' });
    // defaultTheme="dark"
    expect(darkTab).toHaveAttribute('aria-selected', 'true');
    expect(lightTab).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(lightTab);

    expect(lightTab).toHaveAttribute('aria-selected', 'true');
    expect(darkTab).toHaveAttribute('aria-selected', 'false');
  });
});
