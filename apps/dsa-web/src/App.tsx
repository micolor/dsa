import type React from 'react';
import { lazy, useEffect } from 'react';
import { BrowserRouter as Router, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { ApiErrorAlert, Button, Shell } from './components/common';
import {
  PageLoadingFallback,
  RouteOutletBoundary,
  StandaloneRouteBoundary,
} from './components/layout/RouteBoundary';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ToastHostProvider } from './contexts/ToastHostContext';
import { UiLanguageProvider, useUiLanguage } from './contexts/UiLanguageContext';
import { useAgentChatStore } from './stores/agentChatStore';
import './App.css';

const HomePage = lazy(() => import('./pages/HomePage'));
const PaperTradingPage = lazy(() => import('./pages/PaperTradingPage'));
const BacktestPage = lazy(() => import('./pages/BacktestPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const LoginPage = lazy(() => import('./pages/LoginPage'));
const NotFoundPage = lazy(() => import('./pages/NotFoundPage'));
const ChatPage = lazy(() => import('./pages/ChatPage'));
const PortfolioPage = lazy(() => import('./pages/PortfolioPage'));
const DecisionSignalsPage = lazy(() => import('./pages/DecisionSignalsPage'));
const AlertsPage = lazy(() => import('./pages/AlertsPage'));
const TokenUsagePage = lazy(() => import('./pages/TokenUsagePage'));
const ResearchLayout = lazy(() => import('./components/layout/ResearchLayout'));
const ScheduledTasksPage = lazy(() => import('./pages/ScheduledTasksPage'));
const StockScreeningPage = lazy(() => import('./pages/StockScreeningPage'));

type LegacyResearchRedirectProps = {
  to: string;
  backtestTo?: string;
};

/** Keep old bookmarks working while the research workspace owns the tabs. */
const LegacyResearchRedirect: React.FC<LegacyResearchRedirectProps> = ({ to, backtestTo }) => {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  if (backtestTo && params.get('tab') === 'backtest') {
    params.delete('tab');
    const search = params.toString();
    return <Navigate to={`${backtestTo}${search ? `?${search}` : ''}${location.hash}`} replace />;
  }
  return <Navigate to={`${to}${location.search}${location.hash}`} replace />;
};

const AppContent: React.FC = () => {
  const location = useLocation();
  const { authEnabled, loggedIn, isLoading, loadError, refreshStatus } = useAuth();
  const { t } = useUiLanguage();

  useEffect(() => {
    useAgentChatStore.getState().setCurrentRoute(location.pathname);
  }, [location.pathname]);

  // 路由切换时回到顶部，避免上一页滚动位置残留造成跳变
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);

  if (isLoading) {
    return <PageLoadingFallback />;
  }

  if (loadError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-base px-4">
        <div className="w-full max-w-lg">
          <ApiErrorAlert error={loadError} />
        </div>
        <Button type="button" variant="primary" onClick={() => void refreshStatus()}>
          {t('common.retry')}
        </Button>
      </div>
    );
  }

  if (authEnabled && !loggedIn) {
    if (location.pathname === '/login') {
      return (
        <StandaloneRouteBoundary>
          <LoginPage />
        </StandaloneRouteBoundary>
      );
    }
    const redirect = encodeURIComponent(location.pathname + location.search);
    return <Navigate to={`/login?redirect=${redirect}`} replace />;
  }

  if (location.pathname === '/login') {
    return <Navigate to="/" replace />;
  }

  return (
    <Routes>
      <Route
        element={(
          <Shell>
            <RouteOutletBoundary />
          </Shell>
        )}
      >
        <Route path="/" element={<HomePage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/portfolio" element={<PortfolioPage />} />
        <Route path="/research" element={<ResearchLayout />}>
          <Route index element={<Navigate to="/research/screening" replace />} />
          <Route path="screening" element={<StockScreeningPage />} />
          <Route path="signals" element={<DecisionSignalsPage embedded />} />
          <Route path="backtest" element={<BacktestPage />} />
        </Route>
        <Route
          path="/decision-signals"
          element={<LegacyResearchRedirect to="/research/signals" backtestTo="/research/backtest" />}
        />
        <Route
          path="/screening"
          element={<LegacyResearchRedirect to="/research/screening" />}
        />
        <Route
          path="/backtest"
          element={<LegacyResearchRedirect to="/research/backtest" />}
        />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/scheduled-tasks" element={<ScheduledTasksPage />} />
        <Route path="/paper" element={<PaperTradingPage />} />
        <Route path="/usage" element={<TokenUsagePage />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
};

const App: React.FC = () => {
  return (
    <UiLanguageProvider>
      {/* 提示容器挂在 Router 之外，登录页与加载/错误早返回分支也才共用同一个右上角区域。 */}
      <ToastHostProvider>
        <Router>
          <AuthProvider>
            <AppContent />
          </AuthProvider>
        </Router>
      </ToastHostProvider>
    </UiLanguageProvider>
  );
};

export default App;
