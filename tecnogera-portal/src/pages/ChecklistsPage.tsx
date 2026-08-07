import { apiClient } from '@/api/client'
import type { ChecklistItemResponse, ChecklistListResponse } from '@/api/types'
import { IndicadorBadge, indicadorAnchor } from '@/components/IndicadorBadge'
import { useQuery } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR } from 'date-fns/locale'
import { type ReactNode, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

const LIMIT = 50

/**
 * Sentinela de URL. A tela abre em "a validar" — é fila de trabalho, não
 * histórico —, então a ausência do parâmetro significa `pendente`. Para ver
 * tudo o operador escolhe explicitamente "Todas", que vira este valor na URL
 * e NÃO é enviado ao backend.
 */
const VALIDACAO_TODAS = 'todas'

/**
 * `validacao` não tem `*_rotulo` no contrato (é enum de processo, não de
 * laudo), então o rótulo de tela mora aqui. Todo o resto do vocabulário vem
 * pronto do backend e não é traduzido no front.
 */
const VALIDACAO_LABEL: Record<string, string> = {
  pendente: 'A validar',
  confirmado: 'Confirmado',
  corrigido: 'Corrigido',
}

const INDICADOR_OPCOES: { valor: string; rotulo: string }[] = [
  { valor: 'nao_conforme', rotulo: 'Não conforme' },
  { valor: 'nao_processavel', rotulo: 'Não processável' },
  { valor: 'conforme', rotulo: 'Conforme' },
  { valor: 'sem_analise', rotulo: 'Sem análise' },
]

interface ChecklistFilters {
  indicador: string
  validacao: string
  filial: string
  formulario: string
  codigo_checklist: string
  data_de: string
  data_ate: string
  ordenar: string
  offset: number
}

async function fetchChecklists(filters: ChecklistFilters): Promise<ChecklistListResponse> {
  const result = await apiClient.GET('/api/v1/portal/checklists', {
    params: {
      query: {
        limit: LIMIT,
        offset: filters.offset,
        ordenar: filters.ordenar,
        ...(filters.indicador ? { indicador: filters.indicador } : {}),
        ...(filters.validacao !== VALIDACAO_TODAS ? { validacao: filters.validacao } : {}),
        ...(filters.filial ? { filial: filters.filial } : {}),
        ...(filters.formulario ? { formulario: filters.formulario } : {}),
        ...(filters.codigo_checklist ? { codigo_checklist: filters.codigo_checklist } : {}),
        ...(filters.data_de ? { data_de: filters.data_de } : {}),
        ...(filters.data_ate ? { data_ate: filters.data_ate } : {}),
      },
    },
  })
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

function formatData(value: string | null | undefined): string {
  if (!value) return '—'
  return format(new Date(value), 'dd/MM/yyyy', { locale: ptBR })
}

/**
 * Os mesmos filtros da lista, **sem** `limit`/`offset` — o gesto do export
 * é "isto que estou vendo, inteiro", não a página atual (ticket
 * `v1-entregavel/06`).
 */
function buildExportParams(filters: ChecklistFilters): URLSearchParams {
  const params = new URLSearchParams()
  params.set('ordenar', filters.ordenar)
  if (filters.indicador) params.set('indicador', filters.indicador)
  if (filters.validacao !== VALIDACAO_TODAS) params.set('validacao', filters.validacao)
  if (filters.filial) params.set('filial', filters.filial)
  if (filters.formulario) params.set('formulario', filters.formulario)
  if (filters.codigo_checklist) params.set('codigo_checklist', filters.codigo_checklist)
  if (filters.data_de) params.set('data_de', filters.data_de)
  if (filters.data_ate) params.set('data_ate', filters.data_ate)
  return params
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const match = /filename="?([^";]+)"?/.exec(header)
  return match?.[1] ?? null
}

/**
 * `fetch` cru com `credentials: "include"` → `Blob` → âncora. Um `<a href>`
 * simples não passa a sessão; `apiClient` (openapi-fetch) é para JSON, não
 * para baixar um binário — por isso este endpoint não passa por ele.
 */
async function exportarChecklistsExcel(filters: ChecklistFilters): Promise<void> {
  const params = buildExportParams(filters)
  const resp = await fetch(`/api/v1/portal/checklists/export.xlsx?${params.toString()}`, {
    credentials: 'include',
  })
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  const blob = await resp.blob()
  const nomeArquivo =
    filenameFromDisposition(resp.headers.get('content-disposition')) ?? 'checklists.xlsx'
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = nomeArquivo
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/**
 * "2 fotos faltando" só quando faltam de verdade: `vistas_ausentes` vazio nunca acende.
 *
 * Em `sem_analise` o backend preenche `vistas_ausentes` com TODAS as vistas esperadas,
 * porque ainda não existe laudo nenhum — mas as fotos podem estar no Dropbox, apenas
 * não processadas. Acender o chip aí diria ao operador que o técnico esqueceu de
 * fotografar quando o que houve foi a esteira não ter rodado. Ausência só é afirmável
 * depois da análise.
 */
function ausentesLabel(item: ChecklistItemResponse): string | null {
  if (item.indicador === 'sem_analise') return null
  const n = item.vistas_ausentes.length
  if (n === 0) return null
  return n === 1 ? '1 foto faltando' : `${n} fotos faltando`
}

export function ChecklistsPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  const filters: ChecklistFilters = {
    indicador: searchParams.get('indicador') ?? '',
    validacao: searchParams.get('validacao') ?? 'pendente',
    filial: searchParams.get('filial') ?? '',
    formulario: searchParams.get('formulario') ?? '',
    codigo_checklist: searchParams.get('codigo_checklist') ?? '',
    data_de: searchParams.get('data_de') ?? '',
    data_ate: searchParams.get('data_ate') ?? '',
    ordenar: searchParams.get('ordenar') ?? 'severidade',
    offset: Number(searchParams.get('offset') ?? '0'),
  }

  const query = useQuery({
    queryKey: ['checklists', filters],
    queryFn: () => fetchChecklists(filters),
  })

  const itens = query.data?.itens ?? []
  const total = query.data?.total ?? 0
  const contadores = query.data?.contadores
  const facetas = query.data?.facetas
  const hasPrev = filters.offset > 0
  const hasNext = filters.offset + itens.length < total

  const [exportando, setExportando] = useState(false)
  const [erroExportacao, setErroExportacao] = useState<string | null>(null)

  async function handleExportar() {
    setExportando(true)
    setErroExportacao(null)
    try {
      await exportarChecklistsExcel(filters)
    } catch {
      setErroExportacao('Não foi possível exportar. Tente novamente.')
    } finally {
      setExportando(false)
    }
  }

  function setParam(key: string, value: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      if (value) next.set(key, value)
      else next.delete(key)
      next.delete('offset')
      return next
    })
  }

  function moveOffset(delta: number) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev)
      next.set('offset', String(Math.max(0, filters.offset + delta)))
      return next
    })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-slate-900">Checklists</h1>
        <span className="text-sm text-slate-500">{total} na seleção</span>
      </div>

      {/* Contadores — sem gráfico, por guarda-corpo do projeto (§2.7) */}
      <div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <Contador
            label="A validar"
            value={contadores?.a_validar}
            testId="contador-a-validar"
            accent="border-t-4 border-t-brand-primary"
          />
          <Contador
            label="Não conformes"
            value={contadores?.nao_conformes}
            testId="contador-nao-conformes"
            accent="border-t-4 border-t-red-600"
          />
          <Contador
            label="Não processáveis"
            value={contadores?.nao_processaveis}
            testId="contador-nao-processaveis"
            accent="border-t-4 border-t-amber-500"
          />
          <Contador
            label="Conformes"
            value={contadores?.conformes}
            testId="contador-conformes"
            accent="border-t-4 border-t-green-600"
          />
          <Contador
            label="Sem análise"
            value={contadores?.sem_analise}
            testId="contador-sem-analise"
            accent="border-t-4 border-t-slate-400"
          />
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Contadores respeitam filial, formulário, período e ID — e ignoram indicador e validação,
          para o volume de trabalho não sumir por causa de um filtro.
        </p>
      </div>

      {/* Exportar Excel — mesmos filtros da tela, mas o conjunto INTEIRO, sem paginação */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={handleExportar}
          disabled={exportando}
          data-testid="botao-exportar-excel"
          className="rounded-md border border-brand-secondary px-3 py-1.5 text-sm font-medium text-brand-hover hover:bg-brand-tint disabled:cursor-not-allowed disabled:opacity-40"
        >
          {exportando ? 'Exportando…' : 'Exportar Excel'}
        </button>
        {erroExportacao && (
          <span role="alert" data-testid="erro-exportar-excel" className="text-sm text-red-600">
            {erroExportacao}
          </span>
        )}
      </div>

      {/* Filtros */}
      <div className="flex flex-wrap items-end gap-4">
        <Campo label="Indicador" htmlFor="filtro-indicador">
          <select
            id="filtro-indicador"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={filters.indicador}
            onChange={(e) => setParam('indicador', e.target.value)}
          >
            <option value="">Todos</option>
            {INDICADOR_OPCOES.map((o) => (
              <option key={o.valor} value={o.valor}>
                {o.rotulo}
              </option>
            ))}
          </select>
        </Campo>

        <Campo label="Validação" htmlFor="filtro-validacao">
          <select
            id="filtro-validacao"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={filters.validacao}
            onChange={(e) => setParam('validacao', e.target.value)}
          >
            <option value="pendente">A validar</option>
            <option value="confirmado">Confirmado</option>
            <option value="corrigido">Corrigido</option>
            <option value={VALIDACAO_TODAS}>Todas</option>
          </select>
        </Campo>

        <Campo label="Filial" htmlFor="filtro-filial">
          <select
            id="filtro-filial"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={filters.filial}
            onChange={(e) => setParam('filial', e.target.value)}
          >
            <option value="">Todas</option>
            {(facetas?.filiais ?? []).map((f) => (
              <option key={f} value={f}>
                {f}
              </option>
            ))}
          </select>
        </Campo>

        {/*
         * Escondido, não removido: com uma opção só o seletor anunciaria uma
         * escolha que não existe. Volta sozinho quando um segundo formulário
         * for ligado (facetas.formularios ganha um segundo item).
         */}
        {(facetas?.formularios.length ?? 0) > 1 && (
          <Campo label="Formulário" htmlFor="filtro-formulario">
            <select
              id="filtro-formulario"
              className="rounded border border-slate-300 px-2 py-1 text-sm"
              value={filters.formulario}
              onChange={(e) => setParam('formulario', e.target.value)}
            >
              <option value="">Todos</option>
              {(facetas?.formularios ?? []).map((f) => (
                <option key={f} value={f}>
                  {f}
                </option>
              ))}
            </select>
          </Campo>
        )}

        <Campo label="ID checklist" htmlFor="filtro-id">
          <input
            id="filtro-id"
            type="text"
            inputMode="numeric"
            className="w-32 rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="ex: 311989"
            value={filters.codigo_checklist}
            onChange={(e) => setParam('codigo_checklist', e.target.value)}
          />
        </Campo>

        <Campo label="De" htmlFor="filtro-data-de">
          <input
            id="filtro-data-de"
            type="date"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={filters.data_de}
            onChange={(e) => setParam('data_de', e.target.value)}
          />
        </Campo>

        <Campo label="Até" htmlFor="filtro-data-ate">
          <input
            id="filtro-data-ate"
            type="date"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={filters.data_ate}
            onChange={(e) => setParam('data_ate', e.target.value)}
          />
        </Campo>

        <Campo label="Ordenar" htmlFor="filtro-ordenar">
          <select
            id="filtro-ordenar"
            className="rounded border border-slate-300 px-2 py-1 text-sm"
            value={filters.ordenar}
            onChange={(e) => setParam('ordenar', e.target.value)}
          >
            <option value="severidade">Severidade</option>
            <option value="recente">Mais recente</option>
          </select>
        </Campo>
      </div>

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
      ) : query.isPending ? (
        <div data-testid="checklists-skeleton" className="animate-pulse space-y-2">
          <div className="h-10 rounded bg-slate-200" />
          <div className="h-10 rounded bg-slate-100" />
          <div className="h-10 rounded bg-slate-200" />
        </div>
      ) : itens.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-10 text-center">
          <p className="text-lg font-medium text-slate-600">Nenhum checklist nesta seleção</p>
          {filters.validacao === 'pendente' ? (
            <p className="mt-2 text-sm text-slate-500">
              Nada a validar por aqui — a fila de trabalho está vazia nesta seleção.
            </p>
          ) : (
            filters.validacao !== VALIDACAO_TODAS && (
              <p className="mt-2 text-sm text-slate-500">
                Nenhum checklist foi{' '}
                {filters.validacao === 'confirmado' ? 'confirmado' : 'corrigido'} nesta seleção.
              </p>
            )
          )}
        </div>
      ) : (
        <>
          {/* Tabela (desktop) */}
          <div className="hidden overflow-x-auto rounded-lg border border-slate-200 md:block">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b bg-brand-tint text-left">
                  <th className="w-1 p-0">
                    <span className="sr-only">Indicador (âncora)</span>
                  </th>
                  {[
                    'ID checklist',
                    'Ativo',
                    'Filial',
                    'Indicador',
                    'Sev.',
                    'Vista',
                    'Data',
                    'Validação',
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
                {itens.map((item) => {
                  const ausentes = ausentesLabel(item)
                  return (
                    <tr
                      key={item.job_id}
                      data-testid={`checklist-row-${item.checklist_id}`}
                      className="border-b bg-white last:border-0 hover:bg-brand-tint/40"
                    >
                      {/* Âncora visual à esquerda — não conforme salta sem depender do pill */}
                      <td
                        aria-hidden="true"
                        data-testid={`anchor-${item.checklist_id}`}
                        className={`w-1 p-0 ${indicadorAnchor(item.indicador)}`}
                      />
                      <td className="px-3 py-2 font-mono font-medium">
                        <Link
                          to={`/checklists/${item.job_id}`}
                          className="text-brand-hover underline underline-offset-2 hover:text-brand-primary"
                        >
                          {item.checklist_id}
                        </Link>
                      </td>
                      <td className="px-3 py-2">
                        <span className="font-mono">{item.patrimonio ?? '—'}</span>
                        {item.multi_ativo && (
                          <span
                            data-testid={`multi-ativo-${item.checklist_id}`}
                            title={`Este checklist cobre ${item.n_linhas ?? 2} ativos — o laudo é de um deles`}
                            className="ml-2 whitespace-nowrap rounded bg-amber-100 px-1.5 py-0.5 text-[11px] font-medium text-amber-900"
                          >
                            {item.n_linhas ?? 2} ativos
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2" data-testid={`filial-${item.checklist_id}`}>
                        {item.filial ?? '—'}
                      </td>
                      <td className="px-3 py-2">
                        <IndicadorBadge
                          indicador={item.indicador}
                          rotulo={item.indicador_rotulo}
                          data-testid={`indicador-${item.checklist_id}`}
                        />
                      </td>
                      <td className="px-3 py-2">{item.severidade_rotulo ?? '—'}</td>
                      <td className="px-3 py-2">
                        <span>{item.vista_determinante_rotulo ?? '—'}</span>
                        {ausentes && (
                          <span
                            data-testid={`ausentes-${item.checklist_id}`}
                            className="ml-2 whitespace-nowrap rounded bg-slate-200 px-1.5 py-0.5 text-[11px] font-medium text-slate-700"
                          >
                            {ausentes}
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">{formatData(item.data)}</td>
                      <td className="px-3 py-2 text-slate-600">
                        {VALIDACAO_LABEL[item.validacao] ?? item.validacao}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          {/* Cards (celular) */}
          <div className="space-y-3 md:hidden">
            {itens.map((item) => {
              const ausentes = ausentesLabel(item)
              return (
                <Link
                  key={item.job_id}
                  to={`/checklists/${item.job_id}`}
                  data-testid={`checklist-card-${item.checklist_id}`}
                  className="flex overflow-hidden rounded-lg border border-slate-200 bg-white active:bg-brand-tint/40"
                >
                  <span
                    aria-hidden="true"
                    className={`w-1.5 shrink-0 ${indicadorAnchor(item.indicador)}`}
                  />
                  <div className="min-w-0 flex-1 p-4">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-mono font-semibold text-slate-900">
                        {item.patrimonio ?? '—'}
                      </span>
                      <IndicadorBadge indicador={item.indicador} rotulo={item.indicador_rotulo} />
                    </div>
                    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-slate-600">
                      <span>
                        Checklist <span className="font-mono">{item.checklist_id}</span>
                      </span>
                      {item.filial && (
                        <span data-testid={`filial-card-${item.checklist_id}`}>{item.filial}</span>
                      )}
                      {item.severidade_rotulo && <span>Sev. {item.severidade_rotulo}</span>}
                      {item.vista_determinante_rotulo && (
                        <span>{item.vista_determinante_rotulo}</span>
                      )}
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                      <span>{formatData(item.data)}</span>
                      <span>{VALIDACAO_LABEL[item.validacao] ?? item.validacao}</span>
                      {item.multi_ativo && (
                        <span className="rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-900">
                          {item.n_linhas ?? 2} ativos
                        </span>
                      )}
                      {ausentes && (
                        <span className="rounded bg-slate-200 px-1.5 py-0.5 font-medium text-slate-700">
                          {ausentes}
                        </span>
                      )}
                    </div>
                  </div>
                </Link>
              )
            })}
          </div>

          <div className="flex items-center justify-between">
            <span className="text-sm text-slate-600">
              {`${filters.offset + 1}–${filters.offset + itens.length} de ${total}`}
            </span>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => moveOffset(-LIMIT)}
                disabled={!hasPrev}
                className="rounded-md border border-brand-secondary px-3 py-1.5 text-sm font-medium text-brand-hover hover:bg-brand-tint disabled:opacity-40"
              >
                Anterior
              </button>
              <button
                type="button"
                onClick={() => moveOffset(LIMIT)}
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

function Campo({
  label,
  htmlFor,
  children,
}: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className="text-xs font-medium text-slate-700">
        {label}
      </label>
      {children}
    </div>
  )
}

function Contador({
  label,
  value,
  testId,
  accent,
}: { label: string; value: number | undefined; testId: string; accent: string }) {
  return (
    <div className={`rounded-lg border border-slate-200 bg-white p-3 shadow-sm ${accent}`}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p data-testid={testId} className="mt-1 text-2xl font-bold text-slate-900">
        {value ?? '—'}
      </p>
    </div>
  )
}
