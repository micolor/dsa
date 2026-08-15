/**
 * 表单控件统一样式
 * 所有 <select> / input 表单元素共用，保证全站风格一致。
 */
export const SELECT_INPUT_CLASS =
  'input-surface input-focus-glow h-11 rounded-xl border bg-transparent px-4 text-sm transition-[border-color,background-color,box-shadow] focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

/** select 专用：隐藏原生箭头，使用统一的自定义 chevron（内缩间距） */
export const SELECT_CHEVRON_CLASS = `${SELECT_INPUT_CLASS} select-input-chevron`;
