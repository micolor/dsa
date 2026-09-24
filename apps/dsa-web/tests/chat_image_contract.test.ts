import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 前端的图片限制是后端契约的副本，副本漂移的后果是「前端放行、后端 400」：用户贴了一张
 * 前端认为合法的图，发送时却被拒，而且两边都没有单侧 bug 可查。
 *
 * 所以这里直接读后端的 Python 源文件做比对，而不是只留一句注释提醒。先例见
 * `tests/ui_governance.test.ts`（同样读仓库源文件做治理断言）。
 *
 * 路径基准是 process.cwd()，即假定从 apps/dsa-web 起跑——与 tests/ui_governance.test.ts
 * 同一约定（vitest 的 root 也钉在这里）。跑错目录会读到不存在的路径并明确报错，不会空过。
 */
const webRoot = process.cwd();
const repoRoot = join(webRoot, '..', '..');

const CHAT_PAGE = join(webRoot, 'src', 'pages', 'ChatPage.tsx');
const AGENT_PY = join(repoRoot, 'api', 'v1', 'endpoints', 'agent.py');
const IMAGE_EXTRACTOR_PY = join(repoRoot, 'src', 'services', 'image_stock_extractor.py');

function readSource(path: string): string {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    // 不静默跳过：这条断言的全部价值就是「两边都在时才校验」，缺一边还当成通过就白写了。
    throw new Error(
      `读不到 ${path}。本测试假定从 apps/dsa-web 起跑、且前端与后端 api 树一起检出；`
      + '移动过后端目录或换了起跑目录时，请同步更新本文件顶部的路径。',
    );
  }
}

/**
 * 取 `NAME = <数字表达式>` 的值。表达式只允许数字、下划线和乘号（由正则限定），
 * 所以自己按 `*` 相乘即可，不需要 eval。
 */
function readSizeConstant(source: string, name: string, where: string): number {
  const match = source.match(
    new RegExp(`^\\s*(?:const\\s+)?${name}\\s*=\\s*([0-9_*\\s]+?)\\s*;?\\s*(?:[#/]{1,2}.*)?$`, 'm'),
  );
  if (!match) {
    throw new Error(`${where} 里找不到 ${name} 的数字字面量定义——改名或换写法时请同步更新本测试。`);
  }

  const expression = match[1].replace(/_/g, '');
  if (expression.includes('**')) {
    // 静默算错比报错更糟：`2 ** 20` 会被 split('*') 拆成 2 和 20。
    throw new Error(`${where} 的 ${name} 用了 ** 幂运算（${expression}），本测试的解析器不支持，请补上解析。`);
  }

  const value = expression
    .split('*')
    .map((part) => part.trim())
    .filter(Boolean)
    .reduce((product, part) => product * Number(part), 1);

  if (!Number.isInteger(value) || value <= 0) {
    throw new Error(`${where} 的 ${name} 解析结果不是正整数：${JSON.stringify(expression)}`);
  }
  return value;
}

/** 取 `NAME = [ 'a', 'b' ]` 或 `NAME = frozenset({"a", "b"})` 里的字符串字面量集合。 */
function readStringSet(source: string, name: string, where: string): string[] {
  const literal = source.match(new RegExp(`^\\s*(?:const\\s+)?${name}\\s*=\\s*\\[([^\\]]*)\\]`, 'm'))
    ?? source.match(
      new RegExp(`^\\s*(?:const\\s+)?${name}\\s*=\\s*[A-Za-z.]*\\(\\s*\\{([^}]*)\\}\\s*\\)`, 'm'),
    );
  if (!literal) {
    throw new Error(`${where} 里找不到 ${name} 的集合字面量定义——改名或换写法时请同步更新本测试。`);
  }

  const values = [...literal[1].matchAll(/["']([^"']+)["']/g)].map(([, value]) => value);
  if (values.length === 0) {
    throw new Error(`${where} 的 ${name} 解析不到任何字符串字面量——解析已失效，不能让它空过。`);
  }
  return [...new Set(values)].sort();
}

describe('chat image contract drift guards', () => {
  it('keeps the composer size cap equal to the backend CHAT_IMAGE_MAX_BYTES', () => {
    const frontend = readSizeConstant(readSource(CHAT_PAGE), 'CHAT_IMAGE_MAX_BYTES', 'ChatPage.tsx');
    const backend = readSizeConstant(readSource(AGENT_PY), 'CHAT_IMAGE_MAX_BYTES', 'api/v1/endpoints/agent.py');

    expect(frontend).toBe(backend);
    // 钉住解析本身读到了真东西，不然「两个错值相等」也会绿。
    expect(frontend).toBe(2 * 1024 * 1024);
  });

  it('keeps the composer MIME allowlist equal to the backend ALLOWED_MIME', () => {
    const frontend = readStringSet(readSource(CHAT_PAGE), 'CHAT_IMAGE_MIME', 'ChatPage.tsx');
    const backend = readStringSet(
      readSource(IMAGE_EXTRACTOR_PY),
      'ALLOWED_MIME',
      'src/services/image_stock_extractor.py',
    );

    expect(frontend).toEqual(backend);
    expect(frontend.length).toBeGreaterThan(1);
  });
});
