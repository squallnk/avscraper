/** 后端 API 客户端。所有请求都过这一层，错误统一成可读文本。 */

export interface HealthInfo {
  status: string
  allowed_roots: string[]
  writes_enabled: boolean
  dry_run: boolean
  organize_enabled: boolean
  queue_depth: number
  sources: number
}

export interface RuntimeConfig {
  dry_run: boolean
  organize_enabled: boolean
  file_op_rate: number
  file_op_burst: number
  http_rate: number
  http_burst: number
  cooldown_seconds: number
  failure_threshold: number
  max_retries: number
  request_timeout: number
  proxy: string
  user_agent: string
  enabled_sources: string[]
  field_priority: Record<string, string[]>
  route_override: Record<string, string[]>
  directory_template: string
  filename_template: string
  image_template: string
  nfo_template: string
  metadata_dir: string
  cd2_mappings: string[][]
  webhook_enabled: boolean
  webhook_token: string
  webhook_debounce_seconds: number
  webhook_max_subtree_files: number
  webhook_auto_scrape: boolean
  source_cookies: Record<string, string>
  metadata_dir: string
  images: ImageDownloadConfig
}

export interface ImageDownloadConfig {
  poster: boolean
  thumb: boolean
  fanart: boolean
  extrafanart: boolean
  extrafanart_limit: number
  overwrite: boolean
  concurrency: number
  timeout: number
}

export interface CookieStatus {
  source: string
  needs_cookie: boolean
  configured: boolean
  length: number
}

export interface SourceInfo {
  id: string
  name: string
  homepage: string
  supports: string[]
  needs_proxy: boolean
  needs_cookie: boolean
  cookie_probe_query: string
  note: string
  active: boolean
}

export interface TaskInfo {
  id: string
  kind: string
  state: string
  message: string
  current: number
  total: number
  error: string | null
  result: Record<string, unknown>
}

export interface RecordInfo {
  id: string
  path: string
  number: string | null
  content_type: string
  season: number | null
  episode: number | null
  cd: number | null
  episode_source: string
  status: string
  error: string | null
  field_sources: Record<string, string>
  metadata: Record<string, unknown> | null
  updated_at: string
}

export interface ClassifyInfo {
  number: string | null
  content_type: string
  content_type_label: string
  confidence: number
  evidence: string[]
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    let detail = `HTTP ${response.status}`
    try {
      const body = await response.json()
      if (body?.detail) detail = String(body.detail)
    } catch {
      /* 响应不是 JSON 时保留状态码提示 */
    }
    throw new Error(detail)
  }
  return (await response.json()) as T
}

export const api = {
  health: () => call<HealthInfo>('/api/health'),
  config: () => call<RuntimeConfig>('/api/config'),
  patchConfig: (patch: Partial<RuntimeConfig>) =>
    call<RuntimeConfig>('/api/config', { method: 'PUT', body: JSON.stringify(patch) }),
  sources: () => call<SourceInfo[]>('/api/sources'),
  cookieStatus: (id: string) => call<CookieStatus>(`/api/sources/${id}/cookie`),
  verifyCookie: (id: string) =>
    call<{ supported: boolean; ok?: boolean; reason?: string; detail?: string }>(
      `/api/sources/${id}/cookie/verify`,
    ),
  setCookie: (id: string, value: string) =>
    call<{ source: string; configured: boolean }>(`/api/sources/${id}/cookie`, {
      method: 'PUT',
      body: JSON.stringify({ value }),
    }),
  sourceHealth: () => call<{ cooldowns: Record<string, unknown> }>('/api/sources/health'),
  tasks: () => call<TaskInfo[]>('/api/tasks'),
  startScan: (payload: Record<string, unknown>) =>
    call<{ task_id: string }>('/api/tasks/scan', { method: 'POST', body: JSON.stringify(payload) }),
  records: (status?: string) =>
    call<RecordInfo[]>(status ? `/api/records?status=${status}` : '/api/records'),
  logs: () => call<{ level: string; logger: string; message: string; created_at: string }[]>('/api/logs'),
  classify: (path: string) =>
    call<ClassifyInfo>('/api/classify', { method: 'POST', body: JSON.stringify({ path }) }),
  render: (template: string, data: Record<string, string>) =>
    call<{ result: string }>('/api/render-template', {
      method: 'POST',
      body: JSON.stringify({ template, data }),
    }),
  browse: (path: string) =>
    call<{ path: string; entries: { name: string; path: string; is_dir: boolean; size: number }[] }>(
      `/api/browse?path=${encodeURIComponent(path)}`,
    ),
}
