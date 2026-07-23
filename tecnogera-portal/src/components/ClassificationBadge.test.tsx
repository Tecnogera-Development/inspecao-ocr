import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ClassificationBadge } from './ClassificationBadge'

describe('ClassificationBadge', () => {
  it('renders valid label', () => {
    render(<ClassificationBadge status="valid" label="Aprovado" />)
    expect(screen.getByRole('status')).toHaveTextContent('Aprovado')
  })

  it('renders inconclusive label', () => {
    render(<ClassificationBadge status="inconclusive" label="Inconclusivo" />)
    expect(screen.getByRole('status')).toHaveTextContent('Inconclusivo')
  })

  it('renders excluded label', () => {
    render(<ClassificationBadge status="excluded" label="Excluído" />)
    expect(screen.getByRole('status')).toHaveTextContent('Excluído')
  })
})
