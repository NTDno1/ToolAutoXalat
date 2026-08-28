import type {
  AlertDelivery,
  Page,
  ResultItem,
  ScannerEvent,
  ScannerStatus,
  TodayStats,
} from './types'

const configuredBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${configuredBase}${path}`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
  })
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}: ${path}`)
  }
  return response.json() as Promise<T>
}

export const api = {
  stats: () => getJson<TodayStats>('/api/stats/today'),
  status: () => getJson<ScannerStatus>('/api/scanner/status'),
  results: (page: number, pageSize: number) =>
    getJson<Page<ResultItem>>(`/api/results?page=${page}&pageSize=${pageSize}`),
  events: () => getJson<Page<ScannerEvent>>('/api/scanner/events?page=1&pageSize=20'),
  alerts: () => getJson<AlertDelivery[]>('/api/alerts'),
}
