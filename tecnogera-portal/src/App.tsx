import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AvariaDetailPage } from './pages/AvariaDetailPage'
import { AvariasPage } from './pages/AvariasPage'
import { ChecklistDetailPage } from './pages/ChecklistDetailPage'
import { ChecklistsPage } from './pages/ChecklistsPage'
import { DashboardPage } from './pages/DashboardPage'
import { DefinirSenhaPage } from './pages/DefinirSenhaPage'
import { JobDetailPage } from './pages/JobDetailPage'
import { LoginPage } from './pages/LoginPage'
import { NewAvariaPage } from './pages/NewAvariaPage'
import { RunPage } from './pages/RunPage'
import { UsuariosPage } from './pages/UsuariosPage'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: false,
    },
  },
})

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  // Pública, fora do AppShell autenticado — quem chega aqui não tem sessão
  // (ticket usuarios-portal/04).
  { path: '/definir-senha', element: <DefinirSenhaPage /> },
  {
    path: '/',
    element: <AppShell />,
    children: [
      // Avarias é a home (foco do produto)
      // Home é a fila de checklists (decisão do dev, 2026-08-03) — /avarias
      // saiu do menu e deixou de ser a porta de entrada, mas segue alcançável
      // por URL direta.
      { index: true, element: <Navigate to="/checklists" replace /> },
      { path: 'avarias', element: <AvariasPage /> },
      { path: 'avarias/nova', element: <NewAvariaPage /> },
      { path: 'avarias/:id', element: <AvariaDetailPage /> },
      // Checklists c54–c57 — fila de trabalho do operador (tela nova, não adapta /avarias)
      { path: 'checklists', element: <ChecklistsPage /> },
      { path: 'checklists/:id', element: <ChecklistDetailPage /> },
      // Relatórios Sisloc→PDF (secundário)
      { path: 'relatorios', element: <DashboardPage /> },
      { path: 'run', element: <RunPage /> },
      { path: 'jobs/:id', element: <JobDetailPage /> },
      // Só existe no menu para admin; um operador que force a URL recebe o
      // 403 do backend na própria tela (ticket usuarios-portal/04).
      { path: 'usuarios', element: <UsuariosPage /> },
    ],
  },
])

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  )
}
