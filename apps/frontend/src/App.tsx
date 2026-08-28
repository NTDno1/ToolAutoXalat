import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from './api'
import type {
  AlertDelivery,
  Category,
  Page,
  ResultItem,
  ScannerEvent,
  ScannerStatus,
  TodayStats,
} from './types'

const PAGE_SIZES = [50, 100, 500, 1000, 2000, 5000]

const ITEM_META: Record<string, { name: string; icon: string; category: Category }> = {
  BANH_MI: { name: 'Bánh mì', icon: '🌭', category: 'MEAT' },
  XIEN: { name: 'Xiên', icon: '🍢', category: 'MEAT' },
  DUI: { name: 'Đùi', icon: '🍗', category: 'MEAT' },
  BO: { name: 'Bò', icon: '🥩', category: 'MEAT' },
  CA_ROT: { name: 'Cà rốt', icon: '🥕', category: 'VEGETABLE' },
  NGO: { name: 'Ngô', icon: '🌽', category: 'VEGETABLE' },
  CAI: { name: 'Cải', icon: '🥬', category: 'VEGETABLE' },
  CA_CHUA: { name: 'Cà chua', icon: '🍅', category: 'VEGETABLE' },
}

const emptyPage: Page<ResultItem> = {
  items: [], page: 1, pageSize: 50, totalItems: 0, totalPages: 0,
}

function formatDate(value: string | null): string {
  if (!value) return 'Chưa có'
  return new Intl.DateTimeFormat('vi-VN', {
    dateStyle: 'short', timeStyle: 'medium', timeZone: 'Asia/Bangkok',
  }).format(new Date(value))
}

function categoryLabel(category: Category): string {
  return category === 'VEGETABLE' ? 'Rau' : 'Thịt'
}

function Sequence({ values }: { values: string[] }) {
  if (values.length === 0) return <span className="muted">Chưa có dữ liệu 8 ô</span>
  return (
    <div className="sequence" aria-label="Chuỗi 8 kết quả mới nhất">
      {values.map((code, index) => {
        const item = ITEM_META[code] ?? { name: code, icon: '•', category: 'VEGETABLE' as Category }
        return (
          <div className={`sequence-item ${item.category.toLowerCase()}`} key={`${code}-${index}`}>
            <span className="sequence-rank">{index === 0 ? 'Mới' : index + 1}</span>
            <span className="sequence-icon">{item.icon}</span>
            <span>{item.name}</span>
          </div>
        )
      })}
    </div>
  )
}

function MetricCard({ label, value, tone, hint }: {
  label: string; value: number; tone: 'green' | 'red' | 'neutral'; hint: string
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span className="metric-label">{label}</span>
      <strong>{value.toLocaleString('vi-VN')}</strong>
      <span className="metric-hint">{hint}</span>
    </article>
  )
}

function App() {
  const [stats, setStats] = useState<TodayStats | null>(null)
  const [status, setStatus] = useState<ScannerStatus | null>(null)
  const [results, setResults] = useState<Page<ResultItem>>(emptyPage)
  const [events, setEvents] = useState<ScannerEvent[]>([])
  const [alerts, setAlerts] = useState<AlertDelivery[]>([])
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [customSize, setCustomSize] = useState('250')
  const [customMode, setCustomMode] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)

  const refresh = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const [nextStats, nextStatus, nextResults, nextEvents, nextAlerts] = await Promise.all([
        api.stats(), api.status(), api.results(page, pageSize), api.events(), api.alerts(),
      ])
      setStats(nextStats)
      setStatus(nextStatus)
      setResults(nextResults)
      setEvents(nextEvents.items)
      setAlerts(nextAlerts)
      setError(null)
      setLastRefresh(new Date())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không thể tải dữ liệu')
    } finally {
      setLoading(false)
    }
  }, [page, pageSize])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(true), 3000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const unacknowledgedEvents = useMemo(
    () => events.filter(event => !event.acknowledged && ['ERROR', 'CRITICAL'].includes(event.severity)),
    [events],
  )

  function applyCustomSize() {
    const value = Number.parseInt(customSize, 10)
    if (Number.isFinite(value) && value >= 1 && value <= 10000) {
      setPage(1)
      setPageSize(value)
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">GREEDY BIGO · LIVE MONITOR</p>
          <h1>Na Thối Na Ngố</h1>
          <p className="subtitle">Tự động quét mỗi 3 giây · múi giờ Asia/Bangkok</p>
        </div>
        <div className={`status-pill ${status?.isOnline ? 'online' : 'offline'}`}>
          <span className="status-dot" />
          <div>
            <strong>{status?.isOnline ? 'Scanner đang chạy' : 'Scanner ngoại tuyến'}</strong>
            <small>{status?.status ?? 'Đang kết nối...'}</small>
          </div>
        </div>
      </header>

      {error && <div className="error-banner">Không tải được backend: {error}</div>}
      {unacknowledgedEvents.length > 0 && (
        <div className="warning-banner">
          Có {unacknowledgedEvents.length} cảnh báo scanner chưa xác nhận. Xem mục “Sự kiện hệ thống”.
        </div>
      )}

      <main>
        <section className="metric-grid">
          <MetricCard label="Tổng kèo hôm nay" value={stats?.totalResults ?? 0} tone="neutral" hint={stats?.localDate ?? '—'} />
          <MetricCard label="Rau hôm nay" value={stats?.vegetableCount ?? 0} tone="green" hint="Cà rốt · Ngô · Cải · Cà chua" />
          <MetricCard label="Thịt hôm nay" value={stats?.meatCount ?? 0} tone="red" hint="Bánh mì · Xiên · Đùi · Bò" />
          <MetricCard label="Rau liên tục hiện tại" value={stats?.currentVegetableStreak ?? 0} tone="green" hint={`Dài nhất: ${stats?.longestVegetableStreak ?? 0}`} />
          <MetricCard label="Thịt liên tục hiện tại" value={stats?.currentMeatStreak ?? 0} tone="red" hint={`Dài nhất: ${stats?.longestMeatStreak ?? 0}`} />
        </section>

        <section className="panel latest-panel">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">LỊCH SỬ NHẬN DIỆN</p>
              <h2>8 ô kết quả mới nhất</h2>
            </div>
            <div className="refresh-meta">
              <span>Tự làm mới: 3 giây</span>
              <strong>{lastRefresh ? lastRefresh.toLocaleTimeString('vi-VN') : '—'}</strong>
            </div>
          </div>
          <Sequence values={status?.lastSequence ?? []} />
        </section>

        <section className="panel">
          <div className="panel-heading history-heading">
            <div>
              <p className="panel-kicker">DATABASE</p>
              <h2>Danh sách cầu</h2>
              <span className="muted">{results.totalItems.toLocaleString('vi-VN')} bản ghi</span>
            </div>
            <div className="page-size-control">
              <label htmlFor="page-size">Số dòng</label>
              <select
                id="page-size"
                value={customMode ? 'custom' : pageSize}
                onChange={event => {
                  if (event.target.value === 'custom') {
                    setCustomMode(true)
                    return
                  }
                  setCustomMode(false)
                  setPage(1)
                  setPageSize(Number(event.target.value))
                }}
              >
                {PAGE_SIZES.map(size => <option value={size} key={size}>{size}</option>)}
                <option value="custom">Tùy chỉnh</option>
              </select>
              {customMode && (
                <>
                  <input
                    value={customSize}
                    onChange={event => setCustomSize(event.target.value)}
                    type="number"
                    min="1"
                    max="10000"
                    aria-label="Số dòng tùy chỉnh"
                  />
                  <button type="button" onClick={applyCustomSize}>Áp dụng</button>
                </>
              )}
            </div>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr><th>#</th><th>Thời gian</th><th>Vật phẩm</th><th>Nhóm</th><th>Độ tin cậy</th><th>Cách nhận diện</th></tr>
              </thead>
              <tbody>
                {results.items.map(result => {
                  const meta = ITEM_META[result.itemCode]
                  return (
                    <tr key={result.id}>
                      <td className="id-cell">{result.id}</td>
                      <td>{formatDate(result.detectedAtUtc)}</td>
                      <td><span className="item-name"><span>{meta?.icon ?? '•'}</span>{result.itemName}</span></td>
                      <td><span className={`category-badge ${result.category.toLowerCase()}`}>{categoryLabel(result.category)}</span></td>
                      <td>{(result.confidence * 100).toFixed(1)}%</td>
                      <td className="reason-cell">{result.detectionReason}</td>
                    </tr>
                  )
                })}
                {!loading && results.items.length === 0 && (
                  <tr><td colSpan={6} className="empty-row">Đang chờ kèo mới từ scanner...</td></tr>
                )}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <button disabled={page <= 1} onClick={() => setPage(value => value - 1)}>← Trước</button>
            <span>Trang <strong>{results.page}</strong> / {Math.max(1, results.totalPages)}</span>
            <button disabled={results.totalPages === 0 || page >= results.totalPages} onClick={() => setPage(value => value + 1)}>Sau →</button>
          </div>
        </section>

        <section className="lower-grid">
          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">SCANNER</p><h2>Sự kiện hệ thống</h2></div></div>
            <div className="event-list">
              {events.slice(0, 8).map(event => (
                <div className={`event-item ${event.severity.toLowerCase()}`} key={event.id}>
                  <div><strong>{event.eventCode}</strong><span>{event.message}</span></div>
                  <time>{formatDate(event.occurredAtUtc)}</time>
                </div>
              ))}
              {events.length === 0 && <p className="muted">Chưa có sự kiện.</p>}
            </div>
          </article>

          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">WEBHOOK</p><h2>Cảnh báo chuỗi</h2></div></div>
            <div className="alert-rules">
              <div><span className="rule-dot vegetable" /><strong>15 Rau liên tục</strong><small>Gọi API cấu hình sau</small></div>
              <div><span className="rule-dot meat" /><strong>3 Thịt liên tục</strong><small>Gọi API cấu hình sau</small></div>
            </div>
            <div className="event-list alert-deliveries">
              {alerts.slice(0, 5).map(alert => (
                <div className="event-item" key={alert.id}>
                  <div><strong>{alert.ruleCode}</strong><span>Chuỗi {alert.streakLength} · {alert.status}</span></div>
                  <time>{formatDate(alert.createdAtUtc)}</time>
                </div>
              ))}
              {alerts.length === 0 && <p className="muted">Chưa chạm ngưỡng cảnh báo.</p>}
            </div>
          </article>
        </section>
      </main>

      <footer>
        <span>Backend: 127.0.0.1:5117</span>
        <span>BlueStacks: 127.0.0.1:5575</span>
        <span>Heartbeat: {formatDate(status?.lastHeartbeatUtc ?? null)}</span>
      </footer>
    </div>
  )
}

export default App
