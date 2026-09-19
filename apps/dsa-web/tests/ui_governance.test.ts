import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const srcRoot = join(process.cwd(), 'src');
const nativeTitlePattern = /<(button|a|input|textarea|select|div|span)\b[^>]*\btitle=/;
const inputTerminalPattern = /\binput-terminal\b/;

function collectSourceFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const fullPath = join(dir, entry);
    const stats = statSync(fullPath);

    if (stats.isDirectory()) {
      return collectSourceFiles(fullPath);
    }

    if (!/\.(ts|tsx)$/.test(fullPath)) {
      return [];
    }

    if (/\.test\.(ts|tsx)$/.test(fullPath)) {
      return [];
    }

    return [fullPath];
  });
}

const sourceFiles = collectSourceFiles(srcRoot);

describe('web UI governance guards', () => {
  it('does not reintroduce native title attributes on common interactive elements', () => {
    const violations = sourceFiles.filter((filePath) => nativeTitlePattern.test(readFileSync(filePath, 'utf8')));
    expect(violations).toEqual([]);
  });

  it('keeps input-terminal out of application source', () => {
    const violations = sourceFiles.filter((filePath) => inputTerminalPattern.test(readFileSync(filePath, 'utf8')));
    expect(violations).toEqual([]);
  });

  // 每个路由页面都要自己设置 document.title。缺了会静默沿用上一页的标题：
  // 浏览器标签页、书签、前进/后退历史里记的都是别的页面名，而页面本身看不出异常。
  it('lets every routed page set its own document.title', () => {
    const appSource = readFileSync(join(srcRoot, 'App.tsx'), 'utf8');

    // App.tsx 用 lazy(() => import('./pages/Xxx')) 引入页面，先把组件名映射回模块路径，
    // 路由里的组件名与文件名并不总是一致（/screening 用的是 StockScreeningPage）。
    const modules = new Map<string, string>();
    for (const match of appSource.matchAll(/const\s+(\w+)\s*=\s*lazy\(\(\)\s*=>\s*import\(['"]\.\/([^'"]+)['"]\)\)/g)) {
      modules.set(match[1], match[2]);
    }

    const routes = [...appSource.matchAll(/<Route\s+path="([^"]+)"\s+element=\{<(\w+)\s*\/>\}/g)].map(
      ([, path, component]) => ({ path, component }),
    );
    // 正则一旦匹配不到路由，下面的断言会空过——先钉死它确实扫到了东西。
    expect(routes.length).toBeGreaterThan(0);

    const missing = routes
      .filter(({ component }) => {
        const relative = modules.get(component) ?? `pages/${component}`;
        const filePath = join(srcRoot, relative.endsWith('.tsx') ? relative : `${relative}.tsx`);
        try {
          return !/document\.title/.test(readFileSync(filePath, 'utf8'));
        } catch {
          return true;
        }
      })
      .map(({ path, component }) => `${path} → ${component}`);

    expect(missing).toEqual([]);
  });
});
