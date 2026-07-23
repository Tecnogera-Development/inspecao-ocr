import { apiClient } from '@/api/client'
import type { PairsListResponse } from '@/api/types'
import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR } from 'date-fns/locale'
import { Link, useSearchParams } from 'react-router-dom'

const LIMIT = 50

async function fetchPairs(params: {
  status?: string
  asset_code?: string
  offset: number
}): Promise<PairsListResponse> {
  const result = await apiClient.GET('/api/v1/portal/avarias/pairs', {
    params: {
      query: {
        limit: LIMIT,
        offset: params.offset,
        ...(params.status ? { status: params.status } : {}),
        ...(params.asset_code ? { asset_code: params.asset_code } : {}),
      },
    },
  })
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

const SEVERITY_COLOR: Record<string, string> = {
  critica: 'bg-red-100 text-red-800',
  alta: 'bg-orange-100 text-orange-800',
  media: 'bg-yellow-100 text-yellow-800',
  baixa: 'bg-green-100 text-green-800',
}

export function AvariasPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const offset = Number(searchParams.get('offset') ?? '0')
  const statusFilter = searchParams.get('status') ?? ''
  const assetCode = searchParams.get('asset_code') ?? ''

  const query = useQuery({
    queryKey: ['avarias', { status: statusFilter, asset_code: assetCode, offset }],
    queryFn: () => fetchPairs({ status: statusFilter, asset_code: assetCode, offset }),
  })

  const pairs = query.data?.items ?? []
  const total = query.data?.total ?? 0
  const hasPrev = offset > 0
  const hasNext = pairs.length >= LIMIT

  function setParam(key: string, value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      next.delete('offset')
      return next
    })
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Avarias</h1>
        <Link
          to="/avarias/nova"
          className="rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover"
        >
          + Nova avaria
        </Link>
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
            onChange={(e) => setParam('status', e.target.value)}
          >
            <option value="">Todos</option>
            <option value="complete">Completo</option>
            <option value="partial">Parcial</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <label htmlFor="asset-filter" className="text-sm font-medium text-slate-700">
            Ativo
          </label>
          <input
            id="asset-filter"
            type="text"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="ex: GER-001"
            value={assetCode}
            onChange={(e) => setParam('asset_code', e.target.value)}
          />
        </div>
        <span className="self-center text-sm text-slate-500">{total} registros</span>
      </div>

      {/* Content */}
      {query.isError ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
          <p className="text-red-700">Não foi possível carregar</p>
          <button
            type="button"
            className="mt-3 rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium hover:bg-red-100"
            onClick={() => query.refetch()}
          >
            Tentar novamente
          </button>
        </div>
      ) : !query.isPending && pairs.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-10 text-center">
          <p className="text-lg font-medium text-slate-600">Nenhum par de avaria encontrado</p>
        </div>
      ) : (
        <>
          {/* Tabela (desktop) */}
          <div className="hidden overflow-x-auto rounded-lg border border-slate-200 md:block">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b bg-brand-tint text-left">
                  {[
                    'ID Checklist',
                    'Ativo',
                    'Indicador',
                    'Severidade',
                    'Data processamento',
                    '',
                  ].map((h) => (
                    <th
                      key={h}
                      className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {pairs.map((pair, idx) => (
                  <tr
                    key={pair.id}
                    className={`border-b last:border-0 hover:bg-brand-tint/50 ${idx % 2 === 0 ? 'bg-white' : 'bg-orange-50'}`}
                  >
                    <td className="px-3 py-2 font-mono font-medium">
                      {pair.checklist_id ?? <span className="text-slate-400">—</span>}
                    </td>
                    <td className="px-3 py-2 font-mono">{pair.asset_code}</td>
                    <td className="px-3 py-2">
                      {pair.has_non_conformity ? (
                        <span className="inline-block rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800">
                          Não conforme
                        </span>
                      ) : (
                        <span className="inline-block rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
                          Conforme
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {pair.saida_damage_severity ? (
                        <span
                          className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_COLOR[pair.saida_damage_severity] ?? 'bg-slate-100 text-slate-700'}`}
                        >
                          {pair.saida_damage_severity}
                        </span>
                      ) : (
                        <span className="text-slate-400">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2">
                      {format(new Date(pair.created_at), 'dd/MM/yyyy HH:mm', { locale: ptBR })}
                    </td>
                    <td className="px-3 py-2">
                      <Link
                        to={`/avarias/${pair.id}`}
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

          {/* Cards (celular) */}
          <div className="space-y-3 md:hidden">
            {pairs.map((pair) => (
              <Link
                key={pair.id}
                to={`/avarias/${pair.id}`}
                className="block rounded-lg border border-slate-200 bg-white p-4 active:bg-brand-tint/40"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono font-semibold text-slate-900">{pair.asset_code}</span>
                  {pair.has_non_conformity ? (
                    <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs font-medium text-red-800">
                      Não conforme
                    </span>
                  ) : (
                    <span className="rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800">
                      Conforme
                    </span>
                  )}
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-600">
                  <span>
                    Checklist <span className="font-mono">{pair.checklist_id ?? '—'}</span>
                  </span>
                  {pair.saida_damage_severity && (
                    <span
                      className={`rounded-full px-2 py-0.5 text-xs font-medium ${SEVERITY_COLOR[pair.saida_damage_severity] ?? 'bg-slate-100 text-slate-700'}`}
                    >
                      {pair.saida_damage_severity}
                    </span>
                  )}
                </div>
                <div className="mt-1 text-xs text-slate-400">
                  {format(new Date(pair.created_at), 'dd/MM/yyyy HH:mm', { locale: ptBR })}
                </div>
              </Link>
            ))}
          </div>

          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-600">
              {pairs.length > 0 ? `${offset + 1}–${offset + pairs.length} de ${total}` : ''}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() =>
                  setSearchParams((p) => {
                    const n = new URLSearchParams(p)
                    n.set('offset', String(Math.max(0, offset - LIMIT)))
                    return n
                  })
                }
                disabled={!hasPrev}
                className="rounded-md border border-brand-secondary px-3 py-1.5 text-sm font-medium text-brand-hover hover:bg-brand-tint disabled:opacity-40"
              >
                Anterior
              </button>
              <button
                type="button"
                onClick={() =>
                  setSearchParams((p) => {
                    const n = new URLSearchParams(p)
                    n.set('offset', String(offset + LIMIT))
                    return n
                  })
                }
                disabled={!hasNext}
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
