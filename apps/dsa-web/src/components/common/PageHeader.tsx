import type React from 'react';
import { cn } from '../../utils/cn';

interface PageHeaderProps {
  eyebrow?: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  eyebrow,
  title,
  description,
  actions,
  className = '',
}) => {
  return (
    <header className={cn('space-y-1.5', className)}>
      {eyebrow ? <span className="label-uppercase">{eyebrow}</span> : null}
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0 space-y-1">
          <h1 className="text-xl font-semibold tracking-tight text-foreground md:text-2xl">{title}</h1>
          {description ? <p className="max-w-2xl text-xs text-secondary-text md:text-sm">{description}</p> : null}
        </div>
        {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
};
