"""Testes do task process_event (IAVS-061/063/064/065)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.tasks.event_tasks import _get_llm_provider, process_event


# ── helpers ───────────────────────────────────────────────────────────────────


def _mock_event(status: str = "queued") -> MagicMock:
    ev = MagicMock()
    ev.id = uuid.uuid4()
    ev.status = status
    ev.source_path = "/Avarias/GER-001/test.jpg"
    ev.asset_code = "GER-001"
    ev.captured_at = datetime(2026, 6, 10, 8, 0, 0, tzinfo=UTC)
    return ev


def _valid_result() -> MagicMock:
    r = MagicMock()
    r.processable = True
    return r


def _invalid_result(reason: str = "invalid_format") -> MagicMock:
    r = MagicMock()
    r.processable = False
    r.reason.value = reason
    return r


def _classification() -> MagicMock:
    c = MagicMock()
    c.no_conformity = False
    c.to_event_columns.return_value = {
        "damage_class": None,
        "damage_confidence": None,
        "damage_severity": None,
        "angle_class": "frontal",
        "angle_confidence": None,
        "result_json": {"no_conformity": False, "classes": []},
    }
    return c


def _make_db(event: MagicMock) -> MagicMock:
    db = MagicMock()
    db.get.return_value = event
    return db


# ── _get_llm_provider ─────────────────────────────────────────────────────────


def test_get_llm_provider_sem_key_retorna_fake():
    from app.services.llm_provider import FakeLLMProvider

    settings = MagicMock()
    settings.openai_api_key = None
    settings.anthropic_api_key = None
    assert isinstance(_get_llm_provider(settings), FakeLLMProvider)


def test_get_llm_provider_openai_tem_prioridade():
    from app.services.llm_provider import OpenAIProvider

    settings = MagicMock()
    settings.openai_api_key.get_secret_value.return_value = "sk-openai"
    settings.openai_model = "gpt-4o"
    assert isinstance(_get_llm_provider(settings), OpenAIProvider)


def test_get_llm_provider_fallback_anthropic_sem_openai():
    from app.services.llm_provider import AnthropicProvider

    settings = MagicMock()
    settings.openai_api_key = None
    settings.anthropic_api_key.get_secret_value.return_value = "sk-test"
    settings.anthropic_model = "claude-haiku-4-5-20251001"
    assert isinstance(_get_llm_provider(settings), AnthropicProvider)


# ── process_event ─────────────────────────────────────────────────────────────


async def test_process_event_not_found():
    """Evento ausente no banco → retorna sem commit."""
    db = MagicMock()
    db.get.return_value = None
    session_factory = MagicMock(return_value=db)

    with (
        patch("app.tasks.event_tasks.get_settings"),
        patch("app.db.session.get_session_factory", return_value=session_factory),
    ):
        await process_event({}, str(uuid.uuid4()))

    db.commit.assert_not_called()


async def test_process_event_nao_processavel():
    """Validação técnica falha → status=nao_processavel, não classifica."""
    ev = _mock_event()
    db = _make_db(ev)
    session_factory = MagicMock(return_value=db)

    mock_dropbox = MagicMock()
    mock_dropbox.download_image.return_value = b"jpeg"
    mock_validator = MagicMock()
    mock_validator.validate_technical.return_value = _invalid_result()

    with (
        patch("app.tasks.event_tasks.get_settings"),
        patch("app.db.session.get_session_factory", return_value=session_factory),
        patch("app.services.dropbox.DropboxService", return_value=mock_dropbox),
        patch("app.services.event_validation.EventValidationService", return_value=mock_validator),
    ):
        await process_event({}, str(ev.id))

    assert ev.status == "nao_processavel"
    db.commit.assert_called()


async def test_process_event_happy_path_sem_par():
    """Caminho feliz: classificado, sem par criado → status=done."""
    ev = _mock_event()
    db = _make_db(ev)
    session_factory = MagicMock(return_value=db)

    mock_dropbox = MagicMock()
    mock_dropbox.download_image.return_value = b"jpeg"
    mock_validator = MagicMock()
    mock_validator.validate_technical.return_value = _valid_result()
    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = _classification()
    mock_pairing = MagicMock()
    mock_pairing.reconcile_event.return_value = None

    with (
        patch("app.tasks.event_tasks.get_settings"),
        patch("app.tasks.event_tasks._get_llm_provider"),
        patch("app.db.session.get_session_factory", return_value=session_factory),
        patch("app.services.dropbox.DropboxService", return_value=mock_dropbox),
        patch("app.services.event_validation.EventValidationService", return_value=mock_validator),
        patch("app.services.damage_classifier.DamageClassifier", return_value=mock_classifier),
        patch("app.services.pairing_service.PairingService", return_value=mock_pairing),
    ):
        await process_event({}, str(ev.id))

    assert ev.status == "done"
    mock_classifier.classify.assert_called_once()


async def test_process_event_par_partial_nao_gera_composto():
    """Par parcial → ArtifactService não é chamado."""
    ev = _mock_event()
    db = _make_db(ev)
    session_factory = MagicMock(return_value=db)

    mock_dropbox = MagicMock()
    mock_dropbox.download_image.return_value = b"jpeg"
    mock_validator = MagicMock()
    mock_validator.validate_technical.return_value = _valid_result()
    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = _classification()

    partial_pair = MagicMock()
    partial_pair.status = "partial"
    mock_pairing = MagicMock()
    mock_pairing.reconcile_event.return_value = partial_pair
    mock_artifact_cls = MagicMock()

    with (
        patch("app.tasks.event_tasks.get_settings"),
        patch("app.tasks.event_tasks._get_llm_provider"),
        patch("app.db.session.get_session_factory", return_value=session_factory),
        patch("app.services.dropbox.DropboxService", return_value=mock_dropbox),
        patch("app.services.event_validation.EventValidationService", return_value=mock_validator),
        patch("app.services.damage_classifier.DamageClassifier", return_value=mock_classifier),
        patch("app.services.pairing_service.PairingService", return_value=mock_pairing),
        patch("app.services.artifact_service.ArtifactService", mock_artifact_cls),
    ):
        await process_event({}, str(ev.id))

    mock_artifact_cls.return_value.generate_composite.assert_not_called()


async def test_process_event_par_completo_gera_composto():
    """Par completo → ArtifactService.generate_composite é chamado."""
    ev = _mock_event()
    db = _make_db(ev)
    session_factory = MagicMock(return_value=db)

    mock_dropbox = MagicMock()
    mock_dropbox.download_image.return_value = b"jpeg"
    mock_validator = MagicMock()
    mock_validator.validate_technical.return_value = _valid_result()
    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = _classification()

    complete_pair = MagicMock()
    complete_pair.status = "complete"
    mock_pairing = MagicMock()
    mock_pairing.reconcile_event.return_value = complete_pair
    mock_artifact_instance = MagicMock()
    mock_artifact_cls = MagicMock(return_value=mock_artifact_instance)

    with (
        patch("app.tasks.event_tasks.get_settings"),
        patch("app.tasks.event_tasks._get_llm_provider"),
        patch("app.db.session.get_session_factory", return_value=session_factory),
        patch("app.services.dropbox.DropboxService", return_value=mock_dropbox),
        patch("app.services.event_validation.EventValidationService", return_value=mock_validator),
        patch("app.services.damage_classifier.DamageClassifier", return_value=mock_classifier),
        patch("app.services.pairing_service.PairingService", return_value=mock_pairing),
        patch("app.services.artifact_service.ArtifactService", mock_artifact_cls),
    ):
        await process_event({}, str(ev.id))

    mock_artifact_instance.generate_composite.assert_called_once_with(complete_pair)


async def test_process_event_composite_falha_nao_propaga():
    """Falha no composto → warning, task não relança, status=done."""
    ev = _mock_event()
    db = _make_db(ev)
    session_factory = MagicMock(return_value=db)

    mock_dropbox = MagicMock()
    mock_dropbox.download_image.return_value = b"jpeg"
    mock_validator = MagicMock()
    mock_validator.validate_technical.return_value = _valid_result()
    mock_classifier = MagicMock()
    mock_classifier.classify.return_value = _classification()

    complete_pair = MagicMock()
    complete_pair.status = "complete"
    mock_pairing = MagicMock()
    mock_pairing.reconcile_event.return_value = complete_pair

    mock_artifact_instance = MagicMock()
    mock_artifact_instance.generate_composite.side_effect = OSError("dropbox timeout")
    mock_artifact_cls = MagicMock(return_value=mock_artifact_instance)

    with (
        patch("app.tasks.event_tasks.get_settings"),
        patch("app.tasks.event_tasks._get_llm_provider"),
        patch("app.db.session.get_session_factory", return_value=session_factory),
        patch("app.services.dropbox.DropboxService", return_value=mock_dropbox),
        patch("app.services.event_validation.EventValidationService", return_value=mock_validator),
        patch("app.services.damage_classifier.DamageClassifier", return_value=mock_classifier),
        patch("app.services.pairing_service.PairingService", return_value=mock_pairing),
        patch("app.services.artifact_service.ArtifactService", mock_artifact_cls),
    ):
        await process_event({}, str(ev.id))  # não deve levantar

    assert ev.status == "done"


async def test_process_event_excecao_marca_failed():
    """Exceção inesperada → status=failed, re-raise."""
    ev = _mock_event()
    db = _make_db(ev)
    session_factory = MagicMock(return_value=db)

    mock_dropbox = MagicMock()
    mock_dropbox.download_image.side_effect = ConnectionError("dropbox down")

    with (
        patch("app.tasks.event_tasks.get_settings"),
        patch("app.db.session.get_session_factory", return_value=session_factory),
        patch("app.services.dropbox.DropboxService", return_value=mock_dropbox),
        pytest.raises(ConnectionError),
    ):
        await process_event({}, str(ev.id))

    assert ev.status == "failed"
