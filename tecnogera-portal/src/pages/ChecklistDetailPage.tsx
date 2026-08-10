import { apiClient } from '@/api/client'
import type {
  ChecklistAchadoResponse,
  ChecklistCorrecaoBody,
  ChecklistDetailResponse,
  ChecklistValidationOptionsResponse,
  ChecklistViewResponse,
} from '@/api/types'
import { IndicadorBadge, indicadorAnchor } from '@/components/IndicadorBadge'
import { Lightbox } from '@/components/Lightbox'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR } from 'date-fns/locale'
import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'

async function fetchChecklistDetail(identificador: string): Promise<ChecklistDetailResponse> {
  const result = await apiClient.GET('/api/v1/portal/checklists/{identificador}', {
    params: { path: { identificador } },
  })
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

function filenameFromDisposition(header: string | null): string | null {
  if (!header) return null
  const match = /filename="?([^";]+)"?/.exec(header)
  return match?.[1] ?? null
}

/**
 * `fetch` cru com `credentials: "include"` → `Blob` → âncora — mesmo padrão
 * de `exportarChecklistsExcel` (`ChecklistsPage.tsx`, ticket
 * `v1-entregavel/06`). Um `<a href>` simples não passa a sessão; o
 * `apiClient` (openapi-fetch) é para JSON, não para baixar um binário.
 *
 * A rota baixa até 4 fotos do Dropbox no servidor antes de responder — não é
 * instantâneo, por isso o chamador mostra estado de carregando.
 */
async function exportarChecklistPdf(identificador: string): Promise<void> {
  const resp = await fetch(`/api/v1/portal/checklists/${identificador}/pdf`, {
    credentials: 'include',
  })
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}`)
  }
  const blob = await resp.blob()
  const nomeArquivo =
    filenameFromDisposition(resp.headers.get('content-disposition')) ?? 'laudo.pdf'
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = nomeArquivo
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}

/** Mensagem de erro do backend, que já vem escrita para o operador. */
function detalheDoErro(erro: unknown, fallback: string): string {
  const detail = (erro as { detail?: unknown })?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

async function confirmarChecklist(identificador: string): Promise<void> {
  const result = await apiClient.POST('/api/v1/portal/checklists/{identificador}/confirmar', {
    params: { path: { identificador } },
  })
  if (result.error || !result.data) {
    throw new Error(detalheDoErro(result.error, 'Não foi possível confirmar.'))
  }
}

async function corrigirVista(identificador: string, body: ChecklistCorrecaoBody): Promise<void> {
  const result = await apiClient.POST('/api/v1/portal/checklists/{identificador}/corrigir', {
    params: { path: { identificador } },
    body,
  })
  if (result.error || !result.data) {
    throw new Error(detalheDoErro(result.error, 'Não foi possível registrar a correção.'))
  }
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  return format(new Date(value), 'dd/MM/yyyy HH:mm', { locale: ptBR })
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '—'
  return format(new Date(value), 'dd/MM/yyyy', { locale: ptBR })
}

function formatConfianca(value: number | null | undefined): string | null {
  if (value == null) return null
  return `${Math.round(value * 100)}%`
}

/**
 * O backend expõe `classe_rotulo` e `tipo_defeito_rotulo` prontos (ticket
 * `v1-entregavel/02`), que eram os dois únicos campos do laudo sem rótulo no
 * contrato. O front só exibe o que vem — nenhum texto de domínio é montado
 * aqui, nem para laudo cujo valor não esteja no mapa: o fallback de
 * `snake_case` → texto agora é responsabilidade do backend
 * (`view_inspection.rotulo_classe`/`rotulo_tipo_defeito`).
 */
function descreverAchado(a: ChecklistAchadoResponse): string {
  return a.tipo_defeito_rotulo ?? a.classe_rotulo ?? 'Achado'
}

function Skeleton() {
  return (
    <div data-testid="checklist-detail-skeleton" className="animate-pulse space-y-6">
      <div className="h-8 w-64 rounded bg-slate-200" />
      <div className="h-16 rounded-lg bg-slate-200" />
      <div className="h-24 rounded-lg bg-slate-100" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div className="h-64 rounded-lg bg-slate-200" />
        <div className="h-64 rounded-lg bg-slate-200" />
        <div className="h-64 rounded-lg bg-slate-200" />
      </div>
    </div>
  )
}

export function ChecklistDetailPage() {
  const { id = '' } = useParams<{ id: string }>()

  const query = useQuery({
    queryKey: ['checklist', id],
    queryFn: () => fetchChecklistDetail(id),
    enabled: Boolean(id),
  })

  const [exportandoPdf, setExportandoPdf] = useState(false)
  const [erroExportacaoPdf, setErroExportacaoPdf] = useState<string | null>(null)

  if (query.isPending) return <Skeleton />

  if (query.isError) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-center">
        <p className="text-red-700">Checklist não encontrado.</p>
        <Link to="/checklists" className="mt-3 inline-block text-sm text-brand-hover underline">
          Voltar para a lista
        </Link>
      </div>
    )
  }

  const detalhe = query.data
  const eq = detalhe.equipamento
  const achados = detalhe.achados ?? []
  const confianca = formatConfianca(detalhe.confianca)
  const semAnalise = detalhe.indicador === 'sem_analise'

  async function handleExportarPdf() {
    setExportandoPdf(true)
    setErroExportacaoPdf(null)
    try {
      await exportarChecklistPdf(id)
    } catch (erro) {
      setErroExportacaoPdf(
        erro instanceof Error && erro.message === 'HTTP 409'
          ? 'Este checklist ainda não tem laudo processado — não há o que exportar.'
          : 'Não foi possível gerar o PDF. Tente novamente.',
      )
    } finally {
      setExportandoPdf(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Cabeçalho */}
      <div>
        <Link to="/checklists" className="text-sm text-brand-hover hover:underline">
          ← Checklists
        </Link>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-2xl font-bold text-slate-900">{eq.patrimonio ?? 'Sem patrimônio'}</h1>
          <span className="text-sm text-slate-500">Checklist {detalhe.checklist_id}</span>
          {eq.formulario && <span className="text-sm text-slate-500">{eq.formulario}</span>}
          {eq.filial && <span className="text-sm text-slate-500">{eq.filial}</span>}
          <span className="text-sm text-slate-500">{formatDate(eq.data_conclusao)}</span>
        </div>
        {eq.responsavel && (
          <p className="mt-1 text-sm text-slate-500">Responsável: {eq.responsavel}</p>
        )}
      </div>

      {/* Aviso de checklist com mais de um ativo — o laudo é de UM deles */}
      {eq.aviso && (
        <div
          data-testid="aviso-multi-ativo"
          className="flex gap-3 rounded-lg border-l-4 border-amber-500 bg-amber-50 p-4"
        >
          <span aria-hidden="true" className="text-lg leading-none text-amber-600">
            ⚠
          </span>
          <div>
            <p className="text-sm font-semibold text-amber-900">
              Este checklist cobre mais de um ativo
            </p>
            <p className="mt-1 text-sm text-amber-900">{eq.aviso}</p>
          </div>
        </div>
      )}

      {/* Exportar laudo em PDF — perto do banner de veredito (ticket v1-entregavel/05) */}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-testid="botao-exportar-pdf"
          onClick={handleExportarPdf}
          disabled={exportandoPdf}
          className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {exportandoPdf ? 'Gerando PDF…' : 'Exportar PDF'}
        </button>
        {exportandoPdf && (
          <span className="text-xs text-slate-500">
            Baixando as fotos do Dropbox para montar o laudo — pode levar alguns segundos.
          </span>
        )}
        {erroExportacaoPdf && (
          <span data-testid="erro-exportar-pdf" className="text-sm text-red-700">
            {erroExportacaoPdf}
          </span>
        )}
      </div>

      {/* Banner de veredito */}
      <div
        data-testid="banner-veredito"
        data-indicador={detalhe.indicador}
        className="overflow-hidden rounded-lg border border-slate-200 bg-white"
      >
        <div className="flex">
          <span
            aria-hidden="true"
            className={`w-2 shrink-0 ${indicadorAnchor(detalhe.indicador)}`}
          />
          <div className="flex-1 p-4">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <IndicadorBadge
                indicador={detalhe.indicador}
                rotulo={detalhe.indicador_rotulo}
                className="px-3 py-1 text-sm"
              />
              {detalhe.severidade_rotulo && (
                <span className="text-sm text-slate-700">
                  severidade <strong>{detalhe.severidade_rotulo}</strong>
                </span>
              )}
              {confianca && (
                <span className="text-sm text-slate-700">
                  confiança <strong>{confianca}</strong>
                </span>
              )}
            </div>
            {detalhe.vista_determinante_rotulo ? (
              <p className="mt-2 text-sm text-slate-600">
                Determinado pela vista:{' '}
                <strong className="text-slate-800">{detalhe.vista_determinante_rotulo}</strong>
              </p>
            ) : semAnalise ? (
              <p className="mt-2 text-sm text-slate-600">
                O job foi criado e ainda não produziu laudo — não há veredito sobre este
                equipamento. Situação do processamento: {detalhe.status}.
              </p>
            ) : (
              <p className="mt-2 text-sm text-slate-600">
                Nenhuma vista puxou o veredito — todas as vistas julgadas estão conformes.
              </p>
            )}
            {detalhe.erro && (
              <p className="mt-2 text-sm text-red-700">Erro do processamento: {detalhe.erro}</p>
            )}
          </div>
        </div>
      </div>

      {/* Equipamento — dados do Sisloc */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Equipamento
        </h2>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
          <Dado label="Patrimônio" value={eq.patrimonio} mono />
          <Dado label="Cliente" value={eq.cliente} />
          <Dado label="Contrato" value={eq.contrato} mono />
          <Dado label="Filial" value={eq.filial} />
          <Dado label="Formulário" value={eq.formulario} />
          <Dado label="Data de conclusão" value={formatDate(eq.data_conclusao)} />
          <Dado label="Responsável" value={eq.responsavel} />
          <Dado label="Origem" value={eq.origem} />
          <Dado label="Nº OM" value={eq.numero_om != null ? String(eq.numero_om) : null} mono />
          <Dado label="Status no Sisloc" value={eq.status_sisloc} />
          <Dado label="Projeto (bruto)" value={eq.projeto_bruto} />
          <Dado label="Processado em" value={formatDateTime(detalhe.criado_em)} />
        </dl>
      </section>

      {/* Grid de vistas — 3 ou 4 molduras, conforme o array `vistas` */}
      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Vistas
        </h2>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {detalhe.vistas.map((vista) => (
            <VistaCard key={vista.campo} vista={vista} />
          ))}
        </div>

        {/* Três molduras podem ser o checklist completo — a nota diz quando é. */}
        {detalhe.nota_vistas && (
          <p
            data-testid="nota-vistas"
            className="mt-3 flex gap-2 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-600"
          >
            <span aria-hidden="true">ⓘ</span>
            {detalhe.nota_vistas}
          </p>
        )}

        {/* Ausência de verdade: a foto era esperada e não chegou. */}
        {detalhe.vistas_ausentes.length > 0 && (
          <p
            data-testid="vistas-ausentes"
            className="mt-3 flex gap-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900"
          >
            <span aria-hidden="true">⚠</span>
            {detalhe.vistas_ausentes.length === 1
              ? 'Uma foto esperada por este formulário não chegou.'
              : `${detalhe.vistas_ausentes.length} fotos esperadas por este formulário não chegaram.`}
          </p>
        )}
      </section>

      {/* Achados — lista achatada, já ordenada pelo backend */}
      <section className="rounded-lg border border-slate-200 bg-white p-4">
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-500">
          Achados
        </h2>
        {achados.length === 0 ? (
          <p className="text-sm text-slate-500">
            {semAnalise
              ? 'Sem laudo — o job ainda não foi processado.'
              : 'Nenhum achado registrado neste checklist.'}
          </p>
        ) : (
          <ul className="space-y-3">
            {achados.map((a, idx) => {
              const conf = formatConfianca(a.confianca)
              return (
                <li
                  // achados não têm id no contrato — a chave é campo + posição na lista ordenada
                  key={`${a.campo ?? 'x'}-${idx}`}
                  data-testid="achado"
                  className="border-l-4 border-red-500 pl-3"
                >
                  <p className="text-sm font-medium text-slate-800">
                    {a.vista && <span className="text-slate-500">{a.vista} — </span>}
                    <span title={a.tipo_defeito ?? undefined}>{descreverAchado(a)}</span>
                    {a.severidade != null && (
                      <span className="ml-2 text-slate-600">severidade {a.severidade}</span>
                    )}
                    {conf && (
                      <span data-testid="achado-confianca" className="ml-2 text-slate-600">
                        confiança {conf}
                      </span>
                    )}
                  </p>
                  {a.local && <p className="text-xs text-slate-500">{a.local}</p>}
                  {a.observacao && <p className="mt-1 text-sm text-slate-600">{a.observacao}</p>}
                </li>
              )
            })}
          </ul>
        )}
      </section>

      <BlocoValidacao id={id} detalhe={detalhe} />
    </div>
  )
}

/**
 * Uma moldura do grid. O array `vistas` já traz uma entrada por moldura a
 * desenhar — este componente NUNCA assume quatro nem inventa a ausência.
 */
function VistaCard({ vista }: { vista: ChecklistViewResponse }) {
  const conf = formatConfianca(vista.confianca)
  const lacuna = vista.esperada && !vista.recebida
  const forcaPrevisto = !vista.esperada && vista.recebida
  const [ampliada, setAmpliada] = useState(false)

  return (
    <div
      data-testid={`vista-${vista.campo}`}
      className={`flex flex-col overflow-hidden rounded-lg border bg-white ${
        vista.determinante ? 'border-slate-400 ring-2 ring-brand-secondary' : 'border-slate-200'
      }`}
    >
      <div className="flex items-center justify-between gap-2 border-b border-slate-100 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-600">
          {vista.rotulo}
        </span>
        {vista.determinante && (
          <span className="rounded bg-brand-tint px-1.5 py-0.5 text-[11px] font-medium text-brand-hover">
            determinou o veredito
          </span>
        )}
      </div>

      <div className="bg-slate-100">
        {vista.foto_url ? (
          // foto_url já vem pronta e escapada do backend — não remontar a partir de foto_path
          <button
            type="button"
            onClick={() => setAmpliada(true)}
            aria-label={`Ampliar foto — ${vista.rotulo}`}
            data-testid={`ampliar-${vista.campo}`}
            className="block w-full cursor-zoom-in"
          >
            <img
              src={vista.foto_url}
              alt={`${vista.rotulo} — checklist`}
              loading="lazy"
              decoding="async"
              className="max-h-64 w-full object-contain"
            />
          </button>
        ) : (
          <div
            data-testid={`sem-foto-${vista.campo}`}
            className="flex h-40 flex-col items-center justify-center gap-1 px-3 text-center"
          >
            <span className="text-sm font-medium text-slate-500">Sem foto</span>
            {lacuna && (
              <span className="text-xs text-slate-500">
                Esta vista era esperada e não chegou no checklist.
              </span>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3">
        {vista.indicador && vista.indicador_rotulo ? (
          <IndicadorBadge
            indicador={vista.indicador}
            rotulo={vista.indicador_rotulo}
            className="self-start"
            data-testid={`vista-indicador-${vista.campo}`}
          />
        ) : (
          <span
            data-testid={`vista-sem-laudo-${vista.campo}`}
            className="self-start rounded-full border border-dashed border-slate-400 bg-slate-50 px-2 py-0.5 text-xs text-slate-600"
          >
            Sem laudo
          </span>
        )}

        {/* Não processável mostra o motivo, nunca um erro genérico */}
        {vista.motivo_rotulo && (
          <p data-testid={`motivo-${vista.campo}`} className="text-sm text-amber-900">
            {vista.motivo_rotulo}
          </p>
        )}

        {(vista.classe || vista.tipo_defeito) && (
          <p className="text-sm text-slate-700">
            <span title={vista.tipo_defeito ?? undefined}>
              {vista.tipo_defeito_rotulo ?? vista.classe_rotulo}
            </span>
            {vista.severidade_rotulo && (
              <span className="ml-2 text-slate-600">· severidade {vista.severidade_rotulo}</span>
            )}
            {conf && <span className="ml-2 text-slate-600">· confiança {conf}</span>}
          </p>
        )}

        {vista.observacao && <p className="text-sm text-slate-600">{vista.observacao}</p>}
        {vista.local && <p className="text-xs text-slate-500">{vista.local}</p>}

        {forcaPrevisto && (
          <p className="text-xs text-slate-500">
            Foto fora do previsto para este formulário — o que chegou foi analisado assim mesmo.
          </p>
        )}

        {vista.erro && <p className="text-sm text-red-700">{vista.erro}</p>}

        {/* Julgamento humano desta vista — o gabarito é POR VISTA */}
        {vista.validacao && (
          <div
            data-testid={`validacao-vista-${vista.campo}`}
            className={`mt-auto rounded-md border p-2 text-xs ${
              vista.validacao.tipo_erro
                ? 'border-amber-300 bg-amber-50 text-amber-900'
                : 'border-green-300 bg-green-50 text-green-900'
            }`}
          >
            <p className="font-medium">
              {vista.validacao.tipo_erro
                ? `Corrigido pelo operador — ${vista.validacao.tipo_erro_rotulo}`
                : 'Confirmado pelo operador'}
            </p>
            {vista.validacao.tipo_erro && (
              <p className="mt-0.5">
                Gabarito: {vista.validacao.classe_rotulo ?? vista.validacao.classe}
                {vista.validacao.severidade_rotulo &&
                  ` · severidade ${vista.validacao.severidade_rotulo}`}
              </p>
            )}
            {vista.validacao.observacao && (
              <p className="mt-0.5 italic">“{vista.validacao.observacao}”</p>
            )}
            {vista.validacao.por && (
              <p className="mt-0.5 text-[11px] opacity-80">
                {vista.validacao.por} · {formatDateTime(vista.validacao.em)}
              </p>
            )}
          </div>
        )}
      </div>

      {ampliada && vista.foto_url && (
        <Lightbox
          src={vista.foto_url}
          alt={`${vista.rotulo} — checklist`}
          onClose={() => setAmpliada(false)}
        />
      )}
    </div>
  )
}

const VALIDACAO_LABEL: Record<string, string> = {
  pendente: 'A validar',
  confirmado: 'Confirmado',
  corrigido: 'Corrigido',
}

/**
 * Bloco de validação humana (HITL) — ticket mvp-c54-c57/10.
 *
 * **Confirmar é um clique.** É o caso comum e precisa ser barato: se validar
 * for caro, não acontece, e o F1 que o contrato exige fica sem fonte. Por isso
 * o botão não abre diálogo, não pede confirmação e não tem formulário.
 *
 * **Corrigir abre a vista específica**, porque os laudos são por vista, e
 * captura o QUÊ estava errado — "corrigido" sem tipo só serve para contar.
 * Os tipos, as classes e as severidades vêm de `opcoes_validacao` do backend;
 * o front não mantém enum próprio do domínio.
 */
function BlocoValidacao({ id, detalhe }: { id: string; detalhe: ChecklistDetailResponse }) {
  const queryClient = useQueryClient()
  const [corrigindo, setCorrigindo] = useState(false)

  const corrigiveis = detalhe.vistas.filter((v) => v.corrigivel)
  const validavel = detalhe.validavel ?? corrigiveis.length > 0

  function aoValidar() {
    queryClient.invalidateQueries({ queryKey: ['checklist', id] })
    queryClient.invalidateQueries({ queryKey: ['checklists'] })
  }

  const confirmar = useMutation({
    mutationFn: () => confirmarChecklist(id),
    onSuccess: () => {
      setCorrigindo(false)
      aoValidar()
    },
  })

  const corrigir = useMutation({
    mutationFn: (body: ChecklistCorrecaoBody) => corrigirVista(id, body),
    onSuccess: () => {
      setCorrigindo(false)
      aoValidar()
    },
  })

  const jaValidado = detalhe.validacao !== 'pendente'

  return (
    <section
      data-testid="bloco-validacao"
      data-validacao={detalhe.validacao}
      className="rounded-lg border border-slate-200 bg-white p-4"
    >
      {!validavel ? (
        <p data-testid="validacao-indisponivel" className="text-sm text-slate-600">
          Este checklist ainda não tem laudo para validar — nenhuma vista produziu veredito.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm font-medium text-slate-700">Esta avaliação está correta?</p>
            <button
              type="button"
              data-testid="botao-confirmar"
              onClick={() => confirmar.mutate()}
              disabled={confirmar.isPending || corrigir.isPending}
              className="rounded-md bg-brand-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50"
            >
              {confirmar.isPending ? 'Confirmando…' : 'Confirmar ✔'}
            </button>
            <button
              type="button"
              data-testid="botao-corrigir"
              onClick={() => setCorrigindo((aberto) => !aberto)}
              disabled={confirmar.isPending}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
            >
              Corrigir ✘
            </button>
          </div>

          <p data-testid="situacao-validacao" className="mt-2 text-xs text-slate-500">
            Situação atual: {VALIDACAO_LABEL[detalhe.validacao] ?? detalhe.validacao}
            {jaValidado && detalhe.validado_por && (
              <>
                {' '}
                por <strong className="text-slate-700">{detalhe.validado_por}</strong> em{' '}
                {formatDateTime(detalhe.validado_em)}
              </>
            )}
            .
          </p>

          {confirmar.isError && (
            <p data-testid="erro-validacao" className="mt-2 text-sm text-red-700">
              {(confirmar.error as Error).message}
            </p>
          )}

          {corrigindo && (
            <FormularioCorrecao
              vistas={corrigiveis}
              opcoes={detalhe.opcoes_validacao}
              padrao={detalhe.vista_determinante ?? corrigiveis[0]?.campo ?? ''}
              enviando={corrigir.isPending}
              erro={corrigir.isError ? (corrigir.error as Error).message : null}
              onCancelar={() => setCorrigindo(false)}
              onEnviar={(body) => corrigir.mutate(body)}
            />
          )}
        </>
      )}
    </section>
  )
}

/**
 * O formulário do mockup do ticket 09. Quatro tipos de erro; dois deles pedem
 * um complemento (a classe certa, a severidade certa) e o campo só aparece
 * quando é o caso — pedir tudo sempre encareceria o caminho comum.
 */
function FormularioCorrecao({
  vistas,
  opcoes,
  padrao,
  enviando,
  erro,
  onCancelar,
  onEnviar,
}: {
  vistas: ChecklistViewResponse[]
  opcoes: ChecklistValidationOptionsResponse
  padrao: string
  enviando: boolean
  erro: string | null
  onCancelar: () => void
  onEnviar: (body: ChecklistCorrecaoBody) => void
}) {
  const primeira = vistas.some((v) => v.campo === padrao) ? padrao : (vistas[0]?.campo ?? '')
  const [campo, setCampo] = useState(primeira)
  const [tipoErro, setTipoErro] = useState(opcoes.tipos_erro[0]?.valor ?? '')
  const [classe, setClasse] = useState(opcoes.classes[0]?.valor ?? '')
  const [severidade, setSeveridade] = useState(opcoes.severidades[0]?.valor ?? '')
  const [observacao, setObservacao] = useState('')

  const pedeClasse = tipoErro === 'classe_errada'
  const pedeSeveridade = tipoErro === 'severidade_errada'
  const rotuloVista = vistas.find((v) => v.campo === campo)?.rotulo ?? campo

  function enviar(evento: React.FormEvent) {
    evento.preventDefault()
    onEnviar({
      campo,
      tipo_erro: tipoErro,
      ...(pedeClasse ? { classe } : {}),
      ...(pedeSeveridade ? { severidade: Number(severidade) } : {}),
      ...(observacao.trim() ? { observacao: observacao.trim() } : {}),
    })
  }

  return (
    <form
      data-testid="formulario-correcao"
      onSubmit={enviar}
      className="mt-4 rounded-lg border border-slate-300 bg-slate-50 p-4"
    >
      <h3 className="text-sm font-semibold text-slate-800">Corrigir — {rotuloVista}</h3>

      <div className="mt-3 flex flex-col gap-1">
        <label htmlFor="correcao-vista" className="text-xs font-medium text-slate-700">
          Vista
        </label>
        <select
          id="correcao-vista"
          value={campo}
          onChange={(e) => setCampo(e.target.value)}
          className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
        >
          {vistas.map((v) => (
            <option key={v.campo} value={v.campo}>
              {v.rotulo}
            </option>
          ))}
        </select>
      </div>

      <fieldset className="mt-3">
        <legend className="text-xs font-medium text-slate-700">O que estava errado?</legend>
        <div className="mt-1 space-y-1">
          {opcoes.tipos_erro.map((opcao) => (
            <label
              key={opcao.valor}
              className="flex items-center gap-2 text-sm text-slate-700"
              htmlFor={`tipo-erro-${opcao.valor}`}
            >
              <input
                id={`tipo-erro-${opcao.valor}`}
                type="radio"
                name="tipo_erro"
                value={opcao.valor}
                checked={tipoErro === opcao.valor}
                onChange={() => setTipoErro(opcao.valor)}
              />
              {opcao.rotulo}
            </label>
          ))}
        </div>
      </fieldset>

      {pedeClasse && (
        <div className="mt-3 flex flex-col gap-1">
          <label htmlFor="correcao-classe" className="text-xs font-medium text-slate-700">
            Classe certa
          </label>
          <select
            id="correcao-classe"
            value={classe}
            onChange={(e) => setClasse(e.target.value)}
            className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
          >
            {opcoes.classes.map((o) => (
              <option key={o.valor} value={o.valor}>
                {o.rotulo}
              </option>
            ))}
          </select>
        </div>
      )}

      {pedeSeveridade && (
        <div className="mt-3 flex flex-col gap-1">
          <label htmlFor="correcao-severidade" className="text-xs font-medium text-slate-700">
            Severidade certa
          </label>
          <select
            id="correcao-severidade"
            value={severidade}
            onChange={(e) => setSeveridade(e.target.value)}
            className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
          >
            {opcoes.severidades.map((o) => (
              <option key={o.valor} value={o.valor}>
                {o.rotulo}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="mt-3 flex flex-col gap-1">
        <label htmlFor="correcao-observacao" className="text-xs font-medium text-slate-700">
          Observação (opcional)
        </label>
        <textarea
          id="correcao-observacao"
          value={observacao}
          onChange={(e) => setObservacao(e.target.value)}
          rows={2}
          className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
        />
      </div>

      {erro && (
        <p data-testid="erro-correcao" className="mt-2 text-sm text-red-700">
          {erro}
        </p>
      )}

      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          data-testid="salvar-correcao"
          disabled={enviando}
          className="rounded-md bg-brand-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50"
        >
          {enviando ? 'Salvando…' : 'Salvar correção'}
        </button>
        <button
          type="button"
          onClick={onCancelar}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-white"
        >
          Cancelar
        </button>
      </div>
    </form>
  )
}

function Dado({
  label,
  value,
  mono = false,
}: { label: string; value: string | null | undefined; mono?: boolean }) {
  return (
    <div>
      <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className={`text-sm text-slate-800 ${mono ? 'font-mono' : ''}`}>{value ?? '—'}</dd>
    </div>
  )
}
