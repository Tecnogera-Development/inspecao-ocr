import { Button } from '@/components/ui/button'

export function HomePage() {
  return (
    <main className="flex min-h-svh flex-col items-center justify-center bg-slate-50 text-slate-900">
      <h1 className="text-3xl font-bold tracking-tight">Portal Tecnogera</h1>
      <p className="mt-2 text-muted-foreground">Bem-vindo ao portal de operação.</p>
      <Button className="mt-6">Entrar</Button>
    </main>
  )
}
