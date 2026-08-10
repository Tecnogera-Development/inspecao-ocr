import { apiClient } from '@/api/client'
import { useMutation } from '@tanstack/react-query'
import { type FormEvent, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

const MIN_SENHA = 8

interface DefinirSenhaInput {
  email: string
  codigo: string
  senha: string
}

/**
 * A mensagem de erro é a que o backend mandar, sempre — nunca trocada por
 * algo mais específico aqui. `POST /definir-senha` devolve uma frase
 * genérica de propósito (não pode diferenciar e-mail inexistente de código
 * errado de janela expirada, senão o front vira oráculo de e-mail válido —
 * ver `_DEFINIR_SENHA_ERRO_GENERICO` em `app/routers/usuarios.py`). O mesmo
 * vale para o 429 do rate limit: o backend já devolve uma frase adequada
 * para o usuário, então o front só exibe `detail` como veio.
 */
async function definirSenha(input: DefinirSenhaInput): Promise<void> {
  const result = await apiClient.POST('/api/v1/portal/definir-senha', { body: input })
  if (result.data?.ok) return
  const detail = (result.error as { detail?: unknown })?.detail
  throw new Error(
    typeof detail === 'string' && detail ? detail : 'Não foi possível conectar. Tente novamente.',
  )
}

/**
 * Rota pública, fora do `AppShell` autenticado — quem chega aqui (link do
 * admin com e-mail e código repassados fora de banda) não tem sessão.
 */
export function DefinirSenhaPage() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [codigo, setCodigo] = useState('')
  const [senha, setSenha] = useState('')
  const [confirmarSenha, setConfirmarSenha] = useState('')
  const [erroValidacao, setErroValidacao] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: definirSenha,
    onSuccess: () => {
      navigate('/login', { state: { senhaDefinida: true } })
    },
  })

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setErroValidacao(null)

    // Validação de formulário (comprimento, confirmação) — não é o "erro
    // genérico" do backend, é checagem local antes de sequer chamar a rota.
    if (senha.length < MIN_SENHA) {
      setErroValidacao(`A senha precisa ter pelo menos ${MIN_SENHA} caracteres.`)
      return
    }
    if (senha !== confirmarSenha) {
      setErroValidacao('As senhas não conferem.')
      return
    }

    mutation.mutate({ email: email.trim(), codigo: codigo.trim(), senha })
  }

  const erro = erroValidacao ?? (mutation.isError ? (mutation.error as Error).message : null)

  return (
    <main
      className="flex min-h-svh items-center justify-center bg-cover bg-center bg-no-repeat"
      style={{ backgroundImage: 'url(/tecnogera-login-bg.jpg)' }}
    >
      <div className="w-[min(90vw,26rem)] space-y-6 rounded-2xl bg-white p-6 shadow-xl sm:p-8">
        <img
          src="/tecnogera-login-logo.png"
          alt="Tecnogera"
          width={200}
          height={60}
          className="mx-auto mb-2 h-10 w-auto"
        />
        <div className="text-center">
          <h1 className="text-xl font-semibold text-slate-700">Definir senha</h1>
          <p className="mt-1 text-sm text-slate-500">
            Use o e-mail e o código de uso único repassados pelo administrador. O código vale por 30
            minutos.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div className="space-y-1">
            <label htmlFor="email" className="text-sm font-medium">
              E-mail
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              autoComplete="email"
              className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="codigo" className="text-sm font-medium">
              Código
            </label>
            <input
              id="codigo"
              type="text"
              value={codigo}
              onChange={(e) => setCodigo(e.target.value)}
              required
              autoComplete="one-time-code"
              className="w-full rounded-md border px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="senha" className="text-sm font-medium">
              Senha nova
            </label>
            <input
              id="senha"
              type="password"
              value={senha}
              onChange={(e) => setSenha(e.target.value)}
              required
              minLength={MIN_SENHA}
              autoComplete="new-password"
              className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="confirmar-senha" className="text-sm font-medium">
              Confirmar senha
            </label>
            <input
              id="confirmar-senha"
              type="password"
              value={confirmarSenha}
              onChange={(e) => setConfirmarSenha(e.target.value)}
              required
              autoComplete="new-password"
              className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
            />
          </div>

          {erro && (
            <p role="alert" data-testid="erro-definir-senha" className="text-sm text-destructive">
              {erro}
            </p>
          )}

          <button
            type="submit"
            disabled={mutation.isPending}
            className="w-full rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50"
          >
            {mutation.isPending ? 'Salvando…' : 'Definir senha'}
          </button>
        </form>

        <p className="text-center text-sm text-slate-500">
          <Link
            to="/login"
            className="text-brand-hover underline underline-offset-2 hover:text-brand-primary"
          >
            Voltar para o login
          </Link>
        </p>
      </div>
    </main>
  )
}
