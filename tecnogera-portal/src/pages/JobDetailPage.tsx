import { apiClient } from '@/api/client'
import type { JobResultResponse } from '@/api/types'
import { ClassificationBadge } from '@/components/ClassificationBadge'
import type { ClassificationStatus } from '@/components/ClassificationBadge'
import { StatusBadge } from '@/components/StatusBadge'
import type { JobStatus } from '@/components/StatusBadge'
import { useMutation, useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR } from 'date-fns/locale'
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

function formatDuration(startedAt: string | null, finishedAt: string | null): string {
  if (!startedAt || !finishedAt) return '—'
  const ms = new Date(finishedAt).getTime() - new Date(startedAt).getTime()
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m ${seconds}s`
}

function isSlowPipeline(startedAt: string | null, status: string): boolean {
  if (status !== 'running' || !startedAt) return false
  return Date.now() - new Date(startedAt).getTime() > 30 * 60 * 1000
}

async function fetchJobResult(jobId: string): Promise<JobResultResponse> {
  const result = await apiClient.GET('/api/v1/portal/jobs/{job_id}/result', {
    params: { path: { job_id: jobId } },
  })
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

async function submitRun(checklistId: string): Promise<{ job_id: string; status: string }> {
  const result = await apiClient.POST('/api/v1/portal/run', {
    body: { checklist_id: checklistId },
  })
  if (!result.data) throw new Error('Erro ao criar job — tente novamente')
  return result.data
}

function Skeleton() {
  return (
    <div data-testid="job-detail-skeleton" className="animate-pulse space-y-6">
      <div className="h-8 w-64 rounded bg-slate-200" />
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: stable static skeleton
          <div key={i} className="h-20 rounded-lg bg-slate-200" />
        ))}
      </div>
      <div className="grid grid-cols-4 gap-2">
        {Array.from({ length: 16 }).map((_, i) => (
          // biome-ignore lint/suspicious/noArrayIndexKey: stable static skeleton
          <div key={i} className="aspect-square rounded-lg bg-slate-200" />
        ))}
      </div>
    </div>
  )
}

export function JobDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const [inconclusivasOpen, setInconclusivasOpen] = useState(true)

  const query = useQuery({
    queryKey: ['job', id],
    queryFn: () => fetchJobResult(id!),
    refetchInterval: (q) => {
      const status = q.state.data?.status
      return status === 'pending' || status === 'running' ? 5_000 : false
    },
    refetchIntervalInBackground: false,
  })

  const rerunMutation = useMutation({
    mutationFn: submitRun,
    onSuccess: (data) => {
      navigate(`/jobs/${data.job_id}`)
    },
  })

  if (query.isPending) return <Skeleton />

  if (query.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
        <p className="text-red-700">Não foi possível carregar os detalhes do job.</p>
      </div>
    )
  }

  const job = query.data
  const slow = isSlowPipeline(job.started_at, job.status)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="space-y-2">
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-slate-900">Checklist {job.checklist_id}</h1>
          <StatusBadge status={job.status as JobStatus} />
        </div>

        <div className="flex flex-wrap gap-6 text-sm text-slate-600">
          {job.started_at && (
            <span>
              Início: {format(new Date(job.started_at), 'dd/MM/yyyy HH:mm', { locale: ptBR })}
            </span>
          )}
          {job.finished_at && (
            <span>
              Fim: {format(new Date(job.finished_at), 'dd/MM/yyyy HH:mm', { locale: ptBR })}
            </span>
          )}
          <span>Duração: {formatDuration(job.started_at, job.finished_at)}</span>
        </div>

        <div className="flex gap-3">
          {job.status === 'done' && job.result_pdf_path && (
            <a
              href={`/api/v1/portal/jobs/${job.job_id}/pdf`}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-brand-hover underline underline-offset-2 hover:text-brand-primary"
            >
              Ver PDF
            </a>
          )}
        </div>
      </div>

      {/* Slow pipeline banner */}
      {slow && (
        <div className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          Pipeline demorando mais que o esperado — verifique os logs
        </div>
      )}

      {/* Failed card */}
      {job.status === 'failed' && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-4 space-y-3">
          <p className="font-medium text-red-800">Erro na execução</p>
          {job.error && <p className="text-sm text-red-700">{job.error}</p>}
          <button
            type="button"
            onClick={() => rerunMutation.mutate(job.checklist_id)}
            disabled={rerunMutation.isPending}
            className="rounded-md border border-red-300 bg-white px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            Re-executar
          </button>
        </div>
      )}

      {/* Photo grid */}
      {job.classifications.length > 0 && (
        <div
          className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-6"
          style={{ contentVisibility: 'auto' }}
        >
          {job.classifications.map((item, idx) => (
            <div
              key={item.photo_id}
              className="relative overflow-hidden rounded-lg border border-slate-200"
            >
              <img
                src={`/api/v1/portal/photos/${item.photo_id}/thumb?w=240`}
                alt={`Foto ${idx + 1} — ${item.field_name} — ${item.confidence}% confiança`}
                loading="lazy"
                decoding="async"
                className="aspect-square w-full object-cover"
              />
              <ClassificationBadge
                data-testid={`badge-${item.photo_id}`}
                status={item.status as ClassificationStatus}
                label={item.label_display}
                className="absolute bottom-1 right-1"
              />
            </div>
          ))}
        </div>
      )}

      {/* Inconclusivas section */}
      {job.inconclusivas.length > 0 && (
        <div className="rounded-lg border border-yellow-200 bg-yellow-50">
          <button
            type="button"
            className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold text-brand-hover"
            onClick={() => setInconclusivasOpen((o) => !o)}
          >
            <span>Inconclusivas ({job.inconclusivas.length})</span>
            <span>{inconclusivasOpen ? '▲' : '▼'}</span>
          </button>

          {inconclusivasOpen && (
            <ul className="divide-y divide-yellow-100 px-4 pb-3">
              {job.inconclusivas.map((item) => (
                <li key={item.photo_id} className="py-2 text-sm text-yellow-800">
                  <span className="font-medium">{item.field_name}</span> — {item.confidence}%
                  confiança
                  {item.second_best_field && (
                    <span className="ml-2 text-yellow-600">
                      / {item.second_best_field} {item.second_best_confidence}%
                    </span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  )
}
