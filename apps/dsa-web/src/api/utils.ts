import camelcaseKeys from 'camelcase-keys';

/**
 * 将 snake_case 对象键转换为 camelCase
 * @param data API 响应数据 (snake_case)
 * @returns 转换后的 camelCase 对象
 */
export function toCamelCase<T>(data: unknown): T {
    if (data === null || data === undefined) {
        return data as T;
    }
    return camelcaseKeys(data as Record<string, unknown>, { deep: true }) as T;
}

/**
 * 将 camelCase 对象键递归转换为 snake_case，用于向 FastAPI 发送请求体。
 *
 * 浏览器发请求前的统一边界：各 API 客户端仍按 camelCase 入参收数据，由这里负责
 * 转成后端 Pydantic schema 期望的 snake_case，避免每个调用点手写同义转换。
 */
export function toSnakeCase(data: unknown): unknown {
    if (data === null || data === undefined || typeof data !== 'object') {
        return data;
    }
    if (Array.isArray(data)) {
        return data.map(toSnakeCase);
    }
    return Object.fromEntries(
        Object.entries(data as Record<string, unknown>).map(([key, value]) => [
            key.replace(/[A-Z]/g, (letter) => `_${letter.toLowerCase()}`),
            toSnakeCase(value),
        ]),
    );
}
