import type React from 'react';
import { cn } from '../../utils/cn';
import { DashboardPanelHeader } from '../dashboard';

interface SettingsSectionCardProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export const SettingsSectionCard: React.FC<SettingsSectionCardProps> = ({
  title,
  description,
  actions,
  children,
  className = '',
}) => {
  return (
    <section className={cn('glass-card !border-transparent p-4 md:p-5', className)}>
      <DashboardPanelHeader
        className="mb-3"
        title={title}
        titleClassName="text-base font-semibold"
        actions={actions}
      />
      {description ? (
        <p className="-mt-1 mb-4 text-sm leading-6 text-muted-text">{description}</p>
      ) : null}
      <div className="space-y-4">{children}</div>
    </section>
  );
};
