"""Aucun contenu de document ne doit apparaître dans les journaux (logs)."""

import io
import json
import logging
import warnings

import pytest

import api.anonymize_file as file_module
from api import index

SECRET = "Gwendoline VANDERSTRAETEN-SECRET"


@pytest.fixture()
def client():
    return index.app.test_client()


def test_ner_library_warnings_with_document_excerpts_are_silenced():
    """spacy_huggingface_pipelines émet « Skipping annotation, {entité} … for
    doc '<100 premiers caractères du texte>' » : jamais affiché."""
    from api.nlp_engine import anonymize_text

    anonymize_text("Monsieur Jean DUPONT")  # une analyse a eu lieu (filtres en place)
    with warnings.catch_warnings(record=True) as caught:
        warnings.warn_explicit(
            f"Skipping annotation, {{'word': '{SECRET}'}} can't be aligned for doc '{SECRET} est né'",
            UserWarning, filename="token_classification.py", lineno=129,
            module="spacy_huggingface_pipelines.token_classification",
        )
        warnings.warn_explicit(
            f"Autre message futur contenant {SECRET}", UserWarning,
            filename="token_classification.py", lineno=200,
            module="spacy_huggingface_pipelines.token_classification",
        )
    assert caught == []


@pytest.mark.parametrize("stream", [False, True])
def test_internal_errors_are_logged_without_their_message(client, monkeypatch, caplog, stream):
    def boom(*_args, **_kwargs):
        raise ValueError(f"impossible de traiter « {SECRET} »")

    monkeypatch.setattr(file_module, "process_file", boom)
    caplog.set_level(logging.DEBUG)
    url = "/api/anonymize_file" + ("?stream=1" if stream else "")
    response = client.post(url, data={"file": (io.BytesIO(SECRET.encode()), "note.txt")})
    body = response.get_data(as_text=True)
    assert SECRET not in body
    assert SECRET not in caplog.text
    assert "ValueError" in caplog.text  # le type d'erreur reste diagnostiquable
    if stream:
        assert json.loads(body.splitlines()[-1])["status"] == 500


def test_text_errors_are_logged_without_their_message(client, monkeypatch, caplog):
    import api.anonymize_text as text_module

    def boom(text):
        raise RuntimeError(text)

    monkeypatch.setattr(text_module, "anonymize_with_review", boom)
    caplog.set_level(logging.DEBUG)
    response = client.post("/api/anonymize_text", json={"text": SECRET})
    assert response.status_code == 500
    assert SECRET not in caplog.text and "RuntimeError" in caplog.text


def test_normal_processing_logs_no_content(client, caplog):
    caplog.set_level(logging.DEBUG)
    response = client.post("/api/anonymize_file", data={
        "file": (io.BytesIO(f"Madame {SECRET}, née le 3 mars 1980.".encode()), "note.txt"),
    })
    assert response.status_code == 200
    assert "VANDERSTRAETEN" not in caplog.text
