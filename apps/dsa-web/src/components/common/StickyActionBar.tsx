import type React from 'react';
import { cn } from '../../utils/cn';

interface StickyActionBarProps {
  children: React.ReactNode;
  className?: string;
}

export const StickyActionBar: React.FC<StickyActionBarProps> = ({ children, className = '' }) => {
  return (
    <div className={cn('sticky bottom-4 z-20 rounded-2xl glass-surface-strong p-3', className)}>
      <div className="flex flex-wrap items-center justify-end gap-2">{children}</div>
    </div>
  );
};
