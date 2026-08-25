/** fetch 封装：API 前缀、Bearer token、统一错误。 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

const TOKEN_KEY = "access_token";

export class ApiError extends Error {
  status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  window.localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function errorDetail(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") return body.detail;
    if (body?.detail != null) return JSON.stringify(body.detail);
    return JSON.stringify(body);
  } catch {
    return `请求失败 (HTTP ${res.status})`;
  }
}

/** 401 时派发的事件：由 AuthProvider 监听并跳转登录页（避免在 fetch 层直接改 location） */
export const AUTH_UNAUTHORIZED_EVENT = "auth:unauthorized";

function handleUnauthorized(): void {
  if (typeof window === "undefined") return;
  clearToken();
  if (!window.location.pathname.startsWith("/login")) {
    window.dispatchEvent(new Event(AUTH_UNAUTHORIZED_EVENT));
  }
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers ?? {}) },
  })

  if (res.status === 204) return undefined as T;

  if (!res.ok) {
    const detail = await errorDetail(res);
    if (res.status === 401) handleUnauthorized();
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

/** multipart 上传（不手动设置 Content-Type，交给浏览器生成 boundary） */
export async function uploadRequest<T>(
  path: string,
  file: File,
  field = "file",
  extra: Record<string, string> = {},
): Promise<T> {
  const form = new FormData();
  form.append(field, file);
  for (const [k, v] of Object.entries(extra)) form.append(k, v);
  return request<T>(path, { method: "POST", body: form });
}

export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}
