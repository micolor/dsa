import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Drawer } from '../Drawer';

describe('Drawer', () => {
  it('portals the dialog to the document body so a transformed ancestor cannot shrink it', () => {
    const { container } = render(
      <div data-testid="header-like-ancestor" style={{ backdropFilter: 'blur(8px)' }}>
        <Drawer isOpen onClose={vi.fn()} title="详情">
          <p>正文</p>
        </Drawer>
      </div>,
    );

    const dialog = screen.getByRole('dialog');
    expect(container.contains(dialog)).toBe(false);
    expect(document.body.contains(dialog)).toBe(true);
    expect(screen.getByText('正文')).toBeInTheDocument();
  });

  it('renders nothing when closed', () => {
    render(
      <Drawer isOpen={false} onClose={vi.fn()} title="详情">
        <p>正文</p>
      </Drawer>,
    );

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});
