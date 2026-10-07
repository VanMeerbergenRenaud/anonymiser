"""
index — Application Flask (API d'anonymisation).

Servie par Gunicorn en production (``gunicorn -c gunicorn.conf.py
api.index:app``) et par le serveur de développement Flask en local
(``npm run dev:api``).

Endpoints
---------
- ``POST /api/anonymize_text`` : anonymise un texte brut (JSON ``{"text": "..."}``)
- ``POST /api/analyze_text`` : renvoie les détections (révision interactive)
- ``POST /api/anonymize_file`` : anonymise un fichier (multipart/form-data)
- ``GET  /api/health`` : état du serveur (moteur NER, OCR, limites)

Fichiers volumineux : ``POST /api/anonymize_file?stream=1``
-----------------------------------------------------------
Le traitement d'un gros document (OCR de centaines de pages) peut durer
plusieurs minutes : une requête silencieuse aussi longue serait coupée par
un proxy ou le navigateur. En mode ``stream``, la réponse est un flux NDJSON
(une ligne JSON par événement) envoyé au fil du traitement :

- ``{"event": "accepted"}`` : fichier reçu (premier événement) ;
- ``{"event": "queued"}`` : en attente d'un créneau (autres fichiers en cours) ;
- ``{"event": "progress", "stage": "ocr", "done": 3, "total": 40}`` ;
- ``{"event": "keepalive"}`` : maintient la connexion pendant un long calcul ;
- ``{"event": "done", "filename": ..., "content": ..., "ocr_images": ...,
  "ocr_skipped": ...}`` : résultat ;
- ``{"event": "error", "status": 400, "error": "..."}`` : échec.

Si le navigateur se déconnecte, le traitement est annulé (voir
``api.progress``). Sans ``stream``, l'endpoint renvoie directement le
fichier ``.txt`` (usage scripts / API).

Confidentialité
---------------
Les messages d'erreur renvoyés ne contiennent jamais de détail interne
(exceptions Python), qui pourrait inclure un extrait du document.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import threading
import time
import unicodedata
from contextlib import contextmanager
from typing import Any, Iterator, Optional
from urllib.parse import quote

from flask import Flask, Response, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

import api.anonymize_file as file_module
import api.anonymize_text as text_module
from api import ocr, settings
from api.nlp_engine import TextTooLongError, select_backend
from api.progress import Cancelled

# ---------------------------------------------------------------------------
# Configuration du logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application Flask
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = settings.MAX_REQUEST_SIZE
# Pas de tri des clés : les événements NDJSON restent lisibles dans l'ordre.
app.json.sort_keys = False

# Le frontend appelle l'API sur la même origine (Nginx en production, proxy
# Next.js en développement) : CORS n'est utile que si un autre site doit
# appeler l'API, ce qu'il faut autoriser explicitement.
_CORS_ORIGINS = [o.strip() for o in os.environ.get("ANON_CORS_ORIGINS", "").split(",") if o.strip()]
if _CORS_ORIGINS:
    from flask_cors import CORS

    CORS(app, origins=_CORS_ORIGINS,
         expose_headers=["Content-Disposition", "X-Filename", "X-Ocr-Images", "X-Ocr-Skipped"])

_KEEPALIVE_SECONDS = 10.0
"""Intervalle maximal sans donnée sur un flux (proxys : 30 à 60 s)."""

_PROGRESS_INTERVAL = 0.25
"""Intervalle minimal entre deux événements de progression d'une même étape."""

_JOB_SLOTS = threading.BoundedSemaphore(settings.MAX_PARALLEL_JOBS)
"""Créneaux de traitement de fichiers (voir ``settings.MAX_PARALLEL_JOBS``)."""


def _error(message: str, status: int) -> tuple[Response, int]:
    return jsonify({"error": message}), status


@contextmanager
def _job_slot() -> Iterator[None]:
    """Attend un créneau de traitement libre (mode synchrone)."""
    _JOB_SLOTS.acquire()
    try:
        yield
    finally:
        _JOB_SLOTS.release()


# ---------------------------------------------------------------------------
# Gestion des erreurs HTTP
# ---------------------------------------------------------------------------

@app.errorhandler(RequestEntityTooLarge)
def _too_large(_exc: RequestEntityTooLarge):
    return _error(f"Le fichier est trop volumineux (max {settings.MAX_FILE_MB} Mo).", 413)


# ---------------------------------------------------------------------------
# Texte
# ---------------------------------------------------------------------------

def _json_body() -> dict[str, Any] | None:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _read_text(data: dict[str, Any] | None) -> str | tuple[Response, int]:
    """Texte à traiter, ou réponse d'erreur (400 / 413)."""
    if data is None:
        return _error("Requête invalide : JSON attendu.", 400)
    text = data.get("text")
    if not isinstance(text, str) or not text.strip():
        return _error("Le champ 'text' est vide.", 400)
    if len(text) > settings.MAX_TEXT_CHARS:
        return _error(str(TextTooLongError(len(text))), 413)
    return text


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


@app.route("/api/anonymize_text", methods=["POST"])
def anonymize_text():
    """Anonymise un texte brut envoyé au format JSON.

    Attend un corps JSON ``{"text": "..."}`` et retourne
    ``{"anonymized": "..."}``.
    """
    text = _read_text(_json_body())
    if not isinstance(text, str):
        return text
    try:
        return jsonify({"anonymized": text_module.anonymize_text(text)})
    except Exception:  # noqa: BLE001
        logger.exception("Erreur lors de l'anonymisation d'un texte")
        return _error("Erreur interne lors de l'anonymisation.", 500)


@app.route("/api/analyze_text", methods=["POST"])
def analyze_text_detailed():
    """Analyse un texte et renvoie les détections structurées (pour révision).

    Attend ``{"text": "...", "whitelist": [...], "blocklist": [...]}`` et
    retourne ``{"text": "...", "detections": [...]}`` — le texte original
    accompagné des entités détectées, à valider/refuser dans l'interface.
    """
    data = _json_body()
    text = _read_text(data)
    if not isinstance(text, str):
        return text
    try:
        result = text_module.analyze_text_detailed(
            text, _string_list(data.get("whitelist")), _string_list(data.get("blocklist")),
        )
        return jsonify(result)
    except Exception:  # noqa: BLE001
        logger.exception("Erreur lors de l'analyse d'un texte")
        return _error("Erreur interne lors de l'analyse.", 500)


# ---------------------------------------------------------------------------
# Fichiers
# ---------------------------------------------------------------------------

def _output_filename(filename: str) -> str:
    """« C:\\dossier\\Jugement 2024.pdf » → « a-Jugement 2024.txt »."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem = os.path.splitext(base)[0]
    stem = re.sub(r"[\x00-\x1f\x7f\"*:<>?|]", "_", stem).strip(" .") or "document"
    return f"a-{stem[:150]}.txt"


def _ascii_filename(filename: str) -> str:
    """Nom de repli ASCII pour ``Content-Disposition`` (anciens clients)."""
    ascii_name = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9._ -]", "_", ascii_name) or "anonymise.txt"


def _file_response(result: file_module.ProcessedFile, output_name: str) -> Response:
    return Response(
        result.content,
        mimetype="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{_ascii_filename(output_name)}"; '
                f"filename*=UTF-8''{quote(output_name)}"
            ),
            "X-Filename": quote(output_name),
            "X-Ocr-Images": str(result.ocr_images),
            "X-Ocr-Skipped": str(result.skipped_images),
            "Cache-Control": "no-store",
        },
    )


def _ndjson(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"


_END = object()
"""Marque la fin des événements d'un traitement."""


def _stream_job(filename: str, content: bytes, output_name: str) -> Response:
    """Traite le fichier dans un thread dédié et relaie ses événements."""
    events: queue.Queue = queue.Queue()
    cancelled = threading.Event()
    last_sent: dict[str, float] = {}

    def progress(stage: str, done: int, total: int) -> None:
        if cancelled.is_set():
            raise Cancelled()
        now = time.monotonic()
        if done < total and now - last_sent.get(stage, 0.0) < _PROGRESS_INTERVAL:
            return  # limite le débit ; la fin de chaque étape est toujours envoyée
        last_sent[stage] = now
        events.put({"event": "progress", "stage": stage, "done": done, "total": total})

    def work() -> None:
        acquired = False
        try:
            if not _JOB_SLOTS.acquire(blocking=False):
                events.put({"event": "queued"})
                while not _JOB_SLOTS.acquire(timeout=1.0):
                    if cancelled.is_set():
                        return
            acquired = True
            if cancelled.is_set():
                return
            result = file_module.process_file(filename, content, progress=progress)
            events.put({
                "event": "done",
                "filename": output_name,
                "ocr_images": result.ocr_images,
                "ocr_skipped": result.skipped_images,
                "content": result.content.decode("utf-8"),
            })
        except Cancelled:
            logger.info("Traitement annulé (client déconnecté)")
        except file_module.FileProcessingError as exc:
            events.put({"event": "error", "status": exc.status, "error": str(exc)})
        except Exception:  # noqa: BLE001
            logger.exception("Erreur lors du traitement d'un fichier")
            events.put({"event": "error", "status": 500,
                        "error": "Erreur interne lors du traitement du fichier."})
        finally:
            if acquired:
                _JOB_SLOTS.release()
            events.put(_END)

    threading.Thread(target=work, name="anonymize-file", daemon=True).start()

    def generate() -> Iterator[str]:
        try:
            yield _ndjson({"event": "accepted"})
            while True:
                try:
                    event = events.get(timeout=_KEEPALIVE_SECONDS)
                except queue.Empty:
                    yield _ndjson({"event": "keepalive"})
                    continue
                if event is _END:
                    return
                yield _ndjson(event)
        finally:
            # Fin normale ou client déconnecté (GeneratorExit) : dans le
            # second cas, le traitement s'arrête à la prochaine étape.
            cancelled.set()

    return Response(
        generate(),
        mimetype="application/x-ndjson",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",  # Nginx : transmettre chaque ligne aussitôt
        },
    )


def _wants_stream() -> bool:
    return request.args.get("stream", "").lower() in {"1", "true", "yes"}


@app.route("/api/anonymize_file", methods=["POST"])
def anonymize_file():
    """Anonymise un fichier uploadé via multipart/form-data.

    Attend un champ ``file`` contenant le document (PDF, DOCX, TXT ou image).
    Retourne toujours un texte ``.txt`` anonymisé (UTF-8) : directement, ou
    dans l'événement ``done`` du flux NDJSON avec ``?stream=1``. Les
    en-têtes ``X-Ocr-Images`` / ``X-Ocr-Skipped`` (mode direct) indiquent le
    nombre d'images lues par OCR / non analysées.
    """
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        return _error("Aucun fichier valide reçu.", 400)

    filename = upload.filename
    content = upload.read()
    if len(content) > settings.MAX_FILE_SIZE:
        return _error(f"Le fichier est trop volumineux (max {settings.MAX_FILE_MB} Mo).", 413)
    if not content:
        return _error("Le fichier est vide.", 400)
    output_name = _output_filename(filename)

    if _wants_stream():
        return _stream_job(filename, content, output_name)

    try:
        with _job_slot():
            result = file_module.process_file(filename, content)
    except file_module.FileProcessingError as exc:
        return _error(str(exc), exc.status)
    except Exception:  # noqa: BLE001
        logger.exception("Erreur lors du traitement d'un fichier")
        return _error("Erreur interne lors du traitement du fichier.", 500)
    return _file_response(result, output_name)


# ---------------------------------------------------------------------------
# Santé
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    """État du backend : moteur NER actif, OCR et limites."""
    return jsonify({
        "status": "ok",
        "nlp_backend": select_backend(),
        "ocr": ocr.status(),
        "formats": list(file_module.SUPPORTED_EXTENSIONS),
        "limits": {
            "max_file_mb": settings.MAX_FILE_MB,
            "max_text_chars": settings.MAX_TEXT_CHARS,
            "max_ocr_pages": file_module.MAX_OCR_PAGES,
            "max_parallel_jobs": settings.MAX_PARALLEL_JOBS,
        },
    })


# ---------------------------------------------------------------------------
# Point d'entrée (développement local)
# ---------------------------------------------------------------------------

def _main(port: Optional[int] = None) -> None:
    port = port or settings.env_int("ANON_API_PORT", 5328, minimum=1)
    print(f"Starting Flask server for local development on http://127.0.0.1:{port}")
    # threaded : un traitement long ne bloque ni /api/health ni les autres
    # fichiers. Pas de rechargement automatique : il chargerait le modèle NER
    # (~1 Go) dans deux processus — relancer la commande après une modification.
    app.run(host="127.0.0.1", port=port, debug=True, threaded=True, use_reloader=False)


if __name__ == "__main__":
    _main()
