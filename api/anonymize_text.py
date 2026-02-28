from http.server import BaseHTTPRequestHandler
import json
import spacy
from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern, RecognizerRegistry
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# ---------------------------------------------------------------------------
# NLP + Presidio initialisation (cold-start, cached across invocations)
# ---------------------------------------------------------------------------

def _build_analyzer() -> AnalyzerEngine:
    """Build a Presidio AnalyzerEngine configured for French legal documents."""

    # --- spaCy NLP engine ---------------------------------------------------
    configuration = {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "fr", "model_name": "fr_core_news_md"}],
        "ner_model_configuration": {
            "labels_to_ignore": ["O"],
            "model_to_presidio_entity_mapping": {
                "PER": "PERSON",
                "LOC": "LOCATION",
                "ORG": "ORGANIZATION",
                "MISC": "NRP",
            }
        }
    }
    provider = NlpEngineProvider(nlp_configuration=configuration)
    nlp_engine = provider.create_engine()

    # --- Custom recognizers for French entities -----------------------------

    # French social security number (NIR) – 15 digits, sometimes with spaces
    nir_pattern = Pattern(
        name="nir_pattern",
        regex=r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b",
        score=0.9,
    )
    nir_recognizer = PatternRecognizer(
        supported_entity="FR_NIR",
        name="French NIR Recognizer",
        patterns=[nir_pattern],
        supported_language="fr",
    )

    # IBAN (French format: FR + 2 check + 23 alphanumeric)
    iban_pattern = Pattern(
        name="iban_fr_pattern",
        regex=r"\b[A-Z]{2}\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{0,3}\b",
        score=0.85,
    )
    iban_recognizer = PatternRecognizer(
        supported_entity="IBAN_CODE",
        name="IBAN Recognizer",
        patterns=[iban_pattern],
        supported_language="fr",
    )

    # French phone numbers
    phone_pattern = Pattern(
        name="fr_phone_pattern",
        regex=r"\b(?:(?:\+33|0033)\s?[1-9](?:[\s.-]?\d{2}){4}|0[1-9](?:[\s.-]?\d{2}){4})\b",
        score=0.85,
    )
    phone_recognizer = PatternRecognizer(
        supported_entity="PHONE_NUMBER",
        name="French Phone Recognizer",
        patterns=[phone_pattern],
        supported_language="fr",
    )

    # French postal codes
    postal_pattern = Pattern(
        name="fr_postal_pattern",
        regex=r"\b\d{5}\b",
        score=0.4,
    )
    postal_recognizer = PatternRecognizer(
        supported_entity="FR_POSTAL_CODE",
        name="French Postal Code Recognizer",
        patterns=[postal_pattern],
        supported_language="fr",
        context=["code postal", "cp", "adresse", "domicilié", "résidant", "habite", "situé"],
    )

    # Email recognizer for French
    email_pattern = Pattern(
        name="email_pattern",
        regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        score=0.9,
    )
    email_recognizer = PatternRecognizer(
        supported_entity="EMAIL_ADDRESS",
        name="Email Recognizer FR",
        patterns=[email_pattern],
        supported_language="fr",
    )

    # French role number (ending in /FA)
    role_pattern = Pattern(
        name="role_pattern",
        regex=r"\b[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/FA\b",
        score=0.9,
    )
    role_recognizer = PatternRecognizer(
        supported_entity="FR_NUM_ROLE",
        name="French Role Number Recognizer",
        patterns=[role_pattern],
        supported_language="fr",
    )

    # French birth date (né le ...)
    birth_date_pattern = Pattern(
        name="birth_date_pattern",
        regex=r"(?i)\bné[es]?\s+le\s+(?:\d{1,2}(?:er)?\s+(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[eé]cembre)\s+\d{4}|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}|\d{1,2}\s+\d{1,2}\s+\d{4})\b",
        score=0.9,
    )
    birth_date_recognizer = PatternRecognizer(
        supported_entity="FR_DATE_NAISSANCE",
        name="French Birth Date Recognizer",
        patterns=[birth_date_pattern],
        supported_language="fr",
    )

    # --- Registry -----------------------------------------------------------
    registry = RecognizerRegistry()
    registry.supported_languages = ["fr"]
    registry.load_predefined_recognizers(nlp_engine=nlp_engine, languages=["fr"])
    registry.add_recognizer(nir_recognizer)
    registry.add_recognizer(iban_recognizer)
    registry.add_recognizer(phone_recognizer)
    registry.add_recognizer(postal_recognizer)
    registry.add_recognizer(email_recognizer)
    registry.add_recognizer(role_recognizer)
    registry.add_recognizer(birth_date_recognizer)

    return AnalyzerEngine(
        nlp_engine=nlp_engine,
        registry=registry,
        supported_languages=["fr"],
    )


# Module-level singletons (kept warm between Vercel invocations)
analyzer = _build_analyzer()
anonymizer = AnonymizerEngine()

# Entity → label mapping for readable output
ENTITY_LABELS: dict[str, str] = {
    "PERSON": "PERSONNE",
    "LOCATION": "LIEU",
    "DATE_TIME": "DATE",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "TÉLÉPHONE",
    "IBAN_CODE": "IBAN",
    "FR_NIR": "NIR",
    "FR_POSTAL_CODE": "CODE_POSTAL",
    "NRP": "NATIONALITÉ",
    "CREDIT_CARD": "CARTE_BANCAIRE",
    "IP_ADDRESS": "ADRESSE_IP",
    "URL": "URL",
    "ORGANIZATION": "SOCIÉTÉ",
    "FR_DATE_NAISSANCE": "DATE_NAISSANCE",
    "FR_NUM_ROLE": "ROLE",
}


def anonymize_text(text: str) -> str:
    """Analyse and anonymize a French text, returning labeled placeholders."""
    results = analyzer.analyze(text=text, language="fr")
    operators = {}
    for entity_type in set(r.entity_type for r in results):
        label = ENTITY_LABELS.get(entity_type, entity_type)
        operators[entity_type] = OperatorConfig("replace", {"new_value": f"[{label}]"})
    anonymized = anonymizer.anonymize(text=text, analyzer_results=results, operators=operators)
    return anonymized.text


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 5 * 1024 * 1024:
                self._error(413, "Le texte est trop volumineux (max 5 Mo).")
                return

            body = self.rfile.read(content_length)
            data = json.loads(body.decode("utf-8"))
            text = data.get("text", "")

            if not text.strip():
                self._error(400, "Le champ 'text' est vide.")
                return

            result = anonymize_text(text)

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"anonymized": result}, ensure_ascii=False).encode("utf-8"))

        except json.JSONDecodeError:
            self._error(400, "JSON invalide.")
        except Exception as e:
            self._error(500, f"Erreur interne : {str(e)}")

    def _error(self, code: int, message: str):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"))
