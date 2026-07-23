import createClient, { type Middleware } from 'openapi-fetch'
import type { paths } from './types'

// CSRF token em cache de módulo. Buscado sob demanda em GET /portal/csrf
// (que exige sessão válida) e injetado nas mutations. Invalidado em 403.
let csrfToken: string | null = null

async function ensureCsrfToken(): Promise<string | null> {
  if (csrfToken) return csrfToken
  try {
    const resp = await globalThis.fetch(`${window.location.origin}/api/v1/portal/csrf`, {
      credentials: 'include',
    })
    if (resp.ok) {
      const data = (await resp.json()) as { token: string }
      csrfToken = data.token
    }
  } catch {
    // sem token — mutation seguirá sem header e o backend responde 403
  }
  return csrfToken
}

export function clearCsrfToken(): void {
  csrfToken = null
}

const MUTATING_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])

const csrfMiddleware: Middleware = {
  async onRequest({ request }) {
    if (MUTATING_METHODS.has(request.method.toUpperCase())) {
      const token = await ensureCsrfToken()
      if (token) request.headers.set('X-CSRF-Token', token)
    }
    return request
  },
}

const unauthorizedMiddleware: Middleware = {
  onResponse({ response }) {
    if (response.status === 401) {
      clearCsrfToken()
      window.dispatchEvent(new Event('api:unauthorized'))
    }
    // CSRF rotacionado/expirado: invalida cache para re-buscar na próxima tentativa
    if (response.status === 403) {
      clearCsrfToken()
    }
    return response
  },
}

// Lazy wrapper ensures vi.stubGlobal('fetch', ...) works in tests
// (openapi-fetch captures the fetch reference at createClient() time)
const lazyFetch: typeof fetch = (...args) => globalThis.fetch(...args)

export const apiClient = createClient<paths>({
  baseUrl: window.location.origin,
  credentials: 'include',
  fetch: lazyFetch,
})

apiClient.use(csrfMiddleware, unauthorizedMiddleware)
