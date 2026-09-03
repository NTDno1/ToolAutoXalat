import type {
  AiPredictionResponse,
  AdminSession,
  AlertDelivery,
  DailyStats,
  DailySummary,
  Page,
  PaymentConfig,
  Prediction,
  ResultItem,
  ScannerEvent,
  ScannerStatus,
  StreakRun,
  Subscriber,
} from './types'

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${configuredBase}${path}`, {
    ...init,
    credentials: 'include',
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
    cache: 'no-store',
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const payload = await response.json() as { error?: string }
      if (payload.error) message = payload.error
    } catch {
      // Keep the HTTP error when the response has no JSON body.
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
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
  adminSession: () => requestJson<AdminSession>('/api/auth/session'),
  adminLogin: (username: string, password: string) =>
    requestJson<AdminSession>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),
  adminLogout: () => requestJson<void>('/api/auth/logout', { method: 'POST' }),
  stats: async (date: string) => expandCompactStats(
    await requestJson<CompactDailyStats>(`/api/stats/daily/compact?date=${queryDate(date)}`),
  ),
  streakBucket: (date: string, category: 'VEGETABLE' | 'MEAT', length: number) =>
    requestJson<import('./types').StreakBucket>(
      `/api/stats/streaks?date=${queryDate(date)}&category=${category}&length=${length}`,
    ),
  days: () => requestJson<DailySummary[]>('/api/stats/days?limit=2'),
  status: () => requestJson<ScannerStatus>('/api/scanner/status'),
  results: (page: number, pageSize: number, date: string) =>
    requestJson<Page<ResultItem>>(
      `/api/results?page=${page}&pageSize=${pageSize}&date=${queryDate(date)}`,
    ),
  events: () => requestJson<Page<ScannerEvent>>('/api/scanner/events?page=1&pageSize=30'),
  acknowledgeEvent: (id: number) =>
    requestJson<void>(`/api/scanner/events/${id}/acknowledge`, { method: 'PATCH' }),
  alerts: () => requestJson<AlertDelivery[]>('/api/alerts'),
  prediction: (date: string) =>
    requestJson<Prediction>(`/api/predictions/next?date=${queryDate(date)}`),
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
