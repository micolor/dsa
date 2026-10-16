import type React from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { BarChart3, X } from 'lucide-react';
import { Outlet, useLocation } from 'react-router-dom';
import { SidebarNav } from './SidebarNav';
import { GlobalTaskCenter } from '../tasks/GlobalTaskCenter';
import { useEscTopLayer } from '../../hooks/useEscTopLayer';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { ROUTE_TITLES } from './routeTitles';

const HOVER_CLOSE_DELAY = 150;

type ShellProps = {
  children?: React.ReactNode;
};

export const Shell: React.FC<ShellProps> = ({ children }) => {
  const [menuOpen, setMenuOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const { modalRef, isCovered } = useEscTopLayer();
  const scrollRafRef = useRef<number | undefined>(undefined);
  const { t } = useUiLanguage();
  const location = useLocation();
  const closeTimerRef = useRef<number | undefined>(undefined);
  const menuRootRef = useRef<HTMLDivElement | null>(null);
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null);
  const current = ROUTE_TITLES[location.pathname];

  const closeMenu = useCallback((restoreFocus = false) => {
    window.clearTimeout(closeTimerRef.current);
    setMenuOpen(false);
    if (restoreFocus) {
      menuTriggerRef.current?.focus();
    }
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

  // 悬停关闭只在有指针设备时可靠：触屏没有 mouseleave，箭头键进入菜单项后也无法靠
  // 悬停退出。因此菜单打开期间额外支持 Escape 与点击外部关闭。
  useEffect(() => {
    if (!menuOpen) {
      return undefined;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      // Esc 只归最上面那层，见 `useEscTopLayer`。这里踩得到的是菜单里的「退出」：点它会在菜单
      // 之上叠出退出确认框，两层都在 `document` 上收 Esc，菜单这份注册得更早、先跑，不让位就会
      // 一按 Esc 把确认框和菜单一起收掉。键盘用户还有一个入口：从菜单里 Tab 出去（菜单不会因此
      // 关闭）再激活页面上的控件，同样会叠出模态。
      if (event.key !== 'Escape' || isCovered()) {
        return;
      }
      event.preventDefault();
      closeMenu(true);
    };
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && menuRootRef.current?.contains(target)) {
        return;
      }
      closeMenu();
    };

    document.addEventListener('keydown', handleKeyDown);
    // 捕获阶段：菜单项自身不会阻止冒泡，但外部点击应先关菜单再执行原动作。
    document.addEventListener('pointerdown', handlePointerDown, true);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('pointerdown', handlePointerDown, true);
    };
  }, [closeMenu, menuOpen, isCovered]);

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
      {/* 通栏顶栏：sticky top-0 钉在顶部，内容从它下方滚过（形态与改前一致）。
          材质换成 index.css 的 .app-header，横向不再做 max-width 收窄。
          scrolled 同时驱动三件事：材质压实的 is-scrolled、投影加码、下边框亮起 —— 让顶栏在
          滚动时读起来是「锁在窗口顶部的一层」，而不是页面顶部的一条静止色带。

          sticky 挂在这层 wrapper 上、而不是挂在 <header> 上，是为了让下面的导航菜单面板成为
          <header> 的**兄弟节点**：.app-header 自带 backdrop-filter，而带 backdrop-filter 的元素
          会形成一个 backdrop root —— 它的后代再写 backdrop-filter 时只能采到该 root 内部的像素，
          采不到页面正文，菜单于是退化成纯 14% 透明（背后的大字原样透出来，看起来就是「有点透」）。
          实测：同一个面板节点克隆后挂到 <body> 下会被 36px 模糊糊掉、留在 header 里则背后文字
          依旧清晰；这层 wrapper 自身没有 filter / opacity / transform，所以菜单在它下面能拿到
          真正的页面内容做模糊，玻璃材质与观感不变，只是模糊终于生效。 */}
      <div className="sticky top-0 z-40">
        <header
          className={
            'app-header transition-[border-color,box-shadow] duration-200 ' +
            (scrolled
              ? 'is-scrolled border-b border-border/40 border-x-transparent border-t-transparent'
              : 'border-b border-transparent')
          }
        >
          <div className="mx-auto flex h-9 w-full max-w-[1680px] items-center pl-12 pr-3 sm:pr-4 lg:pr-5">
            {/* 不带 mt-0.5：那 2px 上边距会在 items-center 下把标题整行压低 1px，
                实测标题墨迹中心 y=19.0、左侧图标墨迹中心 y=17.75，两者错开 1.25px，
                看起来就是「图标在导航栏里没居中」。去掉后两者都回到行中心 y=18。 */}
            <div className="flex min-w-0 flex-1 items-baseline gap-2 overflow-hidden">
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

        {/* 汉堡按钮 + 菜单面板。高度取 h-9（36px，与上面的内容行同高）而不是 top-0 bottom-0：
            在 <header> 里时它的包含块是通栏的 padding box（37px 减去 1px 下边框 = 36px），
            换成 wrapper 后 bottom-0 会变成 37px，图标因此下移 0.5px、破坏下面 translate-y 的
            半像素对齐。写死 36px 与改前的几何完全一致。 */}
        <div
          ref={menuRootRef}
          className="absolute left-0 top-0 z-10 flex h-9 items-center"
          onMouseEnter={openMenu}
          onMouseLeave={scheduleClose}
        >
          <button
            ref={menuTriggerRef}
            type="button"
            onClick={() => setMenuOpen((o) => !o)}
            /* translate-y-[0.5px] = 下移半个像素。通栏的可见高度是 37px（36px 内容 + 1px 下边框，
               边框区照样绘制通栏自己的材质），比内容行高 1px，所以图标按内容行居中（方框 4–32）
               落在通栏里是偏上的；28px 的方块在 37px 里居中必然是半个像素（4.5 / 4.5），
               下移 0.5px 后正好压在通栏的几何中心，和标题行也只差 0.5px。 */
            className="ml-2 inline-flex h-7 w-7 translate-y-[0.5px] items-center justify-center rounded-lg border border-border/60 bg-card/60 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
            aria-label={menuOpen ? t('layout.closeNav') : t('layout.openNav')}
            aria-expanded={menuOpen}
          >
            {menuOpen ? <X className="h-3.5 w-3.5" /> : <BarChart3 className="h-3.5 w-3.5 text-primary" />}
          </button>

          {menuOpen ? (
            <div
              ref={modalRef}
              // 参与 `useEscTopLayer` 的叠层排序：菜单是非模态弹出层，但 Esc 归最上面那层，
              // 压着它的模态（例如从菜单里点「退出」弹出的确认框）在下面时它要让位。
              data-esc-layer="true"
              className="absolute left-2 top-full mt-1.5 w-60 max-h-[calc(100vh-6rem)] overflow-y-auto rounded-2xl glass-surface-strong p-2.5 shadow-soft-card"
            >
              <SidebarNav onNavigate={() => closeMenu()} />
            </div>
          ) : null}
        </div>
      </div>

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
