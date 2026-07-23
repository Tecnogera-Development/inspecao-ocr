import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearCsrfToken } from '@/api/client'
import { LoginPage } from './LoginPage'

// Toda mutation dispara um preflight GET /portal/csrf (middleware CSRF em
// client.ts). Os mocks precisam responder esse preflight sem consumir a
// resposta do endpoint real.
function csrfResponse() {
  return new Response(JSON.stringify({ token: 'test-csrf' }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

function isCsrfRequest(req: Request | string) {
  const href = req instanceof Request ? req.url : req
  return new URL(href).pathname.includes('portal/csrf')
}

function mockFetchOnce(status: number, body: unknown = {}) {
  const fetchMock = vi.fn().mockImplementation((req: Request | string) => {
    if (isCsrfRequest(req)) return Promise.resolve(csrfResponse())
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

function renderLoginPage(initialEntries = ['/login']) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/login', element: <LoginPage /> },
      { path: '/', element: <div>dashboard</div> },
    ],
    { initialEntries },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { router }
}

afterEach(() => {
  vi.unstubAllGlobals()
  clearCsrfToken()
})

describe('LoginPage', () => {
  it('renders email input, password input, and Entrar button', () => {
    renderLoginPage()
    expect(screen.getByLabelText(/email/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/senha/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /entrar/i })).toBeInTheDocument()
  })

  it('navigates to / on successful login', async () => {
    mockFetchOnce(200, { id: '1', email: 'op@tg.com' })
    renderLoginPage()

    await userEvent.type(screen.getByLabelText(/email/i), 'op@tg.com')
    await userEvent.type(screen.getByLabelText(/senha/i), 'senha123')
    await userEvent.click(screen.getByRole('button', { name: /entrar/i }))

    await waitFor(() => {
      expect(screen.getByText('dashboard')).toBeInTheDocument()
    })
  })

  it('shows error message on 401', async () => {
    mockFetchOnce(401, { detail: 'Credenciais inválidas' })
    renderLoginPage()

    await userEvent.type(screen.getByLabelText(/email/i), 'op@tg.com')
    await userEvent.type(screen.getByLabelText(/senha/i), 'errada')
    await userEvent.click(screen.getByRole('button', { name: /entrar/i }))

    await waitFor(() => {
      expect(screen.getByText(/email ou senha inválidos/i)).toBeInTheDocument()
    })
  })

  it('disables button while submitting', async () => {
    let resolve: (v: Response) => void = () => {}
    const promise = new Promise<Response>((r) => {
      resolve = r
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((req: Request | string) => {
        if (isCsrfRequest(req)) return Promise.resolve(csrfResponse())
        return promise
      }),
    )

    renderLoginPage()

    await userEvent.type(screen.getByLabelText(/email/i), 'op@tg.com')
    await userEvent.type(screen.getByLabelText(/senha/i), 'senha123')
    await userEvent.click(screen.getByRole('button', { name: /entrar/i }))

    expect(screen.getByRole('button', { name: /entrar/i })).toBeDisabled()

    resolve?.(
      new Response(JSON.stringify({ id: '1', email: 'op@tg.com' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })
})
