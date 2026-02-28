"""
nlp_engine — Moteur NLP partagé pour l'anonymisation de textes français.

Ce module centralise toute la configuration Presidio / spaCy utilisée par
les endpoints ``anonymize_text`` et ``anonymize_file``.  Il est chargé une
seule fois au démarrage du serveur et les instances sont ensuite réutilisées
(singletons au niveau du module).

Entités détectées
-----------------
- PERSON / PER → [PERSONNE]
- LOCATION / LOC → [LIEU]
- ORGANIZATION / ORG → [SOCIÉTÉ]
- DATE_TIME → [DATE]
- EMAIL_ADDRESS → [EMAIL]
- PHONE_NUMBER → [TÉLÉPHONE]
- IBAN_CODE → [IBAN]
- FR_NIR (n° sécu) → [NIR]
- FR_POSTAL_CODE → [CODE_POSTAL]
- FR_DATE_NAISSANCE → [DATE_NAISSANCE]
- FR_NUM_ROLE (n° de rôle /FA) → [ROLE]
- NRP → [NATIONALITÉ]
- CREDIT_CARD → [CARTE_BANCAIRE]
- IP_ADDRESS → [ADRESSE_IP]
- URL → [URL]
"""

from __future__ import annotations

from presidio_analyzer import (
    AnalyzerEngine,
    PatternRecognizer,
    Pattern,
    RecognizerRegistry,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# ---------------------------------------------------------------------------
# Mapping entité Presidio → label français lisible
# ---------------------------------------------------------------------------

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


def get_label(entity_type: str) -> str:
    """Retourne le label français pour un type d'entité Presidio."""
    return ENTITY_LABELS.get(entity_type, entity_type)


# ---------------------------------------------------------------------------
# Construction de l'AnalyzerEngine
# ---------------------------------------------------------------------------

def _build_analyzer() -> AnalyzerEngine:
    """Construit un ``AnalyzerEngine`` configuré pour le français juridique.

    Inclut les recognizers prédéfinis de Presidio **plus** des recognizers
    custom pour les entités françaises courantes (NIR, IBAN, téléphone,
    code postal, date de naissance, numéro de rôle, e-mail).
    """
    # --- Moteur spaCy -------------------------------------------------------
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
            },
        },
    }
    provider = NlpEngineProvider(nlp_configuration=configuration)
    nlp_engine = provider.create_engine()

    # --- Recognizers custom -------------------------------------------------

    custom_recognizers = [
        # Numéro de sécurité sociale français (NIR) — 15 chiffres
        PatternRecognizer(
            supported_entity="FR_NIR",
            name="French NIR Recognizer",
            patterns=[
                Pattern(
                    name="nir_pattern",
                    regex=r"\b[12]\s?\d{2}\s?\d{2}\s?\d{2}\s?\d{3}\s?\d{3}\s?\d{2}\b",
                    score=0.9,
                ),
            ],
            supported_language="fr",
        ),
        # IBAN (format FR + 2 contrôle + 23 alphanumérique)
        PatternRecognizer(
            supported_entity="IBAN_CODE",
            name="IBAN Recognizer",
            patterns=[
                Pattern(
                    name="iban_fr_pattern",
                    regex=r"\b[A-Z]{2}\d{2}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\s?\d{0,3}\b",
                    score=0.85,
                ),
            ],
            supported_language="fr",
        ),
        # Numéro de téléphone français
        PatternRecognizer(
            supported_entity="PHONE_NUMBER",
            name="French Phone Recognizer",
            patterns=[
                Pattern(
                    name="fr_phone_pattern",
                    regex=r"\b(?:(?:\+33|0033)\s?[1-9](?:[\s.-]?\d{2}){4}|0[1-9](?:[\s.-]?\d{2}){4})\b",
                    score=0.85,
                ),
            ],
            supported_language="fr",
        ),
        # Code postal français (5 chiffres, score faible sauf si contexte)
        PatternRecognizer(
            supported_entity="FR_POSTAL_CODE",
            name="French Postal Code Recognizer",
            patterns=[
                Pattern(
                    name="fr_postal_pattern",
                    regex=r"\b\d{5}\b",
                    score=0.4,
                ),
            ],
            supported_language="fr",
            context=[
                "code postal", "cp", "adresse",
                "domicilié", "résidant", "habite", "situé",
            ],
        ),
        # Adresse e-mail
        PatternRecognizer(
            supported_entity="EMAIL_ADDRESS",
            name="Email Recognizer FR",
            patterns=[
                Pattern(
                    name="email_pattern",
                    regex=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
                    score=0.9,
                ),
            ],
            supported_language="fr",
        ),
        # Numéro de rôle français (se termine par /FA)
        PatternRecognizer(
            supported_entity="FR_NUM_ROLE",
            name="French Role Number Recognizer",
            patterns=[
                Pattern(
                    name="role_pattern",
                    regex=r"\b[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/FA\b",
                    score=0.9,
                ),
            ],
            supported_language="fr",
        ),
        # Date de naissance (« né(e) le … »)
        PatternRecognizer(
            supported_entity="FR_DATE_NAISSANCE",
            name="French Birth Date Recognizer",
            patterns=[
                Pattern(
                    name="birth_date_pattern",
                    regex=(
                        r"(?i)\bné[es]?\s+le\s+"
                        r"(?:\d{1,2}(?:er)?\s+"
                        r"(?:janvier|f[eé]vrier|mars|avril|mai|juin|juillet|"
                        r"ao[uû]t|septembre|octobre|novembre|d[eé]cembre)\s+\d{4}"
                        r"|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}"
                        r"|\d{1,2}\s+\d{1,2}\s+\d{4})\b"
                    ),
                    score=0.9,
                ),
            ],
            supported_language="fr",
        ),
    ]

    # --- Registre -----------------------------------------------------------
    registry = RecognizerRegistry()
    registry.supported_languages = ["fr"]
    registry.load_predefined_recognizers(nlp_engine=nlp_engine, languages=["fr"])
    for recognizer in custom_recognizers:
        registry.add_recognizer(recognizer)

    return AnalyzerEngine(
        nlp_engine=nlp_engine,
        registry=registry,
        supported_languages=["fr"],
    )


# ---------------------------------------------------------------------------
# Singletons (chargés une seule fois au démarrage)
# ---------------------------------------------------------------------------

analyzer: AnalyzerEngine = _build_analyzer()
"""Instance partagée de l'analyseur Presidio."""

anonymizer_engine: AnonymizerEngine = AnonymizerEngine()
"""Instance partagée du moteur d'anonymisation Presidio."""


# ---------------------------------------------------------------------------
# Fonction utilitaire d'anonymisation de texte brut
# ---------------------------------------------------------------------------

def anonymize_text(text: str) -> str:
    """Analyse et anonymise un texte français.

    Chaque entité détectée est remplacée par un label entre crochets,
    par exemple ``[PERSONNE]``, ``[LIEU]``, ``[DATE_NAISSANCE]``, etc.

    Parameters
    ----------
    text : str
        Texte brut en français à anonymiser.

    Returns
    -------
    str
        Texte anonymisé avec les labels de remplacement.
    """
    results = analyzer.analyze(text=text, language="fr")
    operators = {
        entity_type: OperatorConfig(
            "replace", {"new_value": f"[{get_label(entity_type)}]"}
        )
        for entity_type in {r.entity_type for r in results}
    }
    anonymized = anonymizer_engine.anonymize(
        text=text, analyzer_results=results, operators=operators,
    )
    return anonymized.text
