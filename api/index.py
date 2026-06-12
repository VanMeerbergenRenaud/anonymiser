"""
server.py — Serveur Flask pour le développement local.

Ce serveur expose les endpoints d'anonymisation de texte et de fichiers.
En production (Vercel), les fichiers ``api/*.py`` sont servis directement
comme serverless functions.

Endpoints
---------
- ``POST /api/anonymize_text`` : anonymise un texte brut (JSON ``{"text": "..."}``)
- ``POST /api/anonymize_file`` : anonymise un fichier uploadé (multipart/form-data)

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

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

import api.anonymize_text as text_module
import api.anonymize_file as file_module

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


# Correspondance extension → fonction de traitement
# Tous les formats sont désormais convertis en texte et anonymisés.
_PROCESSORS = {
    ".txt": file_module._process_txt,
    ".docx": file_module._process_docx,
    ".pdf": file_module._process_pdf,
}


@app.route("/api/anonymize_file", methods=["POST"])
def anonymize_file():
    """Anonymise un fichier uploadé via multipart/form-data.

    Attend un champ ``file`` contenant le document (PDF, DOCX ou TXT).
    Retourne toujours un fichier ``.txt`` anonymisé (UTF-8).
    """
    try:
        if "file" not in request.files:
            return jsonify({"error": "Aucun fichier valide reçu."}), 400

        file = request.files["file"]
        if file.filename == "":
            return jsonify({"error": "Aucun fichier valide reçu."}), 400

        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()
        file_data = file.read()

        # Vérification de la taille
        if len(file_data) > file_module.MAX_FILE_SIZE:
            return jsonify({"error": "Le fichier est trop volumineux (max 10 Mo)."}), 413

        # Vérification du format
        processor = _PROCESSORS.get(ext)
        if processor is None:
            return jsonify({
                "error": f"Format non supporté : {ext}. Formats acceptés : .txt, .docx, .pdf"
            }), 400

        result_data = processor(file_data)

        # Le fichier de sortie est toujours du .txt (UTF-8)
        base_name = os.path.splitext(filename)[0]
        anon_filename = f"a-{base_name}.txt"

        return Response(
            result_data,
            mimetype="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{anon_filename}"',
                "X-Filename": anon_filename,
            },
        )

    except Exception as e:
        logger.error("Error in anonymize-file: %s", e, exc_info=True)
        return jsonify({"error": f"Erreur lors du traitement du fichier : {e}"}), 500


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting Flask server for local development on http://127.0.0.1:5328")
    app.run(host="127.0.0.1", port=5328, debug=True)
