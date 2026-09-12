import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api'
import { ITEM_META, ITEM_ORDER } from './items'
import PlayDemo from './PlayDemo'
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
  StreakRun,
  Subscriber,
} from './types'

const MOBILE_RESULTS_BATCH_SIZE = 10
const PAGE_SIZES = [30, 50, 100, 500, 1000, 2000, 5000]
const STATUS_POLL_INTERVAL_MS = 250

type SelectedBucketState = {
  category: 'VEGETABLE' | 'MEAT'
  bucket: StreakBucket
  loading: boolean
  error: string | null
}

type ResultAnnouncement = {
  resultId: number
  round: number | null
  code: string
  verified: boolean
  corrected: boolean
}
const bangkokDatePartsFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Bangkok', year: 'numeric', month: '2-digit', day: '2-digit',
})
const dateTimeFormatter = new Intl.DateTimeFormat('vi-VN', {
  dateStyle: 'short', timeStyle: 'medium', timeZone: 'Asia/Bangkok',
})
const timeFormatter = new Intl.DateTimeFormat('vi-VN', {
  hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Asia/Bangkok',
})
const localDateFormatter = new Intl.DateTimeFormat('vi-VN', {
  weekday: 'long', day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC',
})

const emptyPage: Page<ResultItem> = {
  items: [], page: 1, pageSize: 30, totalItems: 0, totalPages: 0,
}

function bangkokToday(): string {
  const businessNow = new Date(Date.now() + 60 * 60 * 1000)
  const parts = bangkokDatePartsFormatter.formatToParts(businessNow)
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]))
  return `${value.year}-${value.month}-${value.day}`
}

function formatDate(value: string | null): string {
  if (!value) return 'Chưa có'
  return dateTimeFormatter.format(new Date(value))
}

function formatTime(value: string): string {
  return timeFormatter.format(new Date(value))
}

function formatLocalDate(value: string): string {
  const [year, month, day] = value.split('-').map(Number)
  if (!year || !month || !day) return value
  const formatted = localDateFormatter.format(new Date(Date.UTC(year, month - 1, day)))
  return formatted.charAt(0).toUpperCase() + formatted.slice(1)
}

function sameJsonValue<T>(current: T, next: T): T {
  return JSON.stringify(current) === JSON.stringify(next) ? current : next
}

function sameStatusDisplay(current: ScannerStatus | null, next: ScannerStatus, includeHeartbeat: boolean): boolean {
  if (!current) return false
  return current.status === next.status &&
    current.isOnline === next.isOnline &&
    current.lastResultId === next.lastResultId &&
    current.lastResultRevision === next.lastResultRevision &&
    current.offlineAfterSeconds === next.offlineAfterSeconds &&
    current.sourceSerial === next.sourceSerial &&
    current.currentRound === next.currentRound &&
    current.activeRound === next.activeRound &&
    JSON.stringify(current.bettingSignals) === JSON.stringify(next.bettingSignals) &&
    (!includeHeartbeat || current.lastHeartbeatUtc === next.lastHeartbeatUtc) &&
    current.lastSequence.length === next.lastSequence.length &&
    current.lastSequence.every((value, index) => value === next.lastSequence[index])
}

function RealtimeClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return <strong>{timeFormatter.format(now)}</strong>
}

function NextResultCountdown({
  status,
  liveStatus,
}: {
  status: ScannerStatus | null
  liveStatus: React.MutableRefObject<ScannerStatus | null>
}) {
  const serverOffsetMs = useRef(0)
  const lastServerUtc = useRef('')
  const [nowMs, setNowMs] = useState(Date.now)

  useEffect(() => {
    const update = () => {
      const snapshot = liveStatus.current ?? status
      if (snapshot?.serverUtc && snapshot.serverUtc !== lastServerUtc.current) {
        const serverMs = Date.parse(snapshot.serverUtc)
        if (Number.isFinite(serverMs)) {
          serverOffsetMs.current = serverMs - Date.now()
          lastServerUtc.current = snapshot.serverUtc
        }
      }
      setNowMs(Date.now() + serverOffsetMs.current)
    }
    update()
    const timer = window.setInterval(update, 250)
    return () => window.clearInterval(timer)
  }, [liveStatus, status])

  const snapshot = liveStatus.current ?? status
  const observedAtMs = Date.parse(snapshot?.countdownObservedAtUtc ?? '')
  const hasScreenTime = snapshot?.countdownSeconds !== null &&
    snapshot?.countdownSeconds !== undefined &&
    Number.isFinite(observedAtMs)
  const elapsedSeconds = hasScreenTime
    ? Math.max(0, Math.floor((nowMs - observedAtMs) / 1000))
    : 0
  const remainingSeconds = hasScreenTime
    ? Math.max(0, Math.min(30, snapshot.countdownSeconds! - elapsedSeconds))
    : 0
  const waiting = hasScreenTime && remainingSeconds === 0
  const timeText = hasScreenTime ? `00:${String(remainingSeconds).padStart(2, '0')}` : '--:--'
  const progress = hasScreenTime ? ((30 - remainingSeconds) / 30) * 100 : 0
  const nextRound = snapshot?.activeRound ?? (
    snapshot?.currentRound === null || snapshot?.currentRound === undefined
      ? null
      : snapshot.currentRound + 1
  )

  return (
    <section
      className={`next-result-countdown ${snapshot?.isOnline ? 'online' : 'offline'} ${waiting ? 'waiting' : ''}`}
      aria-live="polite"
      data-countdown-source="screen-ocr"
    >
      <div
        className="countdown-ring"
        style={{ '--countdown-progress': `${progress * 3.6}deg` } as React.CSSProperties}
        aria-hidden="true"
      >
        <span>{timeText}</span>
      </div>
      <div className="countdown-copy">
        <p className="panel-kicker">CẦU TIẾP THEO</p>
        <h2>
          {!snapshot?.isOnline
            ? 'Đang chờ scanner kết nối lại'
            : !hasScreenTime
              ? 'Đang đọc thời gian trên ứng dụng'
              : waiting
                ? 'Đang nhận kết quả mới'
                : `Cầu tiếp theo sau ${remainingSeconds} giây`}
        </h2>
        <p>Đọc trực tiếp số giây giữa màn hình ứng dụng · tối đa 30 giây</p>
      </div>
      <div className="countdown-round">
        <small>Dự kiến</small>
        <strong>Round {nextRound || '—'}</strong>
        <span>{waiting ? 'Sắp cập nhật lên web' : 'Đồng bộ trực tiếp từ app'}</span>
      </div>
      <div className="countdown-progress" aria-hidden="true">
        <span style={{ width: `${progress}%` }} />
      </div>
    </section>
  )
}

function LiveBettingSignals({ status }: { status: ScannerStatus | null }) {
  const signals = status?.bettingSignals
  if (!status?.isOnline || !signals) return null
  const byCode = new Map(signals.items.map(item => [item.itemCode, item]))
  const codes = [
    'BANH_MI', 'CA_CHUA', 'XIEN', 'CAI',
    'DUI', 'NGO', 'BO', 'CA_ROT',
  ]
  const hotMeta = signals.hotItemCode ? ITEM_META[signals.hotItemCode] : null

  return (
    <section className="live-betting-signals" aria-live="polite">
      <div className="live-betting-heading">
        <div>
          <p className="panel-kicker">TÍN HIỆU CƯỢC TRỰC TIẾP</p>
          <h2>Hot và mức người đặt theo màn hình</h2>
        </div>
        <div className={`live-hot-summary ${hotMeta ? 'active' : ''}`}>
          <span>HOT</span>
          <strong>{hotMeta ? `${hotMeta.icon} ${hotMeta.name}` : 'Đang quét'}</strong>
        </div>
      </div>
      <div className="live-betting-grid">
        {codes.map(code => {
          const meta = ITEM_META[code]
          const signal = byCode.get(code)
          const coinCount = signal?.coinCount ?? 0
          const isHot = signals.hotItemCode === code
          return (
            <div className={`live-betting-item ${isHot ? 'hot' : ''}`} key={code}>
              {isHot && <span className="live-hot-badge">HOT</span>}
              <span className="live-betting-icon" aria-hidden="true">{meta.icon}</span>
              <strong>{meta.name}</strong>
              <small>Mức đặt: {coinCount} xu</small>
              <div className="live-coin-row" aria-label={`${coinCount} đồng xu`}>
                {[0, 1, 2].map(index => (
                  <i className={index < coinCount ? 'active' : ''} key={index}>●</i>
                ))}
              </div>
            </div>
          )
        })}
      </div>
      <p className="live-betting-note">
        Round {signals.round ?? '—'} · {signals.countdownSeconds}s lúc quét · số xu được đọc trực tiếp dưới từng cửa cược
      </p>
    </section>
  )
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

function effectiveStreakCategory(result: Pick<ResultItem, 'itemCode' | 'category'>): Category {
  if (result.itemCode === 'SALAD') return 'VEGETABLE'
  if (result.itemCode === 'PIZZA') return 'MEAT'
  return result.category
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

function ResultAnnouncementPopup({
  announcement,
  onClose,
}: {
  announcement: ResultAnnouncement
  onClose: () => void
}) {
  const item = ITEM_META[announcement.code] ?? {
    name: announcement.code,
    icon: '•',
    category: 'SPECIAL' as Category,
  }
  const resultName = item.name.startsWith('Nổ ') ? item.name : `Nổ ${item.name}`
  const popupRef = useRef<HTMLElement>(null)

  useEffect(() => {
    const closeWhenClickingOutside = (event: PointerEvent) => {
      if (popupRef.current && !popupRef.current.contains(event.target as Node)) onClose()
    }
    document.addEventListener('pointerdown', closeWhenClickingOutside)
    return () => document.removeEventListener('pointerdown', closeWhenClickingOutside)
  }, [onClose])

  return (
    <aside
      ref={popupRef}
      className={`result-announcement ${item.category.toLowerCase()} ${announcement.verified ? 'verified' : 'pending'} ${announcement.corrected ? 'corrected' : ''}`}
      role="status"
      aria-live="assertive"
      aria-atomic="true"
    >
      <button type="button" onClick={onClose} aria-label="Đóng thông báo kết quả">×</button>
      <div className="result-announcement-icon" aria-hidden="true">{item.icon}</div>
      <div className="result-announcement-copy">
        <small>{announcement.corrected ? 'KẾT QUẢ ĐÃ ĐƯỢC SỬA' : 'CẦU MỚI VỀ'}</small>
        <strong>{resultName}</strong>
        <span>
          Round {announcement.round ?? '—'} · {announcement.verified
            ? 'Đã đối chiếu đủ 8 cầu'
            : 'Đang hậu kiểm 8 cầu'}
        </span>
      </div>
    </aside>
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

function RunConversation({
  runs,
  progressive = false,
  expandable = false,
  loadRunDetails,
}: {
  runs: StreakRun[]
  progressive?: boolean
  expandable?: boolean
  loadRunDetails?: (run: StreakRun) => Promise<StreakRun>
}) {
  const initialLimit = progressive ? Math.min(30, runs.length) : runs.length
  const [visibleCount, setVisibleCount] = useState(initialLimit)
  const [expandedRun, setExpandedRun] = useState<string | null>(null)
  const [runDetails, setRunDetails] = useState<Record<string, {
    run: StreakRun | null
    loading: boolean
    error: string | null
  }>>({})
  const firstRunId = runs[0]?.endResultId ?? 0
  const lastRunId = runs[runs.length - 1]?.startResultId ?? 0

  useEffect(() => {
    setVisibleCount(progressive ? Math.min(30, runs.length) : runs.length)
    setExpandedRun(current => current && runs.some(run => (
      `${run.startResultId}-${run.endResultId}` === current
    )) ? current : null)
  }, [firstRunId, lastRunId, progressive, runs.length])

  if (runs.length === 0) {
    return <p className="muted streak-chat-empty">Chưa có nhóm bệt trong khoảng đã chọn.</p>
  }

  const visibleRuns = progressive ? runs.slice(0, visibleCount) : runs

  function loadMoreWhenNeeded(event: React.UIEvent<HTMLDivElement>) {
    if (!progressive || visibleCount >= runs.length) return
    const element = event.currentTarget
    if (element.scrollHeight - element.scrollTop - element.clientHeight <= 120) {
      setVisibleCount(current => Math.min(runs.length, current + 30))
    }
  }

  async function fetchRunDetails(run: StreakRun, runKey: string) {
    if (!loadRunDetails) return
    setRunDetails(current => ({
      ...current,
      [runKey]: { run: null, loading: true, error: null },
    }))
    try {
      const detailedRun = await loadRunDetails(run)
      setRunDetails(current => ({
        ...current,
        [runKey]: { run: detailedRun, loading: false, error: null },
      }))
    } catch (caught) {
      setRunDetails(current => ({
        ...current,
        [runKey]: {
          run: null,
          loading: false,
          error: caught instanceof Error ? caught.message : 'Không thể tải chi tiết nhóm bệt',
        },
      }))
    }
  }

  async function toggleRun(run: StreakRun, runKey: string) {
    if (!expandable) return
    if (expandedRun === runKey) {
      setExpandedRun(null)
      return
    }
    setExpandedRun(runKey)
    const cached = runDetails[runKey]
    if (run.items.length > 0 || cached?.run || !loadRunDetails) return
    await fetchRunDetails(run, runKey)
  }

  return (
    <div
      className={`streak-chat ${progressive ? 'progressive' : ''} ${expandable && !progressive ? 'expandable' : ''} ${expandable ? 'can-expand' : ''}`}
      aria-label="Tóm tắt các nhóm bệt"
      onScroll={loadMoreWhenNeeded}
    >
      {visibleRuns.map(run => {
        const runKey = `${run.startResultId}-${run.endResultId}`
        const expanded = expandable && expandedRun === runKey
        const detailState = runDetails[runKey]
        const detailedRun = detailState?.run ?? run
        return (
          <div className={`streak-chat-entry ${run.category === 'MEAT' ? 'meat' : 'vegetable'} ${expanded ? 'expanded' : ''}`} key={runKey}>
            <button
              type="button"
              className={`streak-chat-bubble ${run.category === 'MEAT' ? 'meat' : 'vegetable'}`}
              aria-expanded={expandable ? expanded : undefined}
              aria-controls={expandable ? `run-detail-${runKey}` : undefined}
              onClick={expandable ? () => void toggleRun(run, runKey) : undefined}
            >
              <span className="streak-chat-title-row">
                <strong>{categoryLabel(run.category)}: {run.length}</strong>
                {expandable && <span className="streak-chat-action">{expanded ? 'Thu gọn' : 'Xem chi tiết'} <i aria-hidden="true">⌄</i></span>}
              </span>
              <span className="streak-chat-meta">
                <span>Round {run.startRound ?? '—'} → {run.endRound ?? '—'}</span>
                <span>{formatTime(run.startedAtUtc)} → {formatTime(run.endedAtUtc)}</span>
              </span>
            </button>
            {expanded && (
              <div className="run-inline-detail" id={`run-detail-${runKey}`}>
                {detailState?.loading ? (
                  <div className="run-inline-loading" role="status">
                    <span className="prediction-spinner" aria-hidden="true" />
                    <span>Đang tải từng cầu trong chuỗi...</span>
                  </div>
                ) : detailState?.error ? (
                  <div className="run-inline-error">
                    <span>{detailState.error}</span>
                    <button type="button" onClick={() => void fetchRunDetails(run, runKey)}>Thử lại</button>
                  </div>
                ) : (
                  <>
                    <div className="run-inline-detail-heading">
                      <strong>{detailedRun.items.length} cầu trong chuỗi</strong>
                      <span>Round và thời gian của từng cầu</span>
                    </div>
                    <div className="run-item-sequence">
                      {detailedRun.items.map((item, index) => {
                        const meta = ITEM_META[item.itemCode]
                        return (
                          <div className="run-item-detail" key={item.resultId}>
                            <span className="run-item-order">{index + 1}</span>
                            <span className="run-item-icon" aria-hidden="true">{meta?.icon ?? '•'}</span>
                            <div>
                              <strong>{item.itemName}</strong>
                              <small>Round {item.roundNumber ?? '—'} · {formatTime(item.detectedAtUtc)}</small>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )
      })}
      {progressive && visibleCount < runs.length && (
        <div className="streak-chat-more">Cuộn xuống để xem thêm · {runs.length - visibleCount} nhóm</div>
      )}
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
  const [latestResults, setLatestResults] = useState<ResultItem[]>([])
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
  const [resultsLimit, setResultsLimit] = useState(MOBILE_RESULTS_BATCH_SIZE)
  const [mobileResults, setMobileResults] = useState(() => window.matchMedia('(max-width: 760px)').matches)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [resultAnnouncement, setResultAnnouncement] = useState<ResultAnnouncement | null>(null)
  const [playDemoOpen, setPlayDemoOpen] = useState(false)
  const [selectedBucket, setSelectedBucket] = useState<SelectedBucketState | null>(null)
  const [streakSummaryOpen, setStreakSummaryOpen] = useState(false)
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
  const statsRequestId = useRef(0)
  const resultsRequestId = useRef(0)
  const bucketRequestId = useRef(0)
  const statusTracker = useRef<{
    initialized: boolean
    lastResultId: number | null
    lastResultRevision: string | null
    lastResultCode: string | null
  }>({ initialized: false, lastResultId: null, lastResultRevision: null, lastResultCode: null })
  const liveStatusRef = useRef<ScannerStatus | null>(null)
  const lastHeartbeatCommit = useRef(0)
  const resultAnnouncementTimer = useRef<number | undefined>(undefined)
  const statisticsDateInputRef = useRef<HTMLInputElement>(null)
  const openingDatePicker = useRef(false)
  const resultsScrollRef = useRef<HTMLDivElement>(null)
  const resultsLoadMoreRef = useRef<HTMLDivElement>(null)

  const showResultAnnouncement = useCallback((announcement: ResultAnnouncement) => {
    if (resultAnnouncementTimer.current !== undefined) {
      window.clearTimeout(resultAnnouncementTimer.current)
    }
    setResultAnnouncement(announcement)
    resultAnnouncementTimer.current = window.setTimeout(() => {
      setResultAnnouncement(null)
      resultAnnouncementTimer.current = undefined
    }, 8000)
  }, [])

  useEffect(() => () => {
    if (resultAnnouncementTimer.current !== undefined) {
      window.clearTimeout(resultAnnouncementTimer.current)
    }
  }, [])

  const refreshStats = useCallback(async (deferred = false) => {
    const requestId = ++statsRequestId.current
    try {
      const nextStats = await api.stats(selectedDate)
      if (requestId !== statsRequestId.current) return
      if (deferred) startTransition(() => setStats(nextStats))
      else setStats(nextStats)
      setError(null)
    } catch (caught) {
      if (requestId === statsRequestId.current) {
        setError(caught instanceof Error ? caught.message : 'Không thể tải thống kê')
      }
    }
  }, [selectedDate])

  const refreshResults = useCallback(async (silent = false) => {
    const requestId = ++resultsRequestId.current
    if (!silent) setLoading(true)
    try {
      const requestedPage = mobileResults ? 1 : page
      const requestedSize = mobileResults ? resultsLimit : pageSize
      const [nextResults, firstPage] = await Promise.all([
        api.results(requestedPage, requestedSize, selectedDate),
        requestedPage === 1 && requestedSize >= 8
          ? Promise.resolve(null)
          : api.results(1, 8, selectedDate),
      ])
      if (requestId !== resultsRequestId.current) return
      // The latest result is the primary realtime surface. Commit it
      // immediately; deferring this large table update can visibly trail the
      // already completed status request on slower phones.
      setResults(nextResults)
      setLatestResults((firstPage ?? nextResults).items.slice(0, 8))
      setError(null)
    } catch (caught) {
      if (requestId === resultsRequestId.current) {
        setError(caught instanceof Error ? caught.message : 'Không thể tải danh sách cầu')
      }
    } finally {
      if (requestId === resultsRequestId.current) setLoading(false)
    }
  }, [mobileResults, page, pageSize, resultsLimit, selectedDate])

  const refreshSecondary = useCallback(async () => {
    try {
      const [nextDays, nextSubscribers, nextPaymentConfig] = await Promise.all([
        api.days(), api.subscribers(), api.paymentConfig(),
      ])
      setDays(current => sameJsonValue(current, nextDays))
      setSubscribers(current => sameJsonValue(current, nextSubscribers))
      setPaymentConfig(current => sameJsonValue(current, nextPaymentConfig))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Không thể tải dữ liệu mở rộng')
    }
  }, [])

  const refreshAdminData = useCallback(async () => {
    if (!adminSession?.isAuthenticated) {
      setEvents(current => current.length === 0 ? current : [])
      setAlerts(current => current.length === 0 ? current : [])
      return
    }
    try {
      const [nextEvents, nextAlerts] = await Promise.all([api.events(), api.alerts()])
      setEvents(current => sameJsonValue(current, nextEvents.items))
      setAlerts(current => sameJsonValue(current, nextAlerts))
    } catch {
      const nextSession = await api.adminSession()
      setAdminSession(nextSession)
      if (!nextSession.isAuthenticated) {
        setEvents([])
        setAlerts([])
      }
    }
  }, [adminSession?.isAuthenticated])

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
    void refreshStats()
  }, [refreshStats])

  useEffect(() => {
    void refreshResults()
  }, [refreshResults])

  useEffect(() => {
    void refreshSecondary()
    const timer = window.setInterval(() => void refreshSecondary(), 10000)
    return () => window.clearInterval(timer)
  }, [refreshSecondary])

  useEffect(() => {
    void refreshAdminData()
    if (!adminSession?.isAuthenticated) return
    const timer = window.setInterval(() => void refreshAdminData(), 3000)
    return () => window.clearInterval(timer)
  }, [adminSession?.isAuthenticated, refreshAdminData])

  useEffect(() => {
    let stopped = false
    let timer: number | undefined
    let polling = false

    async function pollStatus() {
      if (stopped || polling) return
      if (document.visibilityState === 'hidden') {
        timer = window.setTimeout(pollStatus, STATUS_POLL_INTERVAL_MS)
        return
      }
      polling = true
      const startedAt = performance.now()
      try {
        const nextStatus = await api.status()
        if (stopped) return
        liveStatusRef.current = nextStatus

        const tracker = statusTracker.current
        const nextResultCode = nextStatus.lastSequence[0] ?? null
        const isNewResult = tracker.initialized &&
          tracker.lastResultId !== nextStatus.lastResultId &&
          nextStatus.lastResultId !== null
        const isResultRevision = tracker.initialized &&
          tracker.lastResultId === nextStatus.lastResultId &&
          tracker.lastResultRevision !== nextStatus.lastResultRevision
        const previousResultCode = tracker.lastResultCode
        const resultChanged = tracker.initialized && (
          tracker.lastResultId !== nextStatus.lastResultId ||
          tracker.lastResultRevision !== nextStatus.lastResultRevision
        )
        tracker.initialized = true
        tracker.lastResultId = nextStatus.lastResultId
        tracker.lastResultRevision = nextStatus.lastResultRevision
        tracker.lastResultCode = nextResultCode

        if (isNewResult && nextResultCode) {
          showResultAnnouncement({
            resultId: nextStatus.lastResultId!,
            round: nextStatus.currentRound,
            code: nextResultCode,
            verified: false,
            corrected: false,
          })
        } else if (isResultRevision && nextStatus.lastResultId !== null && nextResultCode) {
          const corrected = previousResultCode !== null && previousResultCode !== nextResultCode
          if (corrected) {
            showResultAnnouncement({
              resultId: nextStatus.lastResultId,
              round: nextStatus.currentRound,
              code: nextResultCode,
              verified: true,
              corrected: true,
            })
          } else {
            setResultAnnouncement(current => current?.resultId === nextStatus.lastResultId
              ? { ...current, verified: true }
              : current)
          }
        }

        const now = Date.now()
        const includeHeartbeat = Boolean(adminSession?.isAuthenticated) && now - lastHeartbeatCommit.current >= 5000
        setStatus(current => {
          if (sameStatusDisplay(current, nextStatus, includeHeartbeat)) return current
          if (includeHeartbeat) lastHeartbeatCommit.current = now
          return nextStatus
        })

        if (resultChanged && selectedDate === bangkokToday()) {
          void refreshStats(true)
          void refreshResults(true)
          void refreshSecondary()
        }
      } catch (caught) {
        if (!stopped) setError(caught instanceof Error ? caught.message : 'Không thể đọc trạng thái scanner')
      } finally {
        polling = false
        if (!stopped) {
          const elapsed = performance.now() - startedAt
          timer = window.setTimeout(
            pollStatus,
            Math.max(50, STATUS_POLL_INTERVAL_MS - elapsed),
          )
        }
      }
    }

    function resumeWhenVisible() {
      if (document.visibilityState !== 'visible' || polling) return
      if (timer !== undefined) window.clearTimeout(timer)
      void pollStatus()
    }

    void pollStatus()
    document.addEventListener('visibilitychange', resumeWhenVisible)
    return () => {
      stopped = true
      if (timer !== undefined) window.clearTimeout(timer)
      document.removeEventListener('visibilitychange', resumeWhenVisible)
    }
  }, [adminSession?.isAuthenticated, refreshResults, refreshSecondary, refreshStats, selectedDate, showResultAnnouncement])

  useEffect(() => {
    const query = window.matchMedia('(max-width: 760px)')
    function switchResultsMode(event: MediaQueryListEvent) {
      setMobileResults(event.matches)
      setPage(1)
      setResultsLimit(MOBILE_RESULTS_BATCH_SIZE)
      if (resultsScrollRef.current) resultsScrollRef.current.scrollTop = 0
    }
    query.addEventListener('change', switchResultsMode)
    return () => query.removeEventListener('change', switchResultsMode)
  }, [])

  useEffect(() => {
    const root = resultsScrollRef.current
    const target = resultsLoadMoreRef.current
    if (!mobileResults || !root || !target || loading || results.items.length >= results.totalItems) return

    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return
      setResultsLimit(current => Math.min(results.totalItems, current + MOBILE_RESULTS_BATCH_SIZE))
    }, { root, rootMargin: '0px 0px 100px 0px', threshold: 0.01 })
    observer.observe(target)
    return () => observer.disconnect()
  }, [loading, mobileResults, results.items.length, results.totalItems])

  useEffect(() => {
    if (!streakSummaryOpen) return
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setStreakSummaryOpen(false)
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [streakSummaryOpen])

  useEffect(() => {
    if (!selectedBucket) return
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') closeSelectedBucket()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [selectedBucket])

  const latestResultId = stats?.latestResult?.id ?? null
  const statsDate = stats?.localDate ?? null
  useEffect(() => {
    if (statsDate !== selectedDate) return
    void refreshPrediction()
  }, [latestResultId, refreshPrediction, selectedDate, statsDate])

  const latestSequence = useMemo(
    () => latestResults.map(result => result.itemCode),
    [latestResults],
  )
  const latestSequenceRound = selectedDate === bangkokToday()
    ? status?.activeRound ?? status?.currentRound ?? latestResults[0]?.roundNumber ?? null
    : latestResults[0]?.roundNumber ?? null

  const unacknowledgedEvents = useMemo(
    () => events.filter(event => !event.acknowledged && ['ERROR', 'CRITICAL'].includes(event.severity)),
    [events],
  )
  const activeSubscribers = useMemo(
    () => subscribers.filter(subscriber => subscriber.isActive),
    [subscribers],
  )
  const streakConversationRuns = useMemo(
    () => [...(stats?.vegetableRuns ?? []), ...(stats?.meatRuns ?? [])]
      .sort((left, right) => Date.parse(right.endedAtUtc) - Date.parse(left.endedAtUtc)),
    [stats?.meatRuns, stats?.vegetableRuns],
  )

  function changeDate(value: string) {
    setSelectedDate(value)
    setPage(1)
    setResultsLimit(MOBILE_RESULTS_BATCH_SIZE)
    if (resultsScrollRef.current) resultsScrollRef.current.scrollTop = 0
    setSelectedBucket(null)
    setStreakSummaryOpen(false)
    setAiPredictionResponse(null)
    setAiPredictionError(null)
    setCompoundPredictionResponse(null)
    setCompoundPredictionError(null)
  }

  function closeSelectedBucket() {
    bucketRequestId.current += 1
    setSelectedBucket(null)
  }

  async function openSelectedBucket(category: 'VEGETABLE' | 'MEAT', bucket: StreakBucket) {
    const requestId = ++bucketRequestId.current
    setSelectedBucket({ category, bucket, loading: true, error: null })
    try {
      const detailedBucket = await api.streakBucket(selectedDate, category, bucket.length)
      if (requestId !== bucketRequestId.current) return
      setSelectedBucket({ category, bucket: detailedBucket, loading: false, error: null })
    } catch (caught) {
      if (requestId !== bucketRequestId.current) return
      setSelectedBucket({
        category,
        bucket,
        loading: false,
        error: caught instanceof Error ? caught.message : 'Không thể tải chi tiết chuỗi bệt',
      })
    }
  }

  const loadConversationRunDetails = useCallback(async (run: StreakRun) => {
    if (run.category !== 'VEGETABLE' && run.category !== 'MEAT') {
      throw new Error('Nhóm bệt không hợp lệ')
    }
    const bucket = await api.streakBucket(selectedDate, run.category, run.length)
    const detailedRun = bucket.runs.find(candidate => (
      candidate.startResultId === run.startResultId && candidate.endResultId === run.endResultId
    ))
    if (!detailedRun) throw new Error('Không tìm thấy chi tiết nhóm bệt này')
    return detailedRun
  }, [selectedDate])

  function applyPageSize(value: number) {
    setPage(1)
    setPageSize(value)
    if (resultsScrollRef.current) resultsScrollRef.current.scrollTop = 0
  }

  function applyCustomSize() {
    const value = Number.parseInt(customSize, 10)
    if (Number.isFinite(value) && value >= 1 && value <= 10000) applyPageSize(value)
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
    await refreshAdminData()
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
  const liveRoundCount = selectedDate === bangkokToday()
    ? Math.max(stats?.roundCount ?? 0, status?.currentRound ?? 0)
    : stats?.roundCount ?? 0
  const liveMissedRoundCount = Math.max(0, liveRoundCount - (stats?.totalResults ?? 0))

  function showStatisticsDatePicker() {
    const input = statisticsDateInputRef.current
    if (!input || openingDatePicker.current) return
    input.focus({ preventScroll: true })
    if (typeof input.showPicker === 'function') {
      try {
        input.showPicker()
        return
      } catch {
        // Trình duyệt cũ sẽ dùng thao tác click dự phòng bên dưới.
      }
    }
    openingDatePicker.current = true
    input.click()
    window.setTimeout(() => { openingDatePicker.current = false }, 0)
  }

  return (
    <div className="app-shell">
      {resultAnnouncement && (
        <ResultAnnouncementPopup
          announcement={resultAnnouncement}
          onClose={() => setResultAnnouncement(null)}
        />
      )}
      <header className="topbar">
        <div>
          <h1>GREEDY BIGO · LIVE MONITOR</h1>
        </div>
        <div className="topbar-actions">
          <button type="button" className="play-demo-trigger" onClick={() => setPlayDemoOpen(true)}>
            <span aria-hidden="true">▶</span>
            <span><small>TRẢI NGHIỆM</small><strong>Chơi thử ngay</strong></span>
          </button>
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
          <label
            className="date-picker"
            htmlFor="statistics-date"
            tabIndex={0}
            aria-label="Mở lịch chọn ngày thống kê"
            aria-haspopup="dialog"
            onClick={event => {
              event.preventDefault()
              showStatisticsDatePicker()
            }}
            onKeyDown={event => {
              if (event.key !== 'Enter' && event.key !== ' ') return
              event.preventDefault()
              showStatisticsDatePicker()
            }}
          >
            <span className="date-picker-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none"><path d="M7 3v3M17 3v3M4 9h16M5 5h14a1 1 0 0 1 1 1v14H4V6a1 1 0 0 1 1-1Z" /></svg>
            </span>
            <span className="date-picker-copy"><small>Ngày thống kê</small><strong>{formatLocalDate(selectedDate)}</strong></span>
            <span className="date-picker-chevron" aria-hidden="true">⌄</span>
            <input ref={statisticsDateInputRef} id="statistics-date" tabIndex={-1} aria-label="Chọn ngày thống kê" type="date" value={selectedDate} onChange={event => changeDate(event.target.value)} />
          </label>
          <div className={`status-pill ${status?.isOnline ? 'online' : 'offline'}`}>
            <span className="status-dot" />
            <div>
              <strong>{status?.isOnline ? 'Scanner đang chạy' : 'Scanner ngoại tuyến'}</strong>
              <small>{adminSession?.isAuthenticated && status?.sourceSerial ? `${status.sourceSerial} · ` : ''}Round {status?.activeRound ?? status?.currentRound ?? '—'}</small>
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

      {selectedDate === bangkokToday() && (
        <>
          <NextResultCountdown status={status} liveStatus={liveStatusRef} />
          <LiveBettingSignals status={status} />
        </>
      )}

      <main>
        <section className="metric-grid">
          <MetricCard label="Tổng Round trong ngày" value={liveRoundCount} tone="neutral" hint={`Đã nhận diện ${(stats?.totalResults ?? 0).toLocaleString('vi-VN')} · chốt lúc 23:00`} />
          <MetricCard label="Kèo scan miss / bảo trì" value={liveMissedRoundCount} tone="gold" hint={`Round ${liveRoundCount.toLocaleString('vi-VN')} − đã scan ${(stats?.totalResults ?? 0).toLocaleString('vi-VN')}`} />
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
              <h2>8 ô kết quả mới nhất · Round {latestSequenceRound ?? '—'}</h2>
            </div>
            <div className="refresh-meta">
              <span>Tự làm mới: 1 giây</span>
              <RealtimeClock />
            </div>
          </div>
          <Sequence values={latestSequence} />
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
            <span className="muted">Bấm để xem tóm tắt dạng bong bóng</span>
          </div>
          <div className="streak-grid">
            <StreakBuckets title="Bệt Rau" tone="vegetable" buckets={stats?.vegetableStreakBuckets ?? []} onSelect={bucket => void openSelectedBucket('VEGETABLE', bucket)} />
            <StreakBuckets title="Bệt Thịt" tone="meat" buckets={stats?.meatStreakBuckets ?? []} onSelect={bucket => void openSelectedBucket('MEAT', bucket)} />
          </div>
        </section>

        <section className={`panel prediction-panel local-prediction-panel ${predictionLoading ? 'is-loading' : ''}`}>
          <div className="panel-heading">
            <div>
              <h2>Dự đoán kết quả tiếp theo từ lịch sử</h2>
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
                <h2>{compoundPredictionResponse?.model ?? 'groq/compound-mini'} phân tích dữ sử</h2>
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

        <section className="panel results-panel">
          <div className="panel-heading history-heading">
            <div>
              <h2 className="results-title">Danh sách cầu <span>· {selectedDate}</span></h2>
              <span className="muted">
                {mobileResults
                  ? `${results.items.length.toLocaleString('vi-VN')} kết quả mới nhất · tổng ${results.totalItems.toLocaleString('vi-VN')} bản ghi`
                  : `${results.totalItems.toLocaleString('vi-VN')} bản ghi · trang ${results.page} / ${Math.max(1, results.totalPages)}`}
              </span>
            </div>
            <div className="results-header-actions">
              {!mobileResults && (
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
              )}
              <button className="streak-chat-launch" type="button" onClick={() => setStreakSummaryOpen(true)} aria-label="Mở danh sách bong bóng nhóm bệt">
                <span className="streak-chat-launch-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><path d="M5 5h14v10H9l-4 4V5Z" /><path d="M8 9h8M8 12h5" /></svg></span>
                <span><small>DÒNG BỆT</small><strong>Mở bong bóng chat</strong></span>
                <b>{streakConversationRuns.length}</b>
              </button>
            </div>
          </div>

          <div className="table-wrap" ref={resultsScrollRef}>
            <table>
              <thead><tr><th>Round</th><th>Thời gian</th><th>Vật phẩm</th><th>Nhóm / bệt</th></tr></thead>
              <tbody>
                {results.items.map((result, index) => {
                  const meta = ITEM_META[result.itemCode]
                  const previous = results.items[index - 1]
                  const streakCategory = effectiveStreakCategory(result)
                  const startsVisibleRun = index === 0 ||
                    effectiveStreakCategory(previous) !== streakCategory ||
                    previous.sourceSerial !== result.sourceSerial ||
                    (previous.roundNumber !== null && result.roundNumber !== null && previous.roundNumber !== result.roundNumber + 1)
                  return (
                    <tr className={`${streakCategory.toLowerCase()}-row ${startsVisibleRun ? 'run-start' : ''}`} key={result.id}>
                      <td className="round-cell">{result.roundNumber ?? '—'}</td>
                      <td><span className="desktop-date">{formatDate(result.detectedAtUtc)}</span><span className="mobile-time">{formatTime(result.detectedAtUtc)}</span></td>
                      <td><span className="item-name"><span>{meta?.icon ?? '•'}</span>{result.itemName}</span></td>
                      <td className="run-chat-cell">
                        {startsVisibleRun
                          ? <button className={`run-chat-trigger ${streakCategory.toLowerCase()}`} type="button" onClick={() => setStreakSummaryOpen(true)}><span>{categoryLabel(streakCategory)}</span><strong>{result.streakLength}</strong></button>
                          : <span className={`run-chat-continuation ${streakCategory.toLowerCase()}`} aria-label={`Tiếp chuỗi ${categoryLabel(streakCategory)}`} />}
                      </td>
                    </tr>
                  )
                })}
                {!loading && results.items.length === 0 && <tr><td colSpan={4} className="empty-row">Chưa có kết quả trong ngày đã chọn.</td></tr>}
              </tbody>
            </table>
            {mobileResults && (
              <div className="infinite-results-status" ref={resultsLoadMoreRef}>
                {results.items.length < results.totalItems
                  ? <><span className="infinite-spinner" /><span>Cuộn để tải thêm 10 cầu…</span></>
                  : results.totalItems > 0 && <span>Đã hiển thị toàn bộ {results.totalItems.toLocaleString('vi-VN')} bản ghi</span>}
              </div>
            )}
          </div>
          {!mobileResults && (
            <div className="pagination">
              <button type="button" disabled={page <= 1} onClick={() => setPage(value => Math.max(1, value - 1))}>← Trước</button>
              <span>Trang <strong>{results.page}</strong> / {Math.max(1, results.totalPages)}</span>
              <button type="button" disabled={results.totalPages === 0 || page >= results.totalPages} onClick={() => setPage(value => value + 1)}>Sau →</button>
            </div>
          )}
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

      <PlayDemo
        open={playDemoOpen}
        status={status}
        liveStatus={liveStatusRef}
        onClose={() => setPlayDemoOpen(false)}
      />

      {adminSession?.isAuthenticated && (
        <footer>
          <span>Backend: 127.0.0.1:5117</span>
          <span>Thiết bị ADB: {status?.sourceSerial ?? 'Đang kết nối...'}</span>
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
        <div className="modal-backdrop" role="presentation" onClick={closeSelectedBucket}>
          <section className="streak-modal streak-detail-modal" role="dialog" aria-modal="true" aria-labelledby="streak-detail-title" onClick={event => event.stopPropagation()}>
            <div className="panel-heading streak-detail-heading">
              <div><p className="panel-kicker">CHI TIẾT BỆT · {selectedDate}</p><h2 id="streak-detail-title">{categoryLabel(selectedBucket.category)} bệt {selectedBucket.bucket.length} ({selectedBucket.bucket.count} lần)</h2></div>
              <button className="close-button" type="button" aria-label="Đóng chi tiết bệt" onClick={closeSelectedBucket}>×</button>
            </div>
            <div className="streak-detail-content">
              {selectedBucket.loading && (
                <div className="streak-detail-loading" role="status"><span className="prediction-spinner" /><div><strong>Đang tải chi tiết</strong><small>Đang lấy từng cầu trong chuỗi...</small></div></div>
              )}
              {selectedBucket.error && <div className="streak-detail-error">{selectedBucket.error}</div>}
              {!selectedBucket.loading && !selectedBucket.error && (
                <RunConversation
                  runs={[...selectedBucket.bucket.runs].sort((left, right) => Date.parse(right.endedAtUtc) - Date.parse(left.endedAtUtc))}
                  expandable
                />
              )}
            </div>
          </section>
        </div>
      )}

      {streakSummaryOpen && (
        <div className="modal-backdrop streak-chat-backdrop" role="presentation" onPointerDown={() => setStreakSummaryOpen(false)}>
          <section className="streak-chat-modal" role="dialog" aria-modal="true" aria-labelledby="streak-summary-title" onPointerDown={event => event.stopPropagation()}>
            <div className="streak-chat-header">
              <span className="streak-chat-avatar" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none"><path d="M5 5h14v10H9l-4 4V5Z" /><path d="M8 9h8M8 12h5" /></svg>
              </span>
              <div className="streak-chat-heading">
                <h2 id="streak-summary-title">Nhóm bệt trong ngày</h2>
                <p><span aria-hidden="true" />{selectedDate} · {streakConversationRuns.length} nhóm</p>
              </div>
              <button className="close-button streak-chat-close" type="button" aria-label="Đóng danh sách nhóm bệt" onClick={() => setStreakSummaryOpen(false)}>×</button>
            </div>
            <RunConversation
              runs={streakConversationRuns}
              progressive
              expandable
              loadRunDetails={loadConversationRunDetails}
            />
          </section>
        </div>
      )}
    </div>
  )
}

export default App
