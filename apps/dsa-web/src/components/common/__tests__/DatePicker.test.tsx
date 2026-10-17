import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { DatePicker } from '../DatePicker';

describe('DatePicker', () => {
  // 触发按钮的可见文案就是日期值（空时是占位符）。同一排两个日期框若只按值取名，
  // 空着的时候会念成同一个名字，读屏分不出哪个是哪个。
  it('names the trigger after the field label so adjacent pickers stay distinguishable', () => {
    render(<DatePicker value="" onChange={vi.fn()} ariaLabel="分析开始日期" />);

    expect(screen.getByRole('button', { name: '分析开始日期' })).toBeInTheDocument();
  });

  it('keeps the selected date inside the trigger name', () => {
    render(<DatePicker value="2026-01-05" onChange={vi.fn()} ariaLabel="分析开始日期" />);

    expect(screen.getByRole('button', { name: '分析开始日期: 2026-01-05' })).toBeInTheDocument();
  });

  // 没有字段名的调用方（如持仓页）仍按可见文案取名，不额外添加 aria-label。
  it('keeps the visible text as the name when no field label is given', () => {
    render(<DatePicker value="" onChange={vi.fn()} placeholder="起始日期" />);

    const trigger = screen.getByRole('button', { name: '起始日期' });
    expect(trigger).not.toHaveAttribute('aria-label');
  });

  it('names the calendar dialog after the field label', async () => {
    render(<DatePicker value="" onChange={vi.fn()} ariaLabel="分析结束日期" />);

    fireEvent.click(screen.getByRole('button', { name: '分析结束日期' }));

    expect(await screen.findByRole('dialog', { name: '分析结束日期' })).toBeInTheDocument();
  });

  it('falls back to the generic dialog label when no field label is given', async () => {
    render(<DatePicker value="" onChange={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '选择日期' }));

    expect(await screen.findByRole('dialog', { name: '选择日期' })).toBeInTheDocument();
  });
});
