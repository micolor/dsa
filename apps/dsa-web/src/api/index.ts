import axios from 'axios';
import { API_BASE_URL } from '../utils/constants';
import { attachParsedApiError, normalizeBlobErrorBody } from './error';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
  },
});

apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    if (error.response?.status === 401) {
      const path = window.location.pathname + window.location.search;
      if (!path.startsWith('/login')) {
        const redirect = encodeURIComponent(path);
        window.location.assign(`/login?redirect=${redirect}`);
      }
    }
    // 必须早于 attachParsedApiError：blob 请求的错误体是 Blob，等到更外层再还原时
    // 错误对象上已经缓存了基于 Blob 解析出来的错误归因，真实原因会被永久覆盖。
    await normalizeBlobErrorBody(error);
    attachParsedApiError(error);
    return Promise.reject(error);
  }
);

export default apiClient;
