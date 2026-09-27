import type {
  AiPredictionResponse,
  AccessKey,
  AccessSession,
  AdminSession,
  AlertDelivery,
  AlertWebhookConfig,
  AutoPlayConfigurationRequest,
  AutoPlayStatus,
  DailyStats,
  DailySummary,
  Page,
  PaymentConfig,
  MarketPredictionResponse,
  PhoneControlStatus,
  Prediction,
  ResultItem,
  ScannerEvent,
  ScannerStatus,
  StreakRun,
  Subscriber,
  Visitor,
  CreatedAccessKey,
} from './types'

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export function getClientDeviceId() {
  const storageKey = 'greedy-device-id'
  let value = window.localStorage.getItem(storageKey)
  if (!value) {
    value = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
    window.localStorage.setItem(storageKey, value)
  }
  return value
}

export function getClientSessionId() {
  const storageKey = 'greedy-session-id'
  let value = window.sessionStorage.getItem(storageKey)
  if (!value) {
    value = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`
    window.sessionStorage.setItem(storageKey, value)
  }
  return value
}

export function getClientDeviceName() {
  const mobile = /Android|iPhone|Mobile/i.test(navigator.userAgent)
  const tablet = /iPad|Tablet/i.test(navigator.userAgent)
  const kind = tablet ? 'Máy tính bảng' : mobile ? 'Điện thoại' : 'Máy tính'
  return `${kind} · ${navigator.platform || 'Trình duyệt'}`
}

function clientContextHeaders(): Record<string, string> {
  const nav = navigator as Navigator & {
    connection?: { effectiveType?: string; type?: string }
    userAgentData?: { platform?: string }
  }
  const encoded = (value: string) => encodeURIComponent(value).slice(0, 900)
  return {
    'X-Device-Id': getClientDeviceId(),
    'X-Session-Id': getClientSessionId(),
    'X-Device-Name': encoded(getClientDeviceName()),
    'X-Client-Language': encoded(navigator.languages?.join(', ') || navigator.language || ''),
    'X-Client-Timezone': encoded(Intl.DateTimeFormat().resolvedOptions().timeZone || ''),
    'X-Client-Screen': `${window.screen.width}x${window.screen.height}`,
    'X-Client-Viewport': `${window.innerWidth}x${window.innerHeight}`,
    'X-Client-Pixel-Ratio': String(window.devicePixelRatio || 1),
    'X-Client-Touch-Points': String(navigator.maxTouchPoints || 0),
    'X-Client-Platform': encoded(nav.userAgentData?.platform || navigator.platform || ''),
    'X-Client-Connection': encoded(nav.connection?.effectiveType || nav.connection?.type || ''),
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${configuredBase}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...clientContextHeaders(),
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
    cache: 'no-store',
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let code: string | undefined
    try {
      const payload = await response.json() as { error?: string; code?: string }
      if (payload.error) message = payload.error
      code = payload.code
    } catch {
      // Keep the HTTP error when the response has no JSON body.
    }
    if (code === 'ACCESS_KEY_REQUIRED' || code === 'ACCESS_KEY_EXPIRED') {
      window.dispatchEvent(new CustomEvent('greedy-access-denied', { detail: { code } }))
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

async function requestBlob(path: string, init?: RequestInit): Promise<Blob> {
  const response = await fetch(`${configuredBase}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      Accept: 'image/png,application/json',
      ...clientContextHeaders(),
      ...init?.headers,
    },
    cache: 'no-store',
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    let code: string | undefined
    try {
      const payload = await response.json() as { error?: string; code?: string }
      if (payload.error) message = payload.error
      code = payload.code
    } catch {
      // Keep the HTTP error when the response has no JSON body.
    }
    if (code === 'ACCESS_KEY_REQUIRED' || code === 'ACCESS_KEY_EXPIRED') {
      window.dispatchEvent(new CustomEvent('greedy-access-denied', { detail: { code } }))
    }
    throw new Error(message)
  }
  return response.blob()
}

const queryDate = (date: string) => encodeURIComponent(date)

type StreakRunSummary = Omit<StreakRun, 'items'>
type StreakBucketSummary = { length: number; count: number }
type CompactDailyStats = Omit<DailyStats,
  'vegetableRuns' | 'meatRuns' | 'vegetableStreakBuckets' | 'meatStreakBuckets'> & {
    vegetableRuns: StreakRunSummary[]
    meatRuns: StreakRunSummary[]
    vegetableStreakBuckets: StreakBucketSummary[]
    meatStreakBuckets: StreakBucketSummary[]
  }

function expandCompactStats(payload: CompactDailyStats): DailyStats {
  const vegetableRuns: StreakRun[] = payload.vegetableRuns.map(run => ({ ...run, items: [] }))
  const meatRuns: StreakRun[] = payload.meatRuns.map(run => ({ ...run, items: [] }))
  return {
    ...payload,
    vegetableRuns,
    meatRuns,
    vegetableStreakBuckets: payload.vegetableStreakBuckets.map(bucket => ({
      ...bucket,
      runs: vegetableRuns.filter(run => run.length === bucket.length),
    })),
    meatStreakBuckets: payload.meatStreakBuckets.map(bucket => ({
      ...bucket,
      runs: meatRuns.filter(run => run.length === bucket.length),
    })),
  }
}

export const api = {
  accessSession: () => requestJson<AccessSession>('/api/access/session'),
  accessLogin: (key: string, deviceId: string, deviceName: string, rememberMe: boolean) =>
    requestJson<AccessSession>('/api/access/login', {
      method: 'POST',
      body: JSON.stringify({ key, deviceId, deviceName, rememberMe }),
    }),
  accessLogout: () => requestJson<void>('/api/access/logout', { method: 'POST' }),
  adminSession: () => requestJson<AdminSession>('/api/auth/session'),
  adminLogin: (username: string, password: string, rememberMe: boolean) =>
    requestJson<AdminSession>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password, rememberMe }),
    }),
  adminLogout: () => requestJson<void>('/api/auth/logout', { method: 'POST' }),
  visitors: () => requestJson<Visitor[]>('/api/admin/visitors?onlineSeconds=60'),
  accessKeys: () => requestJson<AccessKey[]>('/api/admin/access-keys'),
  createAccessKey: (label: string, validDays: number) =>
    requestJson<CreatedAccessKey>('/api/admin/access-keys', {
      method: 'POST',
      body: JSON.stringify({ label: label || null, validDays }),
    }),
  setAccessKeyActive: (id: number, isActive: boolean) =>
    requestJson<void>(`/api/admin/access-keys/${id}/active`, {
      method: 'PUT',
      body: JSON.stringify({ isActive }),
    }),
  disconnectAccessKey: (id: number) =>
    requestJson<void>(`/api/admin/access-keys/${id}/disconnect`, { method: 'POST' }),
  deleteAccessKey: (id: number) =>
    requestJson<void>(`/api/admin/access-keys/${id}`, { method: 'DELETE' }),
  stats: async (date: string) => expandCompactStats(
    await requestJson<CompactDailyStats>(`/api/stats/daily/compact?date=${queryDate(date)}`),
  ),
  streakBucket: (date: string, category: 'VEGETABLE' | 'MEAT', length: number) =>
    requestJson<import('./types').StreakBucket>(
      `/api/stats/streaks?date=${queryDate(date)}&category=${category}&length=${length}`,
    ),
  days: () => requestJson<DailySummary[]>('/api/stats/days?limit=2'),
  status: () => requestJson<ScannerStatus>('/api/scanner/status'),
  phoneControlStatus: () => requestJson<PhoneControlStatus>('/api/phone/control/status'),
  phoneScreenshot: () => requestBlob(`/api/phone/screenshot?ts=${Date.now()}`),
  phonePreviewStop: () => requestJson<void>('/api/phone/preview/stop', { method: 'POST' }),
  phoneTap: (x: number, y: number) =>
    requestJson<void>('/api/phone/tap', {
      method: 'POST',
      body: JSON.stringify({ x, y }),
    }),
  phoneSwipe: (startX: number, startY: number, endX: number, endY: number, durationMs: number) =>
    requestJson<void>('/api/phone/swipe', {
      method: 'POST',
      body: JSON.stringify({ startX, startY, endX, endY, durationMs }),
    }),
  phoneKey: (keyCode: number) =>
    requestJson<void>('/api/phone/key', {
      method: 'POST',
      body: JSON.stringify({ keyCode }),
    }),
  autoPlayStatus: () => requestJson<AutoPlayStatus>('/api/admin/autoplay/status'),
  saveAutoPlayConfiguration: (request: AutoPlayConfigurationRequest) =>
    requestJson<AutoPlayStatus>('/api/admin/autoplay/config', {
      method: 'PUT',
      body: JSON.stringify(request),
    }),
  emergencyStopAutoPlay: () =>
    requestJson<AutoPlayStatus>('/api/admin/autoplay/emergency-stop', { method: 'POST' }),
  results: (page: number, pageSize: number, date: string) =>
    requestJson<Page<ResultItem>>(
      `/api/results?page=${page}&pageSize=${pageSize}&date=${queryDate(date)}`,
    ),
  events: () => requestJson<Page<ScannerEvent>>('/api/scanner/events?page=1&pageSize=30'),
  acknowledgeEvent: (id: number) =>
    requestJson<void>(`/api/scanner/events/${id}/acknowledge`, { method: 'PATCH' }),
  alerts: () => requestJson<AlertDelivery[]>('/api/alerts'),
  alertWebhookConfig: () => requestJson<AlertWebhookConfig>('/api/alerts/webhook/config'),
  saveAlertWebhookConfig: (enabled: boolean, webhookUrl: string) =>
    requestJson<AlertWebhookConfig>('/api/alerts/webhook/config', {
      method: 'PUT',
      body: JSON.stringify({ enabled, webhookUrl }),
    }),
  prediction: (date: string) =>
    requestJson<Prediction>(`/api/predictions/next?date=${queryDate(date)}`),
  marketPrediction: (date: string) =>
    requestJson<MarketPredictionResponse>(`/api/predictions/market?date=${queryDate(date)}`),
  aiPrediction: (date: string, force = false) =>
    requestJson<AiPredictionResponse>(`/api/predictions/ai?date=${queryDate(date)}&force=${force}`),
  compoundPrediction: (date: string, force = false) =>
    requestJson<AiPredictionResponse>(`/api/predictions/compound?date=${queryDate(date)}&force=${force}`),
  paymentConfig: () => requestJson<PaymentConfig>('/api/payment/config'),
  subscribers: () => requestJson<Subscriber[]>('/api/subscribers'),
  registerSubscriber: (phoneNumber: string, displayName: string) =>
    requestJson<Subscriber>('/api/subscribers', {
      method: 'POST',
      body: JSON.stringify({ phoneNumber, displayName: displayName || null }),
    }),
  deactivateSubscriber: (id: number) =>
    requestJson<void>(`/api/subscribers/${id}`, { method: 'DELETE' }),
}
