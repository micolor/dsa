import type React from 'react';
import { cn } from '../../utils/cn';

interface AppPageProps {
  children: React.ReactNode;
  className?: string;
}

export const AppPage: React.FC<AppPageProps> = ({ children, className = '' }) => {
  return (
    <main className="h-[calc(100vh-5rem)] w-full sm:h-[calc(100vh-5.5rem)] lg:h-[calc(100vh-2rem)]">
      <div className={cn('h-full overflow-y-auto px-4 pb-6 pt-4 md:px-6', className)}>
        {children}
      </div>
    </main>
  );
};
