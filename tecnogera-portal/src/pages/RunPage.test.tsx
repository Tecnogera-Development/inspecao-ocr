import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { clearCsrfToken } from '@/api/client'
import { RunPage } from './RunPage'

function mockFetchByPath(map: Record<string, { status: number; body: unknown }>) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((req: Request | string) => {
      const href = req instanceof Request ? req.url : req
      const pathname = new URL(href).pathname
      for (const [key, res] of Object.entries(map)) {
        if (pathname.includes(key)) {
          return Promise.resolve(
            new Response(JSON.stringify(res.body), {
              status: res.status,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
      }
      return Promise.resolve(new Response('not found', { status: 404 }))
    }),
  )
}

function renderRunPage(initialEntry = '/run') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/run', element: <RunPage /> },
      { path: '/', element: <div>dashboard</div> },
      { path: '/relatorios', element: <div>relatorios page</div> },
    ],
    { initialEntries: [initialEntry] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { queryClient }
}

afterEach(() => {
  vi.unstubAllGlobals()
  clearCsrfToken()
})

describe('RunPage', () => {
  it('renders form with checklist_id input and Executar button', () => {
    renderRunPage()

    expect(screen.getByLabelText(/checklist_id/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /executar/i })).toBeInTheDocument()
  })

  it('shows validation error when submitting empty checklist_id', async () => {
    renderRunPage()

    await userEvent.click(screen.getByRole('button', { name: /executar/i }))

    expect(screen.getByText(/checklist_id deve ser numérico/i)).toBeInTheDocument()
  })

  it('shows validation error when checklist_id is non-numeric', async () => {
    renderRunPage()

    await userEvent.type(screen.getByLabelText(/checklist_id/i), 'abc')
    await userEvent.click(screen.getByRole('button', { name: /executar/i }))

    expect(screen.getByText(/checklist_id deve ser numérico/i)).toBeInTheDocument()
  })

  it('disables button during submit', async () => {
    let resolveRun!: (v: Response) => void
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((req: Request | string) => {
        const href = req instanceof Request ? req.url : req
        if (new URL(href).pathname.includes('portal/run')) {
          return new Promise<Response>((r) => {
            resolveRun = r
          })
        }
        return Promise.resolve(new Response('not found', { status: 404 }))
      }),
    )

    renderRunPage()

    await userEvent.type(screen.getByLabelText(/checklist_id/i), '12345')
    await userEvent.click(screen.getByRole('button', { name: /executar/i }))

    expect(screen.getByRole('button', { name: /executar/i })).toBeDisabled()

    resolveRun(
      new Response(JSON.stringify({ job_id: 'abc-123', status: 'pending' }), {
        status: 202,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
  })

  it('shows success toast and navigates to /relatorios on 202', async () => {
    mockFetchByPath({
      'portal/csrf': { status: 200, body: { token: 'test-csrf' } },
      'portal/run': { status: 202, body: { job_id: 'abc-123', status: 'pending' } },
    })

    renderRunPage()

    await userEvent.type(screen.getByLabelText(/checklist_id/i), '12345')
    await userEvent.click(screen.getByRole('button', { name: /executar/i }))

    await waitFor(() => {
      expect(screen.getByText(/relatorios page/i)).toBeInTheDocument()
    })
  })

  it('shows backend 422 error inline', async () => {
    mockFetchByPath({
      'portal/run': { status: 422, body: { detail: 'checklist_id 99999 não existe' } },
    })

    renderRunPage()

    await userEvent.type(screen.getByLabelText(/checklist_id/i), '99999')
    await userEvent.click(screen.getByRole('button', { name: /executar/i }))

    await waitFor(() => {
      expect(screen.getByText(/checklist_id 99999 não existe/i)).toBeInTheDocument()
    })
  })

  it('shows generic error toast on 5xx', async () => {
    mockFetchByPath({
      'portal/run': { status: 500, body: { detail: 'internal error' } },
    })

    renderRunPage()

    await userEvent.type(screen.getByLabelText(/checklist_id/i), '12345')
    await userEvent.click(screen.getByRole('button', { name: /executar/i }))

    await waitFor(() => {
      expect(screen.getByText(/erro ao criar job — tente novamente/i)).toBeInTheDocument()
    })
  })
})
