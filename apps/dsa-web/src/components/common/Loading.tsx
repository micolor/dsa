import React from 'react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';

interface LoadingProps {
  label?: string;
  className?: string;
}

export const Loading: React.FC<LoadingProps> = ({ label, className = '' }) => {
  const { t } = useUiLanguage();

  return (
    <div className={`flex items-center justify-center p-8 ${className}`}>
      <div className="inline-flex items-center gap-2 rounded-full border border-border/60 bg-card px-4 py-2 text-sm text-secondary-text shadow-soft-card">
        <span className="spinner-ring inline-block h-4 w-4 animate-spin border-2" aria-hidden="true" />
        {label ?? t('common.loading')}
      </div>
    </div>
  );
};
