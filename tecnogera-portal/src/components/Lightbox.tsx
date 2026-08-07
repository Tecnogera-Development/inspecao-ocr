import { X } from 'lucide-react'
import { useEffect, useRef } from 'react'

/**
 * Sobreposição para ver uma foto do checklist ampliada.
 *
 * O operador julga dano em foto de conjunto: no grid a imagem cabe em ~256px de
 * altura, o que basta para "tem algo ali" e não basta para "é ferrugem ou
 * sombra". Sem ampliar, a saída era abrir a imagem em aba nova — que perde a
 * sessão do proxy autenticado e mostra um erro.
 *
 * Usa `<dialog>` nativo em vez de uma div com `role="dialog"`: foco presoquando
 * aberto, Esc e `::backdrop` vêm do navegador, e são exatamente as três coisas
 * que costumam sair erradas numa implementação à mão.
 */
export function Lightbox({
  src,
  alt,
  onClose,
}: {
  src: string
  alt: string
  onClose: () => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)

  useEffect(() => {
    const el = dialogRef.current
    if (!el) return
    // jsdom não implementa showModal em toda versão; sem ele o `open` já
    // renderiza o conteúdo, que é o suficiente para o teste enxergar.
    if (typeof el.showModal === 'function') el.showModal()
    else el.setAttribute('open', '')

    // O Esc do <dialog> nativo dispara `cancel`, já tratado no JSX. Este
    // listener existe para o caminho sem modal nativo (jsdom, e navegador
    // antigo sem showModal), onde `cancel` nunca chega. Fechar duas vezes é
    // inofensivo — o estado do pai já é `false` na segunda.
    function aoTeclar(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', aoTeclar)

    return () => {
      document.removeEventListener('keydown', aoTeclar)
      if (typeof el.close === 'function' && el.open) el.close()
    }
  }, [onClose])

  return (
    // Fechar clicando no fundo é afordância de mouse; a contrapartida de
    // teclado é o Esc, que já fecha (evento `cancel` do <dialog> nativo +
    // listener no useEffect). Um onKeyDown aqui seria só para calar a regra.
    // biome-ignore lint/a11y/useKeyWithClickEvents: ver comentário acima
    <dialog
      ref={dialogRef}
      aria-label={alt}
      data-testid="lightbox"
      onClose={onClose}
      onCancel={onClose}
      onClick={(e) => {
        // Clique no backdrop tem como alvo o próprio <dialog>; clique na
        // imagem tem como alvo os filhos, e não deve fechar.
        if (e.target === e.currentTarget) onClose()
      }}
      className="max-h-full max-w-full bg-transparent p-0 backdrop:bg-black/80"
    >
      <div className="relative flex max-h-[90vh] max-w-[90vw] flex-col items-center gap-3 p-4">
        <button
          type="button"
          onClick={onClose}
          aria-label="Fechar"
          className="absolute right-2 top-2 rounded-full bg-white/90 p-2 text-slate-700 shadow hover:bg-white"
        >
          <X className="h-5 w-5" aria-hidden="true" />
        </button>

        <img
          src={src}
          alt={alt}
          className="max-h-[80vh] max-w-full object-contain"
          data-testid="lightbox-img"
        />
        <p className="text-sm text-white/90">{alt}</p>
      </div>
    </dialog>
  )
}
