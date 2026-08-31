import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import type {
  AiPredictionResponse,
  AdminSession,
  AlertDelivery,
  Category,
  DailyStats,
  DailySummary,
  Page,
  PaymentConfig,
  Prediction,
  ResultItem,
  ScannerEvent,
  ScannerStatus,
  StreakBucket,
  Subscriber,
} from './types'

const PAGE_SIZES = [30, 50, 100, 500, 1000, 2000, 5000]

const ITEM_META: Record<string, { name: string; icon: string; category: Category; payout?: number }> = {
  CA_ROT: { name: 'Cà rốt', icon: '🥕', category: 'VEGETABLE', payout: 5 },
  NGO: { name: 'Ngô', icon: '🌽', category: 'VEGETABLE', payout: 5 },
  CAI: { name: 'Cải', icon: '🥬', category: 'VEGETABLE', payout: 5 },
  CA_CHUA: { name: 'Cà chua', icon: '🍅', category: 'VEGETABLE', payout: 5 },
  BANH_MI: { name: 'Bánh mì', icon: '🌭', category: 'MEAT', payout: 10 },
  XIEN: { name: 'Xiên', icon: '🍢', category: 'MEAT', payout: 15 },
  DUI: { name: 'Đùi', icon: '🍗', category: 'MEAT', payout: 25 },
  BO: { name: 'Bò', icon: '🥩', category: 'MEAT', payout: 45 },
  PIZZA: { name: 'Nổ Pizza', icon: '🍕', category: 'SPECIAL' },
  SALAD: { name: 'Nổ Xà lách', icon: '🥗', category: 'SPECIAL' },
}

const ITEM_ORDER = ['CA_ROT', 'NGO', 'CAI', 'CA_CHUA', 'BANH_MI', 'XIEN', 'DUI', 'BO', 'PIZZA', 'SALAD']

const emptyPage: Page<ResultItem> = {
  items: [], page: 1, pageSize: 30, totalItems: 0, totalPages: 0,
}

function bangkokToday(): string {
  const businessNow = new Date(Date.now() + 60 * 60 * 1000)
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Bangkok', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(businessNow)
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

function formatDate(value: string | null): string {
  if (!value) return 'Chưa có'
  return new Intl.DateTimeFormat('vi-VN', {
    dateStyle: 'short', timeStyle: 'medium', timeZone: 'Asia/Bangkok',
  }).format(new Date(value))
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat('vi-VN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Bangkok',
  }).format(new Date(value))
}

function formatLocalDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return value
  const formatted = new Intl.DateTimeFormat('vi-VN', {
    weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
  }).format(new Date(Date.UTC(year, month - 1, day)))
  return formatted.charAt(0).toUpperCase() + formatted.slice(1)
}

function formatPaymentAmount(amount: number, currency: string): string {
  if (amount <= 0) return 'Theo nội dung QR'
  return new Intl.NumberFormat('vi-VN', {
    style: 'currency', currency: currency || 'VND', maximumFractionDigits: 0,
  }).format(amount)
}

function paymentTransferContent(config: PaymentConfig, subscriber: Subscriber): string {
  const suffix = subscriber.phoneNumber.replace(/\D/g, '').slice(-9)
  return `${config.transferPrefix || 'DK'} ${suffix}`.trim().toUpperCase()
}

function resolvePaymentQrUrl(config: PaymentConfig, subscriber: Subscriber): string {
  const content = paymentTransferContent(config, subscriber)
  return config.qrImageUrl
    .replaceAll('{phone}', encodeURIComponent(subscriber.phoneNumber))
    .replaceAll('{content}', encodeURIComponent(content))
    .replaceAll('{amount}', encodeURIComponent(String(config.amount)))
}

function categoryLabel(category: Category): string {
  if (category === 'VEGETABLE') return 'Rau'
  if (category === 'MEAT') return 'Thịt'
  return 'Nổ đặc biệt'
}

function Sequence({ values }: { values: string[] }) {
  if (values.length === 0) return <span className="muted">Chưa có dữ liệu 8 ô</span>
  return (
    <div className="sequence" aria-label="Chuỗi 8 kết quả mới nhất">
      {values.map((code, index) => {
        const item = ITEM_META[code] ?? { name: code, icon: '•', category: 'SPECIAL' as Category }
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
  label: string
  value: number
  tone: 'green' | 'red' | 'gold' | 'neutral'
  hint: string
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span className="metric-label">{label}</span>
      <strong>{value.toLocaleString('vi-VN')}</strong>
      <span className="metric-hint">{hint}</span>
    </article>
  )
}

function StreakBuckets({
  title,
  tone,
  buckets,
  onSelect,
}: {
  title: string
  tone: 'vegetable' | 'meat'
  buckets: StreakBucket[]
  onSelect: (bucket: StreakBucket) => void
}) {
  return (
    <div className={`streak-column ${tone}`}>
      <div className="streak-title"><span className={`rule-dot ${tone}`} /><strong>{title}</strong></div>
      <div className="bucket-grid">
        {buckets.map(bucket => (
          <button type="button" key={bucket.length} onClick={() => onSelect(bucket)}>
            <span>Bệt {bucket.length}</span>
            <strong>{bucket.count}</strong>
          </button>
        ))}
        {buckets.length === 0 && <span className="muted">Chưa có bệt từ 2 cầu.</span>}
      </div>
    </div>
  )
}

function PredictionResult({ prediction }: { prediction: Prediction }) {
  const leading = prediction.items[0]
  const itemVegetableProbability = prediction.items
    .filter(item => item.category === 'VEGETABLE')
    .reduce((total, item) => total + item.probabilityPercent, 0)
  const vegetableProbability = Number.isFinite(prediction.vegetableProbabilityPercent)
    ? prediction.vegetableProbabilityPercent
    : itemVegetableProbability
  const meatProbability = Number.isFinite(prediction.meatProbabilityPercent)
    ? prediction.meatProbabilityPercent
    : Math.max(0, 100 - vegetableProbability)
  return (
    <>
      {leading && (
        <div className={`prediction-highlight ${leading.category.toLowerCase()}`}>
          <div className="prediction-leader-icon">{ITEM_META[leading.itemCode]?.icon ?? '•'}</div>
          <div className="prediction-leader-copy">
            <span>LỰA CHỌN NỔI BẬT HIỆN TẠI</span>
            <strong>{leading.itemName}</strong>
            <small>{leading.reason}</small>
          </div>
          <div className="prediction-leader-metrics">
            <div className="primary"><span>Chỉ số dự đoán</span><strong>{leading.signalScore.toFixed(1)}/100</strong></div>
            <div><span>Xác suất thống kê</span><strong>{leading.probabilityPercent.toFixed(2)}%</strong></div>
            <div><span>Độ tin cậy mẫu</span><strong>{prediction.modelConfidencePercent.toFixed(1)}%</strong></div>
          </div>
        </div>
      )}
      <div className="category-forecast" aria-label="Xác suất cầu Rau và Thịt tiếp theo">
        <div className="category-forecast-title">
          <span>DỰ ĐOÁN CẦU BỆT TIẾP THEO</span>
          <small>Tổng hai nhóm = 100%</small>
        </div>
        <div className="category-forecast-grid">
          <article className="vegetable">
            <span><i /> Rau tiếp theo</span>
            <strong>{vegetableProbability.toFixed(2)}%</strong>
            <div><i style={{ width: `${vegetableProbability}%` }} /></div>
          </article>
          <article className="meat">
            <span><i /> Thịt tiếp theo</span>
            <strong>{meatProbability.toFixed(2)}%</strong>
            <div><i style={{ width: `${meatProbability}%` }} /></div>
          </article>
        </div>
      </div>
      <span className="mobile-swipe-hint">Vuốt ngang để xem đủ 8 vật phẩm →</span>
      <div className="prediction-grid">
        {prediction.items.map(item => {
          const meta = ITEM_META[item.itemCode]
          return (
            <article className={`prediction-item ${item.category.toLowerCase()} ${item.itemCode === prediction.leadingItemCode ? 'leading' : ''}`} key={item.itemCode}>
              <div className="prediction-row"><span>{meta?.icon} {item.itemName}</span><strong>{item.signalScore.toFixed(1)}/100</strong></div>
              <div className="signal-row"><span>Xác suất thống kê</span><strong>{item.probabilityPercent.toFixed(2)}%</strong></div>
              <div className="probability-track"><span style={{ width: `${Math.min(100, item.signalScore)}%` }} /></div>
              <small>Ăn ×{item.payoutMultiplier} · hôm nay {item.todayCount} · lịch sử {item.historicalCount}</small>
              <small className="prediction-reason">{item.reason}</small>
            </article>
          )
        })}
      </div>
      <p className="prediction-note">{prediction.methodNote}</p>
    </>
  )
}

function PredictionLoading({ label, tone = 'local' }: {
  label: string
  tone?: 'local' | 'ai' | 'compound'
}) {
  return (
    <div className={`prediction-loading-overlay ${tone}`} role="status" aria-live="polite">
      <span className="prediction-spinner" aria-hidden="true" />
      <div><strong>{label}</strong><small>Đang đọc và tính lại dữ liệu mới nhất...</small></div>
    </div>
  )
}

async function keepLoadingVisible(startedAt: number, minimumMilliseconds = 700) {
  const remaining = minimumMilliseconds - (Date.now() - startedAt)
  if (remaining > 0) await new Promise(resolve => window.setTimeout(resolve, remaining))
}

function App() {
  const [selectedDate, setSelectedDate] = useState(bangkokToday)
  const [stats, setStats] = useState<DailyStats | null>(null)
  const [days, setDays] = useState<DailySummary[]>([])
  const [status, setStatus] = useState<ScannerStatus | null>(null)
  const [results, setResults] = useState<Page<ResultItem>>(emptyPage)
  const [events, setEvents] = useState<ScannerEvent[]>([])
  const [alerts, setAlerts] = useState<AlertDelivery[]>([])
  const [prediction, setPrediction] = useState<Prediction | null>(null)
  const [predictionLoading, setPredictionLoading] = useState(false)
  const [predictionError, setPredictionError] = useState<string | null>(null)
  const [aiPredictionResponse, setAiPredictionResponse] = useState<AiPredictionResponse | null>(null)
  const [aiPredictionLoading, setAiPredictionLoading] = useState(false)
  const [aiPredictionError, setAiPredictionError] = useState<string | null>(null)
  const [compoundPredictionResponse, setCompoundPredictionResponse] = useState<AiPredictionResponse | null>(null)
  const [compoundPredictionLoading, setCompoundPredictionLoading] = useState(false)
  const [compoundPredictionError, setCompoundPredictionError] = useState<string | null>(null)
  const [subscribers, setSubscribers] = useState<Subscriber[]>([])
  const [paymentConfig, setPaymentConfig] = useState<PaymentConfig | null>(null)
  const [paymentSubscriber, setPaymentSubscriber] = useState<Subscriber | null>(null)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(30)
  const [customSize, setCustomSize] = useState('250')
  const [customMode, setCustomMode] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [selectedBucket, setSelectedBucket] = useState<{ category: Category; bucket: StreakBucket } | null>(null)
  const [phoneNumber, setPhoneNumber] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [subscriberMessage, setSubscriberMessage] = useState<string | null>(null)
  const [savingSubscriber, setSavingSubscriber] = useState(false)
  const [adminSession, setAdminSession] = useState<AdminSession | null>(null)
  const [adminLoginOpen, setAdminLoginOpen] = useState(false)
  const [adminUsername, setAdminUsername] = useState('admin')
  const [adminPassword, setAdminPassword] = useState('')
  const [adminAuthError, setAdminAuthError] = useState<string | null>(null)
  const [adminAuthLoading, setAdminAuthLoading] = useState(false)
  const predictionRequestId = useRef(0)
  const aiPredictionRequestId = useRef(0)
  const compoundPredictionRequestId = useRef(0)
  const refreshRealtime = useCallback(async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const [nextStats, nextStatus, nextResults] = await Promise.all([
        api.stats(selectedDate),
        api.status(),
        api.results(page, pageSize, selectedDate),
      ])
      setStats(nextStats)
      setStatus(nextStatus)
      setResults(nextResults)
      if (adminSession?.isAuthenticated) {
        try {
          const [nextEvents, nextAlerts] = await Promise.all([api.events(), api.alerts()])
          setEvents(nextEvents.items)
          setAlerts(nextAlerts)
        } catch {
          const nextSession = await api.adminSession()
          setAdminSession(nextSession)
          if (!nextSession.isAuthenticated) {
            setEvents([])
            setAlerts([])
          }
        }
      } else {
        setEvents([])
        setAlerts([])
      }
      setError(null)
      setLastRefresh(new Date())
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không thể tải dữ liệu')
    } finally {
      setLoading(false)
    }
  }, [adminSession?.isAuthenticated, page, pageSize, selectedDate])

  const refreshSecondary = useCallback(async () => {
    try {
      const [nextDays, nextSubscribers, nextPaymentConfig] = await Promise.all([
        api.days(), api.subscribers(), api.paymentConfig(),
      ])
      setDays(nextDays)
      setSubscribers(nextSubscribers)
      setPaymentConfig(nextPaymentConfig)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không thể tải dữ liệu mở rộng')
    }
  }, [selectedDate])

  const refreshPrediction = useCallback(async () => {
    const requestId = ++predictionRequestId.current
    const startedAt = Date.now()
    setPredictionLoading(true)
    setPredictionError(null)
    try {
      const response = await api.prediction(selectedDate)
      if (requestId === predictionRequestId.current) setPrediction(response)
    } catch (caught) {
      if (requestId === predictionRequestId.current) {
        setPredictionError(caught instanceof Error ? caught.message : 'Không thể tính dự đoán thống kê')
      }
    } finally {
      await keepLoadingVisible(startedAt)
      if (requestId === predictionRequestId.current) setPredictionLoading(false)
    }
  }, [selectedDate])

  const refreshAiPrediction = useCallback(async (force = false) => {
    const requestId = ++aiPredictionRequestId.current
    const startedAt = Date.now()
    setAiPredictionLoading(true)
    try {
      const response = await api.aiPrediction(selectedDate, force)
      if (requestId === aiPredictionRequestId.current) {
        setAiPredictionResponse(response)
        setAiPredictionError(null)
      }
    } catch (caught) {
      if (requestId === aiPredictionRequestId.current) {
        setAiPredictionError(caught instanceof Error ? caught.message : 'Không thể gọi dự đoán AI')
      }
    } finally {
      await keepLoadingVisible(startedAt)
      if (requestId === aiPredictionRequestId.current) setAiPredictionLoading(false)
    }
  }, [selectedDate])

  const refreshCompoundPrediction = useCallback(async (force = false) => {
    const requestId = ++compoundPredictionRequestId.current
    const startedAt = Date.now()
    setCompoundPredictionLoading(true)
    try {
      const response = await api.compoundPrediction(selectedDate, force)
      if (requestId === compoundPredictionRequestId.current) {
        setCompoundPredictionResponse(response)
        setCompoundPredictionError(null)
      }
    } catch (caught) {
      if (requestId === compoundPredictionRequestId.current) {
        setCompoundPredictionError(caught instanceof Error ? caught.message : 'Không thể gọi Compound Mini')
      }
    } finally {
      await keepLoadingVisible(startedAt)
      if (requestId === compoundPredictionRequestId.current) setCompoundPredictionLoading(false)
    }
  }, [selectedDate])

  useEffect(() => {
    void api.adminSession()
      .then(setAdminSession)
      .catch(() => setAdminSession({ isAuthenticated: false, isConfigured: false, username: null }))
  }, [])

  useEffect(() => {
    void refreshRealtime()
    const timer = window.setInterval(() => void refreshRealtime(true), 1500)
    return () => window.clearInterval(timer)
  }, [refreshRealtime])

  useEffect(() => {
    void refreshSecondary()
    const timer = window.setInterval(() => void refreshSecondary(), 10000)
    return () => window.clearInterval(timer)
  }, [refreshSecondary])

  const latestResultId = stats?.latestResult?.id ?? null
  const statsDate = stats?.localDate ?? null
  useEffect(() => {
    if (statsDate !== selectedDate) return
    void refreshPrediction()
  }, [latestResultId, refreshPrediction, selectedDate, statsDate])

  const unacknowledgedEvents = useMemo(
    () => events.filter(event => !event.acknowledged && ['ERROR', 'CRITICAL'].includes(event.severity)),
    [events],
  )
  const activeSubscribers = useMemo(
    () => subscribers.filter(subscriber => subscriber.isActive),
    [subscribers],
  )

  function changeDate(value: string) {
    setSelectedDate(value)
    setPage(1)
    setSelectedBucket(null)
    setAiPredictionResponse(null)
    setAiPredictionError(null)
    setCompoundPredictionResponse(null)
    setCompoundPredictionError(null)
  }

  function applyPageSize(value: number) {
    setPage(1)
    setPageSize(value)
  }

  function applyCustomSize() {
    const value = Number.parseInt(customSize, 10)
    if (Number.isFinite(value) && value >= 1 && value <= 10000) {
      applyPageSize(value)
    }
  }

  async function registerSubscriber(event: React.FormEvent) {
    event.preventDefault()
    setSavingSubscriber(true)
    try {
      const subscriber = await api.registerSubscriber(phoneNumber, displayName)
      setSubscriberMessage(`Đã ghi nhận ${subscriber.phoneNumber}. Vui lòng hoàn tất thanh toán.`)
      setPaymentSubscriber(subscriber)
      setPhoneNumber('')
      setDisplayName('')
      await refreshSecondary()
    } catch (caught) {
      setSubscriberMessage(caught instanceof Error ? caught.message : 'Đăng ký thất bại')
    } finally {
      setSavingSubscriber(false)
    }
  }

  async function deactivateSubscriber(id: number) {
    await api.deactivateSubscriber(id)
    await refreshSecondary()
  }

  async function acknowledgeEvent(id: number) {
    await api.acknowledgeEvent(id)
    await refreshRealtime(true)
  }

  async function loginAdmin(event: React.FormEvent) {
    event.preventDefault()
    setAdminAuthLoading(true)
    setAdminAuthError(null)
    try {
      const session = await api.adminLogin(adminUsername, adminPassword)
      setAdminSession(session)
      setAdminPassword('')
      setAdminLoginOpen(false)
    } catch (caught) {
      setAdminAuthError(caught instanceof Error ? caught.message : 'Đăng nhập Admin thất bại')
    } finally {
      setAdminAuthLoading(false)
    }
  }

  async function logoutAdmin() {
    try {
      await api.adminLogout()
    } finally {
      setAdminSession(current => ({
        isAuthenticated: false,
        isConfigured: current?.isConfigured ?? true,
        username: null,
      }))
      setEvents([])
      setAlerts([])
    }
  }

  const transferContent = paymentConfig && paymentSubscriber
    ? paymentTransferContent(paymentConfig, paymentSubscriber)
    : ''
  const paymentQrUrl = paymentConfig && paymentSubscriber && paymentConfig.isConfigured
    ? resolvePaymentQrUrl(paymentConfig, paymentSubscriber)
    : ''
  const aiPrediction = aiPredictionResponse?.prediction ?? null
  const compoundPrediction = compoundPredictionResponse?.prediction ?? null

  return (
    <div className="app-shell">
      <header className="topbar">
        <div>
          <p className="eyebrow">GREEDY BIGO · LIVE MONITOR</p>
          <h1>Thống kê cầu Rau / Thịt</h1>
        </div>
        <div className="topbar-actions">
          <div className={`admin-access ${adminSession?.isAuthenticated ? 'authenticated' : ''}`}>
            <span className="admin-access-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none"><path d="M12 3 5 6v5c0 4.7 2.9 8.3 7 10 4.1-1.7 7-5.3 7-10V6l-7-3Z" /><path d="m9.5 12 1.7 1.7 3.5-4" /></svg>
            </span>
            <span className="admin-access-copy">
              <small>{adminSession?.isAuthenticated ? 'Phiên bảo mật' : 'Khu vực riêng'}</small>
              <strong>{adminSession?.isAuthenticated ? adminSession.username : 'Quản trị viên'}</strong>
            </span>
            {adminSession?.isAuthenticated
              ? <button type="button" onClick={() => void logoutAdmin()}>Đăng xuất</button>
              : <button type="button" onClick={() => { setAdminAuthError(null); setAdminLoginOpen(true) }}>Đăng nhập</button>}
          </div>
          <label className="date-picker" htmlFor="statistics-date">
            <span className="date-picker-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none"><path d="M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z" /></svg>
            </span>
            <span className="date-picker-copy"><small>Ngày thống kê</small><strong>{formatLocalDate(selectedDate)}</strong></span>
            <span className="date-picker-chevron" aria-hidden="true">⌄</span>
            <input id="statistics-date" aria-label="Chọn ngày thống kê" type="date" value={selectedDate} onChange={event => changeDate(event.target.value)} />
          </label>
          <div className={`status-pill ${status?.isOnline ? 'online' : 'offline'}`}>
            <span className="status-dot" />
            <div>
              <strong>{status?.isOnline ? 'Scanner đang chạy' : 'Scanner ngoại tuyến'}</strong>
              <small>{adminSession?.isAuthenticated && status?.sourceSerial ? `${status.sourceSerial} · ` : ''}Round {status?.currentRound ?? '—'}</small>
            </div>
          </div>
        </div>
      </header>

      {error && <div className="error-banner">Không tải được backend: {error}</div>}
      {adminSession?.isAuthenticated && unacknowledgedEvents.length > 0 && (
        <div className="warning-banner">
          Có {unacknowledgedEvents.length} cảnh báo scanner chưa xác nhận. Xem mục “Sự kiện hệ thống”.
        </div>
      )}

      <main>
        <section className="metric-grid">
          <MetricCard label="Tổng Round trong ngày" value={stats?.roundCount ?? 0} tone="neutral" hint={`Đã nhận diện ${(stats?.totalResults ?? 0).toLocaleString('vi-VN')} · chốt lúc 23:00`} />
          <MetricCard label="Kèo scan miss / bảo trì" value={stats?.missedRoundCount ?? 0} tone="gold" hint={`Round ${(stats?.roundCount ?? 0).toLocaleString('vi-VN')} − đã scan ${(stats?.totalResults ?? 0).toLocaleString('vi-VN')}`} />
          <MetricCard label="Rau trong ngày" value={stats?.vegetableCount ?? 0} tone="green" hint="Cà rốt · Ngô · Cải · Cà chua" />
          <MetricCard label="Thịt trong ngày" value={stats?.meatCount ?? 0} tone="red" hint="Bánh mì · Xiên · Đùi · Bò" />
          <MetricCard label="Nổ đặc biệt" value={stats?.specialCount ?? 0} tone="gold" hint="Pizza · Xà lách" />
          <MetricCard label="Rau liên tục hiện tại" value={stats?.currentVegetableStreak ?? 0} tone="green" hint={`Dài nhất: ${stats?.longestVegetableStreak ?? 0}`} />
          <MetricCard label="Thịt liên tục hiện tại" value={stats?.currentMeatStreak ?? 0} tone="red" hint={`Dài nhất: ${stats?.longestMeatStreak ?? 0}`} />
        </section>

        <section className="panel latest-panel">
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">LỊCH SỬ NHẬN DIỆN</p>
              <h2>8 ô kết quả mới nhất · Round {status?.currentRound ?? '—'}</h2>
            </div>
            <div className="refresh-meta">
              <span>Tự làm mới: 1,5 giây</span>
              <strong>{lastRefresh ? lastRefresh.toLocaleTimeString('vi-VN') : '—'}</strong>
            </div>
          </div>
          <Sequence values={status?.lastSequence ?? []} />
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div><p className="panel-kicker">VẬT PHẨM THEO NGÀY</p><h2>Số lần xuất hiện từng loại</h2></div>
          </div>
          <div className="item-stat-grid">
            {ITEM_ORDER.map(code => {
              const meta = ITEM_META[code]
              return (
                <article className={`item-stat ${meta.category.toLowerCase()}`} key={code}>
                  <span className="item-stat-icon">{meta.icon}</span>
                  <div><strong>{(stats?.itemCounts[code] ?? 0).toLocaleString('vi-VN')}</strong><span>{meta.name}{meta.payout ? ` · ×${meta.payout}` : ''}</span></div>
                </article>
              )
            })}
          </div>
        </section>

        <section className="panel streak-panel">
          <div className="panel-heading">
            <div><p className="panel-kicker">THỐNG KÊ BỆT</p><h2>Số bệt theo độ dài trong ngày</h2></div>
            <span className="muted">Bấm vào từng mức để xem thời gian chi tiết</span>
          </div>
          <div className="streak-grid">
            <StreakBuckets title="Bệt Rau" tone="vegetable" buckets={stats?.vegetableStreakBuckets ?? []} onSelect={bucket => setSelectedBucket({ category: 'VEGETABLE', bucket })} />
            <StreakBuckets title="Bệt Thịt" tone="meat" buckets={stats?.meatStreakBuckets ?? []} onSelect={bucket => setSelectedBucket({ category: 'MEAT', bucket })} />
          </div>
        </section>

        <section className={`panel prediction-panel local-prediction-panel ${predictionLoading ? 'is-loading' : ''}`}>
          <div className="panel-heading">
            <div>
              <p className="panel-kicker">DỰ ĐOÁN NỘI BỘ</p>
              <h2>Phân tích toàn bộ database theo ngày</h2>
            </div>
            <div className="refresh-meta"><span>Provider</span><strong>{prediction?.provider ?? 'Đang tính...'}</strong></div>
          </div>
          {prediction ? <PredictionResult prediction={prediction} /> : <p className="prediction-note">Đang tải mô hình thống kê...</p>}
          {predictionError && !predictionLoading && <div className="prediction-inline-error">{predictionError}</div>}
          {predictionLoading && <PredictionLoading label="Đang tính dự đoán từ toàn bộ database" />}
        </section>

        <div className="ai-prediction-grid">
          <section className={`panel prediction-panel ai-prediction-panel ${aiPredictionLoading ? 'is-loading' : ''}`}>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker ai-kicker">DỰ ĐOÁN AI GPT</p>
                <h2>{aiPredictionResponse?.model ?? 'openai/gpt-oss-120b'} phân tích dữ liệu lịch sử</h2>
              </div>
              <div className="ai-prediction-actions">
                <div className="refresh-meta"><span>Provider</span><strong>{aiPrediction?.provider ?? aiPredictionResponse?.model ?? 'openai/gpt-oss-120b'}</strong></div>
                <button type="button" onClick={() => void refreshAiPrediction(Boolean(aiPrediction))} disabled={aiPredictionLoading}>{aiPredictionLoading ? 'AI đang phân tích...' : aiPrediction ? 'Phân tích lại' : 'Phân tích bằng AI'}</button>
              </div>
            </div>
            {aiPrediction && <PredictionResult prediction={aiPrediction} />}
            {!aiPrediction && (
              <div className={`ai-prediction-state ${(aiPredictionResponse?.status ?? 'LOADING').toLowerCase()}`}>
                <strong>{aiPredictionLoading ? 'Đang gửi dữ liệu lịch sử tới AI...' : aiPredictionResponse?.status === 'NOT_CONFIGURED' ? 'AI chưa được cấu hình API key' : 'Chưa có kết quả AI'}</strong>
                <span>{aiPredictionError || aiPredictionResponse?.message || 'Bấm “Phân tích bằng AI” khi bạn muốn Groq đọc toàn bộ database và tạo dự đoán mới. Bảng này không tự gọi lại theo mỗi kèo.'}</span>
                {aiPredictionResponse?.status === 'NOT_CONFIGURED' && <code>Prediction__AiApiKey=API_KEY_CỦA_BẠN</code>}
              </div>
            )}
            {aiPredictionLoading && <PredictionLoading tone="ai" label="GPT đang phân tích dữ liệu lịch sử" />}
          </section>

          <section className={`panel prediction-panel compound-prediction-panel ${compoundPredictionLoading ? 'is-loading' : ''}`}>
            <div className="panel-heading">
              <div>
                <p className="panel-kicker compound-kicker">DỰ ĐOÁN AI COMPOUND MINI</p>
                <h2>{compoundPredictionResponse?.model ?? 'groq/compound-mini'} phân tích dữ liệu lịch sử</h2>
              </div>
              <div className="ai-prediction-actions compound-actions">
                <div className="refresh-meta"><span>Provider</span><strong>{compoundPrediction?.provider ?? compoundPredictionResponse?.model ?? 'groq/compound-mini'}</strong></div>
                <button type="button" onClick={() => void refreshCompoundPrediction(Boolean(compoundPrediction))} disabled={compoundPredictionLoading}>{compoundPredictionLoading ? 'Compound đang phân tích...' : compoundPrediction ? 'Phân tích lại' : 'Phân tích bằng Compound'}</button>
              </div>
            </div>
            {compoundPrediction && <PredictionResult prediction={compoundPrediction} />}
            {!compoundPrediction && (
              <div className={`ai-prediction-state compound-state ${(compoundPredictionResponse?.status ?? 'LOADING').toLowerCase()}`}>
                <strong>{compoundPredictionLoading ? 'Đang gửi toàn bộ dữ liệu tới Compound Mini...' : compoundPredictionResponse?.status === 'NOT_CONFIGURED' ? 'Compound Mini chưa được cấu hình API key' : 'Chưa có kết quả Compound Mini'}</strong>
                <span>{compoundPredictionError || compoundPredictionResponse?.message || 'Bấm “Phân tích bằng Compound” để tạo một dự đoán độc lập. Kênh này dùng chung Groq API key nhưng không ghi đè bảng GPT.'}</span>
                {compoundPredictionResponse?.status === 'NOT_CONFIGURED' && <code>Prediction__AiApiKey=API_KEY_CỦA_BẠN</code>}
              </div>
            )}
            {compoundPredictionLoading && <PredictionLoading tone="compound" label="Compound Mini đang phân tích dữ liệu lịch sử" />}
          </section>
        </div>

        <section className="panel">
          <div className="panel-heading history-heading">
            <div>
              <p className="panel-kicker">DATABASE · {selectedDate}</p>
              <h2>Danh sách cầu</h2>
              <span className="muted">{results.totalItems.toLocaleString('vi-VN')} bản ghi · mặc định 30 dòng</span>
            </div>
            <div className="page-size-control">
              <label htmlFor="page-size">Số dòng</label>
              <select
                id="page-size"
                value={customMode ? 'custom' : String(pageSize)}
                onChange={event => {
                  if (event.target.value === 'custom') {
                    setCustomMode(true)
                    return
                  }
                  setCustomMode(false)
                  applyPageSize(Number(event.target.value))
                }}
              >
                {PAGE_SIZES.map(size => <option value={size} key={size}>{size}</option>)}
                <option value="custom">Tùy chỉnh</option>
              </select>
              {customMode && (
                <>
                  <input value={customSize} onChange={event => setCustomSize(event.target.value)} type="number" min="1" max="10000" aria-label="Số dòng tùy chỉnh" />
                  <button type="button" onClick={applyCustomSize}>Áp dụng</button>
                </>
              )}
            </div>
          </div>

          <span className="mobile-swipe-hint table-swipe-hint">Vuốt ngang để xem đầy đủ các cột →</span>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Round</th><th>Thời gian</th><th>Vật phẩm</th><th>Nhóm / bệt</th></tr></thead>
              <tbody>
                {results.items.map((result, index) => {
                  const meta = ITEM_META[result.itemCode]
                  const previous = results.items[index - 1]
                  const startsVisibleRun = index === 0 ||
                    previous.category !== result.category ||
                    previous.sourceSerial !== result.sourceSerial
                  const showStreak = result.category !== 'SPECIAL' && startsVisibleRun && result.streakLength >= 2
                  return (
                    <tr className={`${result.category.toLowerCase()}-row ${startsVisibleRun ? 'run-start' : ''}`} key={result.id}>
                      <td className="round-cell">{result.roundNumber ?? '—'}</td>
                      <td>{formatDate(result.detectedAtUtc)}</td>
                      <td><span className="item-name"><span>{meta?.icon ?? '•'}</span>{result.itemName}</span></td>
                      <td>
                        <span className={`category-badge ${result.category.toLowerCase()}`}>{categoryLabel(result.category)}</span>
                        {showStreak && <span className="streak-badge">({result.streakLength})</span>}
                      </td>
                    </tr>
                  )
                })}
                {!loading && results.items.length === 0 && <tr><td colSpan={4} className="empty-row">Chưa có kết quả trong ngày đã chọn.</td></tr>}
              </tbody>
            </table>
          </div>
          <div className="pagination">
            <button disabled={page <= 1} onClick={() => setPage(value => value - 1)}>← Trước</button>
            <span>Trang <strong>{results.page}</strong> / {Math.max(1, results.totalPages)}</span>
            <button disabled={results.totalPages === 0 || page >= results.totalPages} onClick={() => setPage(value => value + 1)}>Sau →</button>
          </div>
        </section>

        <section className="lower-grid history-grid">
          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">THEO NGÀY</p><h2>Lịch sử thống kê</h2></div></div>
            <div className="day-list">
              {days.slice(0, 2).map(day => (
                <button className={day.localDate === selectedDate ? 'active' : ''} type="button" key={day.localDate} onClick={() => changeDate(day.localDate)}>
                  <strong>{formatLocalDate(day.localDate)}</strong>
                  <span>{day.roundCount} Round · Đã scan {day.totalResults} · Miss {day.missedRoundCount}</span>
                  <span>Rau {day.vegetableCount} · Thịt {day.meatCount} · Nổ {day.specialCount}</span>
                  <small>Bệt dài nhất: Rau {day.longestVegetableStreak} · Thịt {day.longestMeatStreak}</small>
                </button>
              ))}
              {days.length === 0 && <p className="muted">Chưa có lịch sử theo ngày.</p>}
            </div>
          </article>

          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">ZALO WEBHOOK</p><h2>Đăng ký nhận cảnh báo</h2></div><span className="subscriber-count">{activeSubscribers.length} người</span></div>
            <form className="subscriber-form" onSubmit={registerSubscriber}>
              <input value={displayName} onChange={event => setDisplayName(event.target.value)} placeholder="Tên hiển thị (không bắt buộc)" />
              <input value={phoneNumber} onChange={event => setPhoneNumber(event.target.value)} placeholder="0912345678" required />
              <button type="submit" disabled={savingSubscriber}>{savingSubscriber ? 'Đang lưu...' : 'Đăng ký'}</button>
            </form>
            {subscriberMessage && <p className="form-message">{subscriberMessage}</p>}
            <div className="subscriber-list">
              {activeSubscribers.slice(0, 8).map(subscriber => (
                <div key={subscriber.id}>
                  <span><strong>{subscriber.displayName || 'Người dùng'}</strong><small>{subscriber.phoneNumber}</small></span>
                  <button type="button" onClick={() => void deactivateSubscriber(subscriber.id)}>Tắt</button>
                </div>
              ))}
            </div>
            <p className="muted webhook-hint">Đăng ký số điện thoại, thanh toán bằng QR và chờ quản trị viên thêm vào webhook cảnh báo chung.</p>
          </article>
        </section>

        {adminSession?.isAuthenticated && <section className="lower-grid admin-only-section">
          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">SCANNER</p><h2>Sự kiện hệ thống</h2></div><span className="muted">Lỗi sẽ gửi AdminWebhookUrl</span></div>
            <div className="event-list">
              {events.slice(0, 10).map(event => (
                <div className={`event-item ${event.severity.toLowerCase()}`} key={event.id}>
                  <div><strong>{event.eventCode}</strong><span>{event.message}</span></div>
                  <div className="event-actions"><time>{formatDate(event.occurredAtUtc)}</time>{!event.acknowledged && <button type="button" onClick={() => void acknowledgeEvent(event.id)}>Xác nhận</button>}</div>
                </div>
              ))}
              {events.length === 0 && <p className="muted">Chưa có sự kiện.</p>}
            </div>
          </article>

          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">WEBHOOK</p><h2>Cảnh báo chuỗi</h2></div></div>
            <div className="alert-rules">
              <div><span className="rule-dot vegetable" /><strong>15 Rau liên tục</strong><small>Gửi tới subscriber qua webhook</small></div>
              <div><span className="rule-dot meat" /><strong>3 Thịt liên tục</strong><small>Gửi tới subscriber qua webhook</small></div>
            </div>
            <div className="event-list alert-deliveries">
              {alerts.slice(0, 8).map(alert => (
                <div className="event-item" key={alert.id}>
                  <div><strong>{alert.ruleCode}</strong><span>Chuỗi {alert.streakLength} · {alert.status}</span></div>
                  <time>{formatDate(alert.createdAtUtc)}</time>
                </div>
              ))}
              {alerts.length === 0 && <p className="muted">Chưa chạm ngưỡng cảnh báo.</p>}
            </div>
          </article>
        </section>}
      </main>

      {adminSession?.isAuthenticated && (
        <footer>
          <span>Backend: 127.0.0.1:5117</span>
          <span>BlueStacks: {status?.sourceSerial ?? 'Đang kết nối...'}</span>
          <span>Heartbeat: {formatDate(status?.lastHeartbeatUtc ?? null)}</span>
        </footer>
      )}

      {paymentSubscriber && (
        <div className="modal-backdrop" role="presentation" onClick={() => setPaymentSubscriber(null)}>
          <section className="payment-modal" role="dialog" aria-modal="true" aria-labelledby="payment-title" onClick={event => event.stopPropagation()}>
            <div className="panel-heading payment-heading">
              <div><p className="panel-kicker">THANH TOÁN ĐĂNG KÝ</p><h2 id="payment-title">Quét QR để hoàn tất</h2></div>
              <button className="close-button" type="button" aria-label="Đóng" onClick={() => setPaymentSubscriber(null)}>×</button>
            </div>
            <div className="payment-layout">
              <div className={`payment-qr ${paymentQrUrl ? '' : 'unconfigured'}`}>
                {paymentQrUrl
                  ? <img src={paymentQrUrl} alt="Mã QR thanh toán đăng ký cảnh báo" />
                  : <div><strong>QR chưa được cấu hình</strong><span>Thêm Payment:QrImageUrl trong appsettings.json</span></div>}
              </div>
              <div className="payment-information">
                <div className="payment-subscriber">
                  <span>Thông tin đăng ký</span>
                  <strong>{paymentSubscriber.displayName || 'Người dùng'} · {paymentSubscriber.phoneNumber}</strong>
                </div>
                <dl>
                  <div><dt>Số tiền</dt><dd>{formatPaymentAmount(paymentConfig?.amount ?? 0, paymentConfig?.currency ?? 'VND')}</dd></div>
                  <div><dt>Ngân hàng</dt><dd>{paymentConfig?.bankName || 'Chưa cấu hình'}</dd></div>
                  <div><dt>Chủ tài khoản</dt><dd>{paymentConfig?.accountName || 'Chưa cấu hình'}</dd></div>
                  <div><dt>Số tài khoản</dt><dd>{paymentConfig?.accountNumber || 'Chưa cấu hình'}</dd></div>
                  <div className="transfer-content"><dt>Nội dung chuyển khoản</dt><dd>{transferContent || '—'}</dd></div>
                </dl>
                <div className="payment-notice">
                  <strong>Thông báo được gửi qua một webhook chung</strong>
                  <p>{paymentConfig?.instructions || 'Sau khi thanh toán, quản trị viên sẽ đối soát và thêm số điện thoại vào webhook chung.'}</p>
                  <p>Backend chỉ gọi webhook một lần để phát cảnh báo đồng loạt, không gọi riêng từng người.</p>
                </div>
                <button className="payment-finish" type="button" onClick={() => setPaymentSubscriber(null)}>Tôi đã quét QR · Chờ xác nhận</button>
              </div>
            </div>
          </section>
        </div>
      )}

      {adminLoginOpen && (
        <div className="modal-backdrop" role="presentation" onClick={() => setAdminLoginOpen(false)}>
          <section className="admin-login-modal" role="dialog" aria-modal="true" aria-labelledby="admin-login-title" onClick={event => event.stopPropagation()}>
            <div className="admin-login-heading">
              <span className="admin-login-shield" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none"><path d="M12 3 5 6v5c0 4.7 2.9 8.3 7 10 4.1-1.7 7-5.3 7-10V6l-7-3Z" /><path d="m9.5 12 1.7 1.7 3.5-4" /></svg>
              </span>
              <div><p className="panel-kicker">QUYỀN QUẢN TRỊ</p><h2 id="admin-login-title">Đăng nhập Admin</h2></div>
              <button className="close-button" type="button" aria-label="Đóng" onClick={() => setAdminLoginOpen(false)}>×</button>
            </div>
            <p className="admin-login-description">Sự kiện scanner và lịch sử gửi webhook chỉ hiển thị sau khi xác thực.</p>
            <form className="admin-login-form" onSubmit={loginAdmin}>
              <label><span>Tên đăng nhập</span><input autoComplete="username" value={adminUsername} onChange={event => setAdminUsername(event.target.value)} required /></label>
              <label><span>Mật khẩu</span><input type="password" autoComplete="current-password" value={adminPassword} onChange={event => setAdminPassword(event.target.value)} autoFocus required /></label>
              {adminAuthError && <p className="admin-login-error">{adminAuthError}</p>}
              {!adminSession?.isConfigured && adminSession !== null && <p className="admin-login-error">Backend chưa có tài khoản Admin.</p>}
              <button className="admin-login-submit" type="submit" disabled={adminAuthLoading || !adminSession?.isConfigured}>{adminAuthLoading ? 'Đang xác thực...' : 'Mở khu vực quản trị'}</button>
            </form>
          </section>
        </div>
      )}

      {selectedBucket && (
        <div className="modal-backdrop" role="presentation" onClick={() => setSelectedBucket(null)}>
          <section className="streak-modal" role="dialog" aria-modal="true" onClick={event => event.stopPropagation()}>
            <div className="panel-heading">
              <div><p className="panel-kicker">CHI TIẾT BỆT · {selectedDate}</p><h2>{categoryLabel(selectedBucket.category)} bệt {selectedBucket.bucket.length} ({selectedBucket.bucket.count} lần)</h2></div>
              <button className="close-button" type="button" onClick={() => setSelectedBucket(null)}>×</button>
            </div>
            <div className="run-detail-list">
              {selectedBucket.bucket.runs.map((run, index) => (
                <article key={`${run.startResultId}-${run.endResultId}`}>
                  <div className="run-detail-summary">
                    <strong>Lần {index + 1} · {formatTime(run.startedAtUtc)} – {formatTime(run.endedAtUtc)}</strong>
                    <span>Round {run.startRound ?? '—'} → {run.endRound ?? '—'} · bản ghi #{run.startResultId}–#{run.endResultId} · {run.items.length} cầu</span>
                  </div>
                  <div className="run-item-sequence">
                    {run.items.map((item, itemIndex) => {
                      const meta = ITEM_META[item.itemCode]
                      return (
                        <div className="run-item-detail" key={item.resultId}>
                          <span className="run-item-order">{itemIndex + 1}</span>
                          <span className="run-item-icon" aria-hidden="true">{meta?.icon ?? '•'}</span>
                          <div>
                            <strong>{item.itemName}</strong>
                            <small>Round {item.roundNumber ?? '—'} · {formatTime(item.detectedAtUtc)} · #{item.resultId}</small>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </article>
              ))}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}

export default App
