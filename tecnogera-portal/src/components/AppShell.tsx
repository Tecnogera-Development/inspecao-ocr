import { apiClient } from '@/api/client'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { NavLink, Navigate, Outlet, useNavigate } from 'react-router-dom'

function fetchMe() {
  return apiClient.GET('/api/v1/portal/me')
}

const navItemClass = ({ isActive }: { isActive: boolean }) =>
  `flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
    isActive
      ? 'bg-brand-tint text-brand-primary'
      : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
  }`

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <>
      <NavLink to="/avarias" className={navItemClass} onClick={onNavigate}>
        Avarias
      </NavLink>
      <NavLink to="/relatorios" className={navItemClass} onClick={onNavigate}>
        Relatórios
      </NavLink>
    </>
  )
}

export function AppShell() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)

  const { data, status } = useQuery({
    queryKey: ['me'],
    queryFn: fetchMe,
  })

  if (status === 'pending') {
    return (
      <div className="flex min-h-svh">
        <div
          data-testid="sidebar-skeleton"
          className="h-14 w-full animate-pulse bg-brand-tint md:h-svh md:w-60"
        />
      </div>
    )
  }

  if (status === 'error' || data?.response.status === 401) {
    return <Navigate to="/login" replace />
  }

  const user = data?.data

  async function handleLogout() {
    await apiClient.POST('/api/v1/portal/logout', {})
    queryClient.clear()
    navigate('/login')
  }

  return (
    <div className="flex min-h-svh flex-col bg-slate-50 md:flex-row">
      {/* Menu lateral (desktop) */}
      <aside className="hidden w-60 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="flex h-16 items-center border-b border-slate-100 px-5">
          <img
            src="/tecnogera-login-logo.png"
            alt="Tecnogera"
            width={94}
            height={28}
            loading="eager"
            decoding="async"
            className="h-7 w-auto"
          />
        </div>
        <nav className="flex-1 space-y-1 p-3">
          <NavLinks />
        </nav>
        <div className="border-t border-slate-100 p-3">
          <p className="truncate px-3 pb-2 text-xs text-slate-500" title={user?.email}>
            {user?.email}
          </p>
          <button
            type="button"
            onClick={handleLogout}
            className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Sair
          </button>
        </div>
      </aside>

      {/* Barra superior (celular) */}
      <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-slate-200 bg-white px-4 md:hidden">
        <img src="/tecnogera-login-logo.png" alt="Tecnogera" className="h-6 w-auto" />
        <button
          type="button"
          aria-label="Menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((v) => !v)}
          className="rounded-md p-2 text-slate-600 hover:bg-slate-100"
        >
          <svg
            aria-hidden="true"
            width="22"
            height="22"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          >
            {menuOpen ? (
              <path d="M6 6l12 12M6 18L18 6" strokeLinecap="round" />
            ) : (
              <path d="M4 7h16M4 12h16M4 17h16" strokeLinecap="round" />
            )}
          </svg>
        </button>
      </header>

      {/* Menu suspenso (celular) */}
      {menuOpen && (
        <div className="border-b border-slate-200 bg-white p-3 md:hidden">
          <nav className="space-y-1">
            <NavLinks onNavigate={() => setMenuOpen(false)} />
          </nav>
          <div className="mt-2 border-t border-slate-100 pt-2">
            <p className="truncate px-3 pb-2 text-xs text-slate-500">{user?.email}</p>
            <button
              type="button"
              onClick={handleLogout}
              className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
            >
              Sair
            </button>
          </div>
        </div>
      )}

      {/* Conteúdo */}
      <main className="flex-1 overflow-x-hidden p-4 md:p-8">
        <Outlet />
      </main>
    </div>
  )
}
