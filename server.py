import os
import json
import logging
from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import io

# Import processing logic from the API files
import api.anonymize_text as target_anonymize_text
import api.anonymize_file as target_anonymize_file

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# Enable CORS for Next.js frontend
CORS(app)

# Increase max payload size to match Next.js logic
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB

@app.route('/api/anonymize_text', methods=['POST'])
def anonymize_text():
    try:
        data = request.get_json()
        if not data or 'text' not in data:
            return jsonify({"error": "Le champ 'text' est vide."}), 400

        text = data.get("text", "")
        if not text.strip():
            return jsonify({"error": "Le champ 'text' est vide."}), 400

        # Run text anonymization
        result = target_anonymize_text.anonymize_text(text)
        
        return jsonify({"anonymized": result})

    except json.JSONDecodeError:
        return jsonify({"error": "JSON invalide."}), 400
    except Exception as e:
        logger.error(f"Error in anonymize-text: {e}", exc_info=True)
        return jsonify({"error": f"Erreur interne : {str(e)}"}), 500

@app.route('/api/anonymize_file', methods=['POST'])
def anonymize_file():
    try:
        # Flask provides the parsed files in request.files
        if 'file' not in request.files:
            return jsonify({"error": "Aucun fichier valide reçu."}), 400
            
        file = request.files['file']
        if file.filename == '':
            return jsonify({"error": "Aucun fichier valide reçu."}), 400
            
        filename = file.filename
        ext = os.path.splitext(filename)[1].lower()
        file_data = file.read()
        
        # Check size (max 4.5 Mo as defined in frontend)
        if len(file_data) > 4.5 * 1024 * 1024:
            return jsonify({"error": "Le fichier est trop volumineux (max 4.5 Mo)."}), 413

        # Process based on file extension
        if ext == ".txt":
            result_data = target_anonymize_file._process_txt(file_data)
            content_type = "text/plain; charset=utf-8"
        elif ext == ".docx":
            result_data = target_anonymize_file._process_docx(file_data)
            content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif ext == ".pdf":
            result_data = target_anonymize_file._process_pdf(file_data)
            content_type = "application/pdf"
        else:
            return jsonify({"error": f"Format non supporté : {ext}. Formats acceptés : .txt, .docx, .pdf"}), 400

        # Build anonymized filename
        anon_filename = f"a-{filename}"

        # Send response exactly as frontend expects
        response = Response(
            result_data,
            mimetype=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{anon_filename}"',
                "X-Filename": anon_filename
            }
        )
        return response

    except Exception as e:
        logger.error(f"Error in anonymize-file: {e}", exc_info=True)
        return jsonify({"error": f"Erreur lors du traitement du fichier : {str(e)}"}), 500

if __name__ == '__main__':
    # Add root folder to sys.path so 'api' module can be imported correctly
    import sys
    sys.path.append(os.path.dirname(os.path.abspath(__name__)))
    
    print("Starting Flask server for local development on http://127.0.0.1:5328")
    app.run(host='127.0.0.1', port=5328, debug=True)
