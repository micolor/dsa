import React, { useEffect, useId, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { SELECT_INPUT_CLASS } from '../../utils/formClasses';

interface SelectOption {
  value: string;
  label: string;
}

interface SelectProps {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: SelectOption[];
  label?: string;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  searchable?: boolean;
  searchPlaceholder?: string;
  emptyText?: string;
}

/**
 * Select component with a fully custom dropdown list (stylable, theme-aware).
 */
export const Select: React.FC<SelectProps> = ({
  id,
  value,
  onChange,
  options,
  label,
  placeholder,
  disabled = false,
  className = '',
}) => {
  const { t } = useUiLanguage();
  const selectId = useId();
  const resolvedId = id ?? selectId;
  const containerRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const resolvedPlaceholder = placeholder ?? t('common.selectPlaceholder');
  const selected = options.find((option) => option.value === value);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const [dropdownStyle, setDropdownStyle] = useState<{ top: number; left: number; width: string } | null>(null);

  // 点击外部 / Escape 关闭（下拉经 portal 渲染，需同时排除下拉自身）
  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      const insideContainer = containerRef.current?.contains(target);
      const insideDropdown = dropdownRef.current?.contains(target);
      if (!insideContainer && !insideDropdown) {
        setOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [open]);

  // 计算下拉位置（基于按钮的视口坐标），滚动/缩放时保持；portal 渲染避免被 overflow 容器裁剪。
  const updateDropdownPosition = () => {
    if (!buttonRef.current) {
      setDropdownStyle(null);
      return;
    }
    const rect = buttonRef.current.getBoundingClientRect();
    setDropdownStyle({
      top: rect.bottom + 4,
      left: rect.left,
      width: `${rect.width}px`,
    });
  };

  useEffect(() => {
    if (!open) return;
    updateDropdownPosition();
    const frameId = requestAnimationFrame(updateDropdownPosition);
    window.addEventListener('resize', updateDropdownPosition);
    window.addEventListener('scroll', updateDropdownPosition, true);
    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('resize', updateDropdownPosition);
      window.removeEventListener('scroll', updateDropdownPosition, true);
    };
  }, [open]);

  return (
    <div className={cn('flex flex-col', className)}>
      {label ? <label htmlFor={resolvedId} className="mb-2 text-sm font-medium text-foreground">{label}</label> : null}
      <div className="relative" ref={containerRef}>
        <button
          type="button"
          id={resolvedId}
          ref={buttonRef}
          onClick={() => setOpen((next) => !next)}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-label={label || resolvedPlaceholder}
          data-value={value}
          className={cn(
            SELECT_INPUT_CLASS,
            'w-full items-center justify-between gap-2 pr-3',
            'flex text-foreground',
            disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
          )}
        >
          <span className={cn('truncate', selected ? 'text-foreground' : 'text-muted-text')}>
            {selected ? selected.label : resolvedPlaceholder}
          </span>
          <svg
            className={cn('h-4 w-4 shrink-0 text-secondary-text transition-transform duration-200', open && 'rotate-180')}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </button>

        {open && dropdownStyle ? createPortal(
          <div
            ref={dropdownRef}
            role="listbox"
            className="fixed left-0 top-0 z-[200] overflow-auto rounded-none border border-border/60 bg-card/95 p-1 shadow-soft-card backdrop-blur-xl"
            style={{ ...dropdownStyle, maxHeight: '15rem' }}
          >
            {options.length === 0 ? (
              <div className="px-3 py-2.5 text-sm text-muted-text">—</div>
            ) : (
              options.map((option) => {
                const isSelected = option.value === value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    data-value={option.value}
                    onClick={() => {
                      onChange(option.value);
                      setOpen(false);
                    }}
                    className={cn(
                      'w-full rounded-none px-3 py-2 text-left text-sm transition-colors',
                      isSelected ? 'bg-primary/15 font-medium text-primary' : 'text-foreground hover:bg-hover',
                    )}
                  >
                    {option.label}
                  </button>
                );
              })
            )}
          </div>,
          document.body,
        ) : null}
      </div>
    </div>
  );
};
