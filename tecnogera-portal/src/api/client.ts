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

// Cópia intacta da requisição, guardada ANTES do envio: depois que o fetch
// consome o corpo, `request.clone()` não é mais possível. É o que permite
// repetir a mutation com o token novo sem pedir ao usuário para clicar de novo.
const copiaParaRetentativa = new WeakMap<Request, Request>()

const csrfMiddleware: Middleware = {
  async onRequest({ request }) {
    if (MUTATING_METHODS.has(request.method.toUpperCase())) {
      const token = await ensureCsrfToken()
      if (token) request.headers.set('X-CSRF-Token', token)
      copiaParaRetentativa.set(request, request.clone())
    }
    return request
  },
}

/** Um 403 pode ser CSRF ou falta de permissão. Só o primeiro se resolve repetindo. */
async function ehFalhaDeCsrf(response: Response): Promise<boolean> {
  try {
    const corpo = (await response.clone().json()) as { detail?: unknown }
    return typeof corpo.detail === 'string' && corpo.detail.toUpperCase().includes('CSRF')
  } catch {
    return false
  }
}

const unauthorizedMiddleware: Middleware = {
  async onResponse({ request, response }) {
    const copia = copiaParaRetentativa.get(request)
    copiaParaRetentativa.delete(request)

    if (response.status === 401) {
      clearCsrfToken()
      window.dispatchEvent(new Event('api:unauthorized'))
      return response
    }

    if (response.status === 403 && copia && (await ehFalhaDeCsrf(response))) {
      // O backend rotaciona o token a cada login, e o cache deste módulo pode
      // estar velho — depois de reentrar sem recarregar a página, ou porque
      // outra aba logou e girou o token. Buscar o novo e repetir UMA vez, em
      // vez de devolver "CSRF token inválido" para o usuário, que só precisaria
      // clicar de novo. A retentativa vai direto no fetch, fora do middleware,
      // então não há risco de laço.
      clearCsrfToken()
      const token = await ensureCsrfToken()
      if (token) {
        copia.headers.set('X-CSRF-Token', token)
        return await globalThis.fetch(copia)
      }
    }

    if (response.status === 403) clearCsrfToken()
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
