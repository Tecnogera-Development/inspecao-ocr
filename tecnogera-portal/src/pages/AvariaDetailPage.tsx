import { apiClient } from '@/api/client'
import type { EventDetailResponse, PairDetailResponse } from '@/api/types'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR } from 'date-fns/locale'
import { Link, useParams } from 'react-router-dom'

async function fetchPairDetail(pairId: string): Promise<PairDetailResponse> {
  const result = await apiClient.GET('/api/v1/portal/avarias/pairs/{pair_id}', {
    params: { path: { pair_id: pairId } },
  })
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

function imageUrl(path: string): string {
  return `/api/v1/portal/avarias/image?path=${encodeURIComponent(path)}`
}

const DAMAGE_LABEL: Record<string, string> = {
  dano_visivel: 'Dano visível',
  ausencia_item: 'Ausência de item',
  fora_padrao_visual: 'Fora do padrão',
  conforme: 'Conforme',
}

const CLASSES = ['conforme', 'dano_visivel', 'ausencia_item', 'fora_padrao_visual']

const SEVERITY_COLOR: Record<string, string> = {
  critica: 'text-red-700',
  alta: 'text-orange-700',
  media: 'text-yellow-700',
  baixa: 'text-green-700',
}

function baselinePath(ev: EventDetailResponse | null): string | null {
  const p = ev?.result_json?.baseline_source_path
  return typeof p === 'string' ? p : null
}

function labelClass(c: string | null | undefined): string {
  return c ? (DAMAGE_LABEL[c] ?? c) : 'Conforme'
}

// ── painel de uma foto (entrega ou retorno) ────────────────────────────────────

function PhotoPanel({ src, label, sub }: { src: string | null; label: string; sub?: string }) {
  return (
    <div className="flex-1 min-w-0 rounded-lg border border-slate-200 bg-white p-3 space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          {label}
        </span>
        {sub && <span className="text-xs text-slate-400">{sub}</span>}
      </div>
      <div className="overflow-hidden rounded-md bg-slate-100">
        {src ? (
          <img
            src={src}
            alt={label}
            loading="lazy"
            decoding="async"
            className="w-full object-contain max-h-80"
          />
        ) : (
          <div className="flex h-48 items-center justify-center text-sm text-slate-400">
            sem imagem
          </div>
        )}
      </div>
    </div>
  )
}

function Skeleton() {
  return (
    <div className="animate-pulse space-y-6">
      <div className="h-8 w-48 rounded bg-slate-200" />
      <div className="h-12 rounded bg-slate-200" />
      <div className="flex gap-4">
        <div className="flex-1 h-80 rounded-lg bg-slate-200" />
        <div className="flex-1 h-80 rounded-lg bg-slate-200" />
      </div>
    </div>
  )
}

export function AvariaDetailPage() {
  const { id = '' } = useParams<{ id: string }>()
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['avaria', id],
    queryFn: () => fetchPairDetail(id),
    enabled: Boolean(id),
  })

  const mutation = useMutation({
    mutationFn: async (vars: { eventId: string; groundTruth: string }) => {
      const r = await apiClient.PATCH('/api/v1/portal/avarias/events/{event_id}/ground-truth', {
        params: { path: { event_id: vars.eventId } },
        body: { ground_truth_class: vars.groundTruth },
      })
      if (!r.data) throw new Error('Falha ao registrar validação')
      return r.data
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['avaria', id] }),
  })

  if (query.isPending) return <Skeleton />

  if (query.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
        <p className="text-red-700">Par de avaria não encontrado.</p>
        <Link to="/avarias" className="mt-3 inline-block text-sm text-brand-hover underline">
          Voltar para lista
        </Link>
      </div>
    )
  }

  const pair = query.data
  // Evento julgado (o retorno é o estado avaliado; cai para saída se não houver)
  const verdictEv = pair.retorno ?? pair.saida
  const isNonConform = Boolean(verdictEv?.damage_class)
  const base = baselinePath(pair.retorno) ?? baselinePath(pair.saida)
  const predicted = verdictEv?.damage_class ?? 'conforme'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-4">
        <Link to="/avarias" className="text-sm text-brand-hover hover:underline">
          ← Avarias
        </Link>
        <h1 className="text-2xl font-bold text-slate-900">{pair.asset_code}</h1>
        {verdictEv?.checklist_id && (
          <span className="text-slate-500 text-sm">Checklist {verdictEv.checklist_id}</span>
        )}
        <span className="text-slate-500 text-sm">
          {format(new Date(pair.pair_date), 'dd/MM/yyyy', { locale: ptBR })}
        </span>
      </div>

      {/* Banner de veredito */}
      <div
        className={`rounded-lg border p-4 ${isNonConform ? 'border-red-300 bg-red-50' : 'border-green-300 bg-green-50'}`}
      >
        <div className="flex flex-wrap items-center gap-3">
          <span
            className={`inline-block rounded-full px-3 py-1 text-sm font-bold ${isNonConform ? 'bg-red-600 text-white' : 'bg-green-600 text-white'}`}
          >
            {isNonConform ? 'NÃO CONFORME' : 'CONFORME'}
          </span>
          {isNonConform && (
            <span className="text-sm text-red-800">
              {labelClass(verdictEv?.damage_class)}
              {verdictEv?.damage_severity && (
                <span
                  className={`ml-2 font-semibold ${SEVERITY_COLOR[verdictEv.damage_severity] ?? ''}`}
                >
                  · severidade {verdictEv.damage_severity}
                </span>
              )}
              {verdictEv?.damage_confidence != null && (
                <span className="ml-2 text-red-600">
                  · {(verdictEv.damage_confidence * 100).toFixed(0)}%
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {/* Comparação ENTREGA | RETORNO */}
      <div className="flex flex-col gap-4 sm:flex-row">
        <PhotoPanel
          src={base ? imageUrl(base) : null}
          label="Entrega (como saiu)"
          sub={base ? 'checklist de origem' : 'sem base vinculada'}
        />
        <PhotoPanel
          src={verdictEv ? imageUrl(verdictEv.source_path) : null}
          label="Retorno (como voltou)"
          sub={
            verdictEv?.captured_at
              ? format(new Date(verdictEv.captured_at), 'dd/MM/yyyy HH:mm', { locale: ptBR })
              : undefined
          }
        />
      </div>

      {/* Não conformidades do JSON */}
      {isNonConform && verdictEv?.result_json && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="mb-1 text-sm font-semibold text-slate-700">Observação</p>
          <p className="text-sm text-slate-600">{describeFinding(verdictEv.result_json)}</p>
        </div>
      )}

      {/* Ação HITL: validação do operador */}
      {verdictEv && (
        <div className="rounded-lg border border-brand-secondary/40 bg-brand-tint/40 p-4">
          {verdictEv.ground_truth_class ? (
            <p className="mb-3 text-sm text-slate-700">
              ✔ Validado pelo operador como{' '}
              <span className="font-semibold">{labelClass(verdictEv.ground_truth_class)}</span> —
              ajuste abaixo se necessário.
            </p>
          ) : (
            <p className="mb-3 text-sm font-medium text-slate-700">Esta avaliação está correta?</p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => mutation.mutate({ eventId: verdictEv.id, groundTruth: predicted })}
              disabled={mutation.isPending}
              className="rounded-md bg-green-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-green-700 disabled:opacity-50"
            >
              Confirmar ({labelClass(predicted)})
            </button>
            <label className="text-sm text-slate-600">
              Corrigir para:{' '}
              <select
                className="rounded border border-slate-300 px-2 py-1 text-sm"
                value={verdictEv.ground_truth_class ?? ''}
                onChange={(e) =>
                  e.target.value &&
                  mutation.mutate({ eventId: verdictEv.id, groundTruth: e.target.value })
                }
              >
                <option value="">selecione…</option>
                {CLASSES.map((c) => (
                  <option key={c} value={c}>
                    {labelClass(c)}
                  </option>
                ))}
              </select>
            </label>
            {mutation.isError && (
              <span className="text-sm text-red-600">Erro ao registrar — tente novamente</span>
            )}
          </div>
        </div>
      )}

      {/* Composto anotado (evidência para download) */}
      {pair.annotated_image_path && (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-500">
            Comparativo anotado (evidência)
          </p>
          <img
            src={imageUrl(pair.annotated_image_path)}
            alt={`Comparativo — ${pair.asset_code}`}
            loading="lazy"
            decoding="async"
            className="w-full rounded-md object-contain max-h-64"
          />
        </div>
      )}
    </div>
  )
}

function describeFinding(resultJson: Record<string, unknown>): string {
  const classes = resultJson.classes
  if (Array.isArray(classes) && classes.length > 0) {
    const first = classes[0] as { observation?: string }
    if (first?.observation) return first.observation
  }
  return 'Não conformidade detectada na comparação com a entrega.'
}
