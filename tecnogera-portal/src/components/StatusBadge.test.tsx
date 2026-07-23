import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { StatusBadge } from './StatusBadge'

describe('StatusBadge', () => {
  it('renders "Pendente" for pending status', () => {
    render(<StatusBadge status="pending" />)
    expect(screen.getByRole('status')).toHaveTextContent('Pendente')
  })

  it('renders "Executando" for running status', () => {
    render(<StatusBadge status="running" />)
    expect(screen.getByRole('status')).toHaveTextContent('Executando')
  })

  it('renders "Concluído" for done status', () => {
    render(<StatusBadge status="done" />)
    expect(screen.getByRole('status')).toHaveTextContent('Concluído')
  })

  it('renders "Falhou" for failed status', () => {
    render(<StatusBadge status="failed" />)
    expect(screen.getByRole('status')).toHaveTextContent('Falhou')
  })
})
