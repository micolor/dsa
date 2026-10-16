import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { ThemeProvider } from '../../theme/ThemeProvider';
import { Shell } from '../Shell';

const mockLogout = vi.fn().mockResolvedValue(undefined);

vi.mock('../../../contexts/AuthContext', () => ({
  useAuth: () => ({
    authEnabled: true,
    logout: mockLogout,
  }),
}));

vi.mock('../../../stores/agentChatStore', () => ({
  useAgentChatStore: (selector: (state: { completionBadge: boolean }) => unknown) =>
    selector({ completionBadge: true }),
}));

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

describe('Shell', () => {
  it('shows a confirmation dialog before logout', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: '打开导航菜单' }));
    fireEvent.click(screen.getByRole('button', { name: '退出' }));

    expect(await screen.findByRole('heading', { name: '退出登录' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认退出' }));
    expect(mockLogout).toHaveBeenCalled();
  });

  /**
   * 菜单单开时 Esc 仍要能关（回归）：让位判断只在「屏上还有一层压着」时才该拦下，把菜单自己
   * 也算进去会让它永远关不掉。
   */
  it('closes the nav menu on Escape when nothing is stacked above it', () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: '打开导航菜单' }));
    expect(screen.getByRole('button', { name: '关闭导航菜单' })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.getByRole('button', { name: '打开导航菜单' })).toBeInTheDocument();
  });

  /**
   * 菜单里点「退出」会在菜单之上叠出确认框，两层都在 `document` 上收 Esc、菜单那份注册得更早
   * 会先跑，不让位就会一次把确认框和菜单一起收掉。这条同时盯住「上层（确认框）仍能收」——
   * 只断言菜单还在，不足以证明是确认框收走了这次 Esc。
   */
  it('keeps the nav menu open when Escape closes the logout dialog above it', async () => {
    render(
      <MemoryRouter initialEntries={['/chat']}>
        <ThemeProvider>
          <Shell>
            <div>page content</div>
          </Shell>
        </ThemeProvider>
      </MemoryRouter>
    );

    fireEvent.click(screen.getByRole('button', { name: '打开导航菜单' }));
    fireEvent.click(screen.getByRole('button', { name: '退出' }));
    expect(await screen.findByRole('heading', { name: '退出登录' })).toBeInTheDocument();

    fireEvent.keyDown(document, { key: 'Escape' });

    expect(screen.queryByRole('heading', { name: '退出登录' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '关闭导航菜单' })).toBeInTheDocument();
  });
});
