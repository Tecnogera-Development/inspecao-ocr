import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'

const mockStats = {
  total_done: 42,
  in_progress: 3,
  failed: 1,
  total_cost_usd: 12.5,
  accuracy_last_week: 0.95,
}

const mockJob = {
  id: 'job-1',
  checklist_id: '12345',
  status: 'done' as const,
  created_at: '2024-01-15T10:00:00Z',
  started_at: '2024-01-15T10:01:00Z',
  finished_at: '2024-01-15T10:05:00Z',
  error: null,
  result_pdf_path: '/reports/job-1.pdf',
  metrics: null,
}

function mockFetchByUrl(map: Record<string, { status: number; body: unknown }>) {
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

function renderDashboard(initialEntry = '/') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const router = createMemoryRouter([{ path: '/', element: <DashboardPage /> }], {
    initialEntries: [initialEntry],
  })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
  return { queryClient }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('DashboardPage', () => {
  it('renders stat cards with correct values', async () => {
    mockFetchByUrl({
      'portal/stats': { status: 200, body: mockStats },
      'portal/jobs': { status: 200, body: [] },
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('42')).toBeInTheDocument()
      expect(screen.getByText('3')).toBeInTheDocument()
      expect(screen.getByText('1')).toBeInTheDocument()
      expect(screen.queryByText(/12,50/)).not.toBeInTheDocument()
    })
  })

  it('renders jobs table with correct columns', async () => {
    mockFetchByUrl({
      'portal/stats': { status: 200, body: mockStats },
      'portal/jobs': { status: 200, body: [mockJob] },
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText('12345')).toBeInTheDocument()
      expect(screen.getByText(/15\/01\/2024/)).toBeInTheDocument()
      expect(screen.getAllByText(/concluído/i).length).toBeGreaterThan(0)
      expect(screen.getByText('4m 0s')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: /pdf/i })).toBeInTheDocument()
    })
  })

  it('shows empty state when no jobs', async () => {
    mockFetchByUrl({
      'portal/stats': { status: 200, body: mockStats },
      'portal/jobs': { status: 200, body: [] },
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText(/nenhuma análise ainda/i)).toBeInTheDocument()
    })
  })

  it('shows error state and retry button when jobs fetch fails', async () => {
    mockFetchByUrl({
      'portal/stats': { status: 200, body: mockStats },
      'portal/jobs': { status: 500, body: { detail: 'internal error' } },
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText(/não foi possível carregar/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /tentar novamente/i })).toBeInTheDocument()
    })
  })

  it('"Ver detalhes" links to /jobs/:id for each job row', async () => {
    mockFetchByUrl({
      'portal/stats': { status: 200, body: mockStats },
      'portal/jobs': { status: 200, body: [mockJob] },
    })

    renderDashboard()

    await waitFor(() => {
      const link = screen.getByRole('link', { name: /ver detalhes/i })
      expect(link).toHaveAttribute('href', '/jobs/job-1')
    })
  })

  it('shows PDF link only when result_pdf_path is set', async () => {
    const jobWithoutPdf = { ...mockJob, id: 'job-no-pdf', result_pdf_path: null }
    mockFetchByUrl({
      'portal/stats': { status: 200, body: mockStats },
      'portal/jobs': { status: 200, body: [mockJob, jobWithoutPdf] },
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByRole('link', { name: /pdf/i })).toBeInTheDocument()
    })
    // Only one PDF link for the two rows
    expect(screen.getAllByRole('link', { name: /pdf/i })).toHaveLength(1)
  })

  it('filters by status and updates URL params', async () => {
    const capturedUrls: string[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((req: Request | string) => {
        const href = req instanceof Request ? req.url : req
        capturedUrls.push(href)
        const pathname = new URL(href).pathname
        if (pathname.includes('portal/stats')) {
          return Promise.resolve(
            new Response(JSON.stringify(mockStats), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify([mockJob]), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByRole('combobox', { name: /status/i })).toBeInTheDocument()
    })

    await userEvent.selectOptions(screen.getByRole('combobox', { name: /status/i }), 'done')

    await waitFor(() => {
      expect(capturedUrls.some((u) => u.includes('status=done'))).toBe(true)
    })
  })

  it('shows pagination indicator and prev/next buttons', async () => {
    mockFetchByUrl({
      'portal/stats': { status: 200, body: mockStats },
      'portal/jobs': { status: 200, body: [mockJob] },
    })

    renderDashboard()

    await waitFor(() => {
      expect(screen.getByText(/mostrando 1/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /anterior/i })).toBeDisabled()
      expect(screen.getByRole('button', { name: /próxima/i })).toBeInTheDocument()
    })
  })
})
