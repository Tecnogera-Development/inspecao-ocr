import type { HTMLAttributes } from 'react'

export type ClassificationStatus = 'valid' | 'inconclusive' | 'excluded'

const CLASSIFICATION_CLASSES: Record<ClassificationStatus, string> = {
  valid: 'bg-green-100 text-green-800',
  inconclusive: 'bg-yellow-100 text-yellow-800',
  excluded: 'bg-gray-100 text-gray-700',
}

interface ClassificationBadgeProps extends HTMLAttributes<HTMLOutputElement> {
  status: ClassificationStatus
  label: string
}

export function ClassificationBadge({
  status,
  label,
  className = '',
  ...rest
}: ClassificationBadgeProps) {
  return (
    <output
      className={`rounded px-1.5 py-0.5 text-xs font-medium ${CLASSIFICATION_CLASSES[status]} ${className}`}
      {...rest}
    >
      {label}
    </output>
  )
}
