import { apiClient } from '@/api/client'
import type { JobDetailResponse, StatsResponse } from '@/api/types'
import { StatusBadge } from '@/components/StatusBadge'
import type { JobStatus } from '@/components/StatusBadge'
import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR } from 'date-fns/locale'
import { Link, useSearchParams } from 'react-router-dom'

export function hasActiveJobs(jobs: JobDetailResponse[] | undefined): boolean {
  return jobs?.some((j) => j.status === 'pending' || j.status === 'running') ?? false
}

function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt || !finishedAt) return '—'
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime()
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds}s`
}

const LIMIT = 50

async function fetchStats(): Promise<StatsResponse> {
  const result = await apiClient.GET('/api/v1/portal/stats')
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

async function fetchJobs(params: {
  status?: string
  date_from?: string
  date_to?: string
  offset: number
}): Promise<JobDetailResponse[]> {
  const result = await apiClient.GET('/api/v1/portal/jobs', {
    params: {
      query: {
        limit: LIMIT,
        offset: params.offset,
        ...(params.status ? { status: params.status } : {}),
        ...(params.date_from ? { date_from: params.date_from } : {}),
        ...(params.date_to ? { date_to: params.date_to } : {}),
      },
    },
  })
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

export function DashboardPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const offset = Number(searchParams.get('offset') ?? '0')
  const statusFilter = searchParams.get('status') ?? ''
  const dateFrom = searchParams.get('date_from') ?? ''
  const dateTo = searchParams.get('date_to') ?? ''

  const statsQuery = useQuery({
    queryKey: ['stats'],
    queryFn: fetchStats,
  })

  const jobsFilters = { status: statusFilter, date_from: dateFrom, date_to: dateTo, offset }

  const jobsQuery = useQuery({
    queryKey: ['jobs', jobsFilters],
    queryFn: () => fetchJobs(jobsFilters),
    refetchInterval: (query) => (hasActiveJobs(query.state.data) ? 10_000 : 60_000),
    refetchIntervalInBackground: false,
  })

  const stats = statsQuery.data
  const jobs = jobsQuery.data ?? []
  const isJobsError = jobsQuery.isError

  const hasPrev = offset > 0
  const hasNextPage = jobs.length >= LIMIT

  function goToPrev() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('offset', String(Math.max(0, offset - LIMIT)))
      return next
    })
  }

  function goToNext() {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('offset', String(offset + LIMIT))
      return next
    })
  }

  function handleStatusChange(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set('status', value)
      else next.delete('status')
      next.delete('offset')
      return next
    })
  }

  function handleDateFrom(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set('date_from', value)
      else next.delete('date_from')
      next.delete('offset')
      return next
    })
  }

  function handleDateTo(value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set('date_to', value)
      else next.delete('date_to')
      next.delete('offset')
      return next
    })
  }

  const fromIndex = offset + 1
  const toIndex = offset + jobs.length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-slate-900">Relatórios de checklist</h1>
        <Link
          to="/run"
          className="rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover"
        >
          Nova análise
        </Link>
      </div>

      {/* Stats cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <StatCard label="Processados no mês" value={stats?.total_done ?? '—'} />
        <StatCard label="Em andamento" value={stats?.in_progress ?? '—'} />
        <StatCard label="Com erro" value={stats?.failed ?? '—'} />
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-4">
        <div className="flex items-center gap-2">
          <label htmlFor="status-filter" className="text-sm font-medium text-slate-700">
            Status
          </label>
          <select
            id="status-filter"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={statusFilter}
            onChange={(e) => handleStatusChange(e.target.value)}
          >
            <option value="">Todos</option>
            <option value="pending">Pendente</option>
            <option value="running">Executando</option>
            <option value="done">Concluído</option>
            <option value="failed">Falhou</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="date-from" className="text-sm font-medium text-slate-700">
            De
          </label>
          <input
            id="date-from"
            type="date"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={dateFrom}
            onChange={(e) => handleDateFrom(e.target.value)}
          />
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="date-to" className="text-sm font-medium text-slate-700">
            Até
          </label>
          <input
            id="date-to"
            type="date"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={dateTo}
            onChange={(e) => handleDateTo(e.target.value)}
          />
        </div>
      </div>

      {/* Jobs content */}
      {isJobsError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
          <p className="text-red-700">Não foi possível carregar — tentar novamente</p>
          <button
            type="button"
            className="mt-3 rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium hover:bg-red-100"
            onClick={() => jobsQuery.refetch()}
          >
            Tentar novamente
          </button>
        </div>
      ) : !jobsQuery.isPending && jobs.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-10 text-center">
          <p className="text-lg font-medium text-slate-600">Nenhuma análise ainda</p>
          <span
            className="mt-2 inline-block cursor-not-allowed text-sm text-slate-400"
            title="disponível em breve"
          >
            Executar primeiro pipeline
          </span>
        </div>
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b bg-brand-tint text-left">
                  <th className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover">
                    Checklist ID
                  </th>
                  <th className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover">
                    Data
                  </th>
                  <th className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover">
                    Status
                  </th>
                  <th className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover">
                    Duração
                  </th>
                  <th className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover">
                    PDF
                  </th>
                  <th className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover">
                    Ações
                  </th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job, idx) => (
                  <tr
                    key={job.id}
                    className={`border-b last:border-0 hover:bg-brand-tint/50 ${idx % 2 === 0 ? 'bg-white' : 'bg-orange-50'}`}
                  >
                    <td className="px-3 py-2 font-mono">{job.checklist_id}</td>
                    <td className="px-3 py-2">
                      {format(new Date(job.created_at), 'dd/MM/yyyy HH:mm', { locale: ptBR })}
                    </td>
                    <td className="px-3 py-2">
                      <StatusBadge status={job.status as JobStatus} />
                    </td>
                    <td className="px-3 py-2">{formatDuration(job.started_at, job.finished_at)}</td>
                    <td className="px-3 py-2">
                      {job.result_pdf_path ? (
                        <a
                          href={`/api/v1/portal/jobs/${job.id}/pdf`}
                          target="_blank"
                          rel="noreferrer"
                          className="text-brand-hover underline underline-offset-2 hover:text-brand-primary"
                        >
                          PDF
                        </a>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      <Link
                        to={`/jobs/${job.id}`}
                        className="rounded border border-brand-secondary px-2 py-1 text-xs font-medium text-brand-hover hover:bg-brand-tint"
                      >
                        Ver detalhes
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-600">
              {jobs.length > 0 ? `Mostrando ${fromIndex}–${toIndex}` : ''}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={goToPrev}
                disabled={!hasPrev}
                className="rounded-md border border-brand-secondary px-3 py-1.5 text-sm font-medium text-brand-hover hover:bg-brand-tint disabled:opacity-40"
              >
                Anterior
              </button>
              <button
                type="button"
                onClick={goToNext}
                disabled={!hasNextPage}
                className="rounded-md border border-brand-secondary px-3 py-1.5 text-sm font-medium text-brand-hover hover:bg-brand-tint disabled:opacity-40"
              >
                Próxima
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function StatCard({
  label,
  value,
  highlight = false,
}: { label: string; value: number | string; highlight?: boolean }) {
  return (
    <div
      className={`rounded-lg border border-border bg-white p-4 shadow-sm${highlight ? ' border-t-4 border-t-brand-primary' : ''}`}
    >
      <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-bold text-brand-primary">{String(value)}</p>
    </div>
  )
}
