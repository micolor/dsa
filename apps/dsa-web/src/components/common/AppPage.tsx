import type React from 'react';
import { cn } from '../../utils/cn';

interface AppPageProps {
  children: React.ReactNode;
  className?: string;
}

export const AppPage: React.FC<AppPageProps> = ({ children, className = '' }) => {
  return (
    <main className="h-full w-full">
      <div className={cn('h-full overflow-y-auto px-4 pb-6 pt-4 md:px-6', className)}>
        {children}
      </div>
    </main>
  );
};
