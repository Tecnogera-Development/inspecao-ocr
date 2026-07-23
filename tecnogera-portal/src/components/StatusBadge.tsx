export type JobStatus = 'pending' | 'running' | 'done' | 'failed'

const STATUS_LABELS: Record<JobStatus, string> = {
  pending: 'Pendente',
  running: 'Executando',
  done: 'Concluído',
  failed: 'Falhou',
}

const STATUS_CLASSES: Record<JobStatus, string> = {
  pending: 'bg-gray-100 text-gray-700',
  running: 'bg-blue-50 text-blue-700',
  done: 'bg-green-100 text-green-800',
  failed: 'bg-red-100 text-red-800',
}

interface StatusBadgeProps {
  status: JobStatus
  className?: string
}

export function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  return (
    <output
      className={`rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLASSES[status]} ${className}`}
    >
      {STATUS_LABELS[status]}
    </output>
  )
}
