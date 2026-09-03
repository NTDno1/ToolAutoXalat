import { spawn } from 'node:child_process'
import { mkdir, mkdtemp, writeFile } from 'node:fs/promises'
import path from 'node:path'

const projectRoot = path.resolve(import.meta.dirname, '..')
const artifactDir = path.join(projectRoot, 'artifacts')
const runtimeDir = path.join(projectRoot, 'runtime')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const targetUrl = process.argv[2] ?? 'http://127.0.0.1:5173/'
const testAiManual = process.argv.includes('--click-ai')
const debuggingPort = 9337

await mkdir(artifactDir, { recursive: true })
await mkdir(runtimeDir, { recursive: true })
const profileDir = await mkdtemp(path.join(runtimeDir, 'responsive-cdp-'))
const chrome = spawn(chromePath, [
  '--headless=new',
  '--disable-gpu',
  '--hide-scrollbars',
  '--no-first-run',
  '--no-default-browser-check',
  `--remote-debugging-port=${debuggingPort}`,
  `--user-data-dir=${profileDir}`,
  'about:blank',
], { windowsHide: true, stdio: 'ignore' })

const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

async function findPage() {
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      const response = await fetch(`http://127.0.0.1:${debuggingPort}/json`)
      const targets = await response.json()
      const page = targets.find(target => target.type === 'page')
      if (page?.webSocketDebuggerUrl) return page
    } catch {
      // Chrome is still starting.
    }
    await delay(100)
  }
  throw new Error('Chrome DevTools endpoint did not become ready.')
}

const page = await findPage()
const socket = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, { once: true })
  socket.addEventListener('error', reject, { once: true })
})

let commandId = 0
const pending = new Map()
const eventWaiters = new Map()
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data)
  if (message.id && pending.has(message.id)) {
    const { resolve, reject } = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) reject(new Error(message.error.message))
    else resolve(message.result)
    return
  }
  const waiters = eventWaiters.get(message.method)
  if (waiters?.length) waiters.shift()(message.params)
})

function send(method, params = {}) {
  const id = ++commandId
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    socket.send(JSON.stringify({ id, method, params }))
  })
}

function waitForEvent(method, timeout = 15000) {
  return new Promise((resolve, reject) => {
    const waiters = eventWaiters.get(method) ?? []
    const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), timeout)
    waiters.push(value => {
      clearTimeout(timer)
      resolve(value)
    })
    eventWaiters.set(method, waiters)
  })
}

async function evaluate(expression) {
  const response = await send('Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  return response.result.value
}

async function screenshot(name) {
  const response = await send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  })
  const outputPath = path.join(artifactDir, name)
  await writeFile(outputPath, Buffer.from(response.data, 'base64'))
  return outputPath
}

await send('Page.enable')
await send('Runtime.enable')
const viewports = [
  { width: 360, height: 800 },
  { width: 390, height: 844 },
  { width: 430, height: 932 },
  { width: 1366, height: 768 },
]
const checks = []

try {
  for (const viewport of viewports) {
    await send('Emulation.setDeviceMetricsOverride', {
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: 1,
      mobile: true,
      screenWidth: viewport.width,
      screenHeight: viewport.height,
    })
    await send('Emulation.setTouchEmulationEnabled', { enabled: true, maxTouchPoints: 5 })
    const loaded = waitForEvent('Page.loadEventFired')
    await send('Page.navigate', { url: targetUrl })
    await loaded
    await delay(3500)
    const metrics = await evaluate(`(() => ({
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      metricCards: document.querySelectorAll('.metric-card').length,
      historySlots: document.querySelectorAll('.sequence-item').length,
      itemCards: document.querySelectorAll('.item-stat').length,
      predictionPanels: document.querySelectorAll('.prediction-panel').length,
      predictionCards: document.querySelectorAll('.prediction-item').length,
      tableColumns: document.querySelectorAll('table thead th').length,
      hasPagination: document.querySelector('.pagination') !== null,
      hasStreakChatLaunch: document.querySelector('.streak-chat-launch') !== null,
      hasGlobalHorizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1
    }))()`)
    const topScreenshot = await screenshot(`responsive_${viewport.width}_top.png`)
    checks.push({ ...viewport, ...metrics, topScreenshot })

    if (viewport.width === 1366) {
      const datePicker = await evaluate(`(() => {
        const input = document.querySelector('#statistics-date')
        const label = document.querySelector('.date-picker')
        window.__datePickerOpenCount = 0
        Object.defineProperty(input, 'showPicker', { configurable: true, value: () => { window.__datePickerOpenCount++ } })
        label.querySelector('.date-picker-icon')?.click()
        label.querySelector('.date-picker-copy')?.click()
        label.querySelector('.date-picker-chevron')?.click()
        label.click()
        label.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
        return { openCount: window.__datePickerOpenCount, allAreasOpen: window.__datePickerOpenCount === 5 }
      })()`)
      checks.push({ section: 'desktop-date-picker', ...datePicker })
      await evaluate("document.querySelector('.results-panel')?.scrollIntoView({ block: 'start' })")
      await delay(250)
      const firstPageRound = await evaluate("document.querySelector('.results-panel tbody .round-cell')?.textContent ?? ''")
      const pageBefore = await evaluate("document.querySelector('.pagination span')?.textContent ?? ''")
      await evaluate("document.querySelector('.pagination button:last-child')?.click()")
      for (let attempt = 0; attempt < 20; attempt++) {
        const pageAfter = await evaluate("document.querySelector('.pagination span')?.textContent ?? ''")
        if (pageAfter !== pageBefore) break
        await delay(100)
      }
      await delay(200)
      const desktopPagination = await evaluate(`(() => ({
        pageBefore: ${JSON.stringify(pageBefore)},
        pageAfter: document.querySelector('.pagination span')?.textContent ?? '',
        firstPageRound: ${JSON.stringify(firstPageRound)},
        secondPageRound: document.querySelector('.results-panel tbody .round-cell')?.textContent ?? '',
        rows: document.querySelectorAll('.results-panel tbody tr').length,
        hasInfiniteStatus: document.querySelector('.infinite-results-status') !== null
      }))()`)
      checks.push({ section: 'desktop-pagination', ...desktopPagination, screenshot: await screenshot('responsive_1366_database.png') })
      await evaluate("document.querySelector('.streak-chat-launch')?.click()")
      await delay(200)
      const desktopChat = await evaluate(`(() => {
        const panel = document.querySelector('.streak-chat-modal')?.getBoundingClientRect()
        return {
          panelWidth: Math.round(panel?.width ?? 0),
          panelHeight: Math.round(panel?.height ?? 0),
          viewportWidth: window.innerWidth,
          viewportHeight: window.innerHeight,
          closeVisible: document.querySelector('.streak-chat-close') !== null
        }
      })()`)
      checks.push({ section: 'streak-chat-desktop', ...desktopChat, screenshot: await screenshot('responsive_1366_streak_chat.png') })
      await evaluate("document.querySelector('.streak-chat-close')?.click()")
    }

    if (viewport.width === 390) {
      await evaluate("document.querySelector('.local-prediction-panel')?.scrollIntoView({block:'start'})")
      await delay(400)
      checks.push({ section: 'prediction', screenshot: await screenshot('responsive_390_prediction.png') })
      if (testAiManual) {
        const initialButtonText = await evaluate("document.querySelector('.ai-prediction-actions button')?.textContent ?? ''")
        await evaluate("document.querySelector('.ai-prediction-actions button')?.click()")
        for (let attempt = 0; attempt < 60; attempt++) {
          const state = await evaluate(`(() => ({
            loading: document.querySelector('.ai-prediction-panel')?.classList.contains('is-loading') ?? false,
            cards: document.querySelectorAll('.ai-prediction-panel .prediction-item').length
          }))()`)
          if (!state.loading && state.cards === 8) break
          await delay(250)
        }
        const manualState = await evaluate(`(() => ({
          initialButtonText: ${JSON.stringify(initialButtonText)},
          finalButtonText: document.querySelector('.ai-prediction-actions button')?.textContent ?? '',
          cards: document.querySelectorAll('.ai-prediction-panel .prediction-item').length,
          categoryCards: document.querySelectorAll('.ai-prediction-panel .category-forecast-grid article').length,
          error: document.querySelector('.ai-prediction-state.error')?.textContent ?? ''
        }))()`)
        checks.push({
          section: 'ai-manual',
          ...manualState,
          screenshot: await screenshot('responsive_390_ai_manual.png'),
        })
      }
      await evaluate("document.querySelector('.table-wrap')?.scrollIntoView({block:'start'})")
      await delay(400)
      checks.push({ section: 'database', screenshot: await screenshot('responsive_390_database.png') })
      const initialRows = await evaluate("document.querySelectorAll('.results-infinite-scroll tbody tr').length")
      await evaluate("(() => { const target = document.querySelector('.results-infinite-scroll'); if (target) target.scrollTop = target.scrollHeight })()")
      for (let attempt = 0; attempt < 20; attempt++) {
        const loadedRows = await evaluate("document.querySelectorAll('.results-infinite-scroll tbody tr').length")
        if (loadedRows > initialRows) break
        await delay(200)
      }
      const loadedRows = await evaluate("document.querySelectorAll('.results-infinite-scroll tbody tr').length")
      checks.push({ section: 'infinite-scroll', initialRows, loadedRows, loadedMore: loadedRows > initialRows })
      await evaluate("document.querySelector('.streak-grid .bucket-grid button')?.click()")
      for (let attempt = 0; attempt < 30; attempt++) {
        const ready = await evaluate("document.querySelector('.streak-detail-loading') === null && document.querySelectorAll('.streak-detail-modal .streak-chat-bubble').length > 0")
        if (ready) break
        await delay(100)
      }
      await evaluate("document.querySelector('.streak-detail-modal .streak-chat-bubble')?.click()")
      await delay(180)
      const bucketDetail = await evaluate(`(() => ({
        dialogOpen: document.querySelector('.streak-detail-modal') !== null,
        bubbles: document.querySelectorAll('.streak-detail-modal .streak-chat-bubble').length,
        expanded: document.querySelector('.streak-detail-modal [aria-expanded="true"]') !== null,
        itemDetails: document.querySelectorAll('.streak-detail-modal .run-item-detail').length,
        title: document.querySelector('.streak-detail-modal h2')?.textContent ?? ''
      }))()`)
      checks.push({ section: 'bucket-detail', ...bucketDetail, screenshot: await screenshot('responsive_390_bucket_detail.png') })
      await evaluate("document.querySelector('.streak-modal .close-button')?.click()")
      await evaluate("(() => { const target = document.querySelector('.results-infinite-scroll'); if (target) target.scrollTop = 0 })()")
      await evaluate("document.querySelector('.streak-chat-launch')?.click()")
      await delay(250)
      const streakChatState = await evaluate(`(() => ({
        bubbles: document.querySelectorAll('.streak-chat-bubble').length,
        itemDetails: document.querySelectorAll('.run-item-detail').length,
        panelHeight: Math.round(document.querySelector('.streak-chat-modal')?.getBoundingClientRect().height ?? 0),
        viewportHeight: window.innerHeight,
        closeVisible: (() => { const node = document.querySelector('.streak-chat-close'); if (!node) return false; const box = node.getBoundingClientRect(); return box.top >= 0 && box.bottom <= window.innerHeight })(),
        summary: Array.from(document.querySelectorAll('.streak-chat-bubble')).slice(0, 4).map(node => node.textContent)
      }))()`)
      checks.push({
        section: 'streak-chat',
        ...streakChatState,
        screenshot: await screenshot('responsive_390_streak_chat.png'),
      })
      await evaluate("document.querySelector('.streak-chat-backdrop')?.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))")
      await delay(100)
      const closedByOutside = await evaluate("document.querySelector('.streak-chat-modal') === null")
      await evaluate("document.querySelector('.streak-chat-launch')?.click()")
      await delay(100)
      await evaluate("document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))")
      await delay(100)
      const closedByEscape = await evaluate("document.querySelector('.streak-chat-modal') === null")
      checks.push({ section: 'streak-chat-close', closedByOutside, closedByEscape })
    }
  }
  process.stdout.write(`${JSON.stringify(checks, null, 2)}\n`)
} finally {
  socket.close()
  chrome.kill()
}
