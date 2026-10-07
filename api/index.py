"""
server.py — Serveur Flask pour le développement local.

Ce serveur expose les endpoints d'anonymisation de texte et de fichiers.
En production (Vercel), les fichiers ``api/*.py`` sont servis directement
comme serverless functions.

Endpoints
---------
- ``POST /api/anonymize_text`` : anonymise un texte brut (JSON ``{"text": "..."}``)
- ``POST /api/analyze_text`` : renvoie les détections (révision interactive)
- ``POST /api/anonymize_file`` : anonymise un fichier uploadé (multipart/form-data)
- ``GET  /api/health`` : état du serveur (moteur NER, disponibilité de l'OCR)

Usage
-----
::

    source venv/bin/activate
    python server.py

Le serveur démarre sur ``http://127.0.0.1:5328``.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.parse import quote

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

import api.anonymize_text as text_module
import api.anonymize_file as file_module
from api import ocr
from api.nlp_engine import select_backend

# ---------------------------------------------------------------------------
# Configuration du logging
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application Flask
# ---------------------------------------------------------------------------

app = Flask(__name__)
CORS(app)  # Autorise les requêtes du frontend Next.js
app.config["MAX_CONTENT_LENGTH"] = 11 * 1024 * 1024  # 11 Mo (marge pour multipart)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/anonymize_text", methods=["POST"])
def anonymize_text():
    """Anonymise un texte brut envoyé au format JSON.

    Attend un corps JSON ``{"text": "..."}`` et retourne
    ``{"anonymized": "..."}``.
    """
    try:
        data = request.get_json()
        if not data or "text" not in data:
            return jsonify({"error": "Le champ 'text' est vide."}), 400

        text = data.get("text", "")
        if not text.strip():
            return jsonify({"error": "Le champ 'text' est vide."}), 400

        result = text_module.anonymize_text(text)
        return jsonify({"anonymized": result})

    except json.JSONDecodeError:
        return jsonify({"error": "JSON invalide."}), 400
    except Exception as e:
        logger.error("Error in anonymize-text: %s", e, exc_info=True)
        return jsonify({"error": f"Erreur interne : {e}"}), 500


@app.route("/api/analyze_text", methods=["POST"])
def analyze_text_detailed():
    """Analyse un texte et renvoie les détections structurées (pour révision).

    Attend ``{"text": "...", "whitelist": [...], "blocklist": [...]}`` et
    retourne ``{"text": "...", "detections": [...]}`` — le texte original
    accompagné des entités détectées, à valider/refuser dans l'interface.
    """
    try:
        data = request.get_json()
        if not data or "text" not in data:
            return jsonify({"error": "Le champ 'text' est vide."}), 400

        text = data.get("text", "")
        if not text.strip():
            return jsonify({"error": "Le champ 'text' est vide."}), 400

        whitelist = data.get("whitelist") or []
        blocklist = data.get("blocklist") or []
        result = text_module.analyze_text_detailed(text, whitelist, blocklist)
        return jsonify(result)

    except json.JSONDecodeError:
        return jsonify({"error": "JSON invalide."}), 400
    except Exception as e:
        logger.error("Error in analyze-text: %s", e, exc_info=True)
        return jsonify({"error": f"Erreur interne : {e}"}), 500


@app.route("/api/anonymize_file", methods=["POST"])
def anonymize_file():
    """Anonymise un fichier uploadé via multipart/form-data.

    Attend un champ ``file`` contenant le document (PDF, DOCX, TXT ou image).
    Retourne toujours un fichier ``.txt`` anonymisé (UTF-8). Les en-têtes
    ``X-Ocr-Images`` / ``X-Ocr-Skipped`` indiquent le nombre d'images lues
    par OCR / non analysées.
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "Aucun fichier valide reçu."}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "Aucun fichier valide reçu."}), 400

        filename = file.filename
        file_data = file.read()

        # Vérification de la taille
        if len(file_data) > file_module.MAX_FILE_SIZE:
            return jsonify({"error": "Le fichier est trop volumineux (max 10 Mo)."}), 413

        result = file_module.process_file(filename, file_data)

        # Le fichier de sortie est toujours du .txt (UTF-8)
        base_name = os.path.splitext(filename)[0]
        anon_filename = f"a-{base_name}.txt"
        ascii_filename = anon_filename.encode("ascii", "replace").decode().replace("?", "_")

        return Response(
            result.content,
            mimetype="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_filename}"; '
                    f"filename*=UTF-8''{quote(anon_filename)}"
                ),
                "X-Filename": quote(anon_filename),
                "X-Ocr-Images": str(result.ocr_images),
                "X-Ocr-Skipped": str(result.skipped_images),
                "Access-Control-Expose-Headers": "X-Filename, X-Ocr-Images, X-Ocr-Skipped",
            },
        )

    except file_module.FileProcessingError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        logger.error("Error in anonymize-file: %s", e, exc_info=True)
        return jsonify({"error": f"Erreur lors du traitement du fichier : {e}"}), 500


@app.route("/api/health", methods=["GET"])
def health():
    """État du backend : moteur NER actif et disponibilité de l'OCR."""
    return jsonify({
        "status": "ok",
        "nlp_backend": select_backend(),
        "ocr": ocr.status(),
        "formats": list(file_module.SUPPORTED_EXTENSIONS),
    })


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting Flask server for local development on http://127.0.0.1:5328")
    app.run(host="127.0.0.1", port=5328, debug=True)
