import { afterEach, describe, expect, it, vi } from 'vitest'
import { apiClient, clearCsrfToken } from './client'

function mockFetchOnce(status: number, body: unknown = {}) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { 'Content-Type': 'application/json' },
    }),
  )
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
  document.head.innerHTML = ''
  clearCsrfToken() // evita que o token em cache de módulo vaze entre testes
})

describe('apiClient', () => {
  it('is defined', () => {
    expect(apiClient).toBeDefined()
  })

  it('sends requests with credentials: include', async () => {
    const fetchMock = mockFetchOnce(200, { id: '1', email: 'a@b.com' })

    await apiClient.GET('/api/v1/portal/me')

    const [request] = fetchMock.mock.calls[0] as [Request]
    expect(request.credentials).toBe('include')
  })

  it('adds X-CSRF-Token on mutations, fetched from /portal/csrf', async () => {
    // 1ª chamada de fetch: GET /portal/csrf → token; 2ª: a mutation em si.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: 'tok-abc' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    vi.stubGlobal('fetch', fetchMock)

    await apiClient.POST('/api/v1/portal/logout')

    // A mutation é a 2ª chamada (a 1ª é o GET /portal/csrf).
    const mutation = fetchMock.mock.calls[1]?.[0] as Request
    expect(mutation.headers.get('X-CSRF-Token')).toBe('tok-abc')
  })

  it('does not add X-CSRF-Token on GET requests', async () => {
    const fetchMock = mockFetchOnce(200, { id: '1', email: 'a@b.com' })

    await apiClient.GET('/api/v1/portal/me')

    const [request] = fetchMock.mock.calls[0] as [Request]
    expect(request.headers.get('X-CSRF-Token')).toBeNull()
  })

  it('dispatches api:unauthorized event on 401', async () => {
    mockFetchOnce(401, { detail: 'Não autenticado' })

    const listener = vi.fn()
    window.addEventListener('api:unauthorized', listener)

    await apiClient.GET('/api/v1/portal/me')

    expect(listener).toHaveBeenCalledOnce()
    window.removeEventListener('api:unauthorized', listener)
  })
  it('repete a mutation uma vez quando o token está velho, sem devolver erro à tela', async () => {
    // O backend gera um csrf_token novo a cada login. Se o cache do módulo
    // ficou com o token da sessão anterior, a primeira escrita leva 403 — era
    // exatamente o "CSRF token inválido ou ausente" que aparecia no portal.
    const chamadas: string[] = []
    const fetchMock = vi.fn(async (input: Request | string) => {
      const url = input instanceof Request ? input.url : String(input)
      const metodo = input instanceof Request ? input.method : 'GET'
      chamadas.push(`${metodo} ${new URL(url).pathname}`)

      if (url.includes('/portal/csrf')) {
        return new Response(JSON.stringify({ token: `tok-${chamadas.length}` }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      const jaTentou = chamadas.filter((c) => c.startsWith('POST')).length > 1
      return new Response(
        JSON.stringify(jaTentou ? { ok: true } : { detail: 'CSRF token inválido ou ausente' }),
        { status: jaTentou ? 200 : 403, headers: { 'Content-Type': 'application/json' } },
      )
    })
    vi.stubGlobal('fetch', fetchMock)

    const { response } = await apiClient.POST('/api/v1/portal/logout')

    expect(response.status).toBe(200)
    expect(chamadas.filter((c) => c.includes('/portal/csrf'))).toHaveLength(2)
    expect(chamadas.filter((c) => c.startsWith('POST'))).toHaveLength(2)
  })

  it('não repete quando o 403 é de permissão, e não de CSRF', async () => {
    // Operador batendo numa rota de admin. Repetir só geraria uma segunda
    // negativa idêntica e mascararia a causa real.
    const chamadas: string[] = []
    const fetchMock = vi.fn(async (input: Request | string) => {
      const url = input instanceof Request ? input.url : String(input)
      const metodo = input instanceof Request ? input.method : 'GET'
      chamadas.push(`${metodo} ${new URL(url).pathname}`)
      const corpo = url.includes('/portal/csrf')
        ? { token: 'tok-ok' }
        : { detail: 'Requer papel admin' }
      return new Response(JSON.stringify(corpo), {
        status: url.includes('/portal/csrf') ? 200 : 403,
        headers: { 'Content-Type': 'application/json' },
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const { response } = await apiClient.POST('/api/v1/portal/logout')

    expect(response.status).toBe(403)
    expect(chamadas.filter((c) => c.startsWith('POST'))).toHaveLength(1)
  })
})
