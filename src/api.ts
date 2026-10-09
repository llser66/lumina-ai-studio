export type User = { id: string; name: string; email: string };
export type Quota = { limit: number; used: number; remaining: number; resets_at: number };
export type Health = { status: string; provider_ready: boolean; model: string; registration_enabled: boolean };
export type JobEvent = { id: number; stage: string; message: string; created_at: number };
export type Job = {
  id: string; prompt: string; negative_prompt: string; enhanced_prompt: string | null;
  style: string; size: string; quality: string; enhance: boolean; reference_id: string | null;
  model: string; status: string; favorite: boolean; created_at: number; updated_at: number;
  finished_at: number | null; asset_id: string | null; image_url: string | null;
  error_code: string | null; error_message: string | null; provider_request_id: string | null;
  events: JobEvent[];
};
export type Spec = { prompt: string; negative_prompt: string; style: string; size: string; quality: string; enhance: boolean; reference_id: string | null };
export type Asset = { id: string; image_url: string; width?: number; height?: number };
let csrfToken = '';
export function setCsrf(token: string) { csrfToken = token; }
export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('X-Lumina-Client', 'web');
  if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  if (typeof options.body === 'string') headers.set('Content-Type', 'application/json');
  let response: Response;
  try { response = await fetch('/api' + path, { ...options, headers, credentials: 'same-origin' }); }
  catch { throw new ApiError(0, '暂时无法连接服务，请检查网络后重试。'); }
  if (!response.ok) {
    let message = '操作未完成，请稍后再试。';
    try { const body = await response.json(); if (typeof body.detail === 'string') message = body.detail; } catch { /* Proxy failures may return HTML. */ }
    throw new ApiError(response.status, message);
  }
  return response.status === 204 ? undefined as T : response.json();
}
export const terminal = new Set(['succeeded', 'failed', 'cancelled', 'uncertain']);
export const statusText: Record<string, string> = {
  queued: '排队中', validating: '整理参数', enhancing: '优化描述', moderating: '内容检查',
  generating: '生成中', saving: '保存作品', succeeded: '已完成', failed: '未完成', cancelled: '已取消', uncertain: '结果待确认',
};
