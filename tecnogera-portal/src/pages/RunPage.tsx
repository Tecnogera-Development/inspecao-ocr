import { apiClient } from '@/api/client'
import { useMutation } from '@tanstack/react-query'
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

const NUMERIC_RE = /^\d+$/

async function submitRun(checklistId: string): Promise<{ job_id: string; status: string }> {
  const result = await apiClient.POST('/api/v1/portal/run', {
    body: { checklist_id: checklistId },
  })
  if (result.response.status === 422) {
    const body = result.error as { detail?: string } | undefined
    throw Object.assign(new Error(body?.detail ?? 'Dados inválidos'), {
      status: 422,
      detail: body?.detail,
    })
  }
  if (!result.data) {
    throw Object.assign(new Error('Erro ao criar job — tente novamente'), {
      status: result.response.status,
    })
  }
  return result.data
}

export function RunPage() {
  const navigate = useNavigate()
  const [checklistId, setChecklistId] = useState('')
  const [validationError, setValidationError] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const mutation = useMutation({
    mutationFn: submitRun,
    onSuccess: () => {
      navigate('/relatorios')
    },
    onError: (err: Error & { status?: number; detail?: string }) => {
      if (err.status === 422) {
        setErrorMessage(err.detail ?? err.message)
      } else {
        setErrorMessage('Erro ao criar job — tente novamente')
      }
    },
  })

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setValidationError(null)
    setErrorMessage(null)

    if (!checklistId || !NUMERIC_RE.test(checklistId)) {
      setValidationError('checklist_id deve ser numérico')
      return
    }

    mutation.mutate(checklistId)
  }

  const isPending = mutation.isPending

  return (
    <div className="mx-auto max-w-md space-y-6 pt-8">
      <h1 className="text-2xl font-bold text-slate-900">Nova análise</h1>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="space-y-1">
          <label htmlFor="checklist_id" className="block text-sm font-medium text-slate-700">
            checklist_id
          </label>
          <input
            id="checklist_id"
            type="text"
            inputMode="numeric"
            value={checklistId}
            onChange={(e) => {
              setChecklistId(e.target.value)
              setValidationError(null)
            }}
            className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-primary focus:ring-offset-2"
            placeholder="ex: 12345"
          />
          {validationError && <p className="text-sm text-red-600">{validationError}</p>}
        </div>

        {errorMessage && (
          <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {errorMessage}
          </div>
        )}

        <button
          type="submit"
          disabled={isPending}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-50"
        >
          {isPending && (
            <svg
              className="h-4 w-4 animate-spin"
              viewBox="0 0 24 24"
              fill="none"
              aria-hidden="true"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
              />
            </svg>
          )}
          Executar
        </button>
      </form>
    </div>
  )
}
