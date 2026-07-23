"""Seed de avarias para teste do fluxo (IAVS-068).

Gera um par saída+retorno em ``/Avarias/{ativo}/`` seguindo a convenção de nome
(com ``checklist_id`` no final) e dispara a ingestão. Serve para validar o fluxo
ponta a ponta: ingest → validação → classificação → pareamento → composto → tela.

Uso (dentro do container ``api``):

    docker compose exec api python scripts/seed_avaria.py GER-999 --checklist 276800
    docker compose exec api python scripts/seed_avaria.py GER-500 --checklist 117183887 \
        --source "/Sisloc/FILIAL SP/0_chklist_0_20220901_155446_01_09_2022 15_54_46.png"

Sem ``--source`` gera uma imagem sintética texturizada (passa resolução e foco).
Com ``--source`` reusa uma foto real de checklist do Dropbox como a evidência.

Requisitos: worker em modo fake OU chave Anthropic válida (senão a classificação
falha e o evento fica ``failed``). Use um ``asset_code`` novo a cada teste, ou
varie ``--date``, para não colidir com um par já existente (ativo + data são únicos).
"""

from __future__ import annotations

import argparse
import io
import random
import urllib.request
from datetime import datetime

from dropbox.files import WriteMode
from PIL import Image, ImageDraw

from app.core.config import get_settings
from app.services.dropbox import DropboxService

_MIN_W, _MIN_H = 1280, 720


def _synthetic(seed: int, label: str) -> Image.Image:
    """Imagem texturizada 1600x1200 — passa resolução (>=1280x720) e foco (Laplacian)."""
    rnd = random.Random(seed)
    w, h = 1600, 1200
    img = Image.new("RGB", (w, h), (40, 40, 40))
    px = img.load()
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            v = rnd.randint(0, 255)
            px[x, y] = (v, v, v)
    draw = ImageDraw.Draw(img)
    for _ in range(80):
        x0, y0 = rnd.randint(0, w - 100), rnd.randint(0, h - 100)
        draw.rectangle(
            [x0, y0, x0 + rnd.randint(20, 100), y0 + rnd.randint(20, 100)],
            outline=(rnd.randint(0, 255), rnd.randint(0, 255), rnd.randint(0, 255)),
            width=3,
        )
    draw.text((20, 20), label, fill=(255, 220, 50))
    return img


def _from_source(svc: DropboxService, src: str) -> Image.Image:
    """Baixa uma imagem real e garante resolução mínima preservando o aspecto."""
    raw = svc.download_image(src)
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    if img.width < _MIN_W or img.height < _MIN_H:
        scale = max(_MIN_W / img.width, _MIN_H / img.height)
        img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    return img


def _to_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed de par de avaria para teste do fluxo.")
    ap.add_argument("asset_code", help="Código do ativo (ex: GER-999)")
    ap.add_argument("--checklist", required=True, help="checklist_id de origem (base de comparação)")
    ap.add_argument("--angle", default="frontal", help="ângulo canônico (sem underscore)")
    ap.add_argument("--uploader", default="tech01", help="responsável pelo upload (sem underscore)")
    ap.add_argument("--source", default=None, help="Dropbox path de uma imagem real (opcional)")
    ap.add_argument("--date", default=None, help="data do par YYYY-MM-DD (default: agora)")
    ap.add_argument("--no-ingest", action="store_true", help="só sobe as fotos, não chama o ingest")
    ap.add_argument("--api", default="http://localhost:8000", help="base URL da API para o ingest")
    args = ap.parse_args()

    settings = get_settings()
    svc = DropboxService(settings)
    client = svc._client
    root = settings.dropbox_avarias_path

    img = _from_source(svc, args.source) if args.source else _synthetic(
        hash(args.asset_code) & 0xFFFF, args.asset_code
    )
    jpeg = _to_jpeg(img)
    print(f"imagem: {img.width}x{img.height} ({len(jpeg)} bytes)")

    if args.date:
        d = datetime.strptime(args.date, "%Y-%m-%d")
        stamp = d.strftime("%Y%m%d") + "_" + datetime.now().strftime("%H%M%S")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for moment in ("saida", "retorno"):
        path = f"{root}/{args.asset_code}/{stamp}_{moment}_{args.angle}_{args.uploader}_{args.checklist}.jpg"
        client.files_upload(jpeg, path, mode=WriteMode.overwrite)
        print(f"upload: {path}")

    if args.no_ingest:
        print("--no-ingest: pule o ingest ou rode manualmente POST /api/v1/events/ingest")
        return

    req = urllib.request.Request(f"{args.api}/api/v1/events/ingest", method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        print(f"ingest: {resp.read().decode()}")
    print("OK — acompanhe o worker: docker compose logs -f worker")


if __name__ == "__main__":
    main()
