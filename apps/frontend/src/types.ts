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

export interface BettingSignalItem {
  itemCode: string
  coinCount: number
  activityPercent: number
}

export interface BettingSignals {
  round: number | null
  observedAtUtc: string
  countdownSeconds: number
  hotItemCode: string | null
  items: BettingSignalItem[]
}

export interface ScannerStatus {
  status: string
  isOnline: boolean
  lastHeartbeatUtc: string | null
  lastResultId: number | null
  lastResultRevision: string | null
  lastSequence: string[]
  offlineAfterSeconds: number
  sourceSerial: string | null
  currentRound: number | null
  activeRound: number | null
  countdownSeconds: number | null
  countdownObservedAtUtc: string | null
  bettingSignals: BettingSignals | null
  financials: {
    observedAtUtc: string
    balanceUnits: number
    ownBets: Record<string, number>
  } | null
  serverUtc: string
}

export interface PhoneControlStatus {
  enabled: boolean
  connected: boolean
  serial: string | null
  model: string | null
  width: number | null
  height: number | null
  message: string | null
}

export interface AutoPlayStrategy {
  key: 'CAPITAL_GUARD' | 'VALUE_SINGLE' | 'BALANCED_MULTI' | 'TOP_TWO_COVER' | 'DIVERSIFIED_THREE'
  name: string
  description: string
  reservePercent: number
  maxStakePercent: number
  maxDailyLossPercent: number
  maxConsecutiveLosses: number
  minimumConfidencePercent: number
  minimumTopGapPercent: number
  minimumExpectedEdgePercent: number
  maximumPayoutMultiplier: number
  maximumSelections: number
  riskLevel: string
}

export interface AutoPlayConfiguration {
  enabled: boolean
  mode: 'SIMULATION' | 'LIVE'
  strategy: AutoPlayStrategy['key']
  bankrollUnits: number
  chipValue: number
  tapsPerItem: number
  liveExecutionAvailable: boolean
  detectedBalanceUnits: number | null
  balanceObservedAtUtc: string | null
  strategySettings: AutoPlayStrategySettings[]
}

export interface AutoPlayStrategySettings {
  strategy: AutoPlayStrategy['key']
  maxConsecutiveLosses: number
  minimumVegetableProbabilityPercent: number
  minimumMeatProbabilityPercent: number
  maximumSelections: number
  lossRecoveryEnabled: boolean
  lossRecoveryMultiplier: number
  maximumRecoverySteps: number
}

export interface AutoPlayConfigurationRequest {
  enabled: boolean
  mode: 'SIMULATION' | 'LIVE'
  strategy: AutoPlayStrategy['key']
  bankrollUnits: number
  chipValue: number
  tapsPerItem: number
  maxConsecutiveLosses: number
  minimumVegetableProbabilityPercent: number
  minimumMeatProbabilityPercent: number
  maximumSelections: number
  lossRecoveryEnabled: boolean
  lossRecoveryMultiplier: number
  maximumRecoverySteps: number
  liveModeAcknowledged: boolean
}

export interface AutoPlayBet {
  itemCode: string
  itemName: string
  stakeUnits: number
  tapCount: number
  payoutMultiplier: number
  probabilityPercent: number
  expectedEdgePercent: number
}

export interface AutoPlayVerification {
  status: string
  balanceBeforeUnits: number | null
  balanceAfterUnits: number | null
  expectedDebitUnits: number
  scannedBets: Record<string, number>
  message: string
}

export interface AutoPlayAction {
  id: string
  runId: string | null
  localDate: string
  roundNumber: number
  mode: 'SIMULATION' | 'LIVE'
  strategy: AutoPlayStrategy['key']
  bets: AutoPlayBet[]
  totalStakeUnits: number
  confidencePercent: number
  topGapPercent: number
  expectedEdgePercent: number
  status: 'SKIPPED' | 'SIMULATED' | 'PLACED' | 'PLACED_UNVERIFIED' | 'WON' | 'LOST' | 'BREAK_EVEN' | 'ERROR' | 'PENDING' | 'UNVERIFIED' | 'NOT_PLACED'
  reason: string
  createdAtUtc: string
  settledAtUtc: string | null
  resultItemCode: string | null
  netUnits: number | null
  riskLevel: string
  recoveryStep: number
  stakeMultiplier: number
  verification: AutoPlayVerification
  participationDiamonds?: number
  participationStatus?: string | null
}

export interface AutoPlayRun {
  id: string
  mode: 'SIMULATION' | 'LIVE'
  strategy: AutoPlayStrategy['key']
  startedAtUtc: string
  endedAtUtc: string | null
  startingBalanceUnits: number
  currentBalanceUnits: number
  endingBalanceUnits: number | null
  netUnits: number
  totalStakeUnits: number
  betRounds: number
  skippedRounds: number
  wonRounds: number
  lostRounds: number
  highRiskSkippedRounds: number
  status: string
  participationDiamonds?: number
}

export interface AutoPlayStatus {
  configuration: AutoPlayConfiguration
  strategies: AutoPlayStrategy[]
  engineStatus: string
  message: string
  activeRound: number | null
  countdownSeconds: number | null
  lastEvaluatedRound: string | null
  todayStakeUnits: number
  todayNetUnits: number
  consecutiveLosses: number
  currentRecoveryStep: number
  protectedReserveUnits: number
  lastAction: AutoPlayAction | null
  recentActions: AutoPlayAction[]
  currentRun: AutoPlayRun | null
  recentRuns: AutoPlayRun[]
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

export interface AlertWebhookConfig {
  enabled: boolean
  configured: boolean
  webhookUrl: string
  updatedAtUtc: string | null
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

export interface HousePerformance {
  analyzedRounds: number
  estimatedStakeUnits: number
  estimatedPayoutUnits: number
  estimatedNetUnits: number
  estimatedMarginPercent: number
  houseWinningRounds: number
  houseLosingRounds: number
  hotObservedRounds: number
  hotHitRatePercent: number
  riskAvoidanceScorePercent: number
  highestCurrentLiabilityItemCode: string | null
  highestCurrentLiabilityUnits: number
  currentEstimatedStakeUnits: number
  currentOutcomeScenarios: HouseOutcomeScenario[]
}

export interface HouseOutcomeScenario {
  itemCode: string
  coinLevel: number
  estimatedPayoutUnits: number
  estimatedHouseNetUnits: number
}

export interface HotOutcomeStat {
  hotItemCode: string
  outcomeItemCode: string
  hotObservedRounds: number
  outcomeRounds: number
  outcomeRatePercent: number
}

export interface MarketSideForecast {
  redProbabilityPercent: number
  greenProbabilityPercent: number
  currentSide: 'RED' | 'GREEN' | null
  currentStreak: number
  matchedTransitions: number
  signalWeightPercent: number
}

export interface MarketPredictionResponse {
  status: 'WAITING_ROUND' | 'WAITING_SIGNAL' | 'WAITING_HOT' | 'COLLECTING_COINS' | 'WAITING_NEXT_ROUND' | 'NO_LIVE_ROUND' | 'READY'
  message: string
  roundNumber: number | null
  countdownSeconds: number | null
  observedAtUtc: string | null
  hotItemCode: string | null
  analysisWindow: number
  matchedSignalRounds: number
  housePerformance: HousePerformance
  hotOutcomeStats: HotOutcomeStat[]
  sideForecast: MarketSideForecast | null
  computeDevice: 'CPU'
  analysisDurationMs: number
  prediction: Prediction | null
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

export interface AccessSession {
  isAuthorized: boolean
  accessType: 'admin' | 'key' | null
  keyLabel: string | null
  keyExpiresAtUtc: string | null
  keyExpired: boolean
  deviceLeaseSeconds: number
}

export interface AccessKey {
  id: number
  keyHint: string
  label: string
  expiresAtUtc: string
  isActive: boolean
  isExpired: boolean
  createdAtUtc: string
  deviceName: string | null
  clientIp: string | null
  lastSeenUtc: string | null
  isOnline: boolean
}

export interface CreatedAccessKey {
  id: number
  key: string
  keyHint: string
  label: string
  expiresAtUtc: string
  isActive: boolean
  createdAtUtc: string
}

export interface Visitor {
  visitorId: string
  deviceId: string
  sessionId: string
  deviceName: string
  deviceType: string
  platform: string
  browser: string
  clientIp: string
  country: string | null
  region: string | null
  city: string | null
  userAgent: string
  browserLanguage: string | null
  timeZone: string | null
  screenSize: string | null
  viewportSize: string | null
  pixelRatio: number | null
  touchPoints: number | null
  connectionType: string | null
  accessType: 'admin' | 'key' | 'guest'
  accountName: string | null
  isAdmin: boolean
  adminName: string | null
  lastMethod: string
  lastPath: string
  referrer: string | null
  host: string | null
  protocol: string | null
  firstSeenUtc: string
  lastSeenUtc: string
  isOnline: boolean
  requestCount: number
}
