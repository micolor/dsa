import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { CalendarDays, ChevronLeft, ChevronRight, Pencil, Play, Plus, Trash2 } from 'lucide-react';
import { getParsedApiError, type ParsedApiError } from '../api/error';
import { scheduledTasksApi } from '../api/scheduledTasks';
import {
  ApiErrorAlert,
  AppPage,
  Button,
  ConfirmDialog,
  Dialog,
  Loading,
  Select,
} from '../components/common';
import { useUiLanguage } from '../contexts/UiLanguageContext';
import { SCHEDULED_TASKS_TEXT } from '../locales/featureText';
import type {
  ScheduledTask,
  ScheduledTaskCalendar,
  ScheduledTaskInput,
  ScheduledTaskOccurrence,
  ScheduledTaskType,
} from '../types/scheduledTasks';
import { cn } from '../utils/cn';

const TYPE_OPTIONS: ScheduledTaskType[] = [
  'stock_analysis',
  'market_review',
  'screening',
  'data_refresh',
  'paper_auto_trade',
  'backtest',
  'agent_prompt',
];

const STATUS_TONE: Record<string, string> = {
  scheduled: 'text-secondary-text',
  submitted: 'text-primary',
  success: 'text-success',
  failed: 'text-danger',
  skipped: 'text-muted-text',
  missed: 'text-warning',
  running: 'text-cyan',
};

type FormState = {
  name: string;
  taskType: ScheduledTaskType;
  timeOfDay: string;
  weekdays: number[];
  tradingDayOnly: boolean;
  enabled: boolean;
  stocksText: string;
  analysisMode: 'standard' | 'quick';
  quickNotify: boolean;
  reportType: 'brief' | 'detailed';
  forceRefresh: boolean;
  region: 'cn' | 'hk' | 'us';
  notify: boolean;
  strategy: string;
  market: 'cn' | 'hk' | 'us';
  maxResults: string;
  lookbackDays: string;
  code: string;
  limit: string;
  prompt: string;
};

const DEFAULT_FORM: FormState = {
  name: '',
  taskType: 'stock_analysis',
  timeOfDay: '08:30',
  weekdays: [0, 1, 2, 3, 4],
  tradingDayOnly: true,
  enabled: true,
  stocksText: '',
  analysisMode: 'standard',
  quickNotify: false,
  reportType: 'detailed',
  forceRefresh: false,
  region: 'cn',
  notify: false,
  strategy: 'capital_heat',
  market: 'cn',
  maxResults: '20',
  lookbackDays: '7',
  code: '',
  limit: '200',
  prompt: '',
};

function toIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, '0');
  const day = String(value.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function startOfGrid(month: Date): Date {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const mondayOffset = (first.getDay() + 6) % 7;
  return new Date(first.getFullYear(), first.getMonth(), first.getDate() - mondayOffset);
}

function addDays(value: Date, days: number): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate() + days);
}

function buildForm(task?: ScheduledTask): FormState {
  if (!task) return { ...DEFAULT_FORM };
  const payload = task.payload || {};
  const asText = (value: unknown) => (Array.isArray(value) ? value.join(', ') : String(value ?? ''));
  return {
    name: task.name,
    taskType: task.taskType,
    timeOfDay: task.timeOfDay,
    weekdays: task.weekdays.length ? task.weekdays : [0, 1, 2, 3, 4],
    tradingDayOnly: task.tradingDayOnly,
    enabled: task.enabled,
    stocksText: asText(payload.stocks),
    analysisMode: payload.mode === 'quick' ? 'quick' : 'standard',
    quickNotify: Boolean(payload.notify),
    reportType: (payload.report_type === 'brief' ? 'brief' : 'detailed'),
    forceRefresh: Boolean(payload.force_refresh),
    region: (['cn', 'hk', 'us'].includes(String(payload.region)) ? payload.region : 'cn') as FormState['region'],
    notify: Boolean(payload.notify),
    strategy: String(payload.strategy || 'capital_heat'),
    market: (['cn', 'hk', 'us'].includes(String(payload.market)) ? payload.market : 'cn') as FormState['market'],
    maxResults: String(payload.max_results ?? 20),
    lookbackDays: String(payload.lookback_days ?? 7),
    code: String(payload.code ?? ''),
    limit: String(payload.limit ?? 200),
    prompt: String(payload.prompt ?? ''),
  };
}

function buildPayload(form: FormState): Record<string, unknown> {
  if (form.taskType === 'stock_analysis') {
    return {
      stocks: form.stocksText.split(/[\s,，;；]+/).map((code) => code.trim()).filter(Boolean),
      report_type: form.reportType,
      force_refresh: form.forceRefresh,
      mode: form.analysisMode,
      notify: form.analysisMode === 'quick' ? form.quickNotify : false,
    };
  }
  if (form.taskType === 'market_review') {
    return { region: form.region, notify: form.notify };
  }
  if (form.taskType === 'screening' || form.taskType === 'data_refresh') {
    return {
      strategy: form.strategy.trim() || 'capital_heat',
      market: form.market,
      max_results: Number(form.maxResults) || 20,
    };
  }
  if (form.taskType === 'paper_auto_trade') {
    return { lookback_days: Number(form.lookbackDays) || 7 };
  }
  if (form.taskType === 'backtest') {
    return {
      code: form.code.trim().toUpperCase() || null,
      force: form.forceRefresh,
      limit: Number(form.limit) || 200,
    };
  }
  return { prompt: form.prompt.trim(), notify: form.notify };
}

const ScheduledTasksPage: React.FC = () => {
  const { language } = useUiLanguage();
  const text = SCHEDULED_TASKS_TEXT[language];
  const [month, setMonth] = useState(() => new Date());
  const [calendar, setCalendar] = useState<ScheduledTaskCalendar | null>(null);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ParsedApiError | null>(null);
  const [selectedDate, setSelectedDate] = useState(() => toIsoDate(new Date()));
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<ScheduledTask | null>(null);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<ScheduledTask | null>(null);
  const [busyTask, setBusyTask] = useState<number | null>(null);
  const [notice, setNotice] = useState('');
  const [dayOpen, setDayOpen] = useState(false);
  const requestIdRef = useRef(0);

  useEffect(() => {
    document.title = text.documentTitle;
  }, [text.documentTitle]);

  const cells = useMemo(() => {
    const start = startOfGrid(month);
    return Array.from({ length: 42 }, (_, index) => addDays(start, index));
  }, [month]);

  const load = useCallback(async () => {
    const start = toIsoDate(cells[0]);
    const end = toIsoDate(cells[cells.length - 1]);
    const requestId = requestIdRef.current + 1;
    requestIdRef.current = requestId;
    const isLatest = () => requestIdRef.current === requestId;
    setLoading(true);
    try {
      const [taskItems, calendarData] = await Promise.all([
        scheduledTasksApi.list(),
        scheduledTasksApi.calendar(start, end),
      ]);
      if (!isLatest()) return;
      setTasks(taskItems);
      setCalendar(calendarData);
      setError(null);
    } catch (loadError) {
      if (isLatest()) setError(getParsedApiError(loadError));
    } finally {
      if (isLatest()) setLoading(false);
    }
  }, [cells]);

  useEffect(() => {
    void load();
  }, [load]);

  const occurrencesByDate = useMemo(() => {
    const grouped: Record<string, ScheduledTaskOccurrence[]> = {};
    for (const occurrence of calendar?.occurrences ?? []) {
      const day = occurrence.scheduledFor.slice(0, 10);
      (grouped[day] ||= []).push(occurrence);
    }
    return grouped;
  }, [calendar]);

  const dayOccurrences = occurrencesByDate[selectedDate] ?? [];
  const todayIso = toIsoDate(new Date());
  const monthLabel = new Intl.DateTimeFormat(language === 'en' ? 'en-US' : 'zh-CN', {
    year: 'numeric',
    month: 'long',
  }).format(month);

  const typeLabel = (type: ScheduledTaskType): string => {
    if (type === 'stock_analysis') return text.typeStockAnalysis;
    if (type === 'market_review') return text.typeMarketReview;
    if (type === 'screening') return text.typeScreening;
    if (type === 'data_refresh') return text.typeDataRefresh;
    if (type === 'paper_auto_trade') return text.typePaperAutoTrade;
    if (type === 'backtest') return text.typeBacktest;
    return text.typeAgentPrompt;
  };

  const statusLabel = (status: string): string => {
    const map: Record<string, string> = {
      scheduled: text.statusScheduled,
      submitted: text.statusSubmitted,
      success: text.statusSuccess,
      failed: text.statusFailed,
      skipped: text.statusSkipped,
      missed: text.statusMissed,
      running: text.statusRunning,
    };
    return map[status] ?? status;
  };

  const openCreate = () => {
    setEditing(null);
    setForm({ ...DEFAULT_FORM });
    setNotice('');
    setDialogOpen(true);
  };

  const openEdit = (task: ScheduledTask) => {
    setEditing(task);
    setForm(buildForm(task));
    setNotice('');
    setDialogOpen(true);
  };

  const save = async () => {
    const input: ScheduledTaskInput = {
      name: form.name.trim(),
      taskType: form.taskType,
      payload: buildPayload(form),
      weekdays: form.weekdays,
      timeOfDay: form.timeOfDay,
      enabled: form.enabled,
      tradingDayOnly: form.tradingDayOnly,
    };
    if (!input.name) {
      setNotice(language === 'en' ? 'Task name is required' : '任务名称不能为空');
      return;
    }
    if (input.taskType === 'agent_prompt' && String(input.payload.prompt || '').trim().length < 5) {
      setNotice(language === 'en' ? 'Prompt must be at least 5 characters' : '助手指令至少 5 个字符');
      return;
    }
    setSaving(true);
    try {
      if (editing) await scheduledTasksApi.update(editing.id, input);
      else await scheduledTasksApi.create(input);
      setDialogOpen(false);
      await load();
    } catch (saveError) {
      setError(getParsedApiError(saveError));
    } finally {
      setSaving(false);
    }
  };

  const toggleTask = async (task: ScheduledTask) => {
    setBusyTask(task.id);
    try {
      await scheduledTasksApi.setEnabled(task.id, !task.enabled);
      await load();
    } catch (toggleError) {
      setError(getParsedApiError(toggleError));
    } finally {
      setBusyTask(null);
    }
  };

  const runTask = async (task: ScheduledTask) => {
    setBusyTask(task.id);
    try {
      await scheduledTasksApi.runNow(task.id);
      setNotice(text.runSubmitted);
      window.setTimeout(() => void load(), 1200);
    } catch (runError) {
      setError(getParsedApiError(runError));
    } finally {
      setBusyTask(null);
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setBusyTask(deleting.id);
    try {
      await scheduledTasksApi.remove(deleting.id);
      setDeleting(null);
      await load();
    } catch (deleteError) {
      setError(getParsedApiError(deleteError));
    } finally {
      setBusyTask(null);
    }
  };

  const updateForm = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const selectedWeekday = (new Date(`${selectedDate}T00:00:00`).getDay() + 6) % 7;
  const disabledDayTasks = tasks.filter((task) => (
    !task.enabled
    && task.weekdays.includes(selectedWeekday)
    && !dayOccurrences.some((item) => item.task.id === task.id)
  ));

  return (
    <AppPage className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-sm text-secondary-text">{text.description}</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" variant="primary" onClick={openCreate}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            {text.newTask}
          </Button>
        </div>
      </div>

      {notice ? (
        <p className="rounded-xl border border-success/30 bg-success/10 px-3 py-2 text-xs text-success">{notice}</p>
      ) : null}
      {error ? <ApiErrorAlert error={error} onDismiss={() => setError(null)} /> : null}

      {!loading && tasks.length === 0 ? (
        <div className="glass-card !border-transparent flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between md:p-5">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground">{text.noTasks}</p>
            <p className="mt-1 text-xs leading-6 text-secondary-text">{text.noTasksHint}</p>
          </div>
          <Button type="button" variant="primary" size="sm" onClick={openCreate}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            {text.newTask}
          </Button>
        </div>
      ) : null}

      <section className="glass-card !border-transparent p-4 md:p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <CalendarDays className="h-4 w-4 text-primary" aria-hidden="true" />
              <h2 className="text-base font-semibold text-foreground">{text.calendar}</h2>
              <span className="text-sm text-secondary-text">{monthLabel}</span>
            </div>
            <div className="flex items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={text.prevMonth}
                onClick={() => {
                  const next = new Date(month.getFullYear(), month.getMonth() - 1, 1);
                  setMonth(next);
                  setSelectedDate(toIsoDate(next));
                }}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  const now = new Date();
                  setMonth(now);
                  setSelectedDate(toIsoDate(now));
                }}
              >
                {text.today}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={text.nextMonth}
                onClick={() => {
                  const next = new Date(month.getFullYear(), month.getMonth() + 1, 1);
                  setMonth(next);
                  setSelectedDate(toIsoDate(next));
                }}
              >
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </Button>
            </div>
          </div>

          <div className="grid grid-cols-7 gap-1 text-center text-[11px] font-medium text-muted-text">
            {text.weekdays.map((day) => <span key={day} className="py-1">{day}</span>)}
          </div>
          <div className="mt-1 grid grid-cols-7 gap-1">
            {cells.map((cell) => {
              const iso = toIsoDate(cell);
              const inMonth = cell.getMonth() === month.getMonth();
              const selected = iso === selectedDate;
              const dayItems = occurrencesByDate[iso] ?? [];
              return (
                <button
                  key={iso}
                  type="button"
                  onClick={() => {
                    setSelectedDate(iso);
                    setDayOpen(true);
                  }}
                  className={cn(
                    'min-h-[5.5rem] rounded-lg border p-1.5 text-left align-top transition-colors',
                    selected
                      ? 'border-primary/40 bg-primary/10'
                      : 'border-border/40 bg-card/30 hover:bg-hover',
                    !inMonth && 'opacity-45',
                  )}
                >
                  <span className={cn(
                    'inline-flex h-5 min-w-5 items-center justify-center rounded-full px-1 text-[11px]',
                    iso === todayIso ? 'bg-primary/20 font-semibold text-primary' : 'text-secondary-text',
                  )}>
                    {cell.getDate()}
                  </span>
                  <span className="mt-1 block space-y-0.5">
                    {dayItems.slice(0, 2).map((item) => (
                      <span
                        key={`${item.task.id}-${item.scheduledFor}`}
                        className={cn('block truncate rounded bg-surface/60 px-1 text-[10px] leading-4', STATUS_TONE[item.status])}
                      >
                        {item.scheduledFor.slice(11)} {item.task.name}
                      </span>
                    ))}
                    {dayItems.length > 2 ? (
                      <span className="block text-[10px] text-muted-text">+{dayItems.length - 2}</span>
                    ) : null}
                  </span>
                </button>
              );
            })}
          </div>
      </section>

      <Dialog
        isOpen={dayOpen}
        onClose={() => setDayOpen(false)}
        title={selectedDate}
        ariaLabel={`${text.calendar} ${selectedDate}`}
        eyebrow={text.calendar}
        widthClassName="sm:max-w-2xl"
      >
        <div className="space-y-2">
          <p className="text-xs text-muted-text">
            {new Intl.DateTimeFormat(language === 'en' ? 'en-US' : 'zh-CN', { weekday: 'long' }).format(
              new Date(`${selectedDate}T00:00:00`),
            )}
          </p>
          {dayOccurrences.length === 0 ? (
            <p className="py-8 text-center text-xs text-muted-text">{text.noDayTasks}</p>
          ) : null}
          {dayOccurrences.map((item) => (
            <div key={`${item.task.id}-${item.scheduledFor}`} className="rounded-xl border border-border/50 bg-card/40 p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-foreground">{item.task.name}</p>
                  <p className="mt-0.5 text-xs text-secondary-text">
                    {item.scheduledFor.slice(11)} · {typeLabel(item.task.taskType)}
                  </p>
                </div>
                <span className={cn('shrink-0 text-[11px]', STATUS_TONE[item.status])}>{statusLabel(item.status)}</span>
              </div>
              {item.message ? <p className="mt-2 line-clamp-3 text-[11px] text-muted-text">{item.message}</p> : null}
              <div className="mt-2 flex flex-wrap justify-end gap-1">
                <Button type="button" variant="ghost" size="sm" onClick={() => void runTask(item.task)} isLoading={busyTask === item.task.id}>
                  <Play className="h-3.5 w-3.5" aria-hidden="true" />
                  {text.runNow}
                </Button>
                <Button type="button" variant="ghost" size="sm" onClick={() => void toggleTask(item.task)} disabled={busyTask === item.task.id}>
                  {item.task.enabled ? text.disable : text.enable}
                </Button>
                <Button type="button" variant="ghost" size="sm" aria-label={text.edit} onClick={() => openEdit(item.task)}>
                  <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
                <Button type="button" variant="ghost" size="sm" aria-label={text.delete} onClick={() => setDeleting(item.task)}>
                  <Trash2 className="h-3.5 w-3.5 text-danger" aria-hidden="true" />
                </Button>
              </div>
            </div>
          ))}
          {disabledDayTasks.map((task) => (
            <div key={`disabled-${task.id}`} className="rounded-xl border border-border/50 bg-card/40 p-3 opacity-75">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold text-foreground">{task.name}</p>
                  <p className="mt-0.5 text-xs text-secondary-text">
                    {task.timeOfDay} · {typeLabel(task.taskType)}
                  </p>
                </div>
                <span className="shrink-0 text-[11px] text-muted-text">{text.disabled}</span>
              </div>
              <div className="mt-2 flex flex-wrap justify-end gap-1">
                <Button type="button" variant="ghost" size="sm" onClick={() => void toggleTask(task)} disabled={busyTask === task.id}>
                  {text.enable}
                </Button>
                <Button type="button" variant="ghost" size="sm" aria-label={text.edit} onClick={() => openEdit(task)}>
                  <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
                <Button type="button" variant="ghost" size="sm" aria-label={text.delete} onClick={() => setDeleting(task)}>
                  <Trash2 className="h-3.5 w-3.5 text-danger" aria-hidden="true" />
                </Button>
              </div>
            </div>
          ))}
        </div>
      </Dialog>

      <Dialog
        isOpen={dialogOpen}
        onClose={() => setDialogOpen(false)}
        title={editing ? text.editTask : text.newTask}
        ariaLabel={editing ? text.editTask : text.newTask}
        eyebrow={text.calendar}
        widthClassName="sm:max-w-2xl"
      >
        <div className="space-y-4">
          <label className="block text-sm">
            <span className="mb-1 block font-medium text-foreground">{text.name}</span>
            <input
              value={form.name}
              onChange={(event) => updateForm('name', event.target.value)}
              placeholder={text.taskNamePlaceholder}
              className="h-10 w-full rounded-lg border border-border/60 bg-background/50 px-3 text-sm text-foreground"
            />
          </label>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Select
              label={text.type}
              value={form.taskType}
              options={TYPE_OPTIONS.map((type) => ({ value: type, label: typeLabel(type) }))}
              onChange={(value) => updateForm('taskType', value as ScheduledTaskType)}
            />
            <label className="block text-sm">
              <span className="mb-1 block font-medium text-foreground">{text.time}</span>
              <input
                type="time"
                value={form.timeOfDay}
                onChange={(event) => updateForm('timeOfDay', event.target.value)}
                className="h-10 w-full rounded-lg border border-border/60 bg-background/50 px-3 text-sm text-foreground"
              />
              <span className="mt-1 block text-[11px] text-muted-text">{text.timeHint}</span>
            </label>
          </div>

          <div>
            <span className="mb-1.5 block text-sm font-medium text-foreground">{text.repeat}</span>
            <div className="flex flex-wrap gap-1.5">
              {text.weekdays.map((label, index) => {
                const active = form.weekdays.includes(index);
                return (
                  <button
                    key={label}
                    type="button"
                    aria-pressed={active}
                    onClick={() => updateForm(
                      'weekdays',
                      active ? form.weekdays.filter((day) => day !== index) : [...form.weekdays, index].sort((a, b) => a - b),
                    )}
                    className={cn(
                      'h-8 min-w-9 rounded-lg border px-2 text-xs transition-colors',
                      active ? 'border-primary/30 bg-primary/15 text-primary' : 'border-border/60 text-secondary-text hover:bg-hover',
                    )}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={form.tradingDayOnly}
              onChange={(event) => updateForm('tradingDayOnly', event.target.checked)}
            />
            {text.tradingDayOnly}
          </label>

          {form.taskType === 'stock_analysis' ? (
            <div className="space-y-3">
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-foreground">{text.stocks}</span>
                <textarea
                  value={form.stocksText}
                  onChange={(event) => updateForm('stocksText', event.target.value)}
                  placeholder="600519, AAPL, hk00700"
                  rows={2}
                  className="w-full rounded-lg border border-border/60 bg-background/50 px-3 py-2 text-sm text-foreground"
                />
                <span className="mt-1 block text-[11px] text-muted-text">{text.stocksHint}</span>
              </label>
              <Select
                label={text.mode}
                value={form.analysisMode}
                options={[
                  { value: 'standard', label: text.modeStandard },
                  { value: 'quick', label: text.modeQuick },
                ]}
                onChange={(value) => updateForm('analysisMode', value as FormState['analysisMode'])}
              />
              {form.analysisMode === 'quick' ? (
                <div className="space-y-2">
                  <p className="text-[11px] text-muted-text">{text.quickHint}</p>
                  <label className="flex items-center gap-2 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={form.quickNotify}
                      onChange={(event) => updateForm('quickNotify', event.target.checked)}
                    />
                    {text.notify}
                  </label>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <Select
                    label={text.reportType}
                    value={form.reportType}
                    options={[
                      { value: 'brief', label: text.brief },
                      { value: 'detailed', label: text.detailed },
                    ]}
                    onChange={(value) => updateForm('reportType', value as FormState['reportType'])}
                  />
                  <label className="flex items-center gap-2 pt-6 text-sm text-foreground">
                    <input
                      type="checkbox"
                      checked={form.forceRefresh}
                      onChange={(event) => updateForm('forceRefresh', event.target.checked)}
                    />
                    {text.forceRefresh}
                  </label>
                </div>
              )}
            </div>
          ) : null}

          {form.taskType === 'market_review' ? (
            <div className="space-y-3">
              <Select
                label={text.region}
                value={form.region}
                options={[
                  { value: 'cn', label: 'A 股' },
                  { value: 'hk', label: '港股' },
                  { value: 'us', label: '美股' },
                ]}
                onChange={(value) => updateForm('region', value as FormState['region'])}
              />
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input type="checkbox" checked={form.notify} onChange={(event) => updateForm('notify', event.target.checked)} />
                {text.notify}
              </label>
            </div>
          ) : null}

          {form.taskType === 'screening' || form.taskType === 'data_refresh' ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <label className="block text-sm sm:col-span-1">
                <span className="mb-1 block font-medium text-foreground">{text.strategy}</span>
                <input
                  value={form.strategy}
                  onChange={(event) => updateForm('strategy', event.target.value)}
                  className="h-10 w-full rounded-lg border border-border/60 bg-background/50 px-3 text-sm text-foreground"
                />
              </label>
              <Select
                label={text.market}
                value={form.market}
                options={[
                  { value: 'cn', label: 'A 股' },
                  { value: 'hk', label: '港股' },
                  { value: 'us', label: '美股' },
                ]}
                onChange={(value) => updateForm('market', value as FormState['market'])}
              />
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-foreground">{text.maxResults}</span>
                <input
                  type="number"
                  min={1}
                  max={100}
                  value={form.maxResults}
                  onChange={(event) => updateForm('maxResults', event.target.value)}
                  className="h-10 w-full rounded-lg border border-border/60 bg-background/50 px-3 text-sm text-foreground"
                />
              </label>
              {form.taskType === 'data_refresh' ? (
                <p className="text-xs leading-relaxed text-muted-text sm:col-span-3">
                  {text.dataRefreshHint}
                </p>
              ) : null}
            </div>
          ) : null}

          {form.taskType === 'paper_auto_trade' ? (
            <label className="block max-w-xs text-sm">
              <span className="mb-1 block font-medium text-foreground">{text.lookbackDays}</span>
              <input
                type="number"
                min={1}
                max={365}
                value={form.lookbackDays}
                onChange={(event) => updateForm('lookbackDays', event.target.value)}
                className="h-10 w-full rounded-lg border border-border/60 bg-background/50 px-3 text-sm text-foreground"
              />
              <span className="mt-1 block text-[11px] text-muted-text">{text.lookbackDaysHint}</span>
            </label>
          ) : null}

          {form.taskType === 'backtest' ? (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-foreground">{text.stocks}</span>
                <input
                  value={form.code}
                  onChange={(event) => updateForm('code', event.target.value)}
                  placeholder="600519"
                  className="h-10 w-full rounded-lg border border-border/60 bg-background/50 px-3 text-sm text-foreground"
                />
              </label>
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-foreground">Limit</span>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  value={form.limit}
                  onChange={(event) => updateForm('limit', event.target.value)}
                  className="h-10 w-full rounded-lg border border-border/60 bg-background/50 px-3 text-sm text-foreground"
                />
              </label>
              <label className="flex items-center gap-2 text-sm text-foreground sm:col-span-2">
                <input
                  type="checkbox"
                  checked={form.forceRefresh}
                  onChange={(event) => updateForm('forceRefresh', event.target.checked)}
                />
                {text.forceRefresh}
              </label>
            </div>
          ) : null}

          {form.taskType === 'agent_prompt' ? (
            <div className="space-y-3">
              <label className="block text-sm">
                <span className="mb-1 block font-medium text-foreground">{text.prompt}</span>
                <textarea
                  value={form.prompt}
                  onChange={(event) => updateForm('prompt', event.target.value)}
                  placeholder={text.promptHint}
                  rows={4}
                  className="w-full rounded-lg border border-border/60 bg-background/50 px-3 py-2 text-sm text-foreground"
                />
              </label>
              <label className="flex items-center gap-2 text-sm text-foreground">
                <input type="checkbox" checked={form.notify} onChange={(event) => updateForm('notify', event.target.checked)} />
                {text.notify}
              </label>
            </div>
          ) : null}

          <div className="flex justify-end gap-2 border-t border-border/50 pt-4">
            <Button type="button" variant="secondary" onClick={() => setDialogOpen(false)}>{text.cancel}</Button>
            <Button type="button" variant="primary" isLoading={saving} loadingText={text.saving} onClick={() => void save()}>
              {editing ? text.save : text.create}
            </Button>
          </div>
        </div>
      </Dialog>

      <ConfirmDialog
        isOpen={Boolean(deleting)}
        title={text.delete}
        message={deleting ? text.confirmDelete.replace('{name}', deleting.name) : ''}
        confirmText={text.delete}
        cancelText={text.cancel}
        isDanger
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleting(null)}
      />

      {loading && !calendar ? <Loading label={text.loading} /> : null}
    </AppPage>
  );
};

export default ScheduledTasksPage;
