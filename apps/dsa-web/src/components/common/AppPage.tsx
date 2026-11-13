import type React from 'react';
import { cn } from '../../utils/cn';

interface AppPageProps {
  children: React.ReactNode;
  className?: string;
}

export const AppPage: React.FC<AppPageProps> = ({ children, className = '' }) => {
  return (
    <main className="h-full w-full">
      <div className={cn('h-full overflow-y-auto px-3 pb-4 pt-4 md:px-4', className)}>
        {children}
      </div>
    </main>
  );
};
