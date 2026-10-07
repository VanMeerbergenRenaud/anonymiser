"""Tests de l'API Flask : validation, erreurs, flux NDJSON, annulation."""

import io
import json
import threading
import time

import pytest

import api.anonymize_file as file_module
from api import index, settings
from api.progress import Cancelled


@pytest.fixture()
def client():
    return index.app.test_client()


def events(response) -> list[dict]:
    return [json.loads(line) for line in response.get_data(as_text=True).splitlines() if line]


def upload(name: str, content: bytes) -> dict:
    return {"file": (io.BytesIO(content), name)}


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------

def test_health_reports_limits(client):
    data = client.get("/api/health").get_json()
    assert data["status"] == "ok"
    assert data["limits"]["max_file_mb"] == settings.MAX_FILE_MB == 100


@pytest.mark.parametrize("body, kwargs", [
    ("pas du json", {"content_type": "application/json"}),
    (None, {"json": ["liste"]}),
    (None, {"json": {"text": 12}}),
    (None, {"json": {"text": "   "}}),
])
def test_invalid_text_requests_are_rejected(client, body, kwargs):
    response = client.post("/api/anonymize_text", data=body, **kwargs)
    assert response.status_code == 400
    assert "error" in response.get_json()


def test_text_too_long(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_TEXT_CHARS", 1_000)
    response = client.post("/api/anonymize_text", json={"text": "a" * 1_001})
    assert response.status_code == 413
    assert "trop long" in response.get_json()["error"]


def test_anonymize_text(client):
    response = client.post("/api/anonymize_text", json={
        "text": "Monsieur Jean DUPONT, R.G. n° 21/123/A, GSM 0475/12.34.56, RN 85.07.30-033.28",
    })
    result = response.get_json()["anonymized"]
    assert "DUPONT" not in result and "21/123/A" not in result
    assert "0475" not in result and "85.07.30" not in result
    assert "R.G. n° [NUMÉRO_RÔLE]" in result and "RN [REGISTRE_NATIONAL]" in result


def test_analyze_text_ignores_invalid_lists(client):
    response = client.post("/api/analyze_text", json={
        "text": "Madame Claire DUBOIS", "whitelist": "pas une liste", "blocklist": [1, "Claire"],
    })
    assert response.status_code == 200
    assert response.get_json()["detections"]


# ---------------------------------------------------------------------------
# Fichiers (mode direct)
# ---------------------------------------------------------------------------

def test_file_download_headers(client):
    response = client.post("/api/anonymize_file", data=upload(
        "Décision n°1.txt", "Madame Claire DUBOIS, NN 85.07.30-033.28".encode(),
    ))
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Filename"] == "a-D%C3%A9cision%20n%C2%B01.txt"
    assert 'filename="a-Decision n1.txt"' in response.headers["Content-Disposition"]
    assert "DUBOIS" not in response.get_data(as_text=True)


@pytest.mark.parametrize("name, expected", [
    ('C:\\Users\\x\\Jugé "final".pdf', "a-Jugé _final_.txt"),
    ("../../etc/passwd", "a-passwd.txt"),
    ("a\r\nb.pdf", "a-a__b.txt"),
    ("   .pdf", "a-document.txt"),
])
def test_output_filename_is_sanitized(name, expected):
    assert index._output_filename(name) == expected


def test_empty_and_missing_files(client):
    assert client.post("/api/anonymize_file", data={}).status_code == 400
    response = client.post("/api/anonymize_file", data=upload("vide.txt", b""))
    assert response.status_code == 400
    assert response.get_json()["error"] == "Le fichier est vide."


def test_request_too_large_returns_json(client, monkeypatch):
    monkeypatch.setitem(index.app.config, "MAX_CONTENT_LENGTH", 1_000)
    response = client.post("/api/anonymize_file", data=upload("gros.txt", b"x" * 5_000))
    assert response.status_code == 413
    assert "100 Mo" in response.get_json()["error"]


def test_document_too_long_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_TEXT_CHARS", 1_000)
    response = client.post("/api/anonymize_file", data=upload("long.txt", b"mot " * 1_000))
    assert response.status_code == 413
    assert "trop long" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# Fichiers (flux NDJSON)
# ---------------------------------------------------------------------------

def test_stream_reports_progress_then_result(client):
    response = client.post("/api/anonymize_file?stream=1", data=upload(
        "note.txt", "Monsieur Pierre DELVAUX, GSM 0475/12.34.56".encode(),
    ))
    assert response.status_code == 200
    assert response.mimetype == "application/x-ndjson"
    assert response.headers["X-Accel-Buffering"] == "no"
    stream = events(response)
    assert stream[0] == {"event": "accepted"}
    assert any(e["event"] == "progress" and e["stage"] == "analyze" for e in stream)
    done = stream[-1]
    assert done["event"] == "done" and done["filename"] == "a-note.txt"
    assert "DELVAUX" not in done["content"] and "0475" not in done["content"]


def test_stream_reports_user_errors(client):
    stream = events(client.post("/api/anonymize_file?stream=1", data=upload("ancien.doc", b"x")))
    assert stream[-1]["event"] == "error"
    assert stream[-1]["status"] == 400
    assert ".docx" in stream[-1]["error"]


def test_stream_hides_internal_errors(client, monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("extrait confidentiel du document")

    monkeypatch.setattr(file_module, "process_file", boom)
    stream = events(client.post("/api/anonymize_file?stream=1", data=upload("a.txt", b"x")))
    assert stream[-1] == {
        "event": "error", "status": 500, "error": "Erreur interne lors du traitement du fichier.",
    }


def test_stream_sends_keepalives(client, monkeypatch):
    monkeypatch.setattr(index, "_KEEPALIVE_SECONDS", 0.05)

    def slow(filename, content, progress=None):
        time.sleep(0.3)
        return file_module.ProcessedFile(b"ok")

    monkeypatch.setattr(file_module, "process_file", slow)
    stream = events(client.post("/api/anonymize_file?stream=1", data=upload("a.txt", b"x")))
    assert any(e["event"] == "keepalive" for e in stream)
    assert stream[-1]["event"] == "done" and stream[-1]["content"] == "ok"


def test_client_disconnect_cancels_processing(client, monkeypatch):
    started, stopped = threading.Event(), threading.Event()

    def endless(filename, content, progress=None):
        started.set()
        try:
            for i in range(10_000):
                progress("analyze", i, 10_000)
                time.sleep(0.01)
        except Cancelled:
            stopped.set()
            raise
        return file_module.ProcessedFile(b"")

    monkeypatch.setattr(file_module, "process_file", endless)
    response = client.post("/api/anonymize_file?stream=1", data=upload("a.txt", b"x"), buffered=False)
    chunks = iter(response.response)
    assert json.loads(next(chunks)) == {"event": "accepted"}
    assert started.wait(5)
    response.close()  # le navigateur ferme la connexion
    assert stopped.wait(5), "le traitement aurait dû être annulé"


def test_jobs_wait_for_a_free_slot(client, monkeypatch):
    monkeypatch.setattr(index, "_JOB_SLOTS", threading.BoundedSemaphore(1))
    release = threading.Event()

    def blocking(filename, content, progress=None):
        release.wait(5)
        return file_module.ProcessedFile(b"ok")

    monkeypatch.setattr(file_module, "process_file", blocking)
    first = client.post("/api/anonymize_file?stream=1", data=upload("a.txt", b"x"), buffered=False)
    first_events = iter(first.response)
    next(first_events)  # accepted : le 1er fichier occupe l'unique créneau
    time.sleep(0.2)
    second = client.post("/api/anonymize_file?stream=1", data=upload("b.txt", b"x"), buffered=False)
    second_events = iter(second.response)
    assert json.loads(next(second_events)) == {"event": "accepted"}
    assert json.loads(next(second_events)) == {"event": "queued"}
    release.set()
    assert json.loads(list(first_events)[-1])["event"] == "done"
    assert json.loads(list(second_events)[-1])["event"] == "done"
