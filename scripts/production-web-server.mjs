import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'

const staticRoot = path.resolve(process.env.STATIC_ROOT || '.')
const port = Number.parseInt(process.env.FRONTEND_PORT || '5173', 10)
const backend = new URL(process.env.BACKEND_URL || 'http://127.0.0.1:5117')

const contentTypes = new Map([
  ['.html', 'text/html; charset=utf-8'],
  ['.js', 'text/javascript; charset=utf-8'],
  ['.css', 'text/css; charset=utf-8'],
  ['.json', 'application/json; charset=utf-8'],
  ['.svg', 'image/svg+xml'],
  ['.png', 'image/png'],
  ['.jpg', 'image/jpeg'],
  ['.jpeg', 'image/jpeg'],
  ['.webp', 'image/webp'],
  ['.ico', 'image/x-icon'],
  ['.woff', 'font/woff'],
  ['.woff2', 'font/woff2'],
])

function proxyToBackend(request, response) {
  const headers = { ...request.headers }
  headers.host = backend.host
  headers['x-forwarded-host'] = request.headers.host || ''
  headers['x-forwarded-proto'] = request.headers['cf-visitor'] ? 'https' : 'http'
  const proxy = http.request({
    protocol: backend.protocol,
    hostname: backend.hostname,
    port: backend.port,
    method: request.method,
    path: request.url,
    headers,
  }, backendResponse => {
    response.writeHead(backendResponse.statusCode || 502, backendResponse.headers)
    backendResponse.pipe(response)
  })
  proxy.on('error', error => {
    if (!response.headersSent) response.writeHead(502, { 'content-type': 'application/json; charset=utf-8' })
    response.end(JSON.stringify({ error: `Backend unavailable: ${error.message}` }))
  })
  request.pipe(proxy)
}

function resolveStaticFile(requestUrl) {
  const urlPath = decodeURIComponent(new URL(requestUrl, 'http://localhost').pathname)
  const relative = urlPath === '/' ? 'index.html' : urlPath.replace(/^\/+/, '')
  const candidate = path.resolve(staticRoot, relative)
  if (candidate !== staticRoot && !candidate.startsWith(`${staticRoot}${path.sep}`)) return null
  if (fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate
  return path.join(staticRoot, 'index.html')
}

const server = http.createServer((request, response) => {
  const pathname = new URL(request.url || '/', 'http://localhost').pathname
  if (pathname === '/health' || pathname.startsWith('/api/')) {
    proxyToBackend(request, response)
    return
  }
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { allow: 'GET, HEAD' })
    response.end()
    return
  }
  let filePath
  try {
    filePath = resolveStaticFile(request.url || '/')
  } catch {
    response.writeHead(400)
    response.end('Bad request')
    return
  }
  if (!filePath || !fs.existsSync(filePath)) {
    response.writeHead(404)
    response.end('Not found')
    return
  }
  const extension = path.extname(filePath).toLowerCase()
  const isIndex = path.basename(filePath).toLowerCase() === 'index.html'
  response.writeHead(200, {
    'content-type': contentTypes.get(extension) || 'application/octet-stream',
    'cache-control': isIndex ? 'no-cache' : 'public, max-age=31536000, immutable',
  })
  if (request.method === 'HEAD') {
    response.end()
    return
  }
  fs.createReadStream(filePath).pipe(response)
})

server.listen(port, '127.0.0.1', () => {
  console.log(`Production frontend: http://127.0.0.1:${port}`)
  console.log(`Static snapshot: ${staticRoot}`)
  console.log(`Backend proxy: ${backend.origin}`)
})

function shutdown() {
  server.close(() => process.exit(0))
  setTimeout(() => process.exit(1), 5000).unref()
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
