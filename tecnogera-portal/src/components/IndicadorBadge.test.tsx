import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { IndicadorBadge, indicadorAnchor } from './IndicadorBadge'

describe('IndicadorBadge', () => {
  it('usa o rótulo que veio do backend, sem traduzir', () => {
    render(
      <IndicadorBadge indicador="nao_processavel" rotulo="Não processável" data-testid="badge" />,
    )
    expect(screen.getByTestId('badge')).toHaveTextContent('Não processável')
  })

  it('distingue os três vereditos por cor', () => {
    const { rerender } = render(
      <IndicadorBadge indicador="nao_conforme" rotulo="Não conforme" data-testid="badge" />,
    )
    expect(screen.getByTestId('badge')).toHaveClass('bg-red-100')

    rerender(
      <IndicadorBadge indicador="nao_processavel" rotulo="Não processável" data-testid="badge" />,
    )
    expect(screen.getByTestId('badge')).toHaveClass('bg-amber-100')

    rerender(<IndicadorBadge indicador="conforme" rotulo="Conforme" data-testid="badge" />)
    expect(screen.getByTestId('badge')).toHaveClass('bg-green-100')
  })

  it('não pinta sem_analise como conforme — é ausência de veredito', () => {
    render(<IndicadorBadge indicador="sem_analise" rotulo="Sem análise" data-testid="badge" />)
    const badge = screen.getByTestId('badge')
    expect(badge).not.toHaveClass('bg-green-100')
    expect(badge).toHaveClass('border-dashed')
  })

  it('cai num neutro para valor desconhecido em vez de quebrar', () => {
    render(<IndicadorBadge indicador="algo_novo" rotulo="Algo novo" data-testid="badge" />)
    expect(screen.getByTestId('badge')).toHaveClass('bg-slate-100')
    expect(indicadorAnchor('algo_novo')).toBe('bg-slate-300')
  })
})
