import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CalendarDays, ChevronLeft, ChevronRight } from 'lucide-react';
import { useUiLanguage } from '../../contexts/UiLanguageContext';
import { cn } from '../../utils/cn';
import { SELECT_INPUT_CLASS } from '../../utils/formClasses';

const WEEKDAY_LABELS: Record<string, string[]> = {
  zh: ['一', '二', '三', '四', '五', '六', '日'],
  en: ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'],
};

const pad = (n: number): string => String(n).padStart(2, '0');

const toDateString = (year: number, month: number, day: number): string =>
  `${year}-${pad(month + 1)}-${pad(day)}`;

const parseDateString = (value: string): { year: number; month: number; day: number } | null => {
  const parts = value.split('-').map(Number);
  if (parts.length !== 3 || parts.some((n) => Number.isNaN(n))) return null;
  return { year: parts[0], month: parts[1] - 1, day: parts[2] };
};

interface DatePickerProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  disabled?: boolean;
  className?: string;
  min?: string;
  max?: string;
}

/**
 * 主题化日期选择组件：隐藏原生 input，用 portal 日历弹层统一风格（与自定义 Select 同一套定位/关闭逻辑）。
 */
export const DatePicker: React.FC<DatePickerProps> = ({
  value,
  onChange,
  placeholder,
  disabled = false,
  className = '',
  min,
  max,
}) => {
  const { language, t } = useUiLanguage();
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [popupStyle, setPopupStyle] = useState<{ top: number; left: number; width: string } | null>(null);

  const parsed = useMemo(() => parseDateString(value), [value]);
  const [viewYear, setViewYear] = useState(() => parsed?.year ?? new Date().getFullYear());
  const [viewMonth, setViewMonth] = useState(() => parsed?.month ?? new Date().getMonth());

  useEffect(() => {
    if (parsed) {
      setViewYear(parsed.year);
      setViewMonth(parsed.month);
    }
  }, [parsed]);

  const todayText = useMemo(() => {
    const now = new Date();
    return toDateString(now.getFullYear(), now.getMonth(), now.getDate());
  }, []);

  // 点击外部 / Escape 关闭（portal 渲染，需同时排除弹层自身）
  useEffect(() => {
    if (!open) return;
    const handlePointerDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (!containerRef.current?.contains(target) && !popupRef.current?.contains(target)) {
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

  // 基于触发按钮视口坐标定位，滚动/缩放时保持
  const updatePopupPosition = useCallback(() => {
    if (!triggerRef.current) {
      setPopupStyle(null);
      return;
    }
    const rect = triggerRef.current.getBoundingClientRect();
    setPopupStyle({
      top: rect.bottom + 4,
      left: rect.left,
      width: '19rem',
    });
  }, []);

  useEffect(() => {
    if (!open) return;
    updatePopupPosition();
    const frameId = requestAnimationFrame(updatePopupPosition);
    window.addEventListener('resize', updatePopupPosition);
    window.addEventListener('scroll', updatePopupPosition, true);
    return () => {
      cancelAnimationFrame(frameId);
      window.removeEventListener('resize', updatePopupPosition);
      window.removeEventListener('scroll', updatePopupPosition, true);
    };
  }, [open, updatePopupPosition]);

  const moveMonth = useCallback((delta: number) => {
    setViewMonth((current) => {
      const next = current + delta;
      if (next < 0) {
        setViewYear((year) => year - 1);
        return 11;
      }
      if (next > 11) {
        setViewYear((year) => year + 1);
        return 0;
      }
      return next;
    });
  }, []);

  const daysInMonth = new Date(viewYear, viewMonth + 1, 0).getDate();
  const firstWeekday = (new Date(viewYear, viewMonth, 1).getDay() + 6) % 7; // 周一开头
  const cells: Array<{ date: string; day: number } | null> = [];
  for (let i = 0; i < firstWeekday; i += 1) cells.push(null);
  for (let day = 1; day <= daysInMonth; day += 1) {
    cells.push({ date: toDateString(viewYear, viewMonth, day), day });
  }

  const isDisabled = (date: string): boolean => {
    if (min && date < min) return true;
    if (max && date > max) return true;
    return false;
  };

  const weekdayLabels = WEEKDAY_LABELS[language] ?? WEEKDAY_LABELS.zh;
  const monthLabel = `${viewYear}年${viewMonth + 1}月`;

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        ref={triggerRef}
        onClick={() => !disabled && setOpen((current) => !current)}
        disabled={disabled}
        aria-haspopup="dialog"
        aria-expanded={open}
        className={cn(
          SELECT_INPUT_CLASS,
          'flex w-full items-center justify-between gap-2 pr-3 text-left',
          className,
          disabled ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
        )}
      >
        <span className={cn('truncate tabular-nums', value ? 'text-foreground' : 'text-muted-text')}>
          {value || placeholder || t('common.selectDate')}
        </span>
        <CalendarDays className="h-4 w-4 shrink-0 text-secondary-text" aria-hidden="true" />
      </button>

      {open && popupStyle ? createPortal(
        <div
          ref={popupRef}
          role="dialog"
          aria-label={t('common.selectDate')}
          className="fixed z-[200] rounded-xl border border-border/60 bg-card/95 p-3 shadow-soft-card backdrop-blur-xl"
          style={{ ...popupStyle, maxHeight: '24rem', overflowY: 'auto' }}
        >
          <div className="flex items-center justify-between">
            <button
              type="button"
              onClick={() => moveMonth(-1)}
              className="rounded-lg p-1 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
              aria-label={t('common.previousMonth')}
            >
              <ChevronLeft className="h-4 w-4" aria-hidden="true" />
            </button>
            <span className="text-sm font-medium text-foreground">{monthLabel}</span>
            <button
              type="button"
              onClick={() => moveMonth(1)}
              className="rounded-lg p-1 text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
              aria-label={t('common.nextMonth')}
            >
              <ChevronRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>

          <div className="mt-2 grid grid-cols-7 gap-1">
            {weekdayLabels.map((label) => (
              <span key={label} className="text-center text-[11px] font-medium text-muted-text">
                {label}
              </span>
            ))}
            {cells.map((cell, index) => {
              if (!cell) {
                return <span key={`blank-${index}`} />;
              }
              const selected = cell.date === value;
              const isToday = cell.date === todayText;
              const disabledDate = isDisabled(cell.date);
              return (
                <button
                  key={cell.date}
                  type="button"
                  disabled={disabledDate}
                  onClick={() => {
                    onChange(cell.date);
                    setOpen(false);
                  }}
                  className={cn(
                    'h-8 w-8 rounded-lg text-sm transition-colors',
                    disabledDate
                      ? 'cursor-not-allowed text-muted-text/40'
                      : selected
                        ? 'bg-primary/20 font-medium text-primary'
                        : 'text-foreground hover:bg-hover',
                    !disabledDate && isToday && !selected ? 'ring-1 ring-inset ring-primary/50' : '',
                  )}
                >
                  {cell.day}
                </button>
              );
            })}
          </div>
        </div>,
        document.body,
      ) : null}
    </div>
  );
};

export default DatePicker;
