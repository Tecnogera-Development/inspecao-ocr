import { apiClient } from '@/api/client'
import type { UsuarioAcaoResponse, UsuarioCriadoResponse, UsuarioListItem } from '@/api/types'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { ptBR } from 'date-fns/locale'
import { type FormEvent, useState } from 'react'

/**
 * `admin`/`operador` é vocabulário de INTERFACE (quem gerencia usuários), não
 * de domínio de inspeção — diferente de `indicador`/`classe`/`severidade`,
 * que vêm sempre prontos do backend. Por isso, e só aqui, o rótulo mora no
 * front (mapa `usuarios-portal`, decisão "papéis").
 */
const ROLE_LABEL: Record<string, string> = {
  admin: 'Administrador',
  operador: 'Operador',
}

function rotuloPapel(role: string): string {
  return ROLE_LABEL[role] ?? role
}

/** Sinaliza "403 do backend" para a UI escolher a tela de acesso negado em vez da funcional. */
class AcessoNegadoError extends Error {}

function detalheDoErro(erro: unknown, fallback: string): string {
  const detail = (erro as { detail?: unknown })?.detail
  return typeof detail === 'string' && detail ? detail : fallback
}

async function fetchUsuarios(): Promise<UsuarioListItem[]> {
  const result = await apiClient.GET('/api/v1/portal/usuarios')
  if (result.response.status === 403) {
    // Esconder o item de menu não é segurança — quem garante é este 403.
    // Se um operador forçar a URL, é isto que ele vê, não a tela funcional.
    throw new AcessoNegadoError('Acesso restrito a administradores.')
  }
  if (!result.data) throw new Error(`HTTP ${result.response.status}`)
  return result.data
}

async function criarUsuario(input: {
  email: string
  role: string
}): Promise<UsuarioCriadoResponse> {
  const result = await apiClient.POST('/api/v1/portal/usuarios', { body: input })
  if (!result.data) {
    throw new Error(detalheDoErro(result.error, 'Não foi possível criar o usuário.'))
  }
  return result.data
}

async function inativarUsuario(id: string): Promise<UsuarioAcaoResponse> {
  const result = await apiClient.POST('/api/v1/portal/usuarios/{user_id}/inativar', {
    params: { path: { user_id: id } },
  })
  if (!result.data) {
    throw new Error(detalheDoErro(result.error, 'Não foi possível inativar este usuário.'))
  }
  return result.data
}

async function reativarUsuario(id: string): Promise<UsuarioAcaoResponse> {
  const result = await apiClient.POST('/api/v1/portal/usuarios/{user_id}/reativar', {
    params: { path: { user_id: id } },
  })
  if (!result.data) {
    throw new Error(detalheDoErro(result.error, 'Não foi possível reativar este usuário.'))
  }
  return result.data
}

async function resetarSenha(id: string): Promise<UsuarioCriadoResponse> {
  const result = await apiClient.POST('/api/v1/portal/usuarios/{user_id}/resetar-senha', {
    params: { path: { user_id: id } },
  })
  if (!result.data) {
    throw new Error(detalheDoErro(result.error, 'Não foi possível gerar um novo código.'))
  }
  return result.data
}

function formatUltimoLogin(value: string | null): string {
  if (!value) return 'Nunca'
  return format(new Date(value), "dd/MM/yyyy 'às' HH:mm", { locale: ptBR })
}

export function UsuariosPage() {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['usuarios'], queryFn: fetchUsuarios })

  const [criando, setCriando] = useState(false)
  const [emailNovo, setEmailNovo] = useState('')
  const [papelNovo, setPapelNovo] = useState('operador')
  const [codigoAtivo, setCodigoAtivo] = useState<UsuarioCriadoResponse | null>(null)

  const criar = useMutation({
    mutationFn: criarUsuario,
    onSuccess: (data) => {
      setCriando(false)
      setEmailNovo('')
      setPapelNovo('operador')
      setCodigoAtivo(data)
      queryClient.invalidateQueries({ queryKey: ['usuarios'] })
    },
  })

  function handleCriarSubmit(e: FormEvent) {
    e.preventDefault()
    criar.mutate({ email: emailNovo.trim(), role: papelNovo })
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-2xl font-bold text-slate-900">Usuários</h1>
        {query.isSuccess && (
          <button
            type="button"
            data-testid="botao-novo-usuario"
            onClick={() => setCriando((v) => !v)}
            className="rounded-md bg-brand-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-hover"
          >
            {criando ? 'Cancelar' : 'Novo usuário'}
          </button>
        )}
      </div>

      {criando && (
        <form
          data-testid="formulario-novo-usuario"
          onSubmit={handleCriarSubmit}
          className="rounded-lg border border-slate-200 bg-white p-4"
        >
          <div className="flex flex-wrap items-end gap-4">
            <div className="flex flex-col gap-1">
              <label htmlFor="novo-usuario-email" className="text-xs font-medium text-slate-700">
                E-mail
              </label>
              <input
                id="novo-usuario-email"
                type="email"
                required
                value={emailNovo}
                onChange={(e) => setEmailNovo(e.target.value)}
                className="w-64 rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="novo-usuario-papel" className="text-xs font-medium text-slate-700">
                Papel
              </label>
              <select
                id="novo-usuario-papel"
                value={papelNovo}
                onChange={(e) => setPapelNovo(e.target.value)}
                className="rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="operador">{ROLE_LABEL.operador}</option>
                <option value="admin">{ROLE_LABEL.admin}</option>
              </select>
            </div>
            <button
              type="submit"
              data-testid="novo-usuario-enviar"
              disabled={criar.isPending}
              className="rounded-md bg-brand-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50"
            >
              {criar.isPending ? 'Criando…' : 'Criar e gerar código'}
            </button>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Depois de criado, o código de uso único aparece uma vez. Copie antes de fechar.
          </p>
          {criar.isError && (
            <p data-testid="novo-usuario-erro" className="mt-2 text-sm text-red-700">
              {(criar.error as Error).message}
            </p>
          )}
        </form>
      )}

      {query.isError ? (
        query.error instanceof AcessoNegadoError ? (
          <div
            data-testid="usuarios-acesso-negado"
            className="rounded-lg border border-amber-200 bg-amber-50 p-6 text-center"
          >
            <p className="font-medium text-amber-900">Acesso restrito a administradores.</p>
            <p className="mt-1 text-sm text-amber-800">
              Sua conta não tem permissão para gerenciar usuários.
            </p>
          </div>
        ) : (
          <div
            data-testid="usuarios-erro"
            className="rounded-lg border border-red-200 bg-red-50 p-6 text-center"
          >
            <p className="text-red-700">Não foi possível carregar os usuários.</p>
            <button
              type="button"
              className="mt-3 rounded-md border border-red-300 px-3 py-1.5 text-sm font-medium hover:bg-red-100"
              onClick={() => query.refetch()}
            >
              Tentar novamente
            </button>
          </div>
        )
      ) : query.isPending ? (
        <div data-testid="usuarios-skeleton" className="animate-pulse space-y-2">
          <div className="h-10 rounded bg-slate-200" />
          <div className="h-10 rounded bg-slate-100" />
          <div className="h-10 rounded bg-slate-200" />
        </div>
      ) : query.data.length === 0 ? (
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-10 text-center">
          <p className="text-slate-600">Nenhum usuário cadastrado ainda.</p>
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table data-testid="usuarios-tabela" className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b bg-brand-tint text-left">
                {['E-mail', 'Papel', 'Ativo', 'Último login', 'Janela de senha', 'Ações'].map(
                  (h) => (
                    <th
                      key={h}
                      className="px-3 py-2 text-xs font-medium uppercase tracking-wide text-brand-hover"
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {query.data.map((usuario) => (
                <UsuarioLinha
                  key={usuario.id}
                  usuario={usuario}
                  onAlterado={() => queryClient.invalidateQueries({ queryKey: ['usuarios'] })}
                  onCodigoGerado={setCodigoAtivo}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {codigoAtivo && <CodigoUnicoPanel info={codigoAtivo} onFechar={() => setCodigoAtivo(null)} />}
    </div>
  )
}

function UsuarioLinha({
  usuario,
  onAlterado,
  onCodigoGerado,
}: {
  usuario: UsuarioListItem
  onAlterado: () => void
  onCodigoGerado: (info: UsuarioCriadoResponse) => void
}) {
  const [confirmando, setConfirmando] = useState(false)
  const acao = usuario.is_active ? 'inativar' : 'reativar'

  const alternarAtivo = useMutation({
    mutationFn: () =>
      usuario.is_active ? inativarUsuario(usuario.id) : reativarUsuario(usuario.id),
    onSuccess: () => {
      setConfirmando(false)
      onAlterado()
    },
  })

  const resetar = useMutation({
    mutationFn: () => resetarSenha(usuario.id),
    onSuccess: (data) => {
      onCodigoGerado(data)
      onAlterado()
    },
  })

  const erro = alternarAtivo.isError
    ? (alternarAtivo.error as Error).message
    : resetar.isError
      ? (resetar.error as Error).message
      : null

  return (
    <tr data-testid={`usuario-linha-${usuario.id}`} className="border-b bg-white last:border-0">
      <td className="px-3 py-2">{usuario.email}</td>
      <td className="px-3 py-2">{rotuloPapel(usuario.role)}</td>
      <td className="px-3 py-2">
        <span
          data-testid={`usuario-status-${usuario.id}`}
          className={
            usuario.is_active
              ? 'rounded-full bg-green-100 px-2 py-0.5 text-xs font-medium text-green-800'
              : 'rounded-full bg-slate-200 px-2 py-0.5 text-xs font-medium text-slate-600'
          }
        >
          {usuario.is_active ? 'Ativo' : 'Inativo'}
        </span>
      </td>
      <td className="px-3 py-2 whitespace-nowrap">{formatUltimoLogin(usuario.last_login_at)}</td>
      <td className="px-3 py-2">
        {usuario.janela_aberta ? (
          <span
            data-testid={`usuario-janela-${usuario.id}`}
            className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-900"
          >
            Aberta
          </span>
        ) : (
          <span className="text-xs text-slate-400">—</span>
        )}
      </td>
      <td className="px-3 py-2">
        {confirmando ? (
          <div
            data-testid={`confirmar-${usuario.id}`}
            className="flex flex-wrap items-center gap-2"
          >
            <span className="text-xs text-slate-600">
              {acao === 'inativar'
                ? `Inativar ${usuario.email}? A sessão dele será encerrada.`
                : `Reativar ${usuario.email}?`}
            </span>
            <button
              type="button"
              data-testid={`confirmar-sim-${usuario.id}`}
              onClick={() => alternarAtivo.mutate()}
              disabled={alternarAtivo.isPending}
              className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
            >
              {alternarAtivo.isPending ? '…' : 'Sim'}
            </button>
            <button
              type="button"
              data-testid={`confirmar-nao-${usuario.id}`}
              onClick={() => setConfirmando(false)}
              disabled={alternarAtivo.isPending}
              className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              Cancelar
            </button>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              data-testid={
                usuario.is_active ? `botao-inativar-${usuario.id}` : `botao-reativar-${usuario.id}`
              }
              onClick={() => setConfirmando(true)}
              className="rounded-md border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            >
              {usuario.is_active ? 'Inativar' : 'Reativar'}
            </button>
            <button
              type="button"
              data-testid={`botao-resetar-senha-${usuario.id}`}
              onClick={() => resetar.mutate()}
              disabled={resetar.isPending}
              className="rounded-md border border-brand-secondary px-2 py-1 text-xs font-medium text-brand-hover hover:bg-brand-tint disabled:opacity-50"
            >
              {resetar.isPending ? 'Gerando…' : 'Resetar senha'}
            </button>
          </div>
        )}
        {erro && (
          <p data-testid={`usuario-erro-${usuario.id}`} className="mt-1 text-xs text-red-700">
            {erro}
          </p>
        )}
      </td>
    </tr>
  )
}

/**
 * O produto inteiro do fluxo: o código de uso único aparece AQUI, uma vez,
 * para criação e reset. Fecha e some — nunca volta a esta tela (ver decisão
 * do mapa `usuarios-portal`, risco 1). Se o admin fechar sem copiar, o único
 * caminho de volta é gerar um código novo com "Resetar senha".
 */
function CodigoUnicoPanel({
  info,
  onFechar,
}: {
  info: UsuarioCriadoResponse
  onFechar: () => void
}) {
  const [copiado, setCopiado] = useState(false)

  async function copiar() {
    try {
      await navigator.clipboard.writeText(info.codigo)
      setCopiado(true)
      window.setTimeout(() => setCopiado(false), 2000)
    } catch {
      // Clipboard indisponível (permissão negada, contexto não seguro) — o
      // código continua selecionável na tela; só o atalho falha em silêncio.
    }
  }

  return (
    <div
      role="alertdialog"
      aria-labelledby="codigo-titulo"
      aria-describedby="codigo-aviso"
      data-testid="painel-codigo"
      className="fixed inset-0 z-30 flex items-center justify-center bg-slate-900/60 p-4"
    >
      <div className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl">
        <h2 id="codigo-titulo" className="text-lg font-semibold text-slate-900">
          Código de acesso: {info.email}
        </h2>
        <p id="codigo-aviso" className="mt-2 text-sm text-slate-600">
          Repasse este código ao usuário fora do portal (chat, telefone). Ele{' '}
          <strong>expira em 30 minutos</strong> e <strong>não será mostrado de novo</strong>. Se
          fechar esta janela sem copiar, o único caminho é gerar um código novo em "Resetar senha".
        </p>

        <div className="mt-4 flex items-center gap-2 rounded-md border border-brand-secondary bg-brand-tint p-3">
          <code
            data-testid="codigo-valor"
            className="flex-1 select-all break-all font-mono text-lg font-bold text-brand-hover"
          >
            {info.codigo}
          </code>
          <button
            type="button"
            data-testid="botao-copiar-codigo"
            onClick={copiar}
            className="shrink-0 rounded-md bg-brand-primary px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-hover"
          >
            {copiado ? 'Copiado ✓' : 'Copiar'}
          </button>
        </div>

        <button
          type="button"
          data-testid="botao-fechar-codigo"
          onClick={onFechar}
          className="mt-5 w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Já copiei, fechar
        </button>
      </div>
    </div>
  )
}
