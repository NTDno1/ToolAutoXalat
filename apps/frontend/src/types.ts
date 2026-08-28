export type Category = 'VEGETABLE' | 'MEAT'

export interface ResultItem {
  id: number
  itemCode: string
  itemName: string
  category: Category
  detectedAtUtc: string
  confidence: number
  sequence: string[]
  detectionReason: string
  capturePath: string | null
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
  startedAtUtc: string
  endedAtUtc: string
}

export interface TodayStats {
  localDate: string
  totalResults: number
  vegetableCount: number
  meatCount: number
  currentVegetableStreak: number
  currentMeatStreak: number
  longestVegetableStreak: number
  longestMeatStreak: number
  itemCounts: Record<string, number>
  vegetableRuns: StreakRun[]
  meatRuns: StreakRun[]
  latestResult: ResultItem | null
}

export interface ScannerStatus {
  status: string
  isOnline: boolean
  lastHeartbeatUtc: string | null
  lastResultId: number | null
  lastSequence: string[]
  offlineAfterSeconds: number
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
