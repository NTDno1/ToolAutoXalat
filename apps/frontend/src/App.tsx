import { startTransition, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, getClientDeviceId, getClientDeviceName } from './api'
import { ITEM_META, ITEM_ORDER } from './items'
import PlayDemo from './PlayDemo'
import type {
  AccessKey,
  AccessSession,
  AiPredictionResponse,
  AdminSession,
  AlertWebhookConfig,
  AutoPlayStrategy,
  AutoPlayStatus,
  Category,
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
  StreakBucket,
  StreakRun,
  Subscriber,
  Visitor,
  CreatedAccessKey,
} from './types'

const MOBILE_RESULTS_BATCH_SIZE = 10
const PAGE_SIZES = [30, 50, 100, 500, 1000, 2000, 5000]
const STATUS_POLL_INTERVAL_MS = 250
const RED_SIDE_CODES = new Set(['CA_CHUA', 'BANH_MI', 'CA_ROT', 'BO'])

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

type PhonePointerState = {
  pointerId: number
  startX: number
  startY: number
  clientX: number
  clientY: number
  startedAt: number
}

function AutoPlayPanel({ adminSession }: { adminSession: AdminSession | null }) {
  const [status, setStatus] = useState<AutoPlayStatus | null>(null)
  const [enabled, setEnabled] = useState(false)
  const [mode, setMode] = useState<'SIMULATION' | 'LIVE'>('SIMULATION')
  const [strategy, setStrategy] = useState<AutoPlayStrategy['key']>('CAPITAL_GUARD')
  const [bankroll, setBankroll] = useState('1000')
  const [chipValue, setChipValue] = useState('2')
  const [tapsPerItem, setTapsPerItem] = useState('1')
  const [maxConsecutiveLosses, setMaxConsecutiveLosses] = useState('2')
  const [minimumVegetableProbability, setMinimumVegetableProbability] = useState('15')
  const [minimumMeatProbability, setMinimumMeatProbability] = useState('7')
  const [maximumSelections, setMaximumSelections] = useState('1')
  const [lossRecoveryEnabled, setLossRecoveryEnabled] = useState(true)
  const [lossRecoveryMultiplier, setLossRecoveryMultiplier] = useState('2')
  const [maximumRecoverySteps, setMaximumRecoverySteps] = useState('2')
  const [liveAcknowledged, setLiveAcknowledged] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const formInitialized = useRef(false)
  const canControl = adminSession?.isAuthenticated === true

  const applyStatus = useCallback((next: AutoPlayStatus, forceForm = false) => {
    setStatus(next)
    if (!formInitialized.current || forceForm) {
      setEnabled(next.configuration.enabled)
      setMode(next.configuration.mode)
      setStrategy(next.configuration.strategy)
      setBankroll(String(next.configuration.bankrollUnits))
      setChipValue(String(next.configuration.chipValue))
      setTapsPerItem(String(next.configuration.tapsPerItem))
      const selectedSettings = next.configuration.strategySettings.find(item => item.strategy === next.configuration.strategy)
      if (selectedSettings) {
        setMaxConsecutiveLosses(String(selectedSettings.maxConsecutiveLosses))
        setMinimumVegetableProbability(String(selectedSettings.minimumVegetableProbabilityPercent))
        setMinimumMeatProbability(String(selectedSettings.minimumMeatProbabilityPercent))
        setMaximumSelections(String(selectedSettings.maximumSelections))
        setLossRecoveryEnabled(selectedSettings.lossRecoveryEnabled)
        setLossRecoveryMultiplier(String(selectedSettings.lossRecoveryMultiplier))
        setMaximumRecoverySteps(String(selectedSettings.maximumRecoverySteps))
      }
      setLiveAcknowledged(false)
      formInitialized.current = true
    }
  }, [])

  const refresh = useCallback(async () => {
    if (!canControl) return
    try {
      applyStatus(await api.autoPlayStatus())
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : 'Không đọc được trạng thái tự động chơi')
    }
  }, [applyStatus, canControl])

  useEffect(() => {
    if (!canControl) return
    void refresh()
    const timer = window.setInterval(() => void refresh(), 1200)
    return () => window.clearInterval(timer)
  }, [canControl, refresh])

  const save = async (enabledOverride?: boolean) => {
    const enabledValue = enabledOverride ?? enabled
    const bankrollValue = Number(bankroll)
    const chipValueNumber = Number.parseInt(chipValue, 10)
    const tapsValue = Number.parseInt(tapsPerItem, 10)
    const maxLossesValue = Number.parseInt(maxConsecutiveLosses, 10)
    const vegetableProbabilityValue = Number(minimumVegetableProbability)
    const meatProbabilityValue = Number(minimumMeatProbability)
    const maximumSelectionsValue = Number.parseInt(maximumSelections, 10)
    const lossRecoveryMultiplierValue = Number(lossRecoveryMultiplier)
    const maximumRecoveryStepsValue = Number.parseInt(maximumRecoverySteps, 10)
    if (!Number.isFinite(bankrollValue) || bankrollValue < 10) {
      setMessage('Vốn phải từ 10 xu trở lên.')
      return
    }
    if (!Number.isFinite(maxLossesValue) || maxLossesValue < 0 || maxLossesValue > 20 ||
        !Number.isFinite(vegetableProbabilityValue) || vegetableProbabilityValue < 0 || vegetableProbabilityValue > 100 ||
        !Number.isFinite(meatProbabilityValue) || meatProbabilityValue < 0 || meatProbabilityValue > 100 ||
        !Number.isFinite(maximumSelectionsValue) || maximumSelectionsValue < 1 || maximumSelectionsValue > 3 ||
        !Number.isFinite(lossRecoveryMultiplierValue) || lossRecoveryMultiplierValue < 1 || lossRecoveryMultiplierValue > 3 ||
        !Number.isFinite(maximumRecoveryStepsValue) || maximumRecoveryStepsValue < 0 || maximumRecoveryStepsValue > 5) {
      setMessage('Kiểm tra lại: chuỗi thua 0–20, xác suất 0–100%, số cửa 1–3, hệ số gỡ 1–3 và tối đa 0–5 bước.')
      return
    }
    if (![2, 10, 50, 100, 1000].includes(chipValueNumber) || !Number.isFinite(tapsValue) || tapsValue < 1 || tapsValue > 5) {
      setMessage('Chọn đúng mệnh giá và từ 1 đến 5 lần chạm cho mỗi cửa.')
      return
    }
    setSaving(true)
    setMessage(null)
    try {
      const next = await api.saveAutoPlayConfiguration({
        enabled: enabledValue,
        mode,
        strategy,
        bankrollUnits: bankrollValue,
        chipValue: chipValueNumber,
        tapsPerItem: tapsValue,
        maxConsecutiveLosses: maxLossesValue,
        minimumVegetableProbabilityPercent: vegetableProbabilityValue,
        minimumMeatProbabilityPercent: meatProbabilityValue,
        maximumSelections: maximumSelectionsValue,
        lossRecoveryEnabled,
        lossRecoveryMultiplier: lossRecoveryMultiplierValue,
        maximumRecoverySteps: maximumRecoveryStepsValue,
        liveModeAcknowledged: liveAcknowledged,
      })
      applyStatus(next, true)
      setMessage(mode === 'LIVE' && enabledValue
        ? 'Đã bật LIVE với giới hạn vốn đã chọn.'
        : enabledValue ? 'Đã bật mô phỏng tự động.' : 'Đã lưu và giữ hệ thống ở trạng thái tắt.')
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : 'Không lưu được cấu hình tự động chơi')
    } finally {
      setSaving(false)
    }
  }

  const emergencyStop = async () => {
    setSaving(true)
    try {
      const next = await api.emergencyStopAutoPlay()
      applyStatus(next, true)
      setMessage('Đã ngắt khẩn cấp. Không có lệnh mới nào được gửi.')
    } catch (caught) {
      setMessage(caught instanceof Error ? caught.message : 'Không thể ngắt tự động chơi')
    } finally {
      setSaving(false)
    }
  }

  const activeStrategy = status?.strategies.find(item => item.key === strategy) ?? null
  const selectStrategy = (key: AutoPlayStrategy['key']) => {
    setStrategy(key)
    const settings = status?.configuration.strategySettings.find(item => item.strategy === key)
    if (!settings) return
    setMaxConsecutiveLosses(String(settings.maxConsecutiveLosses))
    setMinimumVegetableProbability(String(settings.minimumVegetableProbabilityPercent))
    setMinimumMeatProbability(String(settings.minimumMeatProbabilityPercent))
    setMaximumSelections(String(settings.maximumSelections))
    setLossRecoveryEnabled(settings.lossRecoveryEnabled)
    setLossRecoveryMultiplier(String(settings.lossRecoveryMultiplier))
    setMaximumRecoverySteps(String(settings.maximumRecoverySteps))
  }
  const selectMode = (nextMode: 'SIMULATION' | 'LIVE') => {
    setMode(nextMode)
    setLiveAcknowledged(false)
    if (nextMode === 'LIVE') {
      // The production launcher intentionally limits real-device validation
      // to one 2-xu tap. Normalize the form before saving so settings left
      // over from simulation cannot make the server reject LIVE.
      setChipValue('2')
      setTapsPerItem('1')
      setMaximumSelections('1')
      setLossRecoveryEnabled(false)
    }
  }
  const statusTone = status?.engineStatus === 'ERROR'
    ? 'danger'
    : status?.configuration.enabled ? 'active' : 'idle'

  return (
    <section className="autoplay-panel">
      <div className="autoplay-heading">
        <div>
          <p className="panel-kicker">ADMIN · AUTO PLAY</p>
          <h2>Tự động đặt theo dự đoán</h2>
          <p>Đặt 1–3 cửa trong khoảng 20–13 giây. Quyết định hiện chỉ dùng xác suất thống kê; không dùng nhãn HOT hay tín hiệu xu đám đông.</p>
        </div>
        <div className="autoplay-heading-actions">
          <span className={`autoplay-status-pill ${statusTone}`}>{status?.engineStatus ?? 'ĐANG TẢI'}</span>
          <button
            className={`autoplay-power-button ${status?.configuration.enabled ? 'running' : ''}`}
            type="button"
            onClick={() => void save(!status?.configuration.enabled)}
            disabled={saving || !status}
          >
            {status?.configuration.enabled ? 'DỪNG AUTO PLAY' : 'BẬT AUTO PLAY'}
          </button>
          <button className="autoplay-stop-button" type="button" onClick={() => void emergencyStop()} disabled={saving}>
            NGẮT KHẨN CẤP
          </button>
        </div>
      </div>

      <div className="autoplay-warning">
        <strong>Không có thuật toán nào đảm bảo thắng.</strong>
        <span>Gỡ lỗ chỉ tăng tiền trên cầu vẫn đạt xác suất; không bảo đảm gỡ được. Trần vốn, lỗ ngày và số bước luôn được áp dụng, không all-in.</span>
      </div>

      <div className="autoplay-metrics">
        <div><small>ROUND / ĐẾM NGƯỢC</small><strong>{status?.activeRound ?? '—'} · {status?.countdownSeconds ?? '--'}s</strong></div>
        <div><small>SỐ DƯ OCR / DỰ PHÒNG</small><strong>{status?.configuration.detectedBalanceUnits?.toLocaleString('vi-VN') ?? '—'} / {status?.protectedReserveUnits.toLocaleString('vi-VN') ?? '—'} xu</strong></div>
        <div><small>LÃI/LỖ CƯỢC THUẦN HÔM NAY</small><strong className={(status?.todayNetUnits ?? 0) < 0 ? 'negative' : 'positive'}>{status?.todayNetUnits ?? 0}</strong></div>
        <div><small>THUA LIÊN TIẾP / BƯỚC GỠ</small><strong>{status?.consecutiveLosses ?? 0} / {status?.currentRecoveryStep ?? 0}</strong></div>
      </div>

      <div className="autoplay-status-message">{status?.message ?? 'Đang kết nối bộ máy tự động…'}</div>

      <div className="autoplay-strategies">
        {status?.strategies.map(item => (
          <button
            type="button"
            key={item.key}
            className={strategy === item.key ? 'selected' : ''}
            onClick={() => selectStrategy(item.key)}
          >
            <strong>{item.name}</strong>
            <span>{item.description}</span>
            {(() => {
              const saved = status.configuration.strategySettings.find(setting => setting.strategy === item.key)
              return <small>{item.riskLevel} · {saved?.maximumSelections ?? item.maximumSelections} cửa · Rau ≥ {saved?.minimumVegetableProbabilityPercent ?? '—'}% · Thịt ≥ {saved?.minimumMeatProbabilityPercent ?? '—'}% · {saved?.lossRecoveryEnabled ? `gỡ x${saved.lossRecoveryMultiplier}, ${saved.maximumRecoverySteps} bước` : 'không gỡ lỗ'} · {saved?.maxConsecutiveLosses === 0 ? 'không dừng theo chuỗi' : `dừng sau ${saved?.maxConsecutiveLosses ?? item.maxConsecutiveLosses} lần thua`}</small>
            })()}
          </button>
        ))}
      </div>

      <div className="autoplay-config-grid">
        <label>
          <span>Chế độ</span>
          <select value={mode} onChange={event => selectMode(event.target.value as 'SIMULATION' | 'LIVE')}>
            <option value="SIMULATION">Mô phỏng — không chạm điện thoại</option>
            <option value="LIVE" disabled={!status?.configuration.liveExecutionAvailable}>LIVE — đặt trên điện thoại</option>
          </select>
          {!status?.configuration.liveExecutionAvailable && <small>LIVE khóa an toàn: chưa có mẫu số dư dương và cược thật để xác nhận OCR/tọa độ ADB.</small>}
          {mode === 'LIVE' && status?.configuration.liveExecutionAvailable && <small>Máy thật: hệ thống tự dùng 1 cửa × 1 chạm × 2 xu; hãy tích xác nhận rủi ro trước khi bật.</small>}
        </label>
        <label>
          <span>Vốn mô phỏng / vốn chuẩn</span>
          <input type="number" min="10" step="1" value={bankroll} onChange={event => setBankroll(event.target.value)} />
        </label>
        <label>
          <span>Mệnh giá xu</span>
          <select value={chipValue} onChange={event => setChipValue(event.target.value)}>
            {[2, 10, 50, 100, 1000].map(value => <option value={value} key={value}>{value} xu</option>)}
          </select>
        </label>
        <label>
          <span>Số lần cộng mệnh giá vào mỗi cửa</span>
          <input type="number" min="1" max="5" step="1" value={tapsPerItem} onChange={event => setTapsPerItem(event.target.value)} />
          <small>{chipValue} xu × {tapsPerItem || 0} = {(Number(chipValue) * Number(tapsPerItem || 0)).toLocaleString('vi-VN')} xu cho mỗi cửa được chọn.</small>
        </label>
      </div>

      <div className="autoplay-strategy-settings">
        <div>
          <strong>Cấu hình riêng: {activeStrategy?.name ?? strategy}</strong>
          <small>Các giá trị này được lưu riêng cho chiến thuật đang chọn.</small>
        </div>
        <label><span>Xác suất Rau tối thiểu</span><input type="number" min="0" max="100" step="0.1" value={minimumVegetableProbability} onChange={event => setMinimumVegetableProbability(event.target.value)} /><small>%</small></label>
        <label><span>Xác suất Thịt tối thiểu</span><input type="number" min="0" max="100" step="0.1" value={minimumMeatProbability} onChange={event => setMinimumMeatProbability(event.target.value)} /><small>%</small></label>
        <label><span>Số cửa tối đa</span><input type="number" min="1" max="3" step="1" value={maximumSelections} onChange={event => setMaximumSelections(event.target.value)} /></label>
        <label><span>Dừng sau chuỗi thua</span><input type="number" min="0" max="20" step="1" value={maxConsecutiveLosses} onChange={event => setMaxConsecutiveLosses(event.target.value)} /><small>Đặt 0 để tắt giới hạn này; giới hạn lỗ ngày vẫn hoạt động.</small></label>
        <label className="autoplay-recovery-toggle"><span>Gỡ lỗ có giới hạn</span><input type="checkbox" checked={lossRecoveryEnabled} onChange={event => setLossRecoveryEnabled(event.target.checked)} /><small>Chỉ tăng tiền khi cầu tiếp theo vẫn vượt toàn bộ bộ lọc xác suất.</small></label>
        <label><span>Hệ số tăng sau mỗi lần thua</span><input type="number" min="1" max="3" step="0.1" value={lossRecoveryMultiplier} onChange={event => setLossRecoveryMultiplier(event.target.value)} disabled={!lossRecoveryEnabled} /><small>Ví dụ x2: 1 → 2 → 4 lần cộng mệnh giá.</small></label>
        <label><span>Số bước gỡ tối đa</span><input type="number" min="0" max="5" step="1" value={maximumRecoverySteps} onChange={event => setMaximumRecoverySteps(event.target.value)} disabled={!lossRecoveryEnabled} /><small>Chạm trần vốn hoặc số lần chạm thì tool bỏ cầu.</small></label>
      </div>

      {mode === 'LIVE' && (
        <label className="autoplay-live-ack">
          <input type="checkbox" checked={liveAcknowledged} onChange={event => setLiveAcknowledged(event.target.checked)} />
          <span>Tôi xác nhận tọa độ cửa và mệnh giá xu trên điện thoại đã được kiểm tra; LIVE có thể làm thay đổi số dư thật.</span>
        </label>
      )}

      {activeStrategy && (
        <div className="autoplay-guardrails">
          <span>Tin cậy ≥ {activeStrategy.minimumConfidencePercent}%</span>
          <span>Khoảng cách top ≥ {activeStrategy.minimumTopGapPercent}%</span>
          <span>Lợi thế kỳ vọng ≥ {activeStrategy.minimumExpectedEdgePercent}%</span>
          <span>Cửa tối đa x{activeStrategy.maximumPayoutMultiplier}</span>
          <span>Lỗ ngày tối đa {activeStrategy.maxDailyLossPercent}%</span>
          <span>Rau ≥ {minimumVegetableProbability}% · Thịt ≥ {minimumMeatProbability}%</span>
          <span>Tối đa {maximumSelections} cửa · {Number(maxConsecutiveLosses) === 0 ? 'không dừng theo chuỗi thua' : `dừng sau ${maxConsecutiveLosses} lần thua`}</span>
          <span>{lossRecoveryEnabled ? `Gỡ lỗ x${lossRecoveryMultiplier}, tối đa ${maximumRecoverySteps} bước` : 'Gỡ lỗ đang tắt'}</span>
        </div>
      )}

      <div className="autoplay-save-row">
        <button type="button" onClick={() => void save()} disabled={saving || !status}>
          {saving ? 'Đang lưu…' : `Lưu cấu hình (${enabled ? 'bật' : 'tắt'})`}
        </button>
        {message && <span>{message}</span>}
      </div>

      <div className="autoplay-history">
        <div className="panel-heading"><div><p className="panel-kicker">PHIÊN TỰ ĐỘNG</p><h3>Vốn và lời/lỗ theo từng lần bật</h3></div></div>
        <div className="autoplay-run-list">
          {status?.recentRuns.map(run => (
            <div className={`autoplay-run ${run.status.toLowerCase()}`} key={run.id}>
              <strong>{run.status} · {run.mode}</strong>
              <span>{new Date(run.startedAtUtc).toLocaleString('vi-VN')}</span>
              <span>Vốn {run.startingBalanceUnits.toLocaleString('vi-VN')} → {run.currentBalanceUnits.toLocaleString('vi-VN')}</span>
              <span className={run.netUnits < 0 ? 'negative' : 'positive'}>Lãi/lỗ cược thuần {run.netUnits > 0 ? '+' : ''}{run.netUnits}</span>
              {run.mode === 'LIVE' && run.endingBalanceUnits !== null && (() => {
                const actualDelta = run.endingBalanceUnits - run.startingBalanceUnits
                return <small>Biến động vốn thực tế {actualDelta > 0 ? '+' : ''}{actualDelta} xu; phần chênh có thể gồm thưởng hoặc phí ngoài cược.</small>
              })()}
              <small>{run.betRounds} cầu đặt · {run.skippedRounds} bỏ · {run.wonRounds} thắng · {run.lostRounds} thua · {run.highRiskSkippedRounds} bỏ rủi ro cao</small>
              {(run.participationDiamonds ?? 0) > 0 && <small>Mức phí đã bấm xác nhận: {run.participationDiamonds} kim cương</small>}
            </div>
          ))}
        </div>
        <div className="panel-heading"><div><p className="panel-kicker">NHẬT KÝ QUYẾT ĐỊNH</p><h3>50 round gần nhất · bấm để xem chi tiết</h3></div></div>
        <div className="autoplay-history-list">
          {status?.recentActions.map(action => (
            <details className={`autoplay-action ${action.status.toLowerCase()}`} key={action.id}>
              <summary>
                <span><strong>#{action.roundNumber} · {action.bets.length ? `${action.bets.length} cửa` : 'Bỏ cầu'}</strong><small>{action.mode} · cược {action.totalStakeUnits} xu · rủi ro {action.riskLevel}{action.recoveryStep > 0 ? ` · gỡ bước ${action.recoveryStep} (x${action.stakeMultiplier})` : ''}</small></span>
                <span><strong>{action.status}</strong><small>KQ {action.resultItemCode ?? 'chưa có'} · lãi/lỗ {action.netUnits ?? '—'}</small></span>
              </summary>
              <p>{action.reason}</p>
              {(action.participationDiamonds ?? 0) > 0 && <p>Đã chạm xác nhận phí tham gia {action.participationDiamonds} kim cương · {action.participationStatus}</p>}
              {action.bets.map(bet => <div className="autoplay-bet-detail" key={bet.itemCode}>
                <strong>{bet.itemName}</strong><span>{bet.stakeUnits} xu · {bet.tapCount} chạm · p {bet.probabilityPercent.toFixed(1)}% · x{bet.payoutMultiplier} · edge {bet.expectedEdgePercent.toFixed(1)}%</span>
              </div>)}
              <div className="autoplay-verification">
                <strong>Xác minh: {action.verification.status}</strong>
                <span>Số dư {action.verification.balanceBeforeUnits ?? '—'} → {action.verification.balanceAfterUnits ?? '—'} · dự kiến trừ {action.verification.expectedDebitUnits}</span>
                <small>{action.verification.message}</small>
              </div>
            </details>
          ))}
          {status?.recentActions.length === 0 && <p className="muted">Chưa có quyết định. Bật mô phỏng để quan sát trước khi cân nhắc LIVE.</p>}
        </div>
      </div>
    </section>
  )
}

function toPhonePoint(event: React.PointerEvent<HTMLImageElement>, element: HTMLImageElement) {
  const rect = element.getBoundingClientRect()
  if (rect.width <= 0 || rect.height <= 0) return null
  return {
    x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
    y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
  }
}

function PhonePreviewPanel({
  status,
  adminSession,
}: {
  status: ScannerStatus | null
  adminSession: AdminSession | null
}) {
  const [controlStatus, setControlStatus] = useState<PhoneControlStatus | null>(null)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [live, setLive] = useState(true)
  const [previewEnabled, setPreviewEnabled] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastAction, setLastAction] = useState<string | null>(null)
  const imageUrlRef = useRef<string | null>(null)
  const pointerRef = useRef<PhonePointerState | null>(null)
  const captureInFlightRef = useRef(false)
  const canControl = adminSession?.isAuthenticated === true

  const clearFrame = useCallback(() => {
    const previousUrl = imageUrlRef.current
    imageUrlRef.current = null
    setImageUrl(null)
    if (previousUrl) URL.revokeObjectURL(previousUrl)
  }, [])

  const refreshStatus = useCallback(async () => {
    if (!canControl) return
    try {
      const next = await api.phoneControlStatus()
      setControlStatus(next)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Khong doc duoc trang thai dien thoai')
    }
  }, [canControl])

  const refreshFrame = useCallback(async () => {
    if (!canControl || captureInFlightRef.current) return
    captureInFlightRef.current = true
    try {
      const blob = await api.phoneScreenshot()
      const nextUrl = URL.createObjectURL(blob)
      const previousUrl = imageUrlRef.current
      imageUrlRef.current = nextUrl
      setImageUrl(nextUrl)
      setError(null)
      if (previousUrl) {
        window.setTimeout(() => URL.revokeObjectURL(previousUrl), 500)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Khong chup duoc man hinh dien thoai')
    } finally {
      captureInFlightRef.current = false
    }
  }, [canControl])

  useEffect(() => () => {
    if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current)
  }, [])

  useEffect(() => {
    if (!canControl) {
      setControlStatus(null)
      setPreviewEnabled(false)
      clearFrame()
      return
    }
    if (!previewEnabled) return
    const timer = window.setInterval(() => void refreshStatus(), 5000)
    return () => window.clearInterval(timer)
  }, [canControl, clearFrame, previewEnabled, refreshStatus])

  useEffect(() => {
    if (!canControl || !previewEnabled || !live) return
    void refreshFrame()
    const timer = window.setInterval(() => void refreshFrame(), expanded ? 700 : 1100)
    return () => window.clearInterval(timer)
  }, [canControl, expanded, live, previewEnabled, refreshFrame])

  const connectPhone = useCallback(async () => {
    if (!canControl || connecting) return
    setConnecting(true)
    setError(null)
    try {
      const next = await api.phoneControlStatus()
      setControlStatus(next)
      setLastAction(next.connected ? 'Da ket noi dien thoai' : 'Chua ket noi duoc dien thoai')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Khong ket noi duoc dien thoai')
    } finally {
      setConnecting(false)
    }
  }, [canControl, connecting])

  const stopPreview = useCallback(async () => {
    setPreviewEnabled(false)
    setLive(false)
    setExpanded(false)
    clearFrame()
    try {
      await api.phonePreviewStop()
      setLastAction('Da tat preview de giam tai nguyen')
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Khong tat duoc preview')
    }
  }, [clearFrame])

  const togglePreview = useCallback(async () => {
    if (!canControl || busy || connecting) return
    if (previewEnabled) {
      await stopPreview()
      return
    }
    setConnecting(true)
    setError(null)
    try {
      const next = await api.phoneControlStatus()
      setControlStatus(next)
      if (!next.connected) {
        setLastAction('Chua ket noi duoc dien thoai')
        return
      }
      setPreviewEnabled(true)
      setLive(true)
      setLastAction('Da bat preview dien thoai')
      await refreshFrame()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Khong bat duoc preview')
    } finally {
      setConnecting(false)
    }
  }, [busy, canControl, connecting, previewEnabled, refreshFrame, stopPreview])

  const runAction = useCallback(async (label: string, action: () => Promise<void>) => {
    if (!canControl || busy) return
    setBusy(true)
    try {
      await action()
      setLastAction(label)
      if (previewEnabled && !live) await refreshFrame()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Khong gui duoc lenh toi dien thoai')
    } finally {
      setBusy(false)
    }
  }, [busy, canControl, live, previewEnabled, refreshFrame])

  const handlePointerDown = (event: React.PointerEvent<HTMLImageElement>) => {
    if (!canControl || !previewEnabled || busy) return
    const point = toPhonePoint(event, event.currentTarget)
    if (!point) return
    pointerRef.current = {
      pointerId: event.pointerId,
      startX: point.x,
      startY: point.y,
      clientX: event.clientX,
      clientY: event.clientY,
      startedAt: Date.now(),
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    event.preventDefault()
  }

  const handlePointerUp = (event: React.PointerEvent<HTMLImageElement>) => {
    const pointer = pointerRef.current
    if (!pointer || pointer.pointerId !== event.pointerId) return
    pointerRef.current = null
    const point = toPhonePoint(event, event.currentTarget)
    if (!point) return
    const movedPixels = Math.hypot(event.clientX - pointer.clientX, event.clientY - pointer.clientY)
    const durationMs = Math.max(80, Date.now() - pointer.startedAt)
    if (movedPixels < 12 && durationMs < 450) {
      void runAction(`Tap ${Math.round(point.x * 100)}% / ${Math.round(point.y * 100)}%`, () =>
        api.phoneTap(point.x, point.y))
    } else {
      void runAction('Swipe tren dien thoai', () =>
        api.phoneSwipe(pointer.startX, pointer.startY, point.x, point.y, durationMs))
    }
    event.preventDefault()
  }

  const phoneSize = controlStatus?.width && controlStatus?.height
    ? `${controlStatus.width} x ${controlStatus.height}`
    : 'Dang doc do phan giai'
  const connected = controlStatus?.connected === true

  return (
    <section className={`phone-preview-panel ${expanded ? 'expanded' : ''}`}>
      <div className="phone-preview-heading">
        <div>
          <p className="panel-kicker">DIEN THOAI THAT</p>
          <h2>Preview full man hinh va thao tac truc tiep</h2>
          <p>
            {canControl
              ? `${controlStatus?.model ?? controlStatus?.serial ?? 'ADB'} · ${phoneSize}`
              : 'Dang nhap admin de xem va dieu khien dien thoai qua web.'}
          </p>
        </div>
        <div className="phone-preview-actions">
          <span className={`phone-connection-pill ${connected ? 'online' : 'offline'}`}>
            {connected ? 'Da ket noi' : 'Chua ket noi'}
          </span>
          <button type="button" onClick={() => void connectPhone()} disabled={!canControl || connecting}>
            {connecting ? 'Dang ket noi' : 'Ket noi dien thoai'}
          </button>
          <button type="button" onClick={() => void togglePreview()} disabled={!canControl || busy || connecting}>
            {previewEnabled ? 'Tat preview' : 'Bat preview'}
          </button>
          <button type="button" onClick={() => setLive(value => !value)} disabled={!canControl || !previewEnabled}>
            {live ? 'Tam dung' : 'Chay live'}
          </button>
          <button type="button" onClick={() => void refreshFrame()} disabled={!canControl || !previewEnabled || busy}>
            Lam moi anh
          </button>
          <button type="button" onClick={() => setExpanded(value => !value)} disabled={!previewEnabled}>
            {expanded ? 'Thu nho' : 'Full man hinh'}
          </button>
        </div>
      </div>

      <div className="phone-preview-layout">
        <div className="phone-screen-shell">
          {imageUrl && canControl && previewEnabled ? (
            <img
              src={imageUrl}
              alt="Man hinh dien thoai"
              draggable={false}
              onPointerDown={handlePointerDown}
              onPointerUp={handlePointerUp}
              onPointerCancel={() => { pointerRef.current = null }}
            />
          ) : (
            <div className="phone-screen-placeholder">
              <strong>{canControl ? (previewEnabled ? 'Dang lay anh tu ADB' : 'Preview dang tat') : 'Can dang nhap admin'}</strong>
              <span>{canControl ? 'Bam Ket noi dien thoai de kiem tra ADB, roi bam Bat preview khi can thao tac.' : 'Chuc nang nay chi danh cho admin.'}</span>
            </div>
          )}
        </div>

        <div className="phone-control-side">
          <div className="phone-control-card">
            <small>Trang thai scanner</small>
            <strong>{status?.isOnline ? 'Dang scan' : 'Ngoai tuyen'}</strong>
            <span>Round {status?.activeRound ?? status?.currentRound ?? '—'} · {status?.countdownSeconds ?? '--'}s</span>
          </div>
          <div className="phone-control-buttons">
            <button type="button" disabled={!canControl || !connected || busy} onClick={() => void runAction('Back', () => api.phoneKey(4))}>Back</button>
            <button type="button" disabled={!canControl || !connected || busy} onClick={() => void runAction('Home', () => api.phoneKey(3))}>Home</button>
            <button type="button" disabled={!canControl || !connected || busy} onClick={() => void runAction('Recent', () => api.phoneKey(187))}>Recent</button>
          </div>
          <p className="phone-preview-note">
            Preview mac dinh se tat de tiet kiem tai nguyen. Khi can tu danh, bam Bat preview roi cham vao anh de tap hoac keo de swipe.
          </p>
          {(error || controlStatus?.message || lastAction) && (
            <p className={`phone-preview-status ${error ? 'error' : connected ? 'online' : 'offline'}`}>
              {error ?? lastAction ?? controlStatus?.message}
            </p>
          )}
        </div>
      </div>
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

function MarketPredictionPanel({
  response,
  loading,
  error,
  onRefresh,
}: {
  response: MarketPredictionResponse | null
  loading: boolean
  error: string | null
  onRefresh: () => void
}) {
  const house = response?.housePerformance
  const side = response?.sideForecast
  const hotMeta = response?.hotItemCode ? ITEM_META[response.hotItemCode] : null
  const liabilityMeta = house?.highestCurrentLiabilityItemCode
    ? ITEM_META[house.highestCurrentLiabilityItemCode]
    : null
  const hotOutcomeLeaders = response?.hotOutcomeStats
    ?.filter(item => item.outcomeRounds > 0)
    .slice(0, 3) ?? []
  const ready = response?.status === 'READY' && response.prediction
  const statusLabel = response?.status === 'READY'
    ? 'Đã chốt dự đoán'
    : response?.status === 'COLLECTING_COINS'
      ? 'Đang gom xu'
      : response?.status === 'WAITING_HOT'
        ? 'Đang chờ HOT'
        : response?.status === 'WAITING_SIGNAL'
          ? 'Nền sẵn · chờ tối đa 10s'
        : 'Đang quan sát'
  return (
    <section className={`panel prediction-panel market-prediction-panel ${loading ? 'is-loading' : ''} ${ready ? 'ready' : 'waiting'}`}>
      <div className="panel-heading">
        <div>
          <p className="panel-kicker market-kicker">MÔ HÌNH MỚI · 500 CẦU GẦN NHẤT</p>
          <h2>Dự đoán theo HOT, lượng xu, cầu Đỏ/Xanh và lợi thế kỳ vọng</h2>
        </div>
        <div className="ai-prediction-actions">
          <div className="market-live-state">
            <span className={`market-status ${ready ? 'ready' : 'waiting'}`}>{statusLabel}</span>
            <strong>{response?.countdownSeconds != null ? `${response.countdownSeconds}s` : '--s'}</strong>
          </div>
          <button type="button" onClick={onRefresh} disabled={loading}>{loading ? 'Đang tính...' : 'Cập nhật ngay'}</button>
        </div>
      </div>

      <div className="market-observation-strip" aria-live="polite">
        <div><span>Round đang phân tích</span><strong>{response?.roundNumber ?? '—'}</strong></div>
        <div><span>HOT (nếu có)</span><strong>{hotMeta ? `${hotMeta.icon} ${hotMeta.name}` : 'Không có / chưa báo'}</strong></div>
        <div><span>Cầu ghép HOT/xu</span><strong>{response?.matchedSignalRounds ?? 0}/{response?.analysisWindow ?? 500}</strong></div>
        <div><span>Bộ xử lý / thời gian</span><strong>{response ? `${response.computeDevice} · ${response.analysisDurationMs}ms` : 'CPU · --ms'}</strong></div>
        <p>{response?.message ?? 'Đang tính sẵn nền 500 cầu. HOT/xu là tín hiệu bổ sung; thiếu tín hiệu vẫn tự chốt sau 10 giây đầu.'}</p>
      </div>

      <div className="market-side-map" aria-label="Phân nhóm hai bên Đỏ và Xanh">
        <article className="red">
          <span>BÊN ĐỎ</span>
          <strong>🍅 Cà chua · 🌭 Bánh mì · 🥕 Cà rốt · 🥩 Bò</strong>
        </article>
        <article className="green">
          <span>BÊN XANH</span>
          <strong>🌽 Ngô · 🥬 Cải · 🍢 Xiên · 🍗 Đùi</strong>
        </article>
      </div>

      {side && (
        <div className="market-side-forecast">
          <article className="red">
            <span>Khả năng bên Đỏ</span>
            <strong>{side.redProbabilityPercent.toFixed(2)}%</strong>
            <div><i style={{ width: `${side.redProbabilityPercent}%` }} /></div>
          </article>
          <article className="green">
            <span>Khả năng bên Xanh</span>
            <strong>{side.greenProbabilityPercent.toFixed(2)}%</strong>
            <div><i style={{ width: `${side.greenProbabilityPercent}%` }} /></div>
          </article>
          <p>
            Nhịp hiện tại: <b>{side.currentSide === 'RED' ? 'Đỏ' : side.currentSide === 'GREEN' ? 'Xanh' : 'chưa đủ dữ liệu'}</b>
            {side.currentSide && ` ${side.currentStreak} cầu`} · khớp {side.matchedTransitions} chuyển tiếp tương tự · trọng số cầu màu {side.signalWeightPercent.toFixed(1)}%
          </p>
        </div>
      )}

      {house && (
        <div className="house-performance-grid">
          <article><span>Kết quả đã định giá</span><strong>{house.analyzedRounds}</strong><small>cầu có đủ xu + kết quả</small></article>
          <article className={house.estimatedNetUnits >= 0 ? 'positive' : 'negative'}><span>Nhà cái ước tính</span><strong>{house.estimatedNetUnits >= 0 ? '+' : ''}{house.estimatedNetUnits.toFixed(1)} xu</strong><small>biên {house.estimatedMarginPercent.toFixed(1)}% · không phải tiền thật</small></article>
          <article><span>HOT về đúng cửa</span><strong>{house.hotHitRatePercent.toFixed(1)}%</strong><small>trên {house.hotObservedRounds} cầu có HOT</small></article>
          <article><span>Dấu hiệu né nghĩa vụ</span><strong>{house.riskAvoidanceScorePercent.toFixed(1)}%</strong><small>50% ≈ chưa thấy thiên lệch rõ</small></article>
          <article><span>Cửa nhà cái ngại nhất</span><strong>{liabilityMeta ? `${liabilityMeta.icon} ${liabilityMeta.name}` : 'Chưa đủ xu'}</strong><small>nghĩa vụ tương đối {house.highestCurrentLiabilityUnits.toFixed(1)}</small></article>
        </div>
      )}

      {(hotOutcomeLeaders.length > 0 || (house?.currentOutcomeScenarios?.length ?? 0) > 0) && (
        <div className="market-evidence-grid">
          <article>
            <span>LOG HOT → KẾT QUẢ</span>
            <strong>{hotMeta ? `${hotMeta.icon} HOT ${hotMeta.name}` : 'Chưa có HOT'}</strong>
            {hotOutcomeLeaders.length > 0 ? hotOutcomeLeaders.map(stat => {
              const outcome = ITEM_META[stat.outcomeItemCode]
              return <small key={stat.outcomeItemCode}>→ {outcome?.icon} {outcome?.name}: {stat.outcomeRounds}/{stat.hotObservedRounds} cầu ({stat.outcomeRatePercent.toFixed(1)}%)</small>
            }) : <small>Đang chờ đủ round HOT có kết quả để học quan hệ chuyển tiếp.</small>}
          </article>
          <article>
            <span>KỊCH BẢN NHÀ CÁI THEO MỨC XU HIỆN TẠI</span>
            <strong>Tổng mức vào tương đối: {house?.currentEstimatedStakeUnits.toFixed(1) ?? '0.0'}</strong>
            {house?.currentOutcomeScenarios.map(scenario => {
              const item = ITEM_META[scenario.itemCode]
              const net = scenario.estimatedHouseNetUnits
              return <small className={net >= 0 ? 'positive' : 'negative'} key={scenario.itemCode}>Nếu về {item?.icon} {item?.name}: nhà cái {net >= 0 ? 'lãi' : 'lỗ'} {Math.abs(net).toFixed(1)} đơn vị (mức xu {scenario.coinLevel}/3)</small>
            })}
          </article>
        </div>
      )}

      {ready ? (
        <div className="market-prediction-result">
          <PredictionResult prediction={response.prediction!} />
          <div className="market-side-item-note">
            {response.prediction!.items.map(item => (
              <span className={RED_SIDE_CODES.has(item.itemCode) ? 'red' : 'green'} key={item.itemCode}>
                {ITEM_META[item.itemCode]?.icon} {item.itemName}: {RED_SIDE_CODES.has(item.itemCode) ? 'Đỏ' : 'Xanh'}
              </span>
            ))}
          </div>
        </div>
      ) : (
        <div className="market-waiting-card">
          <span className="market-radar" aria-hidden="true"><i /></span>
          <div><strong>Đã chuẩn bị nền 500 cầu</strong><p>HOT/xu có thì dùng ngay. Nếu không có, hệ thống tự chốt khi hết 10 giây đầu của round.</p></div>
        </div>
      )}
      {error && !loading && <div className="prediction-inline-error">{error}</div>}
      {loading && <PredictionLoading tone="ai" label="Đang chốt từ nền 500 cầu và tín hiệu hiện có" />}
    </section>
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
  const [activeView, setActiveView] = useState<'report' | 'admin'>(() =>
    window.sessionStorage.getItem('greedy-active-view') === 'admin' ? 'admin' : 'report')
  const [selectedDate, setSelectedDate] = useState(bangkokToday)
  const [stats, setStats] = useState<DailyStats | null>(null)
  const [days, setDays] = useState<DailySummary[]>([])
  const [status, setStatus] = useState<ScannerStatus | null>(null)
  const [results, setResults] = useState<Page<ResultItem>>(emptyPage)
  const [latestResults, setLatestResults] = useState<ResultItem[]>([])
  const [events, setEvents] = useState<ScannerEvent[]>([])
  const [visitors, setVisitors] = useState<Visitor[]>([])
  const [accessKeys, setAccessKeys] = useState<AccessKey[]>([])
  const [createdAccessKey, setCreatedAccessKey] = useState<CreatedAccessKey | null>(null)
  const [newKeyLabel, setNewKeyLabel] = useState('')
  const [newKeyDays, setNewKeyDays] = useState('30')
  const [keyAdminMessage, setKeyAdminMessage] = useState<string | null>(null)
  const [keyAdminSaving, setKeyAdminSaving] = useState(false)
  const [alertWebhookConfig, setAlertWebhookConfig] = useState<AlertWebhookConfig | null>(null)
  const [webhookUrlDraft, setWebhookUrlDraft] = useState('')
  const [webhookSaving, setWebhookSaving] = useState(false)
  const [webhookMessage, setWebhookMessage] = useState<string | null>(null)
  const [prediction, setPrediction] = useState<Prediction | null>(null)
  const [predictionLoading, setPredictionLoading] = useState(false)
  const [predictionError, setPredictionError] = useState<string | null>(null)
  const [marketPredictionResponse, setMarketPredictionResponse] = useState<MarketPredictionResponse | null>(null)
  const [marketPredictionLoading, setMarketPredictionLoading] = useState(false)
  const [marketPredictionError, setMarketPredictionError] = useState<string | null>(null)
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
  const [accessSession, setAccessSession] = useState<AccessSession | null>(null)
  const [rememberAccessKey, setRememberAccessKey] = useState(() => window.localStorage.getItem('greedy-remember-key') !== '0')
  const [accessKeyInput, setAccessKeyInput] = useState('')
  const [accessKeyError, setAccessKeyError] = useState<string | null>(null)
  const [accessKeyLoading, setAccessKeyLoading] = useState(false)
  const [adminLoginOpen, setAdminLoginOpen] = useState(false)
  const [rememberAdmin, setRememberAdmin] = useState(() => window.localStorage.getItem('greedy-remember-admin') !== '0')
  const [adminUsername, setAdminUsername] = useState(() => window.localStorage.getItem('greedy-admin-username') ?? '1')
  const [adminPassword, setAdminPassword] = useState('')
  const [adminAuthError, setAdminAuthError] = useState<string | null>(null)
  const [adminAuthLoading, setAdminAuthLoading] = useState(false)
  const [selectedVisitor, setSelectedVisitor] = useState<Visitor | null>(null)
  const predictionRequestId = useRef(0)
  const marketPredictionRequestId = useRef(0)
  const marketPredictionInFlight = useRef(false)
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
      setVisitors(current => current.length === 0 ? current : [])
      setAccessKeys(current => current.length === 0 ? current : [])
      setAlertWebhookConfig(null)
      setWebhookUrlDraft('')
      return
    }
    try {
      const [nextEvents, nextWebhookConfig, nextVisitors, nextAccessKeys] = await Promise.all([
        api.events(), api.alertWebhookConfig(), api.visitors(), api.accessKeys(),
      ])
      setEvents(current => sameJsonValue(current, nextEvents.items))
      setVisitors(current => sameJsonValue(current, nextVisitors))
      setAccessKeys(current => sameJsonValue(current, nextAccessKeys))
      setAlertWebhookConfig(current => sameJsonValue(current, nextWebhookConfig))
      setWebhookUrlDraft(current => current || nextWebhookConfig.webhookUrl || '')
    } catch {
      const nextSession = await api.adminSession()
      setAdminSession(nextSession)
      if (!nextSession.isAuthenticated) {
        setEvents([])
        setVisitors([])
        setAccessKeys([])
        setAlertWebhookConfig(null)
        setWebhookUrlDraft('')
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

  const refreshMarketPrediction = useCallback(async (showLoading = true) => {
    // Coalesce frequent polling calls. Overlapping requests used to keep
    // invalidating each other, so a response taking >1 second could never be
    // committed and the loading overlay remained visible indefinitely.
    if (marketPredictionInFlight.current) return
    marketPredictionInFlight.current = true
    const requestId = ++marketPredictionRequestId.current
    const startedAt = Date.now()
    if (showLoading) setMarketPredictionLoading(true)
    try {
      const response = await api.marketPrediction(selectedDate)
      if (requestId === marketPredictionRequestId.current) {
        setMarketPredictionResponse(current => {
          // Keep a completed prediction visible through the 0-second lock
          // state. It is cleared naturally when the scanner reports a new
          // round, so a late HOT detection cannot flash for only one poll.
          if (current?.status === 'READY' &&
              response.status !== 'READY' &&
              current.roundNumber === response.roundNumber) {
            return current
          }
          return response
        })
        setMarketPredictionError(null)
      }
    } catch (caught) {
      if (requestId === marketPredictionRequestId.current) {
        const message = caught instanceof Error ? caught.message : 'Không thể tính dự đoán HOT và xu'
        setMarketPredictionError(/^404(?:\s|$)/.test(message)
          ? 'Backend chưa nạp API mô hình mới. Hãy khởi động lại toàn bộ hệ thống để đồng bộ backend và giao diện.'
          : message)
      }
    } finally {
      if (showLoading) await keepLoadingVisible(startedAt, 150)
      if (showLoading && requestId === marketPredictionRequestId.current) setMarketPredictionLoading(false)
      marketPredictionInFlight.current = false
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
    void Promise.all([api.adminSession(), api.accessSession()])
      .then(([nextAdminSession, nextAccessSession]) => {
        setAdminSession(nextAdminSession)
        setAccessSession(nextAccessSession)
      })
      .catch(() => {
        setAdminSession({ isAuthenticated: false, isConfigured: false, username: null })
        setAccessSession({ isAuthorized: false, accessType: null, keyLabel: null, keyExpiresAtUtc: null, keyExpired: false, deviceLeaseSeconds: 90 })
      })
  }, [])

  useEffect(() => {
    if (adminSession && !adminSession.isAuthenticated && activeView === 'admin') {
      setActiveView('report')
      window.sessionStorage.setItem('greedy-active-view', 'report')
    }
  }, [activeView, adminSession])

  useEffect(() => {
    function denyAccess(event: Event) {
      const code = (event as CustomEvent<{ code?: string }>).detail?.code
      setAccessSession(current => ({
        isAuthorized: false,
        accessType: null,
        keyLabel: null,
        keyExpiresAtUtc: null,
        keyExpired: code === 'ACCESS_KEY_EXPIRED',
        deviceLeaseSeconds: current?.deviceLeaseSeconds ?? 90,
      }))
      setStats(null)
      setResults(emptyPage)
      setLatestResults([])
      setStatus(null)
    }
    window.addEventListener('greedy-access-denied', denyAccess)
    return () => window.removeEventListener('greedy-access-denied', denyAccess)
  }, [])

  useEffect(() => {
    if (!accessSession?.isAuthorized) return
    void refreshStats()
  }, [accessSession?.isAuthorized, refreshStats])

  useEffect(() => {
    if (!accessSession?.isAuthorized) return
    void refreshResults()
  }, [accessSession?.isAuthorized, refreshResults])

  useEffect(() => {
    if (!accessSession?.isAuthorized) return
    void refreshSecondary()
    const timer = window.setInterval(() => void refreshSecondary(), 10000)
    return () => window.clearInterval(timer)
  }, [accessSession?.isAuthorized, refreshSecondary])

  useEffect(() => {
    void refreshAdminData()
    if (!adminSession?.isAuthenticated) return
    const timer = window.setInterval(() => void refreshAdminData(), 3000)
    return () => window.clearInterval(timer)
  }, [adminSession?.isAuthenticated, refreshAdminData])

  useEffect(() => {
    if (!accessSession?.isAuthorized) return
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
  }, [accessSession?.isAuthorized, adminSession?.isAuthenticated, refreshResults, refreshSecondary, refreshStats, selectedDate, showResultAnnouncement])

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

  useEffect(() => {
    if (!accessSession?.isAuthorized || activeView !== 'report' || selectedDate !== bangkokToday()) return
    void refreshMarketPrediction(marketPredictionResponse === null)
    const timer = window.setInterval(() => void refreshMarketPrediction(false), 500)
    return () => window.clearInterval(timer)
  }, [accessSession?.isAuthorized, activeView, marketPredictionResponse === null, refreshMarketPrediction, selectedDate])

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
  const currentVisitor = useMemo(
    () => visitors.find(visitor => visitor.deviceId === getClientDeviceId()) ?? null,
    [visitors],
  )
  const onlineVisitors = useMemo(
    () => visitors.filter(visitor => visitor.isOnline),
    [visitors],
  )
  const streakConversationRuns = useMemo(
    () => [...(stats?.vegetableRuns ?? []), ...(stats?.meatRuns ?? [])]
      .sort((left, right) => Date.parse(right.endedAtUtc) - Date.parse(left.endedAtUtc)),
    [stats?.meatRuns, stats?.vegetableRuns],
  )

  function switchView(view: 'report' | 'admin') {
    if (view === 'admin' && !adminSession?.isAuthenticated) {
      setAdminAuthError(null)
      setAdminLoginOpen(true)
      return
    }
    setActiveView(view)
    window.sessionStorage.setItem('greedy-active-view', view)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function changeDate(value: string) {
    setSelectedDate(value)
    setPage(1)
    setResultsLimit(MOBILE_RESULTS_BATCH_SIZE)
    if (resultsScrollRef.current) resultsScrollRef.current.scrollTop = 0
    setSelectedBucket(null)
    setStreakSummaryOpen(false)
    setMarketPredictionResponse(null)
    setMarketPredictionError(null)
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

  async function saveWebhookConfig(enabled = alertWebhookConfig?.enabled ?? false) {
    setWebhookSaving(true)
    setWebhookMessage(null)
    try {
      const next = await api.saveAlertWebhookConfig(enabled, webhookUrlDraft.trim())
      setAlertWebhookConfig(next)
      setWebhookUrlDraft(next.webhookUrl)
      setWebhookMessage(next.enabled ? 'Đã bật thông báo webhook.' : 'Đã tắt thông báo webhook.')
    } catch (caught) {
      setWebhookMessage(caught instanceof Error ? caught.message : 'Không lưu được cấu hình webhook')
    } finally {
      setWebhookSaving(false)
    }
  }

  async function toggleWebhookConfig() {
    const nextEnabled = !(alertWebhookConfig?.enabled ?? false)
    await saveWebhookConfig(nextEnabled)
  }

  async function loginWithAccessKey(event: React.FormEvent) {
    event.preventDefault()
    setAccessKeyLoading(true)
    setAccessKeyError(null)
    try {
      const normalizedKey = accessKeyInput.trim().toUpperCase()
      const session = await api.accessLogin(normalizedKey, getClientDeviceId(), getClientDeviceName(), rememberAccessKey)
      setAccessSession(session)
      if (rememberAccessKey) {
        window.localStorage.setItem('greedy-remember-key', '1')
      } else {
        window.localStorage.setItem('greedy-remember-key', '0')
      }
      setAccessKeyInput('')
      setError(null)
    } catch (caught) {
      setAccessKeyError(caught instanceof Error ? caught.message : 'Không thể xác thực key')
    } finally {
      setAccessKeyLoading(false)
    }
  }

  async function logoutAccessKey() {
    await api.accessLogout()
    setAccessSession({
      isAuthorized: false,
      accessType: null,
      keyLabel: null,
      keyExpiresAtUtc: null,
      keyExpired: false,
      deviceLeaseSeconds: accessSession?.deviceLeaseSeconds ?? 90,
    })
  }

  async function createAccessKey(event: React.FormEvent) {
    event.preventDefault()
    const validDays = Number.parseInt(newKeyDays, 10)
    if (!Number.isFinite(validDays) || validDays < 1 || validDays > 3650) {
      setKeyAdminMessage('Số ngày phải từ 1 đến 3650.')
      return
    }
    setKeyAdminSaving(true)
    setKeyAdminMessage(null)
    try {
      const created = await api.createAccessKey(newKeyLabel.trim(), validDays)
      setCreatedAccessKey(created)
      setNewKeyLabel('')
      setKeyAdminMessage('Đã tạo key. Hãy sao chép ngay vì backend không lưu key gốc.')
      setAccessKeys(await api.accessKeys())
    } catch (caught) {
      setKeyAdminMessage(caught instanceof Error ? caught.message : 'Không tạo được key')
    } finally {
      setKeyAdminSaving(false)
    }
  }

  async function toggleAccessKey(item: AccessKey) {
    await api.setAccessKeyActive(item.id, !item.isActive)
    setAccessKeys(await api.accessKeys())
  }

  async function disconnectAccessKey(id: number) {
    await api.disconnectAccessKey(id)
    setAccessKeys(await api.accessKeys())
  }

  async function deleteAccessKey(item: AccessKey) {
    if (!window.confirm(`Xóa key của ${item.label}? Phiên đang dùng sẽ bị ngắt ngay.`)) return
    await api.deleteAccessKey(item.id)
    setAccessKeys(await api.accessKeys())
    if (createdAccessKey?.id === item.id) setCreatedAccessKey(null)
  }

  async function copyCreatedAccessKey() {
    if (!createdAccessKey) return
    await navigator.clipboard.writeText(createdAccessKey.key)
    setKeyAdminMessage('Đã sao chép key vào clipboard.')
  }

  async function loginAdmin(event: React.FormEvent) {
    event.preventDefault()
    setAdminAuthLoading(true)
    setAdminAuthError(null)
    try {
      const session = await api.adminLogin(adminUsername, adminPassword, rememberAdmin)
      setAdminSession(session)
      setAccessSession({ isAuthorized: true, accessType: 'admin', keyLabel: null, keyExpiresAtUtc: null, keyExpired: false, deviceLeaseSeconds: 90 })
      if (rememberAdmin) {
        window.localStorage.setItem('greedy-remember-admin', '1')
        window.localStorage.setItem('greedy-admin-username', adminUsername.trim())
      } else {
        window.localStorage.setItem('greedy-remember-admin', '0')
        window.localStorage.removeItem('greedy-admin-username')
      }
      setAdminPassword('')
      setAdminLoginOpen(false)
      setActiveView('admin')
      window.sessionStorage.setItem('greedy-active-view', 'admin')
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
      setAccessKeys([])
      setVisitors([])
      setSelectedVisitor(null)
      setActiveView('report')
      window.sessionStorage.setItem('greedy-active-view', 'report')
      try {
        setAccessSession(await api.accessSession())
      } catch {
        setAccessSession({ isAuthorized: false, accessType: null, keyLabel: null, keyExpiresAtUtc: null, keyExpired: false, deviceLeaseSeconds: 90 })
      }
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

  if (!accessSession?.isAuthorized) {
    return (
      <div className="access-gate" role="dialog" aria-modal="true" aria-labelledby="access-gate-title">
        <section className="access-gate-card">
          <div className="access-gate-brand"><span>G</span><div><small>GREEDY BIGO</small><strong id="access-gate-title">Nhập key để tiếp tục</strong></div></div>
          {accessSession === null ? (
            <div className="access-gate-loading"><span className="prediction-spinner" /><p>Đang kiểm tra quyền truy cập...</p></div>
          ) : (
            <>
              <p className="access-gate-description">Mỗi key chỉ dùng trên một thiết bị tại cùng một thời điểm. Nội dung chỉ được tải sau khi backend xác thực thành công.</p>
              <form className="access-key-form" onSubmit={loginWithAccessKey}>
                <label><span>Key truy cập</span><input value={accessKeyInput} onChange={event => setAccessKeyInput(event.target.value.toUpperCase())} placeholder="GRD-XXXXX-XXXXX-XXXXX-XXXXX" autoComplete="off" autoFocus required /></label>
                <label className="remember-login"><input type="checkbox" checked={rememberAccessKey} onChange={event => setRememberAccessKey(event.target.checked)} /><span>Duy trì đăng nhập key trên thiết bị này</span></label>
                {accessKeyError && <p className="admin-login-error">{accessKeyError}</p>}
                {accessSession.keyExpired && !accessKeyError && <p className="admin-login-error">Key đã hết hạn. Vui lòng xin key mới từ quản trị viên.</p>}
                <button className="access-key-submit" type="submit" disabled={accessKeyLoading}>{accessKeyLoading ? 'Đang xác thực...' : 'Mở trang thống kê'}</button>
              </form>
              <div className="access-gate-divider"><span>hoặc đăng nhập quản trị</span></div>
              <form className="admin-login-form access-admin-form" onSubmit={loginAdmin}>
                <div className="access-admin-inputs">
                  <label><span>Tài khoản Admin</span><input autoComplete="username" value={adminUsername} onChange={event => setAdminUsername(event.target.value)} required /></label>
                  <label><span>Mật khẩu</span><input type="password" autoComplete="current-password" value={adminPassword} onChange={event => setAdminPassword(event.target.value)} required /></label>
                </div>
                <label className="remember-login"><input type="checkbox" checked={rememberAdmin} onChange={event => setRememberAdmin(event.target.checked)} /><span>Duy trì đăng nhập Admin trong 30 ngày</span></label>
                {adminAuthError && <p className="admin-login-error">{adminAuthError}</p>}
                <button className="admin-login-submit" type="submit" disabled={adminAuthLoading || !adminSession?.isConfigured}>{adminAuthLoading ? 'Đang xác thực...' : 'Đăng nhập Admin'}</button>
              </form>
            </>
          )}
        </section>
      </div>
    )
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
        <div className={`topbar-actions ${accessSession.accessType === 'key' ? 'has-key-session' : ''}`}>
          {accessSession.accessType === 'key' && (
            <div className="key-session-badge">
              <span><small>KEY ĐANG DÙNG</small><strong>{accessSession.keyLabel || 'Người dùng'}</strong></span>
              <button type="button" onClick={() => void logoutAccessKey()}>Thoát key</button>
            </div>
          )}
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

      <nav className="workspace-tabs" aria-label="Chuyển khu vực">
        <button className={activeView === 'report' ? 'active' : ''} type="button" onClick={() => switchView('report')}>
          <span aria-hidden="true">⌁</span><span><strong>Báo cầu</strong><small>Thống kê & dự đoán</small></span>
        </button>
        <button className={activeView === 'admin' ? 'active admin' : 'admin'} type="button" onClick={() => switchView('admin')}>
          <span aria-hidden="true">⚙</span><span><strong>Quản trị</strong><small>{adminSession?.isAuthenticated ? 'Thiết bị & hệ thống' : 'Yêu cầu đăng nhập'}</small></span>
        </button>
      </nav>

      {error && <div className="error-banner">Không tải được backend: {error}</div>}
      {adminSession?.isAuthenticated && unacknowledgedEvents.length > 0 && (
        <div className="warning-banner">
          Có {unacknowledgedEvents.length} cảnh báo scanner chưa xác nhận. Xem mục “Sự kiện hệ thống”.
        </div>
      )}

      {activeView === 'report' && selectedDate === bangkokToday() && (
        <>
          <NextResultCountdown status={status} liveStatus={liveStatusRef} />
          <LiveBettingSignals status={status} />
        </>
      )}

      <main>
        {activeView === 'report' && <>
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

        <MarketPredictionPanel
          response={marketPredictionResponse}
          loading={marketPredictionLoading}
          error={marketPredictionError}
          onRefresh={() => void refreshMarketPrediction()}
        />

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

        </>}

        {activeView === 'admin' && adminSession?.isAuthenticated && <>
          <section className="admin-view-heading">
            <div><p className="panel-kicker">TRUNG TÂM QUẢN TRỊ</p><h2>Thiết bị, người dùng và vận hành</h2><p>Mỗi phiên được tách theo thiết bị và tab trình duyệt, kể cả khi dùng chung tài khoản Admin.</p></div>
            <div className="admin-live-summary"><strong>{onlineVisitors.length}</strong><span>phiên đang online</span></div>
          </section>

          <section className="admin-device-overview">
            <article className="panel current-device-card">
              <div className="panel-heading"><div><p className="panel-kicker">THIẾT BỊ CỦA TÔI</p><h2>Phiên đăng nhập hiện tại</h2></div><span className="current-device-badge">Đang dùng</span></div>
              {currentVisitor ? (
                <button className="current-device-button" type="button" onClick={() => setSelectedVisitor(currentVisitor)}>
                  <span className="device-avatar" aria-hidden="true">{currentVisitor.deviceType === 'Điện thoại' ? '▯' : '▰'}</span>
                  <span><strong>{currentVisitor.deviceName}</strong><small>{currentVisitor.browser} · {currentVisitor.platform}</small><small>IP {currentVisitor.clientIp} · {currentVisitor.city || currentVisitor.region || currentVisitor.country || 'Chưa có vị trí từ mạng'}</small></span>
                  <span className="device-online">● Online</span>
                </button>
              ) : <p className="muted">Đang nhận diện thông tin thiết bị hiện tại…</p>}
            </article>
            <article className="panel admin-status-card">
              <div><span>Scanner</span><strong>{status?.isOnline ? 'Đang chạy' : 'Ngoại tuyến'}</strong></div>
              <div><span>Admin online</span><strong>{onlineVisitors.filter(visitor => visitor.isAdmin).length}</strong></div>
              <div><span>User online</span><strong>{onlineVisitors.filter(visitor => !visitor.isAdmin).length}</strong></div>
            </article>
          </section>

          {selectedDate === bangkokToday() && <AutoPlayPanel adminSession={adminSession} />}
          {selectedDate === bangkokToday() && <PhonePreviewPanel status={status} adminSession={adminSession} />}

          <section className="lower-grid admin-only-section">
          <article className="panel compact-panel access-key-admin-panel">
            <div className="panel-heading">
              <div><p className="panel-kicker">CẤP QUYỀN</p><h2>Quản lý key người dùng</h2></div>
              <span className="subscriber-count">{accessKeys.filter(item => item.isActive && !item.isExpired).length} còn hạn</span>
            </div>
            <form className="access-key-create-form" onSubmit={createAccessKey}>
              <label><span>Tên người dùng / ghi chú</span><input value={newKeyLabel} onChange={event => setNewKeyLabel(event.target.value)} placeholder="Ví dụ: Khách A" maxLength={120} /></label>
              <label><span>Số ngày sử dụng</span><input type="number" min="1" max="3650" value={newKeyDays} onChange={event => setNewKeyDays(event.target.value)} required /></label>
              <button type="submit" disabled={keyAdminSaving}>{keyAdminSaving ? 'Đang tạo...' : 'Tạo key mới'}</button>
            </form>
            {createdAccessKey && (
              <div className="created-key-box">
                <small>KEY VỪA TẠO · CHỈ HIỂN THỊ LẦN NÀY</small>
                <strong>{createdAccessKey.key}</strong>
                <span>Hết hạn: {formatDate(createdAccessKey.expiresAtUtc)}</span>
                <button type="button" onClick={() => void copyCreatedAccessKey()}>Sao chép key</button>
              </div>
            )}
            {keyAdminMessage && <p className="form-message">{keyAdminMessage}</p>}
            <div className="access-key-list">
              {accessKeys.map(item => (
                <div className={`access-key-item ${item.isOnline ? 'online' : ''} ${!item.isActive || item.isExpired ? 'disabled' : ''}`} key={item.id}>
                  <div className="access-key-main">
                    <strong>{item.label}</strong>
                    <code>{item.keyHint}</code>
                    <small>Hết hạn {formatDate(item.expiresAtUtc)} · {item.isExpired ? 'Đã hết hạn' : item.isActive ? 'Đang mở' : 'Đã khóa'}</small>
                  </div>
                  <div className="access-key-device">
                    <strong>{item.isOnline ? '● Đang online' : '○ Không hoạt động'}</strong>
                    <span>{item.deviceName || 'Chưa đăng nhập'}{item.clientIp ? ` · ${item.clientIp}` : ''}</span>
                    <small>{item.lastSeenUtc ? `Hoạt động ${formatDate(item.lastSeenUtc)}` : 'Chưa có phiên thiết bị'}</small>
                  </div>
                  <div className="access-key-actions">
                    {item.lastSeenUtc && <button type="button" onClick={() => void disconnectAccessKey(item.id)}>Ngắt thiết bị</button>}
                    <button type="button" onClick={() => void toggleAccessKey(item)}>{item.isActive ? 'Khóa key' : 'Mở key'}</button>
                    <button type="button" onClick={() => void deleteAccessKey(item)}>Xóa</button>
                  </div>
                </div>
              ))}
              {accessKeys.length === 0 && <p className="muted">Chưa có key người dùng. Chọn số ngày rồi tạo key đầu tiên.</p>}
            </div>
          </article>

          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">SCANNER</p><h2>Sự kiện hệ thống</h2></div><span className="muted">Chỉ lỗi mất cầu 6 phút mới gửi webhook</span></div>
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
            <div className="panel-heading">
              <div><p className="panel-kicker">WEBHOOK</p><h2>Cảnh báo mất cập nhật cầu</h2></div>
              <span className={`webhook-status-pill ${alertWebhookConfig?.enabled ? 'online' : 'offline'}`}>
                {alertWebhookConfig?.enabled ? 'Đang bật' : 'Đang tắt'}
              </span>
            </div>
            <div className="webhook-config-box">
              <label>
                <span>URL webhook n8n / Zalo</span>
                <input value={webhookUrlDraft} onChange={event => setWebhookUrlDraft(event.target.value)} placeholder="http://localhost:5678/webhook/..." />
              </label>
              <div className="webhook-config-actions">
                <button type="button" onClick={() => void saveWebhookConfig()} disabled={webhookSaving}>Lưu URL</button>
                <button type="button" onClick={() => void toggleWebhookConfig()} disabled={webhookSaving || (!alertWebhookConfig?.enabled && webhookUrlDraft.trim().length === 0)}>
                  {alertWebhookConfig?.enabled ? 'Tắt thông báo' : 'Bật thông báo'}
                </button>
              </div>
              {webhookMessage && <p className="form-message">{webhookMessage}</p>}
            </div>
            <div className="alert-rules">
              <div><span className="rule-dot vegetable" /><strong>Không có cầu mới sau 6 phút</strong><small>Đây là điều kiện duy nhất được gửi tới webhook</small></div>
            </div>
            <p className="muted webhook-hint">Đã tắt cảnh báo bệt 3/10 và các lỗi nhận diện thiếu 8 ô. Các sự kiện đó vẫn có thể xem nội bộ ở mục Scanner nhưng không gửi Zalo.</p>
          </article>

          <article className="panel compact-panel">
            <div className="panel-heading"><div><p className="panel-kicker">TRUY CẬP</p><h2>Thiết bị và người dùng</h2></div><span className="subscriber-count">{onlineVisitors.length} online</span></div>
            <div className="visitor-list">
              {visitors.slice(0, 30).map(visitor => (
                <button className={`visitor-item ${visitor.isOnline ? 'online' : 'offline'} ${visitor.deviceId === getClientDeviceId() ? 'current' : ''}`} type="button" key={visitor.visitorId} onClick={() => setSelectedVisitor(visitor)}>
                  <span className="visitor-device-icon" aria-hidden="true">{visitor.deviceType === 'Điện thoại' ? '▯' : '▰'}</span>
                  <span className="visitor-main"><strong>{visitor.accountName || 'Khách chưa định danh'} · {visitor.deviceName}</strong><span>{visitor.accessType === 'admin' ? 'Quản trị viên' : visitor.accessType === 'key' ? 'Người dùng key' : 'Khách'} · IP {visitor.clientIp}</span><small>{visitor.browser} · {visitor.platform} · {visitor.city || visitor.region || visitor.country || 'Chưa có vị trí từ mạng'}</small></span>
                  <span className="visitor-last-seen"><b>{visitor.isOnline ? '● Online' : '○ Offline'}</b><time>{formatTime(visitor.lastSeenUtc)}</time></span>
                </button>
              ))}
              {visitors.length === 0 && <p className="muted">Chưa có lượt truy cập nào được ghi nhận.</p>}
            </div>
            <p className="muted webhook-hint">Bấm vào từng dòng để xem chi tiết. Vị trí chỉ hiển thị khi proxy/CDN gửi thông tin địa lý từ IP; hệ thống không tự xin quyền GPS.</p>
          </article>
          </section>
        </>}
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

      {selectedVisitor && (
        <div className="modal-backdrop" role="presentation" onClick={() => setSelectedVisitor(null)}>
          <section className="visitor-detail-modal" role="dialog" aria-modal="true" aria-labelledby="visitor-detail-title" onClick={event => event.stopPropagation()}>
            <div className="panel-heading visitor-detail-heading">
              <div><p className="panel-kicker">CHI TIẾT PHIÊN TRUY CẬP</p><h2 id="visitor-detail-title">{selectedVisitor.accountName || 'Khách chưa định danh'}</h2></div>
              <button className="close-button" type="button" aria-label="Đóng chi tiết thiết bị" onClick={() => setSelectedVisitor(null)}>×</button>
            </div>
            <div className="visitor-detail-hero">
              <span className="device-avatar" aria-hidden="true">{selectedVisitor.deviceType === 'Điện thoại' ? '▯' : '▰'}</span>
              <div><strong>{selectedVisitor.deviceName}</strong><span>{selectedVisitor.accessType === 'admin' ? 'Tài khoản Admin' : selectedVisitor.accessType === 'key' ? 'Tài khoản dùng key' : 'Chưa đăng nhập'}</span></div>
              <b className={selectedVisitor.isOnline ? 'online' : 'offline'}>{selectedVisitor.isOnline ? '● Đang online' : '○ Đã offline'}</b>
            </div>
            <dl className="visitor-detail-grid">
              <div><dt>Thiết bị</dt><dd>{selectedVisitor.deviceType}</dd></div>
              <div><dt>Trình duyệt</dt><dd>{selectedVisitor.browser}</dd></div>
              <div><dt>Nền tảng</dt><dd>{selectedVisitor.platform}</dd></div>
              <div><dt>Địa chỉ IP</dt><dd>{selectedVisitor.clientIp}</dd></div>
              <div><dt>Vị trí từ mạng</dt><dd>{[selectedVisitor.city, selectedVisitor.region, selectedVisitor.country].filter(Boolean).join(', ') || 'Không có dữ liệu'}</dd></div>
              <div><dt>Ngôn ngữ trình duyệt</dt><dd>{selectedVisitor.browserLanguage || 'Không xác định'}</dd></div>
              <div><dt>Múi giờ</dt><dd>{selectedVisitor.timeZone || 'Không xác định'}</dd></div>
              <div><dt>Màn hình</dt><dd>{selectedVisitor.screenSize || 'Không xác định'} · khung nhìn {selectedVisitor.viewportSize || 'Không xác định'}</dd></div>
              <div><dt>Mật độ điểm ảnh</dt><dd>{selectedVisitor.pixelRatio ?? 'Không xác định'}</dd></div>
              <div><dt>Cảm ứng</dt><dd>{selectedVisitor.touchPoints ?? 0} điểm chạm</dd></div>
              <div><dt>Kết nối</dt><dd>{selectedVisitor.connectionType || 'Không xác định'}</dd></div>
              <div><dt>Trang gần nhất</dt><dd>{selectedVisitor.lastMethod} {selectedVisitor.lastPath}</dd></div>
              <div><dt>Máy chủ / giao thức</dt><dd>{selectedVisitor.protocol || '—'} · {selectedVisitor.host || '—'}</dd></div>
              <div><dt>Nguồn truy cập</dt><dd>{selectedVisitor.referrer || 'Truy cập trực tiếp'}</dd></div>
              <div><dt>Bắt đầu phiên</dt><dd>{formatDate(selectedVisitor.firstSeenUtc)}</dd></div>
              <div><dt>Hoạt động gần nhất</dt><dd>{formatDate(selectedVisitor.lastSeenUtc)}</dd></div>
              <div><dt>Số request</dt><dd>{selectedVisitor.requestCount.toLocaleString('vi-VN')}</dd></div>
              <div className="visitor-user-agent"><dt>Chuỗi nhận diện trình duyệt</dt><dd>{selectedVisitor.userAgent}</dd></div>
              <div className="visitor-user-agent"><dt>Mã thiết bị / phiên</dt><dd>{selectedVisitor.deviceId} / {selectedVisitor.sessionId}</dd></div>
            </dl>
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
              <label className="remember-login"><input type="checkbox" checked={rememberAdmin} onChange={event => setRememberAdmin(event.target.checked)} /><span>Duy trì đăng nhập Admin trong 30 ngày</span></label>
              <p className="login-security-note">Mật khẩu được trình duyệt tự điền an toàn; ứng dụng không lưu mật khẩu dạng rõ.</p>
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
