import { apiClient } from '@/api/client'
import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

export function LoginPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
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
      } else {
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
      </div>
    </main>
  )
}
