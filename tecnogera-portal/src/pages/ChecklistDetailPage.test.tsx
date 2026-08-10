import type {
  ChecklistDetailResponse,
  ChecklistValidationOptionsResponse,
  ChecklistViewResponse,
} from '@/api/types'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ChecklistDetailPage } from './ChecklistDetailPage'

function vista(over: Partial<ChecklistViewResponse> & { campo: string; rotulo: string }) {
  return {
    esperada: true,
    recebida: true,
    status: 'analisada',
    indicador: 'conforme',
    indicador_rotulo: 'Conforme',
    motivo_nao_processavel: null,
    motivo_rotulo: null,
    classe: null,
    tipo_defeito: null,
    severidade: null,
    severidade_rotulo: null,
    confianca: null,
    observacao: null,
    local: null,
    conteudo_observado: 'Gerador em pátio de obra, vista de conjunto.',
    vista_confere: true,
    foto_path: `/Sisloc/MG-CGE/311989 01/${over.campo} foto 01.jpg`,
    foto_url: `/api/v1/portal/avarias/image?path=%2FSisloc%2FMG-CGE%2F311989%2001%2F${over.campo}%20foto%2001.jpg`,
    achados: [],
    erro: null,
    determinante: false,
    corrigivel: true,
    validacao: null,
    ...over,
  } satisfies ChecklistViewResponse
}

// As opções do formulário de correção vêm do backend — o front não mantém
// enum próprio do vocabulário do domínio (ticket 10).
const OPCOES: ChecklistValidationOptionsResponse = {
  tipos_erro: [
    { valor: 'falso_positivo', rotulo: 'Falso positivo — não há defeito aqui' },
    { valor: 'classe_errada', rotulo: 'Classe errada' },
    { valor: 'severidade_errada', rotulo: 'Severidade errada' },
    { valor: 'nao_julgavel', rotulo: 'Foto não era julgável' },
  ],
  classes: [
    { valor: 'ausencia_item', rotulo: 'Ausência de item' },
    { valor: 'fora_padrao_visual', rotulo: 'Fora do padrão visual' },
    { valor: 'dano_visivel', rotulo: 'Dano visível' },
  ],
  severidades: [
    { valor: '1', rotulo: 'Crítica' },
    { valor: '2', rotulo: 'Alta' },
    { valor: '3', rotulo: 'Média' },
    { valor: '4', rotulo: 'Baixa' },
  ],
}

// F180 não conforme — grid de TRÊS molduras, que é o checklist completo.
const f180NaoConforme: ChecklistDetailResponse = {
  job_id: '94aaf94e-783e-4499-9f83-97935e266456',
  checklist_id: '311989',
  status: 'done',
  indicador: 'nao_conforme',
  indicador_rotulo: 'Não conforme',
  severidade: 2,
  severidade_rotulo: 'Alta',
  confianca: 0.87,
  vista_determinante: 'c54',
  vista_determinante_rotulo: 'Lateral direita',
  validacao: 'pendente',
  validado_por: null,
  validado_em: null,
  validavel: true,
  opcoes_validacao: OPCOES,
  criado_em: '2026-08-02T16:00:00Z',
  iniciado_em: null,
  finalizado_em: null,
  erro: null,
  equipamento: {
    codigo_checklist: '311989',
    patrimonio: 'TECG01364',
    cliente: 'EBAZAR.COM.BR. LTDA',
    contrato: '035514',
    projeto_bruto: '035514/2026-EBAZAR.COM.BR. LTDA',
    projeto_padrao_reconhecido: true,
    filial: 'MG-CGE',
    formulario: 'F180-VISITA GMG_REV04',
    formulario_codigo: 'F180',
    data_conclusao: '2026-08-02T14:30:00Z',
    responsavel: 'MATHEUS.PARAISO',
    numero_om: 36729,
    origem: 'OM',
    status_sisloc: 'Concluído',
    n_linhas: 1,
    multi_ativo: false,
    aviso: null,
    lido_em: '2026-08-02T15:00:00Z',
  },
  vistas: [
    vista({
      campo: 'c54',
      rotulo: 'Lateral direita',
      indicador: 'nao_conforme',
      indicador_rotulo: 'Não conforme',
      classe: 'dano_visivel',
      classe_rotulo: 'Dano visível',
      tipo_defeito: 'amassado_deformacao',
      tipo_defeito_rotulo: 'Amassado / deformação',
      severidade: 2,
      severidade_rotulo: 'Alta',
      confianca: 0.87,
      observacao: 'Amassado visível na chapa inferior, cerca de 30 cm, com tinta lascada.',
      local: 'quadrante inferior direito, chapa da lateral',
      determinante: true,
      achados: [
        {
          classe: 'dano_visivel',
          classe_rotulo: 'Dano visível',
          tipo_defeito: 'amassado_deformacao',
          tipo_defeito_rotulo: 'Amassado / deformação',
          severidade: 2,
          local: 'quadrante inferior direito, chapa da lateral',
          observacao: 'Amassado visível na chapa inferior, cerca de 30 cm, com tinta lascada.',
          confianca: 0.87,
        },
      ],
    }),
    vista({ campo: 'c55', rotulo: 'Lateral esquerda' }),
    vista({ campo: 'c56', rotulo: 'Frontal (painel)' }),
  ],
  vistas_esperadas: ['c54', 'c55', 'c56'],
  vistas_recebidas: ['c54', 'c55', 'c56'],
  vistas_ausentes: [],
  nota_vistas:
    'O formulário F180 não inclui a foto traseira (c57) desde setembro/2025 — três vistas é o checklist completo.',
  achados: [
    {
      classe: 'dano_visivel',
      classe_rotulo: 'Dano visível',
      tipo_defeito: 'amassado_deformacao',
      tipo_defeito_rotulo: 'Amassado / deformação',
      severidade: 2,
      local: 'quadrante inferior direito, chapa da lateral',
      observacao: 'Amassado visível na chapa inferior, cerca de 30 cm, com tinta lascada.',
      confianca: 0.87,
      campo: 'c54',
      vista: 'Lateral direita',
    },
  ],
  custo_usd: 0.0061,
  chamadas_llm: 3,
}

// F038 não processável — QUATRO molduras esperadas e a c57 não chegou.
const f038Ausente: ChecklistDetailResponse = {
  ...f180NaoConforme,
  job_id: 'job-f038',
  checklist_id: '311902',
  indicador: 'nao_processavel',
  indicador_rotulo: 'Não processável',
  severidade: null,
  severidade_rotulo: null,
  confianca: null,
  vista_determinante: 'c55',
  vista_determinante_rotulo: 'Lateral esquerda',
  equipamento: {
    ...f180NaoConforme.equipamento,
    codigo_checklist: '311902',
    patrimonio: 'TECG01798',
    formulario: 'F038-CHECKLIST GMG',
    formulario_codigo: 'F038',
  },
  vistas: [
    vista({ campo: 'c54', rotulo: 'Lateral direita' }),
    vista({
      campo: 'c55',
      rotulo: 'Lateral esquerda',
      indicador: 'nao_processavel',
      indicador_rotulo: 'Não processável',
      motivo_nao_processavel: 'contraluz',
      motivo_rotulo: 'Contraluz / superexposição',
      determinante: true,
    }),
    vista({ campo: 'c56', rotulo: 'Frontal (painel)' }),
    vista({
      campo: 'c57',
      rotulo: 'Traseira',
      recebida: false,
      status: null,
      indicador: null,
      indicador_rotulo: null,
      foto_path: null,
      foto_url: null,
    }),
  ],
  vistas_esperadas: ['c54', 'c55', 'c56', 'c57'],
  vistas_recebidas: ['c54', 'c55', 'c56'],
  vistas_ausentes: ['c57'],
  nota_vistas: null,
  achados: [],
}

// Job criado e ainda não processado — ausência de veredito, não veredito limpo.
const semAnalise: ChecklistDetailResponse = {
  ...f180NaoConforme,
  job_id: 'job-sa',
  checklist_id: '311500',
  status: 'pending',
  indicador: 'sem_analise',
  indicador_rotulo: 'Sem análise',
  severidade: null,
  severidade_rotulo: null,
  confianca: null,
  vista_determinante: null,
  vista_determinante_rotulo: null,
  vistas: [
    vista({
      campo: 'c54',
      rotulo: 'Lateral direita',
      indicador: null,
      indicador_rotulo: null,
      foto_path: null,
      foto_url: null,
      recebida: false,
    }),
  ],
  vistas_esperadas: ['c54'],
  vistas_recebidas: [],
  vistas_ausentes: ['c54'],
  nota_vistas: null,
  achados: [],
}

// Checklist que cobre dois ativos: o laudo é de um deles.
const multiAtivo: ChecklistDetailResponse = {
  ...f180NaoConforme,
  job_id: 'job-multi',
  checklist_id: '312001',
  equipamento: {
    ...f180NaoConforme.equipamento,
    codigo_checklist: '312001',
    n_linhas: 2,
    multi_ativo: true,
    aviso:
      'Este checklist cobre 2 ativos no Sisloc; o laudo abaixo é do patrimônio TECG01364 (primeiro por ordem).',
  },
}

function mockDetail(body: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(() =>
      Promise.resolve(
        new Response(JSON.stringify(body), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    ),
  )
}

function renderDetail(id = '94aaf94e-783e-4499-9f83-97935e266456') {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/checklists/:id', element: <ChecklistDetailPage /> },
      { path: '/checklists', element: <div>lista</div> },
    ],
    { initialEntries: [`/checklists/${id}`] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ChecklistDetailPage — relatório', () => {
  it('mostra o skeleton enquanto busca', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => new Promise<Response>(() => {})),
    )

    renderDetail()

    expect(screen.getByTestId('checklist-detail-skeleton')).toBeInTheDocument()
  })

  it('mostra o veredito e qual vista o determinou', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => {
      expect(screen.getByTestId('banner-veredito')).toHaveTextContent('Não conforme')
    })
    const banner = screen.getByTestId('banner-veredito')
    expect(banner).toHaveAttribute('data-indicador', 'nao_conforme')
    expect(banner).toHaveTextContent('Alta')
    expect(banner).toHaveTextContent('87%')
    expect(banner).toHaveTextContent('Determinado pela vista')
    expect(banner).toHaveTextContent('Lateral direita')
  })

  it('mostra o bloco de equipamento vindo do Sisloc', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getAllByText('TECG01364').length).toBeGreaterThan(0))
    expect(screen.getByText('EBAZAR.COM.BR. LTDA')).toBeInTheDocument()
    expect(screen.getByText('035514')).toBeInTheDocument()
    expect(screen.getAllByText('MG-CGE').length).toBeGreaterThan(0)
    expect(screen.getAllByText('F180-VISITA GMG_REV04').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/MATHEUS\.PARAISO/).length).toBeGreaterThan(0)
    expect(screen.getByText('36729')).toBeInTheDocument()
  })

  it('F180 com 3 vistas: desenha 3 molduras, mostra a nota e NÃO sugere foto faltando', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('vista-c54')).toBeInTheDocument())
    expect(screen.getByTestId('vista-c55')).toBeInTheDocument()
    expect(screen.getByTestId('vista-c56')).toBeInTheDocument()
    expect(screen.queryByTestId('vista-c57')).not.toBeInTheDocument()

    expect(screen.getByTestId('nota-vistas')).toHaveTextContent(
      /três vistas é o checklist completo/i,
    )
    expect(screen.queryByTestId('vistas-ausentes')).not.toBeInTheDocument()
    expect(screen.queryByText(/sem foto/i)).not.toBeInTheDocument()
  })

  it('F038 com 3 de 4 vistas: desenha a moldura vazia e avisa a ausência real', async () => {
    mockDetail(f038Ausente)

    renderDetail('job-f038')

    await waitFor(() => expect(screen.getByTestId('vista-c57')).toBeInTheDocument())
    expect(screen.getByTestId('vista-c54')).toBeInTheDocument()
    expect(screen.getByTestId('vista-c55')).toBeInTheDocument()
    expect(screen.getByTestId('vista-c56')).toBeInTheDocument()

    expect(screen.getByTestId('sem-foto-c57')).toHaveTextContent(/não chegou/i)
    expect(screen.getByTestId('vistas-ausentes')).toBeInTheDocument()
    // Sem nota: aqui faltou mesmo, não é formulário de 3 vistas.
    expect(screen.queryByTestId('nota-vistas')).not.toBeInTheDocument()
  })

  it('vista não processável mostra o motivo no card, não um erro genérico', async () => {
    mockDetail(f038Ausente)

    renderDetail('job-f038')

    await waitFor(() => expect(screen.getByTestId('motivo-c55')).toBeInTheDocument())
    expect(screen.getByTestId('motivo-c55')).toHaveTextContent('Contraluz / superexposição')
    expect(screen.getByTestId('vista-indicador-c55')).toHaveTextContent('Não processável')
  })

  it('usa foto_url do backend sem remontar a partir de foto_path', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByAltText(/Lateral direita/)).toBeInTheDocument())
    const img = screen.getByAltText(/Lateral direita/)
    expect(img).toHaveAttribute('src', f180NaoConforme.vistas[0]?.foto_url ?? '')
    expect(img).toHaveAttribute('loading', 'lazy')
  })

  it('clicar na foto abre a versão ampliada, com a mesma URL do backend', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByAltText(/Lateral direita/)).toBeInTheDocument())
    expect(screen.queryByTestId('lightbox')).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('ampliar-c54'))

    const ampliada = await screen.findByTestId('lightbox-img')
    // A ampliada usa a MESMA foto_url do card — nada é remontado a partir de foto_path,
    // senão a rota do proxy autenticado quebraria com espaço e acento no caminho.
    expect(ampliada).toHaveAttribute('src', f180NaoConforme.vistas[0]?.foto_url ?? '')
  })

  it('fecha a foto ampliada com Esc e pelo botão', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('ampliar-c54')).toBeInTheDocument())

    await userEvent.click(screen.getByTestId('ampliar-c54'))
    await screen.findByTestId('lightbox')
    await userEvent.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByTestId('lightbox')).not.toBeInTheDocument())

    await userEvent.click(screen.getByTestId('ampliar-c54'))
    await screen.findByTestId('lightbox')
    await userEvent.click(screen.getByRole('button', { name: 'Fechar' }))
    await waitFor(() => expect(screen.queryByTestId('lightbox')).not.toBeInTheDocument())
  })

  it('vista sem foto não oferece ampliação', async () => {
    mockDetail(f038Ausente)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('sem-foto-c57')).toBeInTheDocument())
    expect(screen.queryByTestId('ampliar-c57')).not.toBeInTheDocument()
  })

  it('lista os achados com a confiança visível', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getAllByTestId('achado').length).toBe(1))
    const achado = screen.getAllByTestId('achado')[0]
    expect(achado).toHaveTextContent('Lateral direita')
    expect(achado).toHaveTextContent('Amassado / deformação')
    expect(achado).toHaveTextContent('confiança 87%')
    expect(achado).toHaveTextContent(/Amassado visível na chapa inferior/)
  })

  it('sem_analise não é veredito: não recebe a cor de conforme e explica que o job não rodou', async () => {
    mockDetail(semAnalise)

    renderDetail('job-sa')

    await waitFor(() => {
      expect(screen.getByTestId('banner-veredito')).toHaveTextContent('Sem análise')
    })
    const badge = screen.getByTestId('banner-veredito').querySelector('[data-indicador]')
    expect(badge).not.toHaveClass('bg-green-100')
    expect(screen.getByTestId('banner-veredito')).toHaveTextContent(/ainda não produziu laudo/i)
    expect(screen.getByTestId('vista-sem-laudo-c54')).toHaveTextContent('Sem laudo')
  })

  it('avisa quando o checklist cobre mais de um ativo (n_linhas > 1)', async () => {
    mockDetail(multiAtivo)

    renderDetail('job-multi')

    await waitFor(() => expect(screen.getByTestId('aviso-multi-ativo')).toBeInTheDocument())
    expect(screen.getByTestId('aviso-multi-ativo')).toHaveTextContent(/cobre 2 ativos/i)
    expect(screen.getByTestId('aviso-multi-ativo')).toHaveTextContent('TECG01364')
  })

  it('não mostra aviso de multi-ativo quando o checklist é de um só ativo', async () => {
    mockDetail(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('banner-veredito')).toBeInTheDocument())
    expect(screen.queryByTestId('aviso-multi-ativo')).not.toBeInTheDocument()
  })

  it('mostra erro quando o checklist não existe', async () => {
    mockDetail({ detail: 'não encontrado' }, 404)

    renderDetail('inexistente')

    await waitFor(() => {
      expect(screen.getByText(/checklist não encontrado/i)).toBeInTheDocument()
    })
  })
})

/**
 * Exportar PDF — ticket v1-entregavel/05.
 *
 * Mesmo padrão de `exportarChecklistsExcel` (`ChecklistsPage.test.tsx`,
 * ticket 06): `fetch` cru roteado por URL — `.../pdf` devolve um Blob com
 * `Content-Disposition`, qualquer outra chamada devolve o JSON do detalhe.
 */
describe('ChecklistDetailPage — exportar PDF', () => {
  const PDF_MEDIA_TYPE = 'application/pdf'

  function mockDetailAndPdf(
    detalhe: ChecklistDetailResponse,
    pdfStatus = 200,
  ): { url: string; init?: RequestInit }[] {
    const calls: { url: string; init?: RequestInit }[] = []
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((req: Request | string, init?: RequestInit) => {
        const url = req instanceof Request ? req.url : String(req)
        calls.push({ url, init: init ?? (req instanceof Request ? undefined : init) })
        if (url.includes('/pdf')) {
          const ok = pdfStatus >= 200 && pdfStatus < 300
          return Promise.resolve(
            new Response(ok ? new Blob(['fake-pdf-bytes']) : JSON.stringify({ detail: 'erro' }), {
              status: pdfStatus,
              headers: {
                'Content-Type': PDF_MEDIA_TYPE,
                'Content-Disposition':
                  'attachment; filename="Laudo_TECG01364_2026-08-02_ckl311989.pdf"',
              },
            }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify(detalhe), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )
    return calls
  }

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

  it('mostra o botão perto do banner de veredito', async () => {
    mockDetailAndPdf(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('banner-veredito')).toBeInTheDocument())
    expect(screen.getByTestId('botao-exportar-pdf')).toHaveTextContent('Exportar PDF')
  })

  it('baixa por fetch com credentials:"include" → Blob → âncora', async () => {
    const calls = mockDetailAndPdf(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-pdf')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-exportar-pdf'))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1))

    const chamadaPdf = calls.find((c) => c.url.includes('/pdf'))
    expect(chamadaPdf).toBeDefined()
    expect(chamadaPdf?.url).toContain('/api/v1/portal/checklists/')
    expect(chamadaPdf?.init?.credentials).toBe('include')

    expect(revokeObjectURL).toHaveBeenCalledWith('blob:mock-url')
    expect(clickSpy).toHaveBeenCalledTimes(1)
  })

  it('usa o nome de arquivo do Content-Disposition na âncora de download', async () => {
    mockDetailAndPdf(f180NaoConforme)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-pdf')).toBeInTheDocument())

    let downloadCapturado = ''
    const setAttrSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'download', 'set')
      .mockImplementation(function (this: HTMLAnchorElement, valor: string) {
        downloadCapturado = valor
      })

    await userEvent.click(screen.getByTestId('botao-exportar-pdf'))

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1))
    expect(downloadCapturado).toBe('Laudo_TECG01364_2026-08-02_ckl311989.pdf')
    setAttrSpy.mockRestore()
  })

  it('mostra estado de carregando enquanto o servidor baixa as fotos', async () => {
    let liberar: () => void = () => {}
    const travada = new Promise<void>((resolve) => {
      liberar = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((req: Request | string) => {
        const url = req instanceof Request ? req.url : String(req)
        if (url.includes('/pdf')) {
          return travada.then(
            () =>
              new Response(new Blob(['x']), {
                status: 200,
                headers: { 'Content-Type': PDF_MEDIA_TYPE },
              }),
          )
        }
        return Promise.resolve(
          new Response(JSON.stringify(f180NaoConforme), {
            status: 200,
            headers: { 'Content-Type': 'application/json' },
          }),
        )
      }),
    )

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-pdf')).toBeInTheDocument())
    const botao = screen.getByTestId('botao-exportar-pdf')
    await userEvent.click(botao)

    await waitFor(() => expect(botao).toHaveTextContent('Gerando PDF…'))
    expect(botao).toBeDisabled()

    liberar()
    await waitFor(() => expect(botao).toHaveTextContent('Exportar PDF'))
    expect(botao).not.toBeDisabled()
  })

  it('mostra erro visível quando a exportação falha, sem travar o botão', async () => {
    mockDetailAndPdf(f180NaoConforme, 500)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-pdf')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-exportar-pdf'))

    await waitFor(() => {
      expect(screen.getByTestId('erro-exportar-pdf')).toHaveTextContent(/não foi possível/i)
    })
    expect(screen.getByTestId('botao-exportar-pdf')).not.toBeDisabled()
    expect(createObjectURL).not.toHaveBeenCalled()
  })

  it('mensagem específica quando o checklist ainda não tem laudo processado (409)', async () => {
    mockDetailAndPdf(f180NaoConforme, 409)

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('botao-exportar-pdf')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-exportar-pdf'))

    await waitFor(() => {
      expect(screen.getByTestId('erro-exportar-pdf')).toHaveTextContent(
        /ainda não tem laudo processado/i,
      )
    })
  })
})

/**
 * HITL — ticket mvp-c54-c57/10.
 *
 * O que estes testes protegem: confirmar é UM clique (sem diálogo, sem
 * formulário), corrigir captura o TIPO do erro, e os dois invalidam as queries
 * `['checklist', id]` e `['checklists']` — senão a lista continua mostrando o
 * checklist na fila depois de ele sair dela.
 */
describe('ChecklistDetailPage — validação humana', () => {
  /** Roteia por método: GET devolve o detalhe, POST devolve a resposta da validação. */
  function mockValidacao(
    detalhe: ChecklistDetailResponse,
    resposta: { status?: number; body?: unknown } = {},
  ) {
    const posts: { url: string; body: unknown }[] = []
    vi.stubGlobal(
      'fetch',
      // `openapi-fetch` chama `fetch(new Request(...))`: o corpo vive no Request,
      // não em `init`. Ler de `init` devolveria null em toda mutação.
      vi
        .fn()
        .mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
          const requisicao = input instanceof Request ? input : null
          const url = typeof input === 'string' ? input : (requisicao?.url ?? String(input))
          const metodo = (init?.method ?? requisicao?.method ?? 'GET').toUpperCase()
          if (metodo === 'POST') {
            const cru = init?.body
              ? String(init.body)
              : requisicao
                ? await requisicao.clone().text()
                : ''
            posts.push({ url, body: cru ? JSON.parse(cru) : null })
            return Promise.resolve(
              new Response(
                JSON.stringify(
                  resposta.body ?? {
                    job_id: detalhe.job_id,
                    checklist_id: detalhe.checklist_id,
                    validacao: 'confirmado',
                    validado_por: 'operador@tecnogera.com',
                    validado_em: '2026-08-03T10:00:00Z',
                    vistas_validadas: 3,
                    vistas_validaveis: 3,
                    vistas_corrigidas: 0,
                  },
                ),
                {
                  status: resposta.status ?? 200,
                  headers: { 'Content-Type': 'application/json' },
                },
              ),
            )
          }
          return Promise.resolve(
            new Response(JSON.stringify(detalhe), {
              status: 200,
              headers: { 'Content-Type': 'application/json' },
            }),
          )
        }),
    )
    return posts
  }

  it('confirmar é um clique — sem diálogo nem formulário', async () => {
    const posts = mockValidacao(f180NaoConforme)

    renderDetail()
    await waitFor(() => expect(screen.getByTestId('bloco-validacao')).toBeInTheDocument())

    await userEvent.click(screen.getByTestId('botao-confirmar'))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]?.url).toContain('/confirmar')
    expect(posts[0]?.body).toBeNull()
  })

  it('confirmar invalida a query do detalhe e a da lista', async () => {
    mockValidacao(f180NaoConforme)
    const invalidadas: unknown[] = []

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    })
    const original = queryClient.invalidateQueries.bind(queryClient)
    queryClient.invalidateQueries = ((filtros: { queryKey?: unknown }) => {
      invalidadas.push(filtros?.queryKey)
      return original(filtros as never)
    }) as typeof queryClient.invalidateQueries

    const router = createMemoryRouter(
      [{ path: '/checklists/:id', element: <ChecklistDetailPage /> }],
      { initialEntries: ['/checklists/311989'] },
    )
    render(
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    )

    await waitFor(() => expect(screen.getByTestId('botao-confirmar')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-confirmar'))

    await waitFor(() => expect(invalidadas).toContainEqual(['checklist', '311989']))
    expect(invalidadas).toContainEqual(['checklists'])
  })

  it('mostra o erro do backend quando confirmar falha', async () => {
    mockValidacao(f180NaoConforme, {
      status: 422,
      body: { detail: 'Este checklist não tem laudo para validar' },
    })

    renderDetail()
    await waitFor(() => expect(screen.getByTestId('botao-confirmar')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-confirmar'))

    await waitFor(() =>
      expect(screen.getByTestId('erro-validacao')).toHaveTextContent(/não tem laudo/i),
    )
  })

  it('corrigir abre o formulário com os quatro tipos de erro do backend', async () => {
    mockValidacao(f180NaoConforme)

    renderDetail()
    await waitFor(() => expect(screen.getByTestId('botao-corrigir')).toBeInTheDocument())
    expect(screen.queryByTestId('formulario-correcao')).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId('botao-corrigir'))

    const form = screen.getByTestId('formulario-correcao')
    expect(form).toHaveTextContent('Falso positivo — não há defeito aqui')
    expect(form).toHaveTextContent('Classe errada')
    expect(form).toHaveTextContent('Severidade errada')
    expect(form).toHaveTextContent('Foto não era julgável')
    // abre já na vista que determinou o veredito
    expect(form).toHaveTextContent('Corrigir — Lateral direita')
  })

  it('envia falso positivo sem pedir complemento', async () => {
    const posts = mockValidacao(f180NaoConforme)

    renderDetail()
    await waitFor(() => expect(screen.getByTestId('botao-corrigir')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-corrigir'))

    expect(screen.queryByLabelText(/classe certa/i)).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/severidade certa/i)).not.toBeInTheDocument()

    await userEvent.type(screen.getByLabelText(/observação/i), 'É sombra de árvore.')
    await userEvent.click(screen.getByTestId('salvar-correcao'))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]?.url).toContain('/corrigir')
    expect(posts[0]?.body).toEqual({
      campo: 'c54',
      tipo_erro: 'falso_positivo',
      observacao: 'É sombra de árvore.',
    })
  })

  it('classe errada revela o seletor de classe e envia a classe escolhida', async () => {
    const posts = mockValidacao(f180NaoConforme)

    renderDetail()
    await waitFor(() => expect(screen.getByTestId('botao-corrigir')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-corrigir'))
    await userEvent.click(screen.getByLabelText('Classe errada'))

    await userEvent.selectOptions(screen.getByLabelText(/classe certa/i), 'ausencia_item')
    await userEvent.click(screen.getByTestId('salvar-correcao'))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]?.body).toEqual({
      campo: 'c54',
      tipo_erro: 'classe_errada',
      classe: 'ausencia_item',
    })
  })

  it('severidade errada revela o seletor de severidade e envia número', async () => {
    const posts = mockValidacao(f180NaoConforme)

    renderDetail()
    await waitFor(() => expect(screen.getByTestId('botao-corrigir')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-corrigir'))
    await userEvent.click(screen.getByLabelText('Severidade errada'))

    await userEvent.selectOptions(screen.getByLabelText(/severidade certa/i), '1')
    await userEvent.click(screen.getByTestId('salvar-correcao'))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]?.body).toEqual({
      campo: 'c54',
      tipo_erro: 'severidade_errada',
      severidade: 1,
    })
  })

  it('foto não julgável envia só o tipo, para a vista escolhida', async () => {
    const posts = mockValidacao(f180NaoConforme)

    renderDetail()
    await waitFor(() => expect(screen.getByTestId('botao-corrigir')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-corrigir'))
    await userEvent.click(screen.getByLabelText('Foto não era julgável'))
    await userEvent.selectOptions(screen.getByLabelText('Vista'), 'c56')
    await userEvent.click(screen.getByTestId('salvar-correcao'))

    await waitFor(() => expect(posts).toHaveLength(1))
    expect(posts[0]?.body).toEqual({ campo: 'c56', tipo_erro: 'nao_julgavel' })
  })

  it('mostra quem validou e quando depois de confirmado', async () => {
    mockDetail({
      ...f180NaoConforme,
      validacao: 'confirmado',
      validado_por: 'operador@tecnogera.com',
      validado_em: '2026-08-03T10:00:00Z',
    })

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('situacao-validacao')).toBeInTheDocument())
    const situacao = screen.getByTestId('situacao-validacao')
    expect(situacao).toHaveTextContent('Confirmado')
    expect(situacao).toHaveTextContent('operador@tecnogera.com')
  })

  it('marca a vista corrigida no card, com o tipo do erro', async () => {
    mockDetail({
      ...f180NaoConforme,
      validacao: 'corrigido',
      validado_por: 'operador@tecnogera.com',
      validado_em: '2026-08-03T10:00:00Z',
      vistas: f180NaoConforme.vistas.map((v) =>
        v.campo === 'c54'
          ? {
              ...v,
              validacao: {
                estado: 'corrigido',
                tipo_erro: 'falso_positivo',
                tipo_erro_rotulo: 'Falso positivo — não há defeito aqui',
                classe: 'conforme',
                classe_rotulo: 'Conforme',
                severidade: null,
                severidade_rotulo: null,
                observacao: 'É sombra de árvore.',
                por: 'operador@tecnogera.com',
                em: '2026-08-03T10:00:00Z',
              },
            }
          : v,
      ),
    })

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('validacao-vista-c54')).toBeInTheDocument())
    const bloco = screen.getByTestId('validacao-vista-c54')
    expect(bloco).toHaveTextContent(/falso positivo/i)
    expect(bloco).toHaveTextContent('É sombra de árvore.')
    expect(screen.queryByTestId('validacao-vista-c55')).not.toBeInTheDocument()
  })

  it('checklist sem laudo não oferece botão de validar', async () => {
    mockDetail({
      ...semAnalise,
      validavel: false,
      vistas: semAnalise.vistas.map((v) => ({ ...v, corrigivel: false })),
    })

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('validacao-indisponivel')).toBeInTheDocument())
    expect(screen.queryByTestId('botao-confirmar')).not.toBeInTheDocument()
  })

  it('usa os rótulos do backend para classe e tipo de defeito', async () => {
    mockDetail({
      ...f180NaoConforme,
      achados: [
        {
          ...(f180NaoConforme.achados ?? [])[0],
          classe_rotulo: 'Dano visível',
          tipo_defeito_rotulo: 'Amassado / deformação',
        },
      ],
    })

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('achado')).toBeInTheDocument())
    expect(screen.getByTestId('achado')).toHaveTextContent('Amassado / deformação')
  })

  it('nunca formata snake_case no front — sem tipo_defeito_rotulo, cai em classe_rotulo, nunca no bruto', async () => {
    // O fallback de "taxonomia sem rótulo" é responsabilidade do backend
    // (`view_inspection.rotulo_tipo_defeito`, ticket v1-entregavel/02). O front
    // só decide qual rótulo pronto mostrar — nunca deriva texto do valor bruto.
    mockDetail({
      ...f180NaoConforme,
      achados: [
        {
          ...(f180NaoConforme.achados ?? [])[0],
          tipo_defeito: 'tipo_defeito_novo_sem_rotulo',
          tipo_defeito_rotulo: undefined,
          classe_rotulo: 'Dano visível',
        },
      ],
    })

    renderDetail()

    await waitFor(() => expect(screen.getByTestId('achado')).toBeInTheDocument())
    const achado = screen.getByTestId('achado')
    expect(achado).toHaveTextContent('Dano visível')
    expect(achado).not.toHaveTextContent('tipo_defeito_novo_sem_rotulo')
  })
})
