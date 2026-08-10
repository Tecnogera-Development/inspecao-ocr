import type { UsuarioListItem } from '@/api/types'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UsuariosPage } from './UsuariosPage'

const admin: UsuarioListItem = {
  id: 'u-admin',
  email: 'admin@tecnogera.com',
  role: 'admin',
  is_active: true,
  last_login_at: '2026-08-01T10:00:00Z',
  janela_aberta: false,
}

const operadorComJanela: UsuarioListItem = {
  id: 'u-op',
  email: 'novo.op@tecnogera.com',
  role: 'operador',
  is_active: true,
  last_login_at: null,
  janela_aberta: true,
}

interface Handler {
  method: string
  test: (pathname: string) => boolean
  respond: (body: unknown) => { status: number; body: unknown }
}

/** Roteador de fetch por método + path — mesmo padrão de ChecklistDetailPage.test.tsx. */
function mockApi(handlers: Handler[]) {
  const chamadas: { method: string; path: string; body: unknown }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const requisicao = input instanceof Request ? input : null
      const url = typeof input === 'string' ? input : (requisicao?.url ?? String(input))
      const pathname = new URL(url, 'http://localhost').pathname
      const metodo = (init?.method ?? requisicao?.method ?? 'GET').toUpperCase()

      let body: unknown = null
      if (metodo !== 'GET') {
        const cru = init?.body
          ? String(init.body)
          : requisicao
            ? await requisicao.clone().text()
            : ''
        body = cru ? JSON.parse(cru) : null
      }
      chamadas.push({ method: metodo, path: pathname, body })

      if (pathname.endsWith('/portal/csrf')) {
        return new Response(JSON.stringify({ token: 'test-csrf' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }

      const handler = handlers.find((h) => h.method === metodo && h.test(pathname))
      if (!handler) return new Response('not found', { status: 404 })
      const { status, body: respBody } = handler.respond(body)
      return new Response(JSON.stringify(respBody), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
  return chamadas
}

function mockLista(usuarios: UsuarioListItem[], status = 200) {
  return mockApi([
    {
      method: 'GET',
      test: (p) => p.endsWith('/portal/usuarios'),
      respond: () => ({ status, body: status === 200 ? usuarios : { detail: 'erro' } }),
    },
  ])
}

function renderUsuarios() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter([{ path: '/usuarios', element: <UsuariosPage /> }], {
    initialEntries: ['/usuarios'],
  })
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('UsuariosPage — lista', () => {
  it('mostra o skeleton enquanto busca', () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() => new Promise<Response>(() => {})),
    )

    renderUsuarios()

    expect(screen.getByTestId('usuarios-skeleton')).toBeInTheDocument()
  })

  it('lista e-mail, papel, ativo, último login e janela aberta', async () => {
    mockLista([admin, operadorComJanela])

    renderUsuarios()

    await waitFor(() => expect(screen.getByTestId('usuarios-tabela')).toBeInTheDocument())

    expect(screen.getByText('admin@tecnogera.com')).toBeInTheDocument()
    expect(screen.getByText('Administrador')).toBeInTheDocument()
    expect(screen.getByText('novo.op@tecnogera.com')).toBeInTheDocument()
    expect(screen.getByText('Operador')).toBeInTheDocument()
    expect(screen.getByTestId('usuario-status-u-admin')).toHaveTextContent('Ativo')
    expect(screen.getByTestId('usuario-janela-u-op')).toHaveTextContent('Aberta')
    expect(screen.queryByTestId('usuario-janela-u-admin')).not.toBeInTheDocument()
    expect(screen.getByText('Nunca')).toBeInTheDocument()
  })

  it('operador que força a URL vê erro de acesso, não a tela funcional (403 do backend)', async () => {
    mockLista([], 403)

    renderUsuarios()

    await waitFor(() => expect(screen.getByTestId('usuarios-acesso-negado')).toBeInTheDocument())
    expect(screen.queryByTestId('usuarios-tabela')).not.toBeInTheDocument()
    expect(screen.queryByTestId('botao-novo-usuario')).not.toBeInTheDocument()
  })

  it('mostra erro genérico com "tentar novamente" para outras falhas', async () => {
    mockLista([], 500)

    renderUsuarios()

    await waitFor(() => expect(screen.getByTestId('usuarios-erro')).toBeInTheDocument())
    expect(screen.queryByTestId('usuarios-acesso-negado')).not.toBeInTheDocument()
  })
})

describe('UsuariosPage — criar usuário e código de uso único', () => {
  function mockCriacao(usuarios: UsuarioListItem[], resposta: { status?: number; body?: unknown }) {
    return mockApi([
      {
        method: 'GET',
        test: (p) => p.endsWith('/portal/usuarios'),
        respond: () => ({ status: 200, body: usuarios }),
      },
      {
        method: 'POST',
        test: (p) => p.endsWith('/portal/usuarios'),
        respond: () => ({ status: resposta.status ?? 201, body: resposta.body }),
      },
    ])
  }

  it('cria usuário e mostra o código com aviso de expiração e botão de copiar', async () => {
    mockCriacao([], {
      body: {
        id: 'novo-id',
        email: 'novato@tecnogera.com',
        role: 'operador',
        codigo: 'ABC-123-XYZ',
      },
    })

    renderUsuarios()
    await waitFor(() => expect(screen.getByTestId('botao-novo-usuario')).toBeInTheDocument())

    await userEvent.click(screen.getByTestId('botao-novo-usuario'))
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'novato@tecnogera.com')
    await userEvent.click(screen.getByTestId('novo-usuario-enviar'))

    await waitFor(() => expect(screen.getByTestId('painel-codigo')).toBeInTheDocument())
    expect(screen.getByTestId('codigo-valor')).toHaveTextContent('ABC-123-XYZ')
    expect(screen.getByTestId('painel-codigo')).toHaveTextContent(/30 minutos/)
    expect(screen.getByTestId('painel-codigo')).toHaveTextContent(/não será mostrado de novo/i)
  })

  it('o código não é re-exibido depois que o painel é fechado', async () => {
    mockCriacao([], {
      body: { id: 'novo-id', email: 'novato@tecnogera.com', role: 'operador', codigo: 'SEGREDO-1' },
    })

    renderUsuarios()
    await waitFor(() => expect(screen.getByTestId('botao-novo-usuario')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-novo-usuario'))
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'novato@tecnogera.com')
    await userEvent.click(screen.getByTestId('novo-usuario-enviar'))

    await waitFor(() => expect(screen.getByTestId('painel-codigo')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-fechar-codigo'))

    expect(screen.queryByTestId('painel-codigo')).not.toBeInTheDocument()
    expect(screen.queryByText('SEGREDO-1')).not.toBeInTheDocument()
  })

  it('botão copiar chama a Clipboard API com o código', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })

    mockCriacao([], {
      body: { id: 'novo-id', email: 'novato@tecnogera.com', role: 'operador', codigo: 'COPIA-ME' },
    })

    renderUsuarios()
    await waitFor(() => expect(screen.getByTestId('botao-novo-usuario')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-novo-usuario'))
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'novato@tecnogera.com')
    await userEvent.click(screen.getByTestId('novo-usuario-enviar'))

    await waitFor(() => expect(screen.getByTestId('painel-codigo')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-copiar-codigo'))

    expect(writeText).toHaveBeenCalledWith('COPIA-ME')
    await waitFor(() =>
      expect(screen.getByTestId('botao-copiar-codigo')).toHaveTextContent(/copiado/i),
    )
  })

  it('mostra o erro do backend quando o e-mail já existe (409)', async () => {
    mockCriacao([], { status: 409, body: { detail: 'e-mail já cadastrado' } })

    renderUsuarios()
    await waitFor(() => expect(screen.getByTestId('botao-novo-usuario')).toBeInTheDocument())
    await userEvent.click(screen.getByTestId('botao-novo-usuario'))
    await userEvent.type(screen.getByLabelText(/e-mail/i), 'repetido@tecnogera.com')
    await userEvent.click(screen.getByTestId('novo-usuario-enviar'))

    await waitFor(() =>
      expect(screen.getByTestId('novo-usuario-erro')).toHaveTextContent(/e-mail já cadastrado/i),
    )
    expect(screen.queryByTestId('painel-codigo')).not.toBeInTheDocument()
  })
})

describe('UsuariosPage — inativar, reativar e resetar senha', () => {
  function mockAcoes(
    usuarios: UsuarioListItem[],
    acao: { path: string; status?: number; body?: unknown },
  ) {
    return mockApi([
      {
        method: 'GET',
        test: (p) => p.endsWith('/portal/usuarios'),
        respond: () => ({ status: 200, body: usuarios }),
      },
      {
        method: 'POST',
        test: (p) => p.endsWith(acao.path),
        respond: () => ({ status: acao.status ?? 200, body: acao.body }),
      },
    ])
  }

  it('inativar pede confirmação antes de chamar a API', async () => {
    const chamadas = mockAcoes([admin], {
      path: `/usuarios/${admin.id}/inativar`,
      body: { id: admin.id, email: admin.email, role: admin.role, is_active: false },
    })

    renderUsuarios()
    await waitFor(() =>
      expect(screen.getByTestId(`botao-inativar-${admin.id}`)).toBeInTheDocument(),
    )

    await userEvent.click(screen.getByTestId(`botao-inativar-${admin.id}`))
    expect(screen.getByTestId(`confirmar-${admin.id}`)).toBeInTheDocument()
    expect(chamadas.some((c) => c.method === 'POST' && c.path.includes('/inativar'))).toBe(false)

    await userEvent.click(screen.getByTestId(`confirmar-sim-${admin.id}`))

    await waitFor(() =>
      expect(chamadas.some((c) => c.method === 'POST' && c.path.includes('/inativar'))).toBe(true),
    )
    expect(screen.queryByTestId(`confirmar-${admin.id}`)).not.toBeInTheDocument()
  })

  it('cancelar a confirmação não chama a API', async () => {
    const chamadas = mockAcoes([admin], {
      path: `/usuarios/${admin.id}/inativar`,
      body: { id: admin.id, email: admin.email, role: admin.role, is_active: false },
    })

    renderUsuarios()
    await waitFor(() =>
      expect(screen.getByTestId(`botao-inativar-${admin.id}`)).toBeInTheDocument(),
    )
    await userEvent.click(screen.getByTestId(`botao-inativar-${admin.id}`))
    await userEvent.click(screen.getByTestId(`confirmar-nao-${admin.id}`))

    expect(screen.queryByTestId(`confirmar-${admin.id}`)).not.toBeInTheDocument()
    expect(chamadas.some((c) => c.method === 'POST')).toBe(false)
  })

  it('mostra o erro do backend ao tentar inativar o único admin ativo', async () => {
    mockAcoes([admin], {
      path: `/usuarios/${admin.id}/inativar`,
      status: 400,
      body: { detail: 'Não é possível inativar o único admin ativo restante' },
    })

    renderUsuarios()
    await waitFor(() =>
      expect(screen.getByTestId(`botao-inativar-${admin.id}`)).toBeInTheDocument(),
    )
    await userEvent.click(screen.getByTestId(`botao-inativar-${admin.id}`))
    await userEvent.click(screen.getByTestId(`confirmar-sim-${admin.id}`))

    await waitFor(() =>
      expect(screen.getByTestId(`usuario-erro-${admin.id}`)).toHaveTextContent(
        /único admin ativo/i,
      ),
    )
  })

  it('reativar mostra o botão certo para usuário inativo', async () => {
    const inativo: UsuarioListItem = { ...admin, id: 'u-inativo', is_active: false }
    mockAcoes([inativo], {
      path: `/usuarios/${inativo.id}/reativar`,
      body: { id: inativo.id, email: inativo.email, role: inativo.role, is_active: true },
    })

    renderUsuarios()
    await waitFor(() =>
      expect(screen.getByTestId(`botao-reativar-${inativo.id}`)).toBeInTheDocument(),
    )
    expect(screen.queryByTestId(`botao-inativar-${inativo.id}`)).not.toBeInTheDocument()

    await userEvent.click(screen.getByTestId(`botao-reativar-${inativo.id}`))
    await userEvent.click(screen.getByTestId(`confirmar-sim-${inativo.id}`))

    await waitFor(() =>
      expect(screen.queryByTestId(`confirmar-${inativo.id}`)).not.toBeInTheDocument(),
    )
  })

  it('resetar senha não pede confirmação e reabre o painel de código (mesmo fluxo da criação)', async () => {
    mockAcoes([admin], {
      path: `/usuarios/${admin.id}/resetar-senha`,
      body: { id: admin.id, email: admin.email, role: admin.role, codigo: 'RESET-999' },
    })

    renderUsuarios()
    await waitFor(() =>
      expect(screen.getByTestId(`botao-resetar-senha-${admin.id}`)).toBeInTheDocument(),
    )
    await userEvent.click(screen.getByTestId(`botao-resetar-senha-${admin.id}`))

    await waitFor(() => expect(screen.getByTestId('painel-codigo')).toBeInTheDocument())
    expect(screen.getByTestId('codigo-valor')).toHaveTextContent('RESET-999')
  })
})
