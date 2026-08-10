/**
 * Vocabulário visual do indicador de um checklist (ticket mvp-c54-c57/09).
 *
 * São TRÊS vereditos — `conforme`, `nao_conforme`, `nao_processavel` — mais
 * `sem_analise`, que NÃO é veredito: é o job criado e ainda não processado.
 * Por isso `sem_analise` nunca recebe a cor de "conforme"; ele é neutro e
 * tracejado, para ler como ausência de laudo e não como laudo limpo.
 *
 * O texto vem sempre do `*_rotulo` do backend — este módulo não traduz nada,
 * senão o vocabulário passaria a viver em dois repositórios.
 */

const INDICADOR_CLASSES: Record<string, string> = {
  nao_conforme: 'border border-red-300 bg-red-100 text-red-900',
  nao_processavel: 'border border-amber-300 bg-amber-100 text-amber-900',
  conforme: 'border border-green-300 bg-green-100 text-green-900',
  sem_analise: 'border border-dashed border-slate-400 bg-slate-50 text-slate-600',
}

const INDICADOR_GLYPHS: Record<string, string> = {
  nao_conforme: '●',
  nao_processavel: '▲',
  conforme: '○',
  sem_analise: '◷',
}

/** Faixa de âncora à esquerda da linha — o não conforme precisa saltar sem depender do pill. */
export const INDICADOR_ANCHOR: Record<string, string> = {
  nao_conforme: 'bg-red-600',
  nao_processavel: 'bg-amber-500',
  conforme: 'bg-transparent',
  sem_analise: 'bg-slate-300',
}

const FALLBACK_CLASS = 'border border-slate-300 bg-slate-100 text-slate-700'

export function indicadorClasses(indicador: string): string {
  return INDICADOR_CLASSES[indicador] ?? FALLBACK_CLASS
}

export function indicadorAnchor(indicador: string): string {
  return INDICADOR_ANCHOR[indicador] ?? 'bg-slate-300'
}

interface IndicadorBadgeProps {
  indicador: string
  /** `indicador_rotulo` do backend — já em português de tela. */
  rotulo: string
  className?: string
  'data-testid'?: string
}

export function IndicadorBadge({
  indicador,
  rotulo,
  className = '',
  'data-testid': testId,
}: IndicadorBadgeProps) {
  return (
    <span
      data-testid={testId}
      data-indicador={indicador}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${indicadorClasses(indicador)} ${className}`}
    >
      <span aria-hidden="true">{INDICADOR_GLYPHS[indicador] ?? '·'}</span>
      {rotulo}
    </span>
  )
}
