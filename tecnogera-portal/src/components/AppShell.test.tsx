import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearCsrfToken } from '@/api/client'
import { AppShell } from './AppShell'

function mockFetch(status: number, body: unknown = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
    ),
  )
}

// Mock ciente do path: responde ao preflight GET /portal/csrf disparado pelo
// middleware CSRF em mutations (ex.: POST /portal/logout), além dos endpoints
// reais. A ordem das chamadas deixa de importar.
function mockFetchByPath(map: Record<string, { status: number; body?: unknown }>) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((req: Request | string) => {
      const href = req instanceof Request ? req.url : req
      const pathname = new URL(href).pathname
      for (const [key, res] of Object.entries(map)) {
        if (pathname.includes(key)) {
          const isNoContent = res.status === 204
          return Promise.resolve(
            new Response(isNoContent ? null : JSON.stringify(res.body ?? {}), {
              status: res.status,
              headers: isNoContent ? {} : { 'Content-Type': 'application/json' },
            }),
          )
        }
      }
      return Promise.resolve(new Response('not found', { status: 404 }))
    }),
  )
}

function renderWithShell(initialEntry = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      {
        path: '/',
        element: <AppShell />,
        children: [{ index: true, element: <div>dashboard content</div> }],
      },
      { path: '/login', element: <div>login page</div> },
    ],
    { initialEntries: [initialEntry] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { queryClient, router }
}

afterEach(() => {
  vi.unstubAllGlobals()
  clearCsrfToken()
})

describe('AppShell', () => {
  it('shows a loading skeleton while /me is pending', async () => {
    let resolve: (v: Response) => void = () => {}
    vi.stubGlobal(
      'fetch',
      vi.fn().mockReturnValue(
        new Promise<Response>((r) => {
          resolve = r
        }),
      ),
    )

    renderWithShell()

    expect(screen.getByTestId('sidebar-skeleton')).toBeInTheDocument()

    resolve?.(
      new Response(JSON.stringify({ id: '1', email: 'op@tg.com' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })

  it('redirects to /login when /me returns 401', async () => {
    mockFetch(401, { detail: 'Não autenticado' })

    renderWithShell()

    await waitFor(() => {
      expect(screen.getByText('login page')).toBeInTheDocument()
    })
  })

  it('renders header with user email and Outlet when /me succeeds', async () => {
    mockFetch(200, { id: '1', email: 'op@tg.com' })

    renderWithShell()

    await waitFor(() => {
      expect(screen.getByText('op@tg.com')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /sair/i })).toBeInTheDocument()
      expect(screen.getByText('dashboard content')).toBeInTheDocument()
    })
  })

  it('renders sidebar nav: Avarias (home) and Relatórios', async () => {
    mockFetch(200, { id: '1', email: 'op@tg.com' })

    renderWithShell()

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /avarias/i })).toHaveAttribute('href', '/avarias')
      expect(screen.getByRole('link', { name: /relatórios/i })).toHaveAttribute(
        'href',
        '/relatorios',
      )
    })
  })

  it('calls logout, clears cache and navigates to /login on Sair click', async () => {
    mockFetchByPath({
      'portal/csrf': { status: 200, body: { token: 'test-csrf' } },
      'portal/logout': { status: 204, body: null },
      'portal/me': { status: 200, body: { id: '1', email: 'op@tg.com' } },
    })

    renderWithShell()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /sair/i })).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: /sair/i }))

    await waitFor(() => {
      expect(screen.getByText('login page')).toBeInTheDocument()
    })
  })
})
