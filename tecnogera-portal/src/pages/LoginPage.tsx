import { apiClient, clearCsrfToken } from '@/api/client'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  // Sinalizado pela DefinirSenhaPage após sucesso (ticket usuarios-portal/04)
  // — fecha o loop "defini a senha, e agora?" sem inventar texto de domínio.
  const senhaDefinida = Boolean(
    (location.state as { senhaDefinida?: boolean } | null)?.senhaDefinida,
  )
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setLoading(true)
    try {
      const { response } = await apiClient.POST('/api/v1/portal/login', {
        body: { email, password },
      })
      if (response.status === 401) {
        setError('Email ou senha inválidos')
      } else if (response.status === 429) {
        setError('Muitas tentativas. Aguarde alguns minutos e tente novamente.')
      } else {
        // O backend gera um csrf_token novo a cada login. Sem limpar aqui, o
        // cache do módulo guarda o token da sessão anterior e a primeira ação
        // de escrita depois de reentrar falha com "CSRF token inválido".
        clearCsrfToken()
        // Descarta o cache de auth (['me'] pode ter um 401 fresco de antes do login,
        // dentro do staleTime) para o AppShell refetchar com a sessão nova.
        queryClient.removeQueries({ queryKey: ['me'] })
        navigate('/')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <main
      className="flex min-h-svh items-center justify-center bg-cover bg-center bg-no-repeat"
      style={{ backgroundImage: 'url(/tecnogera-login-bg.jpg)' }}
    >
      <div className="w-[min(90vw,24rem)] space-y-6 rounded-2xl bg-white p-6 shadow-xl sm:p-8">
        <img
          src="/tecnogera-login-logo.png"
          alt="Tecnogera"
          width={200}
          height={60}
          className="mx-auto mb-2 h-10 w-auto"
        />
        <h1 className="text-center text-xl font-semibold text-slate-700">
          Entre com seu email corporativo
        </h1>
        {senhaDefinida && (
          <output
            data-testid="aviso-senha-definida"
            className="block rounded-md bg-green-50 px-3 py-2 text-center text-sm text-green-700"
          >
            Senha definida com sucesso. Entre com suas novas credenciais.
          </output>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1">
            <label htmlFor="email" className="text-sm font-medium">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="senha" className="text-sm font-medium">
              Senha
            </label>
            <input
              id="senha"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50"
          >
            Entrar
          </button>
        </form>
        {/*
         * Conta nova nasce SEM senha: o usuário recebe um código do
         * administrador e define a senha aqui. Sem este link, ele tenta o
         * login, erra cinco vezes e cai no bloqueio por tentativas, sem
         * nunca ter tido uma senha para acertar.
         */}
        <p className="text-center text-sm text-slate-600">
          Primeiro acesso ou recebeu um código?{' '}
          <Link to="/definir-senha" className="font-medium text-brand-primary underline">
            Definir senha
          </Link>
        </p>
      </div>
    </main>
  )
}
