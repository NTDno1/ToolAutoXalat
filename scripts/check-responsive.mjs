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
      hasGlobalHorizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1
    }))()`)
    const topScreenshot = await screenshot(`responsive_${viewport.width}_top.png`)
    checks.push({ ...viewport, ...metrics, topScreenshot })

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
    }
  }
  process.stdout.write(`${JSON.stringify(checks, null, 2)}\n`)
} finally {
  socket.close()
  chrome.kill()
}
