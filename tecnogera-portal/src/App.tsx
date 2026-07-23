import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Navigate, RouterProvider, createBrowserRouter } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { AvariaDetailPage } from './pages/AvariaDetailPage'
import { AvariasPage } from './pages/AvariasPage'
import { DashboardPage } from './pages/DashboardPage'
import { JobDetailPage } from './pages/JobDetailPage'
import { LoginPage } from './pages/LoginPage'
import { NewAvariaPage } from './pages/NewAvariaPage'
import { RunPage } from './pages/RunPage'

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
  {
    path: '/',
    element: <AppShell />,
    children: [
      // Avarias é a home (foco do produto)
      { index: true, element: <Navigate to="/avarias" replace /> },
      { path: 'avarias', element: <AvariasPage /> },
      { path: 'avarias/nova', element: <NewAvariaPage /> },
      { path: 'avarias/:id', element: <AvariaDetailPage /> },
      // Relatórios Sisloc→PDF (secundário)
      { path: 'relatorios', element: <DashboardPage /> },
      { path: 'run', element: <RunPage /> },
      { path: 'jobs/:id', element: <JobDetailPage /> },
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
