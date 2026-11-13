import type React from 'react';
import { useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { AppPage, Button } from '../components/common';

const NotFoundPage: React.FC = () => {
  const navigate = useNavigate();

  // Set page title
  useEffect(() => {
    document.title = '页面未找到 - DSA';
  }, []);

  return (
    <AppPage className="flex min-h-full flex-col items-center justify-center text-center">
      {/* 404 */}
      <div className="relative mb-8">
        <span 
          className="text-8xl font-bold text-transparent bg-clip-text"
          style={{
            backgroundImage: 'linear-gradient(135deg, #00d4ff 0%, #a855f7 100%)',
          }}
        >
          404
        </span>
      </div>

      <h1 className="text-2xl font-bold text-foreground mb-2">页面未找到</h1>
      <p className="text-muted-text mb-8">抱歉，您访问的页面不存在或已被移动</p>

      <Button variant="primary" size="md" onClick={() => navigate('/')}>
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6" />
        </svg>
        返回首页
      </Button>
    </AppPage>
  );
};

export default NotFoundPage;
