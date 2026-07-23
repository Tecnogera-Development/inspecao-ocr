import { apiClient } from '@/api/client'
import type { EventStatusResponse } from '@/api/types'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

const ANGLES = [
  { value: 'frontal', label: 'Frontal' },
  { value: 'traseira', label: 'Traseira' },
  { value: 'latdir', label: 'Lateral direita' },
  { value: 'latesq', label: 'Lateral esquerda' },
  { value: 'teto', label: 'Teto' },
  { value: 'interior', label: 'Interior' },
]

const DAMAGE_LABEL: Record<string, string> = {
  dano_visivel: 'Dano visível',
  ausencia_item: 'Ausência de item',
  fora_padrao_visual: 'Fora do padrão',
}

const MIN_W = 1280
const MIN_H = 720

type Form = { asset_code: string; checklist_id: string; moment: string; angle: string }

async function uploadAvaria(form: Form, file: File): Promise<{ event_id: string; status: string }> {
  const csrf = await fetch('/api/v1/portal/csrf', { credentials: 'include' })
  const { token } = (await csrf.json()) as { token: string }

  const fd = new FormData()
  fd.append('foto', file)
  fd.append('asset_code', form.asset_code.trim())
  fd.append('checklist_id', form.checklist_id.trim())
  fd.append('moment', form.moment)
  fd.append('angle', form.angle)

  const r = await fetch('/api/v1/portal/avarias/upload', {
    method: 'POST',
    credentials: 'include',
    headers: { 'X-CSRF-Token': token },
    body: fd,
  })
  if (!r.ok) {
    const body = (await r.json().catch(() => ({}))) as { detail?: string }
    throw new Error(body.detail ?? `Falha no envio (HTTP ${r.status})`)
  }
  return r.json()
}

function Stepper({ step }: { step: number }) {
  const labels = ['Dados', 'Foto', 'Análise', 'Resultado']
  return (
    <ol className="flex gap-1.5 text-xs sm:gap-2">
      {labels.map((l, i) => (
        <li
          key={l}
          className={`flex-1 truncate rounded-full px-2 py-1 text-center font-medium sm:px-3 ${
            i + 1 === step
              ? 'bg-brand-primary text-white'
              : i + 1 < step
                ? 'bg-brand-tint text-brand-hover'
                : 'bg-slate-100 text-slate-400'
          }`}
        >
          {i + 1}. {l}
        </li>
      ))}
    </ol>
  )
}

export function NewAvariaPage() {
  const navigate = useNavigate()
  const fileInput = useRef<HTMLInputElement>(null)

  const [step, setStep] = useState(1)
  const [form, setForm] = useState<Form>({
    asset_code: '',
    checklist_id: '',
    moment: 'retorno',
    angle: 'frontal',
  })
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [fileError, setFileError] = useState<string | null>(null)
  const [eventId, setEventId] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)

  // Passo 3: acompanha o processamento. "Pronto" = terminou E o par existe
  // (o status vira 'done' antes do par ser criado — segue no poll até o pair_id).
  const statusQuery = useQuery({
    queryKey: ['event-status', eventId],
    queryFn: async () => {
      const r = await apiClient.GET('/api/v1/portal/avarias/events/{event_id}', {
        params: { path: { event_id: eventId ?? '' } },
      })
      if (!r.data) throw new Error('Falha ao consultar status')
      return r.data
    },
    enabled: Boolean(eventId) && step === 3,
    refetchInterval: (q) => {
      const s = q.state.data?.status
      return s === 'done' || s === 'nao_processavel' || s === 'failed' ? false : 2500
    },
  })

  const status = statusQuery.data?.status
  useEffect(() => {
    if (step === 3 && (status === 'done' || status === 'nao_processavel' || status === 'failed')) {
      setStep(4)
    }
  }, [step, status])

  function updateForm(patch: Partial<Form>) {
    setForm((f) => ({ ...f, ...patch }))
  }

  function onPickFile(f: File | null) {
    setFileError(null)
    setFile(null)
    setPreview(null)
    if (!f) return
    if (!f.type.startsWith('image/')) {
      setFileError('Selecione uma imagem (JPG ou PNG).')
      return
    }
    const url = URL.createObjectURL(f)
    const img = new Image()
    img.onload = () => {
      if (img.naturalWidth < MIN_W || img.naturalHeight < MIN_H) {
        setFileError(
          `Resolução mínima ${MIN_W}×${MIN_H}. Esta foto tem ${img.naturalWidth}×${img.naturalHeight}.`,
        )
        URL.revokeObjectURL(url)
        return
      }
      setFile(f)
      setPreview(url)
    }
    img.onerror = () => setFileError('Não foi possível ler a imagem.')
    img.src = url
  }

  async function startUpload() {
    if (!file) return
    setUploadError(null)
    setStep(3)
    try {
      const res = await uploadAvaria(form, file)
      setEventId(res.event_id)
    } catch (err) {
      setUploadError((err as Error).message)
    }
  }

  const canNext1 = form.asset_code.trim() && form.checklist_id.trim()

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Nova avaria</h1>
      <Stepper step={step} />

      {/* Passo 1 — Identificação */}
      {step === 1 && (
        <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-6">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <label className="text-sm font-medium text-slate-700">
              Ativo
              <input
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
                placeholder="ex: GER-1234"
                value={form.asset_code}
                onChange={(e) => updateForm({ asset_code: e.target.value })}
              />
            </label>
            <label className="text-sm font-medium text-slate-700">
              Checklist de entrega
              <input
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
                placeholder="ex: 276800"
                value={form.checklist_id}
                onChange={(e) => updateForm({ checklist_id: e.target.value })}
              />
            </label>
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="text-sm font-medium text-slate-700">
              Momento
              <div className="mt-1 flex gap-4">
                {['retorno', 'saida'].map((m) => (
                  <label key={m} className="flex items-center gap-1 font-normal">
                    <input
                      type="radio"
                      name="moment"
                      checked={form.moment === m}
                      onChange={() => updateForm({ moment: m })}
                    />
                    {m === 'retorno' ? 'Retorno' : 'Saída'}
                  </label>
                ))}
              </div>
            </div>
            <label className="text-sm font-medium text-slate-700">
              Ângulo
              <select
                className="mt-1 w-full rounded border border-slate-300 px-3 py-2 text-sm"
                value={form.angle}
                onChange={(e) => updateForm({ angle: e.target.value })}
              >
                {ANGLES.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="flex justify-end">
            <button
              type="button"
              disabled={!canNext1}
              onClick={() => setStep(2)}
              className="rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-40"
            >
              Continuar
            </button>
          </div>
        </div>
      )}

      {/* Passo 2 — Foto */}
      {step === 2 && (
        <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-6">
          <button
            type="button"
            onClick={() => fileInput.current?.click()}
            className="flex w-full flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed border-slate-300 p-8 text-slate-500 hover:border-brand-secondary hover:bg-brand-tint/30"
          >
            {preview ? (
              <img src={preview} alt="prévia" className="max-h-64 rounded object-contain" />
            ) : (
              <>
                <span className="text-3xl">📷</span>
                <span className="text-sm">Clique para escolher a foto de {form.moment}</span>
                <span className="text-xs text-slate-400">JPG ou PNG, mínimo 1280×720</span>
              </>
            )}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
          />
          {fileError && <p className="text-sm text-red-600">{fileError}</p>}
          <div className="flex justify-between">
            <button
              type="button"
              onClick={() => setStep(1)}
              className="rounded-md border border-slate-300 px-4 py-2 text-sm font-medium text-slate-600 hover:bg-slate-50"
            >
              Voltar
            </button>
            <button
              type="button"
              disabled={!file}
              onClick={startUpload}
              className="rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover disabled:opacity-40"
            >
              Enviar e analisar
            </button>
          </div>
        </div>
      )}

      {/* Passo 3 — Processando */}
      {step === 3 && (
        <div className="rounded-lg border border-slate-200 bg-white p-10 text-center">
          {uploadError ? (
            <>
              <p className="text-red-700">{uploadError}</p>
              <button
                type="button"
                onClick={() => setStep(2)}
                className="mt-4 rounded-md border border-slate-300 px-4 py-2 text-sm font-medium hover:bg-slate-50"
              >
                Voltar
              </button>
            </>
          ) : (
            <>
              <div className="mx-auto mb-4 h-10 w-10 animate-spin rounded-full border-4 border-brand-tint border-t-brand-primary" />
              <p className="font-medium text-slate-700">Analisando a foto…</p>
              <p className="mt-1 text-sm text-slate-500">
                Comparando com o checklist de entrega {form.checklist_id}. Isso leva alguns
                segundos.
              </p>
            </>
          )}
        </div>
      )}

      {/* Passo 4 — Resultado com a comparação completa */}
      {step === 4 && statusQuery.data && (
        <ResultCard
          data={statusQuery.data}
          onDetail={() =>
            statusQuery.data?.pair_id && navigate(`/avarias/${statusQuery.data.pair_id}`)
          }
          onNew={() => window.location.assign('/avarias/nova')}
        />
      )}
    </div>
  )
}

function imageUrl(path: string): string {
  return `/api/v1/portal/avarias/image?path=${encodeURIComponent(path)}`
}

function ComparePanel({ src, label, sub }: { src: string | null; label: string; sub?: string }) {
  return (
    <div className="flex-1 min-w-0 rounded-lg border border-slate-200 bg-white p-3 space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
          {label}
        </span>
        {sub && <span className="text-xs text-slate-400">{sub}</span>}
      </div>
      <div className="overflow-hidden rounded-md bg-slate-100">
        {src ? (
          <img
            src={src}
            alt={label}
            loading="lazy"
            decoding="async"
            className="w-full object-contain max-h-72"
          />
        ) : (
          <div className="flex h-40 items-center justify-center text-sm text-slate-400">
            sem imagem
          </div>
        )}
      </div>
    </div>
  )
}

function ResultCard({
  data,
  onDetail,
  onNew,
}: {
  data: EventStatusResponse
  onDetail: () => void
  onNew: () => void
}) {
  const processavel = data.status === 'done'
  const nonConform = Boolean(data.damage_class)

  if (!processavel) {
    return (
      <div className="space-y-4 rounded-lg border border-slate-200 bg-white p-6">
        <div className="rounded-lg border border-amber-300 bg-amber-50 p-4">
          <p className="font-medium text-amber-800">Foto não processável</p>
          <p className="text-sm text-amber-700">
            {data.validation_reason
              ? `Motivo: ${data.validation_reason}`
              : 'A foto não passou na validação técnica.'}
          </p>
        </div>
        <button
          type="button"
          onClick={onNew}
          className="rounded-md border border-brand-secondary px-4 py-2 text-sm font-medium text-brand-hover hover:bg-brand-tint"
        >
          Nova avaria
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Veredito */}
      <div
        className={`rounded-lg border p-4 ${nonConform ? 'border-red-300 bg-red-50' : 'border-green-300 bg-green-50'}`}
      >
        <span
          className={`inline-block rounded-full px-3 py-1 text-sm font-bold ${nonConform ? 'bg-red-600 text-white' : 'bg-green-600 text-white'}`}
        >
          {nonConform ? 'NÃO CONFORME' : 'CONFORME'}
        </span>
        {data.damage_class && (
          <span className="ml-3 text-sm text-red-800">
            {DAMAGE_LABEL[data.damage_class] ?? data.damage_class}
            {data.damage_severity && (
              <span className="ml-1 font-semibold">· {data.damage_severity}</span>
            )}
            {data.damage_confidence != null && (
              <span className="ml-1 text-red-600">
                · {(data.damage_confidence * 100).toFixed(0)}%
              </span>
            )}
          </span>
        )}
      </div>

      {/* Comparação entrega | retorno */}
      <div className="flex flex-col gap-4 sm:flex-row">
        <ComparePanel
          src={data.baseline_source_path ? imageUrl(data.baseline_source_path) : null}
          label="Entrega (como saiu)"
          sub={data.baseline_source_path ? `checklist ${data.checklist_id ?? ''}` : 'sem base'}
        />
        <ComparePanel
          src={data.source_path ? imageUrl(data.source_path) : null}
          label="Retorno (como voltou)"
          sub={data.asset_code}
        />
      </div>

      {/* Observação da IA */}
      {data.observation && (
        <div className="rounded-lg border border-slate-200 bg-white p-4">
          <p className="mb-1 text-sm font-semibold text-slate-700">Observação</p>
          <p className="text-sm text-slate-600">{data.observation}</p>
        </div>
      )}

      <div className="flex gap-3">
        {data.pair_id && (
          <button
            type="button"
            onClick={onDetail}
            className="rounded-md bg-brand-primary px-4 py-2 text-sm font-medium text-white hover:bg-brand-hover"
          >
            Ver detalhes
          </button>
        )}
        <button
          type="button"
          onClick={onNew}
          className="rounded-md border border-brand-secondary px-4 py-2 text-sm font-medium text-brand-hover hover:bg-brand-tint"
        >
          Nova avaria
        </button>
      </div>
    </div>
  )
}
