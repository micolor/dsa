import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BreadthBar, MiniChangeBar, SectorBarList, type MarketBarRow } from '../MarketReviewBarChart';

describe('BreadthBar', () => {
  it('renders a labelled bar with up/down segments', () => {
    render(
      <BreadthBar
        upCount={3013}
        downCount={2389}
        ariaLabel="上涨 3013 家 / 下跌 2389 家"
      />,
    );

    const bar = screen.getByRole('img', { name: '上涨 3013 家 / 下跌 2389 家' });
    expect(bar).toBeInTheDocument();
    expect(bar.querySelector('[data-direction="up"]')).toBeInTheDocument();
    expect(bar.querySelector('[data-direction="down"]')).toBeInTheDocument();
  });

  it('normalizes widths so up+down fills 100%', () => {
    const { container } = render(
      <BreadthBar upCount={75} downCount={25} ariaLabel="75 / 25" />,
    );
    const up = container.querySelector('[data-direction="up"]') as HTMLElement;
    const down = container.querySelector('[data-direction="down"]') as HTMLElement;

    expect(parseFloat(up.style.width.replace('%', ''))).toBe(75);
    expect(parseFloat(down.style.width.replace('%', ''))).toBe(25);
  });
});

describe('MiniChangeBar', () => {
  it('renders a centered bar with a value segment', () => {
    const { container } = render(<MiniChangeBar value={1.2} maxAbs={2.4} />);

    expect(container.querySelectorAll('[data-testid="market-index-bar"]')).toHaveLength(1);
    expect(container.querySelector('[data-testid="market-index-bar"] [data-direction="up"]')).toBeInTheDocument();
  });

  it('marks a negative change with direction down and positive with up', () => {
    const { container } = render(<MiniChangeBar value={-1.4} maxAbs={2.4} />);
    expect(container.querySelector('[data-testid="market-index-bar"] [data-direction="down"]')).toBeInTheDocument();
    expect(container.querySelectorAll('[data-testid="market-index-bar"] [data-direction="up"]')).toHaveLength(0);
  });

  it('handles a null value without any direction segment', () => {
    const { container } = render(<MiniChangeBar value={null} maxAbs={1} />);
    expect(container.querySelectorAll('[data-testid="market-index-bar"]')).toHaveLength(1);
    expect(container.querySelectorAll('[data-testid="market-index-bar"] [data-direction]')).toHaveLength(0);
  });

  it('avoids division by zero when maxAbs is 0', () => {
    const { container } = render(<MiniChangeBar value={0} maxAbs={0} />);
    expect(container.querySelector('[data-testid="market-index-bar"] [data-direction="up"]')).toBeInTheDocument();
  });
});

describe('SectorBarList', () => {
  it('renders one row per sector with the given direction', () => {
    const rows: MarketBarRow[] = [
      { name: '林业', value: 5.75, label: '+5.75%' },
      { name: '渔业', value: 3.82, label: '+3.82%' },
    ];
    render(<SectorBarList rows={rows} direction="up" />);

    const bars = screen.getAllByTestId('market-sector-bar');
    expect(bars).toHaveLength(2);
    expect(screen.getByText('林业')).toBeInTheDocument();
    expect(screen.getByText('+5.75%')).toBeInTheDocument();
    expect(bars[0].querySelector('[data-direction="up"]')).toBeInTheDocument();
  });

  it('colors the whole group red for a lagging (down) ranking', () => {
    const rows: MarketBarRow[] = [{ name: '煤炭', value: -1.1, label: '-1.10%' }];
    const { container } = render(<SectorBarList rows={rows} direction="down" />);

    const bar = container.querySelector('[data-testid="market-sector-bar"] [data-direction]');
    expect(bar?.getAttribute('data-direction')).toBe('down');
  });
});
