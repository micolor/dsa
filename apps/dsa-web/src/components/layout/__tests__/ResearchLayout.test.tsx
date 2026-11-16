import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it } from 'vitest';
import { UiLanguageProvider } from '../../../contexts/UiLanguageContext';
import { ResearchLayout } from '../ResearchLayout';

function renderResearch(path: string) {
  return render(
    <UiLanguageProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/research" element={<ResearchLayout />}>
            <Route path="screening" element={<div>screening content</div>} />
            <Route path="signals" element={<div>signals content</div>} />
            <Route path="backtest" element={<div>backtest content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </UiLanguageProvider>,
  );
}

describe('ResearchLayout', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem('dsa.uiLanguage', 'zh');
  });

  it('renders one tab bar and the matching child route', () => {
    renderResearch('/research/signals');

    expect(screen.getByRole('tab', { name: '选股' })).toHaveAttribute('href', '/research/screening');
    expect(screen.getByRole('tab', { name: 'AI 建议' })).toHaveAttribute('href', '/research/signals');
    expect(screen.getByRole('tab', { name: '回测' })).toHaveAttribute('href', '/research/backtest');
    expect(screen.getByRole('tab', { name: 'AI 建议' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('signals content')).toBeInTheDocument();
  });

  it('offers an assistant entry for the active research tab', () => {
    renderResearch('/research/backtest');

    expect(screen.getByRole('button', { name: '问助手' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '回测' })).toHaveAttribute('aria-selected', 'true');
  });
});
