"""Serviço de integração com Dropbox para download de imagens de checklist.

Encapsula o SDK oficial ``dropbox`` por trás de uma interface estável da
aplicação. As chamadas externas são tratadas como ``IntegrationError`` em
caso de falha; configuração ausente vira ``ConfigurationError``.

Nomenclatura suportada (na ordem de tentativa):

1. **Real (Tecnogera/Sisloc)**:
   ``[loc_id]_checklist_[checklist_id]_[campo]_[seq]_[DD_MM_YYYY HH_MM_SS].ext``
   Ex.: ``153269005_checklist_276800_c33_0_10_04_2026 12_16_22.jpeg``
2. **Simplificada legada** (template inicial):
   ``[checklist_id]_[campo]_[YYYY-MM-DD]_[HH-MM].ext`` — mantido para
   testes e migração futura.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from dropbox import Dropbox  # type: ignore[import-untyped]
from dropbox.exceptions import ApiError, AuthError, DropboxException  # type: ignore[import-untyped]
from dropbox.files import FileMetadata, SearchMatchV2, WriteMode  # type: ignore[import-untyped]
from dropbox.sharing import SharedLinkSettings  # type: ignore[import-untyped]

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    ConfigurationError,
    IntegrationError,
    ResourceNotFoundError,
)
from app.core.logging import get_logger
from app.models.dropbox import ImageMetadata, LocalImage, ParsedEventFilename, ParsedFilename, UploadedReport

if TYPE_CHECKING:  # pragma: no cover - apenas para tipos
    from collections.abc import Iterable

_log = get_logger(__name__)

# Apenas extensões realmente esperadas em checklists fotográficos.
_VALID_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png", ".heic", ".webp"})

# Extensões aceitas para eventos de avaria (só rasters comprimidos, sem HEIC para interop).
_AVARIAS_EXTENSIONS: frozenset[str] = frozenset({".jpg", ".jpeg", ".png"})

# Convenção: /Avarias/{asset_code}/{YYYYMMDD}_{HHMMSS}_{moment}_{angle}_{uploader}[_{checklist_id}].ext
# moment: saida|retorno; angle, uploader e checklist_id sem underscores.
# checklist_id é opcional (retrocompatível) — vincula a avaria ao checklist Sisloc de origem.
_EVENT_FILENAME_RE = re.compile(
    r"^(?P<date>\d{8})_(?P<time>\d{6})_(?P<moment>saida|retorno)"
    r"_(?P<angle>[a-zA-Z0-9]+)_(?P<uploader>[a-zA-Z0-9]+)"
    r"(?:_(?P<checklist>[a-zA-Z0-9]+))?$"
)

# Sentinela de proteção: a pasta-fonte do Dropbox (DROPBOX_ROOT_PATH) é
# **leitura-only**. Toda escrita futura (uploads em IAVS-006) deve recusar
# qualquer destino sob este caminho — ver ``DropboxService.assert_writable``.
_READ_ONLY_PATH_MARKER = "/sisloc"

# Formato real do Sisloc:
#   {loc_id}_checklist_{checklist_id}_{campo}_{seq}_{DD_MM_YYYY HH_MM_SS}.ext
_REAL_FORMAT = re.compile(
    r"^(?P<loc>\d+)_checklist_(?P<id>\d+)_(?P<campo>[a-zA-Z0-9]+)_(?P<seq>\d+)_"
    r"(?P<dia>\d{2})_(?P<mes>\d{2})_(?P<ano>\d{4})\s+"
    r"(?P<h>\d{2})_(?P<m>\d{2})_(?P<s>\d{2})$"
)
# Formato legado (template inicial / testes):
#   {checklist_id}_{campo}[_YYYY-MM-DD_HH-MM].ext
_LEGACY_TIMESTAMP_TAIL = re.compile(r"_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})$")


def parse_filename(filename: str) -> ParsedFilename:
    """Extrai checklist_id / field_name / data-hora do nome do arquivo.

    Tenta primeiro o formato real do Sisloc; se não casar, cai no legado.
    Levanta ``ValueError`` se nenhum formato reconhecer.
    """
    path = Path(filename)
    extension = path.suffix.lower()
    stem = path.stem

    real = _REAL_FORMAT.match(stem)
    if real:
        try:
            captured_at: datetime | None = datetime(
                int(real["ano"]),
                int(real["mes"]),
                int(real["dia"]),
                int(real["h"]),
                int(real["m"]),
                int(real["s"]),
            )
        except ValueError:
            captured_at = None
        return ParsedFilename(
            raw=filename,
            checklist_id=real["id"],
            field_name=real["campo"],
            captured_at=captured_at,
            extension=extension,
        )

    if "_" not in stem:
        raise ValueError(f"nome '{filename}' não segue o padrão checklist_id_campo")

    captured_at = None
    base = stem
    legacy = _LEGACY_TIMESTAMP_TAIL.search(stem)
    if legacy:
        date_part, time_part = legacy.group(1), legacy.group(2).replace("-", ":")
        try:
            captured_at = datetime.fromisoformat(f"{date_part}T{time_part}")
        except ValueError:
            captured_at = None
        base = stem[: legacy.start()]

    checklist_id, _, field_name = base.partition("_")
    if not checklist_id or not field_name:
        raise ValueError(f"nome '{filename}' sem checklist_id ou field_name")

    return ParsedFilename(
        raw=filename,
        checklist_id=checklist_id,
        field_name=field_name,
        captured_at=captured_at,
        extension=extension,
    )


def parse_event_path(dropbox_path: str, *, avarias_root: str = "/Avarias") -> ParsedEventFilename:
    """Extrai metadados de evento a partir do Dropbox path completo.

    Estrutura esperada: {avarias_root}/{asset_code}/{filename}.{ext}
    Filename: {YYYYMMDD}_{HHMMSS}_{moment}_{angle}_{uploaded_by}

    Levanta ``ValueError`` se o path não estiver sob avarias_root ou não
    tiver pelo menos dois níveis após a raiz (asset_code + filename).
    Se o filename não casar com o padrão, retorna ParsedEventFilename com
    campos opcionais como None (has_complete_metadata=False).
    """
    norm_root = ("/" + avarias_root.strip("/")).lower()
    path = Path(dropbox_path)
    extension = path.suffix.lower()
    stem = path.stem

    # Verifica que o path está sob avarias_root e tem asset_code como pasta pai
    parts = dropbox_path.replace("\\", "/").split("/")
    # parts: ['', 'Avarias', 'FROTA001', 'filename.jpg']  (index 0 é vazio)
    root_parts = norm_root.strip("/").split("/")
    n_root = len(root_parts)

    path_lower = dropbox_path.lower().replace("\\", "/")
    if not path_lower.startswith(norm_root + "/"):
        raise ValueError(f"path '{dropbox_path}' não está sob '{avarias_root}'")

    remaining = dropbox_path[len(norm_root) + 1:].split("/")
    # remaining: ['FROTA001', 'filename.jpg']
    if len(remaining) < 2 or not remaining[0]:  # noqa: SIM102
        raise ValueError(f"path '{dropbox_path}' sem pasta de ativo_code")

    asset_code = remaining[0]
    # Pastas de sistema (prefixo "_": _anotados, _gabaritos) não são ativos.
    if asset_code.startswith("_"):
        raise ValueError(f"path '{dropbox_path}' está em pasta de sistema '{asset_code}'")

    match = _EVENT_FILENAME_RE.match(stem)
    if not match:
        return ParsedEventFilename(
            raw=dropbox_path,
            asset_code=asset_code,
            extension=extension,
        )

    try:
        date_s, time_s = match["date"], match["time"]
        captured_at: datetime | None = datetime(
            int(date_s[:4]), int(date_s[4:6]), int(date_s[6:8]),
            int(time_s[:2]), int(time_s[2:4]), int(time_s[4:6]),
        )
    except ValueError:
        captured_at = None

    return ParsedEventFilename(
        raw=dropbox_path,
        asset_code=asset_code,
        captured_at=captured_at,
        moment=match["moment"],
        canonical_angle=match["angle"],
        uploaded_by=match["uploader"],
        checklist_id=match["checklist"],
        extension=extension,
    )


class DropboxService:
    """Adapter de alto nível sobre o SDK oficial do Dropbox.

    Uso típico:

        service = DropboxService(settings)
        imagens = service.list_checklist_images("276800")
        baixadas = service.download_checklist_batch("276800")
    """

    def __init__(self, settings: Settings | None = None, *, client: Dropbox | None = None) -> None:
        self._settings = settings or get_settings()
        self._client = client or self._build_client()
        self._root = self._normalize_root(self._settings.dropbox_root_path)

    @staticmethod
    def _normalize_root(raw: str) -> str:
        if not raw:
            return ""
        return "/" + raw.strip("/")

    @staticmethod
    def assert_writable(target_path: str) -> None:
        """Garante que ``target_path`` não cai na pasta-fonte read-only.

        Levanta ``ConfigurationError`` se houver tentativa de escrita sob
        ``/Sisloc`` (case-insensitive). Usar antes de qualquer ``files_upload``.
        """
        if ".." in target_path.split("/"):
            raise ConfigurationError(
                "destino de escrita inválido: path traversal ('..')",
                details={"path": target_path},
            )
        if target_path.lower().startswith(_READ_ONLY_PATH_MARKER):
            raise ConfigurationError(
                "destino de escrita inválido: pasta read-only",
                details={"path": target_path, "read_only_root": _READ_ONLY_PATH_MARKER},
            )

    def _build_client(self) -> Dropbox:
        """Constrói cliente Dropbox priorizando o modo mais robusto.

        Ordem de preferência:

        1. **Refresh token + app_key** (produção): o SDK renova
           automaticamente quando o access expira. ``app_secret`` é opcional
           (PKCE flow não usa). Se ``access_token`` também estiver presente,
           é passado como bootstrap (evita um refresh inicial desnecessário).
        2. **Access token direto** (dev/teste): só funciona enquanto válido
           (~4h). Sem renovação.

        Sem nenhum caminho viável, levanta ``ConfigurationError``.
        """
        cfg = self._settings

        if cfg.dropbox_refresh_token is not None and cfg.dropbox_app_key is not None:
            app_secret = (
                cfg.dropbox_app_secret.get_secret_value()
                if cfg.dropbox_app_secret is not None
                else None
            )
            access_token = (
                cfg.dropbox_access_token.get_secret_value()
                if cfg.dropbox_access_token is not None
                else None
            )
            return Dropbox(
                oauth2_refresh_token=cfg.dropbox_refresh_token.get_secret_value(),
                oauth2_access_token=access_token,
                app_key=cfg.dropbox_app_key.get_secret_value(),
                app_secret=app_secret,
                timeout=30,
            )

        if cfg.dropbox_access_token is not None:
            return Dropbox(
                oauth2_access_token=cfg.dropbox_access_token.get_secret_value(),
                timeout=30,
            )

        raise ConfigurationError(
            "credenciais do Dropbox ausentes",
            details={
                "missing": [
                    "DROPBOX_REFRESH_TOKEN+DROPBOX_APP_KEY",
                    "ou DROPBOX_ACCESS_TOKEN",
                ],
                "hint": (
                    "produção: defina DROPBOX_REFRESH_TOKEN + DROPBOX_APP_KEY "
                    "(app_secret opcional); teste rápido: defina DROPBOX_ACCESS_TOKEN"
                ),
            },
        )

    def list_checklist_images(self, checklist_id: str) -> list[ImageMetadata]:
        """Lista imagens cuja nomenclatura começa com ``checklist_id_``.

        Faz busca textual no Dropbox limitada ao ``dropbox_root_path`` (se
        definido). Filtra por extensões válidas e parseia a nomenclatura;
        arquivos que não casam com o padrão são ignorados (com log).
        """
        query = f"checklist_{checklist_id}"
        try:
            result = self._client.files_search_v2(query=query, options=None)
        except AuthError as exc:
            raise IntegrationError(
                "falha de autenticação no Dropbox",
                details={"reason": str(exc)},
            ) from exc
        except (ApiError, DropboxException) as exc:
            raise IntegrationError(
                "falha ao buscar imagens no Dropbox",
                details={"reason": str(exc)},
            ) from exc

        matches = list(result.matches)
        while result.has_more:
            result = self._client.files_search_continue_v2(result.cursor)
            matches.extend(result.matches)

        return self._materialize_matches(matches, checklist_id)

    def _materialize_matches(
        self,
        matches: Iterable[SearchMatchV2],
        checklist_id: str,
    ) -> list[ImageMetadata]:
        out: list[ImageMetadata] = []
        for match in matches:
            metadata = match.metadata.get_metadata()
            if not isinstance(metadata, FileMetadata):
                continue
            ext = Path(metadata.name).suffix.lower()
            if ext not in _VALID_EXTENSIONS:
                continue
            if self._root and not metadata.path_lower.startswith(self._root.lower()):
                continue
            try:
                parsed = parse_filename(metadata.name)
            except ValueError:
                _log.warning(
                    "dropbox_arquivo_ignorado",
                    motivo="nome fora do padrão",
                    nome=metadata.name,
                )
                continue
            if parsed.checklist_id != checklist_id:
                continue
            out.append(
                ImageMetadata(
                    dropbox_path=metadata.path_display or metadata.path_lower,
                    filename=metadata.name,
                    size_bytes=metadata.size,
                    parsed=parsed,
                )
            )
        out.sort(key=lambda i: i.parsed.field_name)
        return out

    def download_image(self, dropbox_path: str) -> bytes:
        """Baixa uma imagem individual do Dropbox e devolve seus bytes."""
        try:
            _, response = self._client.files_download(dropbox_path)
        except ApiError as exc:
            err = exc.error
            if hasattr(err, "is_path") and err.is_path():  # pragma: no branch
                raise ResourceNotFoundError(
                    f"arquivo não encontrado no Dropbox: {dropbox_path}",
                    details={"path": dropbox_path},
                ) from exc
            raise IntegrationError(
                "falha ao baixar arquivo do Dropbox",
                details={"path": dropbox_path, "reason": str(exc)},
            ) from exc
        except (AuthError, DropboxException) as exc:
            raise IntegrationError(
                "falha ao baixar arquivo do Dropbox",
                details={"path": dropbox_path, "reason": str(exc)},
            ) from exc
        return bytes(response.content)

    def download_checklist_batch(
        self,
        checklist_id: str,
        *,
        dest_dir: Path | None = None,
    ) -> list[LocalImage]:
        """Baixa todas as imagens de um checklist para o disco local.

        Diretório de destino: ``dest_dir`` (se passado) ou
        ``settings.dropbox_local_cache_dir / checklist_id``. Cria a pasta se
        necessário. Retorna a lista das imagens baixadas em ordem
        determinística (por ``field_name``).
        """
        target = dest_dir or Path(self._settings.dropbox_local_cache_dir) / checklist_id
        target.mkdir(parents=True, exist_ok=True)

        imagens = self.list_checklist_images(checklist_id)
        baixadas: list[LocalImage] = []
        for img in imagens:
            content = self.download_image(img.dropbox_path)
            local_path = target / Path(img.filename).name
            assert local_path.is_relative_to(target), f"path traversal detectado: {img.filename}"
            local_path.write_bytes(content)
            baixadas.append(LocalImage(metadata=img, local_path=local_path))
            _log.info(
                "dropbox_imagem_baixada",
                checklist_id=checklist_id,
                field=img.parsed.field_name,
                bytes=len(content),
            )
        _log.info(
            "dropbox_batch_concluido",
            checklist_id=checklist_id,
            total=len(baixadas),
            destino=str(target),
        )
        return baixadas

    def list_avarias_paths(self, avarias_root: str) -> list[str]:
        """Lista todos os paths de arquivo sob avarias_root recursivamente.

        Retorna somente extensões aceitas (_AVARIAS_EXTENSIONS). Ignora as
        pastas de sistema (asset_code com prefixo ``_``, ex.: ``_anotados``,
        ``_gabaritos``) — são artefatos do próprio pipeline, não eventos.
        Levanta IntegrationError em caso de falha no Dropbox.
        """
        norm_root = self._normalize_root(avarias_root)
        try:
            result = self._client.files_list_folder(norm_root, recursive=True)
        except AuthError as exc:
            raise IntegrationError(
                "falha de autenticação ao listar avarias",
                details={"reason": str(exc)},
            ) from exc
        except (ApiError, DropboxException) as exc:
            raise IntegrationError(
                "falha ao listar pasta de avarias no Dropbox",
                details={"path": norm_root, "reason": str(exc)},
            ) from exc

        paths: list[str] = []
        entries = list(result.entries)
        while result.has_more:
            result = self._client.files_list_folder_continue(result.cursor)
            entries.extend(result.entries)

        root_prefix_len = len(norm_root) + 1  # + "/"
        for entry in entries:
            if not isinstance(entry, FileMetadata):
                continue
            ext = Path(entry.name).suffix.lower()
            if ext not in _AVARIAS_EXTENSIONS:
                continue
            path = entry.path_display or entry.path_lower
            # Ignora pastas de sistema: asset_code (1º segmento sob a raiz) com prefixo "_"
            first_segment = path[root_prefix_len:].split("/", 1)[0]
            if first_segment.startswith("_"):
                continue
            paths.append(path)

        return paths

    def upload_report(
        self,
        checklist_id: str,
        pdf_bytes: bytes,
        *,
        captured_at: datetime | None = None,
    ) -> UploadedReport:
        """Publica o PDF de relatório no Dropbox em ``dropbox_reports_path``.

        Nome do arquivo: ``{checklist_id}_relatorio_{YYYY-MM-DD}.pdf``. Se já
        existir, sobrescreve (``WriteMode.overwrite``). Após o upload tenta
        criar (ou recuperar) um shared link público; falha de share **não**
        invalida o upload — ``shared_url`` volta como ``None``.

        Recusa qualquer destino sob ``/Sisloc`` via :meth:`assert_writable`.
        """
        when = captured_at or datetime.now(UTC)
        reports_root = self._normalize_root(self._settings.dropbox_reports_path)
        if not reports_root:
            raise ConfigurationError(
                "dropbox_reports_path não configurado",
                details={"setting": "DROPBOX_REPORTS_PATH"},
            )
        target = f"{reports_root}/{checklist_id}_{when:%Y%m%d_%H%M%S}.pdf"
        self.assert_writable(target)

        try:
            metadata = self._client.files_upload(pdf_bytes, target, mode=WriteMode.overwrite)
        except (AuthError, ApiError, DropboxException) as exc:
            raise IntegrationError(
                "falha ao enviar relatório ao Dropbox",
                details={"path": target, "reason": str(exc)},
            ) from exc

        shared_url = self._obter_shared_link(target)

        _log.info(
            "dropbox_relatorio_uploaded",
            checklist_id=checklist_id,
            path=target,
            bytes=metadata.size,
            tem_shared_url=shared_url is not None,
        )
        return UploadedReport(
            dropbox_path=metadata.path_display or target,
            shared_url=shared_url,
            size_bytes=metadata.size,
        )

    def upload_annotated_image(
        self,
        asset_code: str,
        pair_date: object,
        composite_bytes: bytes,
        *,
        annotated_root: str | None = None,
    ) -> str:
        """Faz upload do JPG composto saída×retorno para o Dropbox.

        Destino: ``{annotated_root}/{asset_code}_{pair_date}.jpg``
        Sobrescreve se já existir.  Retorna o dropbox_path do arquivo.
        """
        root = self._normalize_root(annotated_root or self._settings.dropbox_annotated_path)
        target = f"{root}/{asset_code}_{pair_date}.jpg"
        self.assert_writable(target)

        try:
            metadata = self._client.files_upload(
                composite_bytes, target, mode=WriteMode.overwrite
            )
        except (AuthError, ApiError, DropboxException) as exc:
            raise IntegrationError(
                "falha ao enviar imagem anotada ao Dropbox",
                details={"path": target, "reason": str(exc)},
            ) from exc

        _log.info(
            "dropbox_annotated_uploaded",
            path=target,
            bytes=metadata.size,
        )
        return metadata.path_display or target

    def upload_avaria_image(
        self,
        asset_code: str,
        filename: str,
        image_bytes: bytes,
        *,
        avarias_root: str | None = None,
    ) -> str:
        """Faz upload de uma foto de avaria (envio pelo portal).

        Destino: ``{avarias_root}/{asset_code}/{filename}``. Recusa /Sisloc via
        assert_writable. Retorna o dropbox_path do arquivo.
        """
        root = self._normalize_root(avarias_root or self._settings.dropbox_avarias_path)
        target = f"{root}/{asset_code}/{filename}"
        self.assert_writable(target)

        try:
            metadata = self._client.files_upload(image_bytes, target, mode=WriteMode.add)
        except (AuthError, ApiError, DropboxException) as exc:
            raise IntegrationError(
                "falha ao enviar foto de avaria ao Dropbox",
                details={"path": target, "reason": str(exc)},
            ) from exc

        _log.info("dropbox_avaria_uploaded", path=target, bytes=metadata.size)
        return metadata.path_display or target

    def _obter_shared_link(self, dropbox_path: str) -> str | None:
        """Cria um shared link para ``dropbox_path``; recupera se já existir.

        Retorna ``None`` em qualquer falha — share é melhor-esforço, jamais
        invalida o upload em si.
        """
        try:
            link = self._client.sharing_create_shared_link_with_settings(
                dropbox_path, settings=SharedLinkSettings()
            )
            url = getattr(link, "url", None)
            return str(url) if url else None
        except ApiError as exc:
            err = exc.error
            if (
                hasattr(err, "is_shared_link_already_exists")
                and err.is_shared_link_already_exists()
            ):
                try:
                    existentes = self._client.sharing_list_shared_links(
                        path=dropbox_path, direct_only=True
                    )
                    if existentes.links:
                        return str(existentes.links[0].url)
                except DropboxException as inner:
                    _log.warning(
                        "dropbox_shared_link_lookup_falhou",
                        path=dropbox_path,
                        reason=str(inner),
                    )
                return None
            _log.warning(
                "dropbox_shared_link_falhou",
                path=dropbox_path,
                reason=str(exc),
            )
            return None
        except (AuthError, DropboxException) as exc:
            _log.warning(
                "dropbox_shared_link_falhou",
                path=dropbox_path,
                reason=str(exc),
            )
            return None
