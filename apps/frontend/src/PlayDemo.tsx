import { useEffect, useMemo, useRef, useState } from 'react'
import type { RefObject } from 'react'

import { ITEM_META } from './items'
import type { ScannerStatus } from './types'


type DemoResult = {
  resultId: number
  round: number | null
  code: string
  wager: number
  reward: number
  shownAt: number
  corrected: boolean
}

const STARTING_BALANCE = 10_000
const RESULT_VISIBLE_MS = 5_000
const CHIP_VALUES = [2, 10, 50, 100, 1000]
const MAIN_CODES = [
  'BANH_MI', 'CA_CHUA', 'XIEN', 'CAI',
  'DUI', 'NGO', 'BO', 'CA_ROT',
] as const
const POSITIONS = [
  'top', 'upper-left', 'upper-right', 'middle-left',
  'middle-right', 'lower-left', 'lower-right', 'bottom',
]
const SPECIAL_CODES = ['SALAD', 'PIZZA'] as const

function formatPoints(value: number): string {
  return new Intl.NumberFormat('vi-VN').format(value)
}

function remainingSeconds(status: ScannerStatus | null, nowMs: number): number | null {
  if (status?.countdownSeconds === null || status?.countdownSeconds === undefined) return null
  const observedAt = Date.parse(status.countdownObservedAtUtc ?? '')
  if (!Number.isFinite(observedAt)) return null
  const elapsed = Math.max(0, Math.floor((nowMs - observedAt) / 1000))
  return Math.max(0, Math.min(30, status.countdownSeconds - elapsed))
}

export default function PlayDemo({
  open,
  status,
  liveStatus,
  onClose,
}: {
  open: boolean
  status: ScannerStatus | null
  liveStatus: RefObject<ScannerStatus | null>
  onClose: () => void
}) {
  const [balance, setBalance] = useState(STARTING_BALANCE)
  const [selectedChip, setSelectedChip] = useState(10)
  const [bets, setBets] = useState<Record<string, number>>({})
  const [result, setResult] = useState<DemoResult | null>(null)
  const [nowMs, setNowMs] = useState(Date.now)
  const sessionActive = useRef(false)
  const observedResultId = useRef<number | null>(null)
  const observedRevision = useRef<string | null>(null)
  const observedCode = useRef<string | null>(null)
  const betsRef = useRef(bets)
  const resultRef = useRef<DemoResult | null>(null)

  betsRef.current = bets
  resultRef.current = result

  const snapshot = liveStatus.current ?? status
  const countdown = remainingSeconds(snapshot, nowMs)
  const acceptingBets = Boolean(snapshot?.isOnline) && countdown !== null && countdown > 0 && result === null
  const displayRound = result?.round ?? snapshot?.activeRound ?? (
    snapshot?.currentRound === null || snapshot?.currentRound === undefined
      ? '—'
      : snapshot.currentRound + 1
  )
  const history = snapshot?.lastSequence ?? []
  const totalBet = useMemo(
    () => Object.values(bets).reduce((sum, value) => sum + value, 0),
    [bets],
  )
  const resultSeconds = result
    ? Math.max(0, Math.ceil((result.shownAt + RESULT_VISIBLE_MS - nowMs) / 1000))
    : 0

  function commitResult(next: DemoResult | null) {
    resultRef.current = next
    setResult(next)
  }

  useEffect(() => {
    if (!open) return
    const update = () => {
      const current = liveStatus.current ?? status
      const serverNow = Date.parse(current?.serverUtc ?? '')
      const offset = Number.isFinite(serverNow) ? serverNow - Date.now() : 0
      setNowMs(Date.now() + offset)
    }
    update()
    const timer = window.setInterval(update, 250)
    return () => window.clearInterval(timer)
  }, [liveStatus, open, status])

  useEffect(() => {
    if (!open || sessionActive.current) return
    const current = liveStatus.current ?? status
    sessionActive.current = true
    observedResultId.current = current?.lastResultId ?? null
    observedRevision.current = current?.lastResultRevision ?? null
    observedCode.current = current?.lastSequence[0] ?? null
  }, [liveStatus, open, status])

  useEffect(() => {
    if (!sessionActive.current) return
    const current = liveStatus.current ?? status
    if (!current?.lastResultId) return
    const code = current.lastSequence[0]
    if (!code || !ITEM_META[code]) return

    if (observedResultId.current !== current.lastResultId) {
      observedResultId.current = current.lastResultId
      observedRevision.current = current.lastResultRevision
      observedCode.current = code
      const wager = betsRef.current[code] ?? 0
      const reward = wager * ITEM_META[code].payout
      if (reward > 0) setBalance(value => value + reward)
      commitResult({
        resultId: current.lastResultId,
        round: current.currentRound,
        code,
        wager,
        reward,
        shownAt: Date.now(),
        corrected: false,
      })
      return
    }

    if (observedRevision.current !== current.lastResultRevision) {
      observedRevision.current = current.lastResultRevision
      const previousCode = observedCode.current
      observedCode.current = code
      const previous = resultRef.current
      if (previous && previous.resultId === current.lastResultId && previousCode !== code) {
        const wager = betsRef.current[code] ?? 0
        const reward = wager * ITEM_META[code].payout
        setBalance(value => value - previous.reward + reward)
        commitResult({
          ...previous,
          code,
          wager,
          reward,
          shownAt: Date.now(),
          corrected: true,
        })
      }
    }
  }, [liveStatus, nowMs, status])

  useEffect(() => {
    if (!result || nowMs < result.shownAt + RESULT_VISIBLE_MS) return
    commitResult(null)
    setBets({})
  }, [nowMs, result])

  useEffect(() => {
    if (!open) return
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [onClose, open])

  function placeBet(code: string) {
    if (!acceptingBets || balance < selectedChip) return
    setBalance(value => value - selectedChip)
    setBets(current => ({ ...current, [code]: (current[code] ?? 0) + selectedChip }))
  }

  function clearBets() {
    if (!acceptingBets || totalBet === 0) return
    setBalance(value => value + totalBet)
    setBets({})
  }

  function dismissResult() {
    commitResult(null)
    setBets({})
  }

  function resetDemo() {
    const current = liveStatus.current ?? status
    setBalance(STARTING_BALANCE)
    setSelectedChip(10)
    setBets({})
    commitResult(null)
    observedResultId.current = current?.lastResultId ?? null
    observedRevision.current = current?.lastResultRevision ?? null
    observedCode.current = current?.lastSequence[0] ?? null
  }

  if (!open) return null

  const countdownText = !snapshot?.isOnline
    ? 'Mất kết nối'
    : countdown === null
      ? 'Đang đồng bộ'
      : countdown === 0
        ? 'Chờ kết quả'
        : `${countdown}s`

  return (
    <div className="demo-overlay" role="presentation" onPointerDown={onClose}>
      <section
        className="demo-game synced"
        role="dialog"
        aria-modal="true"
        aria-labelledby="demo-game-title"
        onPointerDown={event => event.stopPropagation()}
      >
        <header className="demo-game-header">
          <button type="button" className="demo-back" onClick={onClose} aria-label="Đóng chơi thử">×</button>
          <div>
            <strong id="demo-game-title">Greedy · Chơi thử</strong>
            <small><i /> ĐỒNG BỘ KẾT QUẢ TRỰC TIẾP</small>
          </div>
          <span>Vòng {displayRound}</span>
        </header>

        <div className="demo-stage">
          <div className="demo-stage-pattern" aria-hidden="true" />
          <div className="demo-wheel-lines" aria-hidden="true" />

          {MAIN_CODES.map((code, index) => {
            const item = ITEM_META[code]
            return (
              <button
                type="button"
                className={`demo-food ${item.category.toLowerCase()} ${POSITIONS[index]} ${result?.code === code ? 'winner' : ''}`}
                key={code}
                disabled={!acceptingBets || balance < selectedChip}
                onClick={() => placeBet(code)}
              >
                <span className="demo-food-image" aria-hidden="true">{item.icon}</span>
                <strong>{item.name}</strong>
                <small>Thắng x{item.payout}</small>
                {(bets[code] ?? 0) > 0 && <b>🪙 {formatPoints(bets[code])}</b>}
              </button>
            )
          })}

          <div className={`demo-wheel-center ${result ? 'result' : 'betting'}`}>
            <span>{result ? 'Cầu vừa về' : 'Cầu tiếp theo'}</span>
            <strong>{result ? ITEM_META[result.code].icon : countdownText}</strong>
            <small>{result ? ITEM_META[result.code].name : 'Theo đồng hồ thật'}</small>
          </div>

          <div className="demo-special-row">
            {SPECIAL_CODES.map(code => {
              const item = ITEM_META[code]
              return (
                <button
                  type="button"
                  className={`demo-special ${code.toLowerCase()} ${result?.code === code ? 'winner' : ''}`}
                  key={code}
                  disabled={!acceptingBets || balance < selectedChip}
                  onClick={() => placeBet(code)}
                >
                  <span aria-hidden="true">{item.icon}</span>
                  <strong>{item.name}</strong>
                  <small>x{item.payout}</small>
                  {(bets[code] ?? 0) > 0 && <b>🪙 {formatPoints(bets[code])}</b>}
                </button>
              )
            })}
          </div>
        </div>

        <div className="demo-betting-panel">
          <div className="demo-betting-title">
            <span>{acceptingBets ? 'Chọn xu rồi chạm vào vật phẩm' : 'Đã khóa cược · chờ cầu về'}</span>
            <button type="button" onClick={clearBets} disabled={!acceptingBets || totalBet === 0}>Hoàn cược</button>
          </div>
          <div className="demo-chip-row">
            {CHIP_VALUES.map(value => (
              <button
                type="button"
                className={selectedChip === value ? 'selected' : ''}
                key={value}
                onClick={() => setSelectedChip(value)}
                aria-label={`Chọn xu ${value}`}
              >
                <span>{value}</span>
              </button>
            ))}
          </div>
          <div className="demo-wallet-row">
            <div><small>Số dư điểm thử</small><strong>🪙 {formatPoints(balance)}</strong></div>
            <div><small>Đã đặt vòng {displayRound}</small><strong>{formatPoints(totalBet)}</strong></div>
            <button type="button" onClick={resetDemo}>Đặt lại điểm</button>
          </div>
        </div>

        <div className="demo-history">
          <div>
            {history.length === 0 && <small>Đang tải lịch sử...</small>}
            {history.slice(0, 8).map((code, index) => {
              const item = ITEM_META[code]
              return (
                <span className={index === 0 ? 'new' : ''} title={item?.name ?? code} key={`${code}-${index}`}>
                  {item?.icon ?? '•'}
                </span>
              )
            })}
          </div>
        </div>

        {result && (
          <div className="demo-result-layer" role="status" aria-live="assertive" onPointerDown={dismissResult}>
            <div
              className={`demo-result-card ${ITEM_META[result.code].category.toLowerCase()}`}
              onPointerDown={event => event.stopPropagation()}
            >
              <small>VÒNG {result.round ?? '—'}</small>
              <div className="demo-result-icon" aria-hidden="true">{ITEM_META[result.code].icon}</div>
              <h2>{ITEM_META[result.code].name.startsWith('Nổ ') ? ITEM_META[result.code].name : `Nổ ${ITEM_META[result.code].name}`}</h2>
              {result.wager > 0
                ? <p className="won">Bạn nhận 🪙 {formatPoints(result.reward)} điểm thử</p>
                : <p>Bạn không đặt cửa này · không trừ thêm điểm</p>}
              <button type="button" onClick={dismissResult}>
                Tiếp tục cược ({resultSeconds}s)
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
