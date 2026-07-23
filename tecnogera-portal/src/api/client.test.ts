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
})
