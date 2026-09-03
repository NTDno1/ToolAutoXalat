import { spawn } from 'node:child_process'
import { mkdtemp, mkdir } from 'node:fs/promises'
import path from 'node:path'

const projectRoot = path.resolve(import.meta.dirname, '..')
const runtimeDir = path.join(projectRoot, 'runtime')
const chromePath = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const targetUrl = process.argv[2] ?? 'http://127.0.0.1:5173/'
const debuggingPort = 9338
const delay = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))

await mkdir(runtimeDir, { recursive: true })
const profileDir = await mkdtemp(path.join(runtimeDir, 'performance-cdp-'))
const chrome = spawn(chromePath, [
  '--headless=new', '--disable-gpu', '--hide-scrollbars', '--no-first-run',
  `--remote-debugging-port=${debuggingPort}`, `--user-data-dir=${profileDir}`, 'about:blank',
], { windowsHide: true, stdio: 'ignore' })

async function findPage() {
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      const targets = await (await fetch(`http://127.0.0.1:${debuggingPort}/json`)).json()
      const page = targets.find(target => target.type === 'page')
      if (page?.webSocketDebuggerUrl) return page
    } catch { /* Chrome is starting. */ }
    await delay(100)
  }
  throw new Error('Chrome DevTools endpoint did not start.')
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
    const request = pending.get(message.id)
    pending.delete(message.id)
    if (message.error) request.reject(new Error(message.error.message))
    else request.resolve(message.result)
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
    const timer = setTimeout(() => reject(new Error(`Timed out waiting for ${method}`)), timeout)
    const waiters = eventWaiters.get(method) ?? []
    waiters.push(value => { clearTimeout(timer); resolve(value) })
    eventWaiters.set(method, waiters)
  })
}

async function evaluate(expression) {
  const response = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  return response.result.value
}

try {
  await send('Page.enable')
  await send('Runtime.enable')
  await send('Performance.enable')
  await send('Emulation.setDeviceMetricsOverride', {
    width: 1366, height: 768, deviceScaleFactor: 1, mobile: false,
    screenWidth: 1366, screenHeight: 768,
  })
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: `window.__longTasks = []; new PerformanceObserver(list => {
      for (const entry of list.getEntries()) window.__longTasks.push(entry.duration)
    }).observe({ type: 'longtask', buffered: true });`,
  })
  const loaded = waitForEvent('Page.loadEventFired')
  await send('Page.navigate', { url: targetUrl })
  await loaded
  await delay(8000)

  const browserMetrics = await evaluate(`(() => {
    const apiEntries = performance.getEntriesByType('resource')
      .filter(entry => entry.name.includes('/api/'))
      .map(entry => ({
        path: new URL(entry.name).pathname,
        durationMs: Math.round(entry.duration),
        transferBytes: entry.transferSize,
        decodedBytes: entry.decodedBodySize
      }))
    const counts = Object.fromEntries([...new Set(apiEntries.map(entry => entry.path))]
      .map(path => [path, apiEntries.filter(entry => entry.path === path).length]))
    return {
      apiRequests: apiEntries.length,
      requestCounts: counts,
      apiEntries,
      longTaskCount: window.__longTasks.length,
      longestTaskMs: Math.round(Math.max(0, ...window.__longTasks)),
      domNodes: document.querySelectorAll('*').length,
      tableRows: document.querySelectorAll('.results-panel tbody tr').length,
      heapBytes: performance.memory?.usedJSHeapSize ?? null,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
    }
  })()`)
  const performanceMetrics = await send('Performance.getMetrics')
  const wanted = new Set(['TaskDuration', 'ScriptDuration', 'LayoutDuration', 'RecalcStyleDuration', 'JSHeapUsedSize'])
  const chromeMetrics = Object.fromEntries(performanceMetrics.metrics
    .filter(metric => wanted.has(metric.name))
    .map(metric => [metric.name, metric.value]))
  process.stdout.write(`${JSON.stringify({ targetUrl, measuredSeconds: 8, ...browserMetrics, chromeMetrics }, null, 2)}\n`)
} finally {
  socket.close()
  chrome.kill()
}
