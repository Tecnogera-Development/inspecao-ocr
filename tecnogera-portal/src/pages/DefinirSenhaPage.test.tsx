import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { RouterProvider, createMemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DefinirSenhaPage } from './DefinirSenhaPage'

function isCsrfRequest(req: Request | string) {
  const href = req instanceof Request ? req.url : req
  return new URL(href, 'http://localhost').pathname.endsWith('/portal/csrf')
}

/**
 * Página é pública — sem sessão, o preflight de CSRF (disparado pelo
 * middleware em `api/client.ts` para qualquer POST) devolve 401, e é
 * silenciosamente ignorado: a rota real não exige CSRF (só rate limit).
 */
function mockDefinirSenha(status: number, body: unknown) {
  const posts: unknown[] = []
  const fetchMock = vi
    .fn()
    .mockImplementation(async (req: Request | string, init?: RequestInit) => {
      if (isCsrfRequest(req)) {
        return new Response(JSON.stringify({ detail: 'Não autenticado' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        })
      }
      const requisicao = req instanceof Request ? req : null
      const cru = init?.body ? String(init.body) : requisicao ? await requisicao.clone().text() : ''
      if (cru) posts.push(JSON.parse(cru))
      return new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    })
  vi.stubGlobal('fetch', fetchMock)
  return { fetchMock, posts }
}

function renderDefinirSenha() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const router = createMemoryRouter(
    [
      { path: '/definir-senha', element: <DefinirSenhaPage /> },
      { path: '/login', element: <div>login page</div> },
    ],
    { initialEntries: ['/definir-senha'] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  )
}

async function preencherFormulario(senha = 'senha1234', confirmar = senha) {
  await userEvent.type(screen.getByLabelText(/e-mail/i), 'usuario@tecnogera.com')
  await userEvent.type(screen.getByLabelText(/código/i), 'CODIGO-1')
  await userEvent.type(screen.getByLabelText(/^senha nova$/i), senha)
  await userEvent.type(screen.getByLabelText(/confirmar senha/i), confirmar)
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('DefinirSenhaPage', () => {
  it('renderiza e-mail, código, senha, confirmar senha e botão de enviar', () => {
    renderDefinirSenha()
    expect(screen.getByLabelText(/e-mail/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/código/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/^senha nova$/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/confirmar senha/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /definir senha/i })).toBeInTheDocument()
  })

  it('sucesso leva para /login', async () => {
    mockDefinirSenha(200, { ok: true })
    renderDefinirSenha()

    await preencherFormulario()
    await userEvent.click(screen.getByRole('button', { name: /definir senha/i }))

    await waitFor(() => expect(screen.getByText('login page')).toBeInTheDocument())
  })

  it('mostra a mensagem de erro genérica do backend exatamente como veio, sem reescrever', async () => {
    const MENSAGEM_GENERICA =
      'Não foi possível definir a senha. Confira e-mail e código, e se a janela ' +
      'de 30 minutos ainda não expirou. Se o problema continuar, peça um novo ' +
      'código ao administrador.'
    mockDefinirSenha(400, { detail: MENSAGEM_GENERICA })
    renderDefinirSenha()

    await preencherFormulario()
    await userEvent.click(screen.getByRole('button', { name: /definir senha/i }))

    await waitFor(() =>
      expect(screen.getByTestId('erro-definir-senha')).toHaveTextContent(MENSAGEM_GENERICA),
    )
  })

  it('trata 429 mostrando a mensagem do backend, sem inventar texto novo', async () => {
    mockDefinirSenha(429, {
      detail: 'Muitas tentativas. Aguarde alguns minutos e tente novamente.',
    })
    renderDefinirSenha()

    await preencherFormulario()
    await userEvent.click(screen.getByRole('button', { name: /definir senha/i }))

    await waitFor(() =>
      expect(screen.getByTestId('erro-definir-senha')).toHaveTextContent(
        'Muitas tentativas. Aguarde alguns minutos e tente novamente.',
      ),
    )
  })

  it('valida localmente que as senhas conferem, sem chamar o backend', async () => {
    const { fetchMock } = mockDefinirSenha(200, { ok: true })
    renderDefinirSenha()

    await preencherFormulario('senha1234', 'outraSenha1')
    await userEvent.click(screen.getByRole('button', { name: /definir senha/i }))

    expect(screen.getByTestId('erro-definir-senha')).toHaveTextContent(/não conferem/i)
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('exige senha com pelo menos 8 caracteres antes de enviar', async () => {
    const { fetchMock } = mockDefinirSenha(200, { ok: true })
    renderDefinirSenha()

    await preencherFormulario('abc123', 'abc123')
    await userEvent.click(screen.getByRole('button', { name: /definir senha/i }))

    expect(screen.getByTestId('erro-definir-senha')).toHaveTextContent(/8 caracteres/i)
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
