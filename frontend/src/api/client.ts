import axios from 'axios'

const api = axios.create({
  baseURL: '/api/v1',
  headers: { 'Content-Type': 'application/json' },
})

export type UserRole = 'admin' | 'sre' | 'viewer'

export type AuthUser = {
  username: string
  name: string
  role: UserRole
  email?: string | null
  permissions?: string[]
}

export type IncidentListItem = {
  incident_id: string
  status: string
  service?: string | null
  severity?: string | null
  created_at: string
  resolved_at?: string | null
  time_to_recovery?: number | null
  retry_count?: number
  total_cost_usd?: number
}

export type IncidentListResponse = {
  incidents: IncidentListItem[]
  pagination?: {
    page: number
    page_size: number
    total: number
  }
}

export type HealthResponse = {
  status: string
  service: string
  version: string
  uptime_seconds: number
  checks: Record<string, boolean>
}

export type MemoryIncident = {
  incident_id: string
  service: string
  status?: string
  outcome?: string
  created_at?: string
  alert_signature?: string
  similarity_score?: number | null
  postmortem?: {
    root_cause_confirmed?: string
    fix_applied?: string
    outcome?: string
    time_to_recovery_seconds?: number | null
    created_at?: string
    alert_signature?: string
  }
}

export type ReportItem = {
  incident_id: string
  service: string
  alert_signature: string
  root_cause_confirmed: string
  fix_applied: string
  outcome: string
  time_to_recovery_seconds?: number | null
  retry_count: number
  total_cost_usd: number
  created_at: string
  status?: string
}

export type IncidentDetail = {
  incident_id: string
  status: string
  created_at?: string
  resolved_at?: string | null
  alert?: any
  hypotheses?: any[]
  fix_options?: any[]
  selected_fix?: any
  execution_log?: any[]
  retry_count?: number
  current_hypothesis_idx?: number
  hitl_required?: boolean
  recovery_confirmed?: boolean | null
  time_to_recovery?: number | null
  total_cost_usd?: number
  error_message?: string | null
  postmortem?: any
  events?: any[]
  raw_metrics?: Record<string, number>
  past_incidents?: any[]
}

let isRefreshing = false
let refreshPromise: Promise<string | null> | null = null

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('access_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error?.config
    if (error?.response?.status !== 401 || !original || original._retry) {
      return Promise.reject(error)
    }

    const refreshToken = localStorage.getItem('refresh_token')
    if (!refreshToken) {
      localStorage.removeItem('access_token')
      return Promise.reject(error)
    }

    original._retry = true
    if (!isRefreshing) {
      isRefreshing = true
      refreshPromise = axios
        .post('/api/v1/auth/refresh', { refresh_token: refreshToken })
        .then((res) => {
          const nextAccess = res.data?.access_token as string | undefined
          const nextRefresh = res.data?.refresh_token as string | undefined
          if (nextAccess) localStorage.setItem('access_token', nextAccess)
          if (nextRefresh) localStorage.setItem('refresh_token', nextRefresh)
          return nextAccess || null
        })
        .catch(() => {
          localStorage.removeItem('access_token')
          localStorage.removeItem('refresh_token')
          return null
        })
        .finally(() => {
          isRefreshing = false
        })
    }

    const newAccess = await refreshPromise
    if (!newAccess) return Promise.reject(error)

    original.headers = original.headers || {}
    original.headers.Authorization = `Bearer ${newAccess}`
    return api(original)
  },
)

export const login = async (username: string, password: string) => {
  const form = new FormData()
  form.append('username', username)
  form.append('password', password)
  const res = await axios.post('/api/v1/auth/login', form)
  localStorage.setItem('access_token', res.data.access_token)
  if (res.data.refresh_token) localStorage.setItem('refresh_token', res.data.refresh_token)
  return res.data
}

export const logout = async () => {
  const refresh = localStorage.getItem('refresh_token')
  try {
    await api.post('/auth/logout', { refresh_token: refresh || undefined })
  } catch {
    // Best effort
  } finally {
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
  }
}

export const getMe = () => api.get('/auth/me').then((r) => r.data)

export type IncidentFilters = {
  status?: string
  severity?: string
  service?: string
  date_from?: string
  date_to?: string
}

export const listIncidents = () => api.get<IncidentListResponse>('/incidents').then((r) => r.data)
export const listIncidentsFiltered = (filters: IncidentFilters) =>
  api.get<IncidentListResponse>('/incidents', { params: filters }).then((r) => r.data)
export const getIncident = (id: string) =>
  api.get<IncidentDetail>(`/incidents/${id}`).then((r) => r.data)
export const triggerIncident = (payload?: { service?: string; notes?: string }) =>
  api.post('/incidents', payload || {}).then((r) => r.data)

export const submitHITL = (
  incidentId: string,
  decision: 'approve' | 'override' | 'abort',
  customInstruction?: string,
  reason?: string,
) =>
  api
    .post(`/incidents/${incidentId}/hitl`, {
      decision,
      custom_instruction: customInstruction,
      reason,
    })
    .then((r) => r.data)

export const listReports = () => api.get<{ reports: ReportItem[] }>('/reports').then((r) => r.data)
export const getReport = (id: string) => api.get<ReportItem>(`/reports/${id}`).then((r) => r.data)
export const exportReport = (id: string, format: 'markdown' | 'json' | 'pdf' = 'markdown') =>
  api
    .get(`/reports/${id}/export`, {
      params: { format },
      responseType: format === 'pdf' ? 'blob' : format === 'markdown' ? 'text' : 'json',
    })
    .then((r) => r.data)
export const getIncidentPostmortem = (id: string) =>
  api.get(`/incidents/${id}/postmortem`).then((r) => r.data)
export const exportIncidentPostmortem = (
  id: string,
  format: 'markdown' | 'json' | 'pdf' = 'markdown',
) =>
  api
    .get(`/incidents/${id}/postmortem/export`, {
      params: { format },
      responseType: format === 'pdf' ? 'blob' : format === 'markdown' ? 'text' : 'json',
    })
    .then((r) => r.data)

export const getHealth = () => api.get<HealthResponse>('/health').then((r) => r.data)
export const getMetricsSummary = () => api.get('/metrics/summary').then((r) => r.data)
export const getControlPlaneMetrics = () =>
  api.get('/metrics/control-plane-summary').then((r) => r.data)
export const getMemoryIncidents = (limit = 20) =>
  api
    .get<{
      memory: Record<string, string>
      incidents: MemoryIncident[]
      total: number
    }>('/memory/incidents', { params: { limit } })
    .then((r) => r.data)
export const deleteMemoryIncident = (incidentId: string) =>
  api.delete(`/memory/incidents/${incidentId}`).then((r) => r.data)
export const getDemoFaultStatus = () => api.get('/demo/fault-status').then((r) => r.data)
export const injectDemoFault = (payload: {
  fault_type: string
  service?: string
  cycles?: number
  duration?: number
  rate?: number
  ratio?: number
  connections?: number
  delay_seconds?: number
  replicas?: number
  target_version?: string
}) => api.post('/demo/fault-inject', payload).then((r) => r.data)
export const resetDemoFaults = () => api.post('/demo/fault-reset').then((r) => r.data)

export const createIncidentStream = (incidentId: string, onEvent: (e: any) => void) => {
  const token = localStorage.getItem('access_token')
  const wsUrl = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/api/v1/incidents/${incidentId}/stream`
  const ws = new WebSocket(wsUrl)
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'auth', token }))
  }
  ws.onmessage = (e) => {
    try {
      onEvent(JSON.parse(e.data))
    } catch {
      // ignore malformed events
    }
  }
  ws.onerror = (e) => console.error('WebSocket error:', e)
  return ws
}

export default api
