import type { ChecklistListResponse } from '@/api/types'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChecklistsPage } from './ChecklistsPage'

// Payload espelhando o contrato do ticket mvp-c54-c57/09. Cobre os TRÊS
// vereditos mais `sem_analise`, que não é veredito.
const listResponse: ChecklistListResponse = {
  total: 4,
  limit: 50,
  offset: 0,
  contadores: {
    total: 4,
    nao_conformes: 1,
    nao_processaveis: 1,
    conformes: 1,
    sem_analise: 1,
    a_validar: 4,
  },
  facetas: { filiais: ['MG-CGE', 'SP-GRU'], formularios: ['F038', 'F180'] },
  itens: [
    {
      job_id: 'job-nc',
      checklist_id: '311989',
      status: 'done',
      indicador: 'nao_conforme',
      indicador_rotulo: 'Não conforme',
      severidade: 2,
      severidade_rotulo: 'Alta',
      vista_determinante: 'c54',
      vista_determinante_rotulo: 'Lateral direita',
      validacao: 'pendente',
      patrimonio: 'TECG01364',
      cliente: 'EBAZAR.COM.BR. LTDA',
      filial: 'MG-CGE',
      formulario: 'F180-VISITA GMG_REV04',
      formulario_codigo: 'F180',
      data: '2026-08-02T14:30:00Z',
      criado_em: '2026-08-02T16:00:00Z',
      n_linhas: 1,
      multi_ativo: false,
      // F180 com 3 vistas é o checklist completo — nada faltando.
      vistas_recebidas: ['c54', 'c55', 'c56'],
      vistas_esperadas: ['c54', 'c55', 'c56'],
      vistas_ausentes: [],
    },
    {
      job_id: 'job-np',
      checklist_id: '311902',
      status: 'done',
      indicador: 'nao_processavel',
      indicador_rotulo: 'Não processável',
      severidade: null,
      severidade_rotulo: null,
      vista_determinante: 'c55',
      vista_determinante_rotulo: 'Lateral esquerda',
      validacao: 'pendente',
      patrimonio: 'TECG01798',
      cliente: 'EBAZAR.COM.BR. LTDA',
      filial: 'SP-GRU',
      formulario: 'F038-CHECKLIST GMG',
      formulario_codigo: 'F038',
      data: '2026-08-02T14:30:00Z',
      criado_em: '2026-08-02T16:00:00Z',
      n_linhas: 1,
      multi_ativo: false,
      // F038 espera 4 — aqui falta de verdade.
      vistas_recebidas: ['c54', 'c55', 'c56'],
      vistas_esperadas: ['c54', 'c55', 'c56', 'c57'],
      vistas_ausentes: ['c57'],
    },
    {
      job_id: 'job-ok',
      checklist_id: '311776',
      status: 'done',
      indicador: 'conforme',
      indicador_rotulo: 'Conforme',
      severidade: null,
      severidade_rotulo: null,
      vista_determinante: null,
      vista_determinante_rotulo: null,
      validacao: 'pendente',
      patrimonio: 'TECG01103',
      cliente: 'EBAZAR.COM.BR. LTDA',
      filial: 'MG-CGE',
      formulario: 'F180-VISITA GMG_REV04',
      formulario_codigo: 'F180',
      data: '2026-08-01T10:00:00Z',
      criado_em: '2026-08-01T12:00:00Z',
      n_linhas: 1,
      multi_ativo: false,
      vistas_recebidas: ['c54', 'c55', 'c56'],
      vistas_esperadas: ['c54', 'c55', 'c56'],
      vistas_ausentes: [],
    },
    {
      job_id: 'job-sa',
      checklist_id: '311500',
      status: 'pending',
      indicador: 'sem_analise',
      indicador_rotulo: 'Sem análise',
      severidade: null,
      severidade_rotulo: null,
      vista_determinante: null,
      vista_determinante_rotulo: null,
      validacao: 'pendente',
      patrimonio: 'TECG00777',
      cliente: 'EBAZAR.COM.BR. LTDA',
      filial: 'SP-GRU',
      formulario: 'F038-CHECKLIST GMG',
      formulario_codigo: 'F038',
      data: '2026-08-01T09:00:00Z',
      criado_em: '2026-08-01T11:00:00Z',
      // Checklist que cobre dois ativos — geradores gêmeos em paralelo.
      n_linhas: 2,
      multi_ativo: true,
      vistas_recebidas: [],
      vistas_esperadas: ['c54', 'c55', 'c56', 'c57'],
      vistas_ausentes: [],
    },
  ],
}

// Primeira linha do payload, reusada nos cenários de validação.
const [primeiroItem] = listResponse.itens

function mockList(body: unknown = listResponse, status = 200): string[] {
  const calls: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((req: Request | string) => {
      const href = req instanceof Request ? req.url : req
      calls.push(href)
      return Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }),
  )
  return calls
}

function renderList(initialEntry = '/checklists') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/checklists', element: <ChecklistsPage /> },
      { path: '/checklists/:id', element: <div>detalhe</div> },
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

function lastCall(calls: string[]): string {
  return calls[calls.length - 1] ?? ''
}

const XLSX_MEDIA_TYPE = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

/**
 * Como `mockList`, mas também responde `GET .../checklists/export.xlsx` —
 * usado só pelos testes do botão "Exportar Excel", para não mudar o
 * comportamento de `mockList` que os outros 23 testes já dependem.
 */
function mockListAndExport(
  listBody: unknown = listResponse,
  exportStatus = 200,
): { url: string; init?: RequestInit }[] {
  const calls: { url: string; init?: RequestInit }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation((req: Request | string, init?: RequestInit) => {
      const url = req instanceof Request ? req.url : String(req)
      calls.push({ url, init })
      if (url.includes('/checklists/export.xlsx')) {
        const ok = exportStatus >= 200 && exportStatus < 300
        return Promise.resolve(
          new Response(ok ? new Blob(['fake-xlsx-bytes']) : JSON.stringify({ detail: 'erro' }), {
            status: exportStatus,
            headers: {
              'Content-Type': XLSX_MEDIA_TYPE,
              'Content-Disposition': 'attachment; filename="checklists-2026-08-03.xlsx"',
            },
          }),
        )
      }
      return Promise.resolve(
        new Response(JSON.stringify(listBody), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
      )
    }),
  )
  return calls
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChecklistsPage — fila de trabalho', () => {
  it('abre em "a validar" e ordenado por severidade (padrão da fila, não histórico)', async () => {
    const calls = mockList()

    renderList()

    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    const url = new URL(lastCall(calls))
    expect(url.searchParams.get('validacao')).toBe('pendente')
    expect(url.searchParams.get('ordenar')).toBe('severidade')
    expect(url.pathname).toBe('/api/v1/portal/checklists')
  })

  it('renderiza os três vereditos e o sem_analise com tratamentos distintos', async () => {
    mockList()

    renderList()

    await waitFor(() => {
      expect(screen.getByTestId('indicador-311989')).toHaveTextContent('Não conforme')
    })

    // Rótulos vêm prontos do backend — o front não traduz nada.
    expect(screen.getByTestId('indicador-311902')).toHaveTextContent('Não processável')
    expect(screen.getByTestId('indicador-311776')).toHaveTextContent('Conforme')
    expect(screen.getByTestId('indicador-311500')).toHaveTextContent('Sem análise')

    // `sem_analise` não é veredito: nunca a cor de conforme.
    const semAnalise = screen.getByTestId('indicador-311500')
    const conforme = screen.getByTestId('indicador-311776')
    expect(conforme).toHaveClass('bg-green-100')
    expect(semAnalise).not.toHaveClass('bg-green-100')
    expect(semAnalise).toHaveClass('border-dashed')
  })

  it('dá âncora visual à esquerda em cada linha, além do pill', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByTestId('anchor-311989')).toBeInTheDocument())
    expect(screen.getByTestId('anchor-311989')).toHaveClass('bg-red-600')
    expect(screen.getByTestId('anchor-311902')).toHaveClass('bg-amber-500')
    expect(screen.getByTestId('anchor-311776')).toHaveClass('bg-transparent')
    expect(screen.getByTestId('anchor-311500')).toHaveClass('bg-slate-300')
  })

  it('mostra qual vista puxou o veredito', async () => {
    mockList()

    renderList()

    await waitFor(() => {
      expect(screen.getByTestId('checklist-row-311989')).toHaveTextContent('Lateral direita')
    })
    expect(screen.getByTestId('checklist-row-311902')).toHaveTextContent('Lateral esquerda')
    // Conforme não tem vista a culpar.
    expect(screen.getByTestId('checklist-row-311776')).not.toHaveTextContent('Lateral')
  })

  it('mostra os contadores sem gráfico', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByTestId('contador-a-validar')).toHaveTextContent('4'))
    expect(screen.getByTestId('contador-nao-conformes')).toHaveTextContent('1')
    expect(screen.getByTestId('contador-nao-processaveis')).toHaveTextContent('1')
    expect(screen.getByTestId('contador-conformes')).toHaveTextContent('1')
    expect(screen.getByTestId('contador-sem-analise')).toHaveTextContent('1')
  })

  it('acende "foto faltando" só quando há ausência de verdade (F038), nunca no F180 de 3 vistas', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByTestId('ausentes-311902')).toBeInTheDocument())
    expect(screen.getByTestId('ausentes-311902')).toHaveTextContent('1 foto faltando')
    // F180 com 3 vistas está completo — não pode parecer foto faltando.
    expect(screen.queryByTestId('ausentes-311989')).not.toBeInTheDocument()
    expect(screen.queryByTestId('ausentes-311776')).not.toBeInTheDocument()
  })

  it('avisa quando o checklist cobre mais de um ativo (n_linhas > 1)', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByTestId('multi-ativo-311500')).toBeInTheDocument())
    expect(screen.getByTestId('multi-ativo-311500')).toHaveTextContent('2 ativos')
    expect(screen.queryByTestId('multi-ativo-311989')).not.toBeInTheDocument()
  })

  it('mostra a filial na tabela (desktop) e no cartão (mobile), na mesma linha do item', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByTestId('filial-311989')).toHaveTextContent('MG-CGE'))
    expect(screen.getByTestId('filial-311902')).toHaveTextContent('SP-GRU')

    // Cartão do mobile — o portal é usado principalmente em celular e a
    // tabela some lá, então a filial precisa estar no cartão também.
    expect(screen.getByTestId('filial-card-311989')).toHaveTextContent('MG-CGE')
    expect(screen.getByTestId('filial-card-311902')).toHaveTextContent('SP-GRU')
  })

  it('esconde o seletor de Formulário quando as facetas trazem uma opção só', async () => {
    mockList({ ...listResponse, facetas: { ...listResponse.facetas, formularios: ['F038'] } })

    renderList()

    await waitFor(() => expect(screen.getByTestId('filial-311989')).toBeInTheDocument())
    expect(screen.queryByLabelText('Formulário')).not.toBeInTheDocument()
  })

  it('mostra o seletor de Formulário quando as facetas trazem mais de uma opção', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByLabelText('Formulário')).toBeInTheDocument())
    expect(screen.getByRole('option', { name: 'F038' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'F180' })).toBeInTheDocument()
  })

  it('filtra por indicador', async () => {
    const calls = mockList()

    renderList()

    await waitFor(() => expect(screen.getByLabelText('Indicador')).toBeInTheDocument())
    await userEvent.selectOptions(screen.getByLabelText('Indicador'), 'nao_conforme')

    await waitFor(() => {
      expect(new URL(lastCall(calls)).searchParams.get('indicador')).toBe('nao_conforme')
    })
  })

  it('filtra por filial usando as facetas devolvidas pelo backend', async () => {
    const calls = mockList()

    renderList()

    // As opções vêm das facetas do backend — o front não monta lista fixa.
    await waitFor(() => expect(screen.getByRole('option', { name: 'MG-CGE' })).toBeInTheDocument())
    expect(screen.getByRole('option', { name: 'SP-GRU' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'F180' })).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText('Filial'), 'MG-CGE')

    await waitFor(() => {
      expect(new URL(lastCall(calls)).searchParams.get('filial')).toBe('MG-CGE')
    })
  })

  it('filtra por ID de checklist e por período', async () => {
    const calls = mockList()

    renderList()

    await waitFor(() => expect(screen.getByLabelText('ID checklist')).toBeInTheDocument())
    await userEvent.type(screen.getByLabelText('ID checklist'), '311989')

    await waitFor(() => {
      expect(new URL(lastCall(calls)).searchParams.get('codigo_checklist')).toBe('311989')
    })
  })

  it('"Todas" na validação remove o filtro em vez de mandar valor inválido', async () => {
    const calls = mockList()

    renderList()

    await waitFor(() => expect(screen.getByLabelText('Validação')).toBeInTheDocument())
    await userEvent.selectOptions(screen.getByLabelText('Validação'), 'todas')

    await waitFor(() => {
      expect(new URL(lastCall(calls)).searchParams.has('validacao')).toBe(false)
    })
  })

  it('troca a ordenação para mais recente', async () => {
    const calls = mockList()

    renderList()

    await waitFor(() => expect(screen.getByLabelText('Ordenar')).toBeInTheDocument())
    await userEvent.selectOptions(screen.getByLabelText('Ordenar'), 'recente')

    await waitFor(() => {
      expect(new URL(lastCall(calls)).searchParams.get('ordenar')).toBe('recente')
    })
  })

  it('respeita os filtros que já vêm na URL', async () => {
    const calls = mockList()

    renderList('/checklists?indicador=conforme&filial=SP-GRU&data_de=2026-08-01')

    await waitFor(() => expect(calls.length).toBeGreaterThan(0))
    const params = new URL(lastCall(calls)).searchParams
    expect(params.get('indicador')).toBe('conforme')
    expect(params.get('filial')).toBe('SP-GRU')
    expect(params.get('data_de')).toBe('2026-08-01')
  })

  it('leva ao relatório pelo job_id da execução', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByRole('link', { name: '311989' })).toBeInTheDocument())
    expect(screen.getByRole('link', { name: '311989' })).toHaveAttribute(
      'href',
      '/checklists/job-nc',
    )
  })

  it('mostra estado vazio quando não há checklists na seleção', async () => {
    mockList({ ...listResponse, total: 0, itens: [] })

    renderList()

    await waitFor(() => {
      expect(screen.getByText(/nenhum checklist nesta seleção/i)).toBeInTheDocument()
    })
  })

  // ── validação humana (ticket mvp-c54-c57/10) ───────────────────────────────
  // A lista passou a devolver dados em `confirmado`/`corrigido` — antes do
  // ticket 10 os dois filtros voltavam sempre vazios, e a tela dizia isso.

  it('lista os checklists já confirmados quando o filtro pede', async () => {
    const urls = mockList({
      ...listResponse,
      total: 1,
      contadores: { ...listResponse.contadores, a_validar: 3 },
      itens: [{ ...primeiroItem, validacao: 'confirmado' }],
    })

    renderList('/checklists?validacao=confirmado')

    await waitFor(() => expect(screen.getByTestId('checklist-row-311989')).toBeInTheDocument())
    expect(urls[0]).toContain('validacao=confirmado')
    expect(screen.getByTestId('checklist-row-311989')).toHaveTextContent('Confirmado')
    // o contador de trabalho não é o total da seleção
    expect(screen.getByTestId('contador-a-validar')).toHaveTextContent('3')
  })

  it('lista os corrigidos e mostra o estado na linha', async () => {
    mockList({
      ...listResponse,
      total: 1,
      itens: [{ ...primeiroItem, validacao: 'corrigido' }],
    })

    renderList('/checklists?validacao=corrigido')

    await waitFor(() => expect(screen.getByTestId('checklist-row-311989')).toBeInTheDocument())
    expect(screen.getByTestId('checklist-row-311989')).toHaveTextContent('Corrigido')
  })

  it('estado vazio de "confirmado" não culpa uma funcionalidade que já existe', async () => {
    mockList({ ...listResponse, total: 0, itens: [] })

    renderList('/checklists?validacao=confirmado')

    await waitFor(() => {
      expect(screen.getByText(/nenhum checklist foi confirmado nesta seleção/i)).toBeInTheDocument()
    })
    expect(screen.queryByText(/ainda não está disponível/i)).not.toBeInTheDocument()
  })

  it('estado vazio de "a validar" fala de fila vazia, não de ausência de recurso', async () => {
    mockList({ ...listResponse, total: 0, itens: [] })

    renderList('/checklists?validacao=pendente')

    await waitFor(() => {
      expect(screen.getByText(/nada a validar por aqui/i)).toBeInTheDocument()
    })
  })

  it('mostra erro com botão de tentar novamente', async () => {
    mockList({ detail: 'boom' }, 500)

    renderList()

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /tentar novamente/i })).toBeInTheDocument()
    })
  })
})

// ── exportar Excel (ticket v1-entregavel/06) ─────────────────────────────────
describe('ChecklistsPage — exportar Excel', () => {
  let createObjectURL: ReturnType<typeof vi.fn>
  let revokeObjectURL: ReturnType<typeof vi.fn>
  let clickSpy: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    createObjectURL = vi.fn(() => 'blob:mock-url')
    revokeObjectURL = vi.fn()
    URL.createObjectURL = createObjectURL
    URL.revokeObjectURL = revokeObjectURL
    // jsdom não navega de verdade; sem isso o clique tenta seguir o `href`
    // do blob e loga "Not implemented: navigation".
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  })

  afterEach(() => {
    clickSpy.mockRestore()
  })

  it('mostra o botão perto dos contadores', async () => {
    mockList()

    renderList()

    await waitFor(() => expect(screen.getByTestId('contador-a-validar')).toBeInTheDocument())
    expect(screen.getByTestId('botao-exportar-excel')).toHaveTextContent('Exportar Excel')
  })

  it('baixa por fetch com credentials:"include" → Blob → âncora, sem limit/offset', async () => {
    const calls = mockListAndExport()

    renderList()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-excel')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-exportar-excel'))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1))

    const chamadaExport = calls.find((c) => c.url.includes('/checklists/export.xlsx'))
    expect(chamadaExport).toBeDefined()
    expect(chamadaExport?.init?.credentials).toBe('include')

    const url = new URL(chamadaExport?.url ?? '', 'http://localhost')
    expect(url.searchParams.has('limit')).toBe(false)
    expect(url.searchParams.has('offset')).toBe(false)
    expect(url.searchParams.get('ordenar')).toBe('severidade')

    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    expect(clickSpy).toHaveBeenCalledTimes(1)
  })

  it('manda os filtros ativos da URL para o export — exportar "isto que estou vendo"', async () => {
    const calls = mockListAndExport()

    renderList('/checklists?indicador=nao_conforme&filial=MG-CGE&data_de=2026-08-01')

    await waitFor(() => expect(screen.getByTestId('botao-exportar-excel')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-exportar-excel'))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1))

    const chamadaExport = calls.find((c) => c.url.includes('/checklists/export.xlsx'))
    const url = new URL(chamadaExport?.url ?? '', 'http://localhost')
    expect(url.searchParams.get('indicador')).toBe('nao_conforme')
    expect(url.searchParams.get('filial')).toBe('MG-CGE')
    expect(url.searchParams.get('data_de')).toBe('2026-08-01')
  })

  it('mostra estado de carregando durante a exportação', async () => {
    let liberar: () => void = () => {}
    const travada = new Promise<void>((resolve) => {
      liberar = resolve
    })
    mockList()
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((req: Request | string) => {
        const url = req instanceof Request ? req.url : String(req)
        if (url.includes('/checklists/export.xlsx')) {
          return travada.then(
            () =>
              new Response(new Blob(['x']), {
                status: 200,
                headers: { 'Content-Type': XLSX_MEDIA_TYPE },
              }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify(listResponse), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    renderList()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-excel')).toBeInTheDocument())
    const botao = screen.getByTestId('botao-exportar-excel')
    await userEvent.click(botao)

    await waitFor(() => expect(botao).toHaveTextContent('Exportando…'))
    expect(botao).toBeDisabled()

    liberar()
    await waitFor(() => expect(botao).toHaveTextContent('Exportar Excel'))
    expect(botao).not.toBeDisabled()
  })

  it('mostra erro visível quando a exportação falha, sem travar o botão', async () => {
    mockListAndExport(listResponse, 500)

    renderList()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-excel')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-exportar-excel'))

    await waitFor(() => {
      expect(screen.getByTestId('erro-exportar-excel')).toHaveTextContent(/não foi possível/i)
    })
    expect(screen.getByTestId('botao-exportar-excel')).not.toBeDisabled()
    expect(createObjectURL).not.toHaveBeenCalled()
  })
})
