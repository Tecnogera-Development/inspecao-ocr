import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { JobDetailPage } from './JobDetailPage'

const mockResult = {
  job_id: 'job-abc',
  checklist_id: '99999',
  status: 'done',
  started_at: '2024-03-10T08:00:00Z',
  finished_at: '2024-03-10T08:05:30Z',
  estimated_cost_usd: 1.25,
  result_pdf_path: '/reports/job-abc.pdf',
  error: null,
  classifications: [
    {
      photo_id: 'ph-1',
      field_name: 'Campo A',
      confidence: 95,
      status: 'valid',
      label_display: 'Campo A',
      second_best_field: null,
      second_best_confidence: null,
    },
    {
      photo_id: 'ph-2',
      field_name: 'Campo B',
      confidence: 60,
      status: 'inconclusive',
      label_display: 'Campo B',
      second_best_field: 'Campo C',
      second_best_confidence: 30,
    },
  ],
  inconclusivas: [
    {
      photo_id: 'ph-2',
      field_name: 'Campo B',
      confidence: 60,
      status: 'inconclusive',
      label_display: 'Campo B',
      second_best_field: 'Campo C',
      second_best_confidence: 30,
    },
  ],
}

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

function renderJobDetail(jobId = 'job-abc') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/jobs/:id', element: <JobDetailPage /> },
      { path: '/jobs/new-job-id', element: <div>new job page</div> },
    ],
    { initialEntries: [`/jobs/${jobId}`] },
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
})

describe('JobDetailPage', () => {
  it('shows loading skeleton while fetching', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => new Promise<Response>(() => {})),
    )

    renderJobDetail()

    expect(screen.getByTestId('job-detail-skeleton')).toBeInTheDocument()
  })

  it('renders job header with metadata', async () => {
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: mockResult } })

    renderJobDetail()

    await waitFor(() => {
      expect(screen.getByText(/99999/)).toBeInTheDocument()
      expect(screen.getByText(/concluído/i)).toBeInTheDocument()
      expect(screen.getAllByText(/10\/03\/2024/).length).toBeGreaterThanOrEqual(1)
      expect(screen.getByText(/5m 30s/)).toBeInTheDocument()
      // Custo removido da UI em 8c29159 ("remove job cost UI"); não é mais exibido.
    })
  })

  it('renders photo grid with correct alt text and status badges', async () => {
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: mockResult } })

    renderJobDetail()

    await waitFor(() => {
      const img1 = screen.getByAltText('Foto 1 — Campo A — 95% confiança')
      expect(img1).toBeInTheDocument()
      expect(img1).toHaveAttribute('loading', 'lazy')
      expect(img1).toHaveAttribute('decoding', 'async')
      expect(img1.getAttribute('src')).toContain('/api/v1/portal/photos/ph-1/thumb?w=240')

      expect(screen.getByAltText('Foto 2 — Campo B — 60% confiança')).toBeInTheDocument()
    })

    expect(screen.getByTestId('badge-ph-1')).toHaveClass('bg-green-100')
    expect(screen.getByTestId('badge-ph-2')).toHaveClass('bg-yellow-100')
  })

  it('renders inconclusivas section with second_best info', async () => {
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: mockResult } })

    renderJobDetail()

    await waitFor(() => {
      expect(screen.getByText(/inconclusivas/i)).toBeInTheDocument()
      expect(screen.getByText(/Campo C/)).toBeInTheDocument()
      expect(screen.getByText(/30%/)).toBeInTheDocument()
    })
  })

  it('hides inconclusivas section when array is empty', async () => {
    const noInconclusivas = { ...mockResult, inconclusivas: [] }
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: noInconclusivas } })

    renderJobDetail()

    await waitFor(() => {
      expect(screen.queryByText(/inconclusivas/i)).not.toBeInTheDocument()
    })
  })

  it('shows Ver PDF link only when status is done', async () => {
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: mockResult } })

    renderJobDetail()

    await waitFor(() => {
      const pdfLink = screen.getByRole('link', { name: /ver pdf/i })
      // PDF servido pelo endpoint do portal (não mais pelo result_pdf_path bruto).
      expect(pdfLink).toHaveAttribute('href', '/api/v1/portal/jobs/job-abc/pdf')
      expect(pdfLink).toHaveAttribute('target', '_blank')
      expect(pdfLink).toHaveAttribute('rel', 'noreferrer')
    })
  })

  it('hides Ver PDF link when status is not done', async () => {
    const runningResult = { ...mockResult, status: 'running', result_pdf_path: null }
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: runningResult } })

    renderJobDetail()

    await waitFor(() => {
      expect(screen.queryByRole('link', { name: /ver pdf/i })).not.toBeInTheDocument()
    })
  })

  it('shows failed card with error and Re-executar button', async () => {
    const failedResult = {
      ...mockResult,
      status: 'failed',
      error: 'Timeout ao processar imagens',
      result_pdf_path: null,
      classifications: [],
      inconclusivas: [],
    }
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: failedResult } })

    renderJobDetail()

    await waitFor(() => {
      expect(screen.getByText(/timeout ao processar imagens/i)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /re-executar/i })).toBeInTheDocument()
    })
  })

  it('Re-executar button posts /portal/run and navigates to new job', async () => {
    const failedResult = {
      ...mockResult,
      status: 'failed',
      error: 'Timeout',
      result_pdf_path: null,
      classifications: [],
      inconclusivas: [],
    }

    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((req: Request | string) => {
        const href = req instanceof Request ? req.url : req
        const pathname = new URL(href).pathname
        if (pathname.includes('jobs/job-abc/result')) {
          return Promise.resolve(
            new Response(JSON.stringify(failedResult), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        if (pathname.includes('portal/run')) {
          return Promise.resolve(
            new Response(JSON.stringify({ job_id: 'new-job-id', status: 'pending' }), {
              status: 202,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }
        return Promise.resolve(new Response('not found', { status: 404 }))
      }),
    )

    renderJobDetail()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /re-executar/i })).toBeInTheDocument()
    })

    await userEvent.click(screen.getByRole('button', { name: /re-executar/i }))

    await waitFor(() => {
      expect(screen.getByText(/new job page/i)).toBeInTheDocument()
    })
  })

  it('shows slow pipeline banner after 30min in running state', async () => {
    const startedLongAgo = new Date(Date.now() - 31 * 60 * 1000).toISOString()
    const runningResult = {
      ...mockResult,
      status: 'running',
      started_at: startedLongAgo,
      finished_at: null,
      result_pdf_path: null,
      classifications: [],
      inconclusivas: [],
    }
    mockFetchByPath({ 'jobs/job-abc/result': { status: 200, body: runningResult } })

    renderJobDetail()

    await waitFor(() => {
      expect(screen.getByText(/pipeline demorando mais que o esperado/i)).toBeInTheDocument()
    })
  })
})
