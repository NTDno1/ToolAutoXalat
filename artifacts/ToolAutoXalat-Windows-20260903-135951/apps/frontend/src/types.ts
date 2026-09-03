export type Category = 'VEGETABLE' | 'MEAT' | 'SPECIAL'

export interface ResultItem {
  id: number
  roundNumber: number | null
  roundLocalDate: string | null
  sourceSerial: string
  itemCode: string
  itemName: string
  category: Category
  detectedAtUtc: string
  confidence: number
  sequence: string[]
  detectionReason: string
  capturePath: string | null
  streakLength: number
}

export interface Page<T> {
  items: T[]
  page: number
  pageSize: number
  totalItems: number
  totalPages: number
}

export interface StreakRun {
  category: Category
  length: number
  startResultId: number
  endResultId: number
  startRound: number | null
  endRound: number | null
  startedAtUtc: string
  endedAtUtc: string
  items: StreakRunItem[]
}

export interface StreakRunItem {
  resultId: number
  roundNumber: number | null
  itemCode: string
  itemName: string
  detectedAtUtc: string
}

export interface StreakBucket {
  length: number
  count: number
  runs: StreakRun[]
}

export interface DailyStats {
  localDate: string
  totalResults: number
  roundCount: number
  missedRoundCount: number
  vegetableCount: number
  meatCount: number
  specialCount: number
  currentVegetableStreak: number
  currentMeatStreak: number
  longestVegetableStreak: number
  longestMeatStreak: number
  itemCounts: Record<string, number>
  vegetableRuns: StreakRun[]
  meatRuns: StreakRun[]
  vegetableStreakBuckets: StreakBucket[]
  meatStreakBuckets: StreakBucket[]
  latestResult: ResultItem | null
}

export interface DailySummary {
  localDate: string
  totalResults: number
  roundCount: number
  missedRoundCount: number
  vegetableCount: number
  meatCount: number
  specialCount: number
  longestVegetableStreak: number
  longestMeatStreak: number
}

export interface ScannerStatus {
  status: string
  isOnline: boolean
  lastHeartbeatUtc: string | null
  lastResultId: number | null
  lastSequence: string[]
  offlineAfterSeconds: number
  sourceSerial: string | null
  currentRound: number | null
  countdownSeconds: number | null
  countdownObservedAtUtc: string | null
  serverUtc: string
}

export interface ScannerEvent {
  id: number
  severity: string
  eventCode: string
  message: string
  details: Record<string, unknown>
  occurredAtUtc: string
  acknowledged: boolean
}

export interface AlertDelivery {
  id: number
  alertKey: string
  ruleCode: string
  category: Category
  streakLength: number
  resultId: number
  status: string
  responseStatus: number | null
  createdAtUtc: string
  attemptedAtUtc: string | null
}

export interface PredictionItem {
  itemCode: string
  itemName: string
  category: Category
  payoutMultiplier: number
  historicalCount: number
  todayCount: number
  probabilityPercent: number
  signalScore: number
  patternMatches: number
  reason: string
}

export interface Prediction {
  provider: string
  localDate: string
  generatedAtUtc: string
  items: PredictionItem[]
  methodNote: string
  modelConfidencePercent: number
  vegetableProbabilityPercent: number
  meatProbabilityPercent: number
  leadingItemCode: string
}

export interface AiPredictionResponse {
  status: 'READY' | 'NOT_CONFIGURED' | 'ERROR'
  isConfigured: boolean
  model: string
  prediction: Prediction | null
  message: string | null
}

export interface Subscriber {
  id: number
  phoneNumber: string
  displayName: string | null
  isActive: boolean
  createdAtUtc: string
  updatedAtUtc: string
}

export interface PaymentConfig {
  isConfigured: boolean
  qrImageUrl: string
  amount: number
  currency: string
  bankName: string
  accountName: string
  accountNumber: string
  transferPrefix: string
  instructions: string
}

export interface AdminSession {
  isAuthenticated: boolean
  isConfigured: boolean
  username: string | null
}
