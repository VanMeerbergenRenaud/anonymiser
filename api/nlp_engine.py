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
- EMAIL_ADDRESS → [EMAIL]
- PHONE_NUMBER → [TÉLÉPHONE]
- IBAN_CODE → [IBAN]
- FR_NIR (n° sécu) → [NIR]
- FR_POSTAL_CODE → [CODE_POSTAL]
- FR_DATE_NAISSANCE → [DATE_NAISSANCE]
- FR_NUM_ROLE (n° de rôle /FA) → [ROLE]
- FR_NOM_PROPRE (noms en capitales) → [Nom propre]
- NRP → [NATIONALITÉ]
- CREDIT_CARD → [CARTE_BANCAIRE]
- IP_ADDRESS → [ADRESSE_IP]
- URL → [URL]

Entités conservées (non anonymisées)
------------------------------------
- DATE_TIME → conservé tel quel pour préserver la chronologie

Moteur NER (backend)
--------------------
Le backend de reconnaissance d'entités est sélectionnable via la variable
d'environnement ``ANON_NLP_BACKEND`` :

- ``transformers`` → CamemBERT-NER (``Jean-Baptiste/camembert-ner``), plus
  précis sur le français, recommandé en local. Nécessite ``torch`` +
  ``transformers`` (voir ``requirements-local.txt``). Le modèle (~440 Mo) est
  téléchargé une seule fois dans ``~/.cache/huggingface`` puis utilisé
  hors-ligne.
- ``spacy`` → modèle spaCy ``fr_core_news_md`` (léger, utilisé sur Vercel où
  CamemBERT ne rentre pas dans une fonction serverless).

Par défaut : ``spacy`` sur Vercel (variable ``VERCEL`` présente), sinon
``transformers``.
"""

from __future__ import annotations

import os
import re

from presidio_analyzer import (
    AnalyzerEngine,
    PatternRecognizer,
    Pattern,
    RecognizerRegistry,
)
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import CreditCardRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# ---------------------------------------------------------------------------
# Mapping entité Presidio → label français lisible
# ---------------------------------------------------------------------------

ENTITY_LABELS: dict[str, str] = {
    "PERSON": "PERSONNE",
    "LOCATION": "LIEU",
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
    "FR_NOM_PROPRE": "Nom propre",
    "FR_TVA": "TVA",
    "FR_SIRET": "SIRET",
    "FR_SIREN": "SIREN",
    "CUSTOM": "CONFIDENTIEL",
}

# Entités détectées par l'analyseur mais conservées dans le texte final
ENTITIES_TO_SKIP: set[str] = {"DATE_TIME"}


def get_label(entity_type: str) -> str:
    """Retourne le label français pour un type d'entité Presidio."""
    return ENTITY_LABELS.get(entity_type, entity_type)


# ---------------------------------------------------------------------------
# Sélection et configuration du backend NER
# ---------------------------------------------------------------------------

_SPACY_MODEL = "fr_core_news_md"
"""Modèle spaCy utilisé (NER en backend spaCy, tokenisation en backend transformers)."""

_TRANSFORMERS_MODEL = "Jean-Baptiste/camembert-ner"
"""Modèle CamemBERT-NER HuggingFace utilisé en backend ``transformers``."""

# Mapping labels NER (spaCy ET CamemBERT utilisent PER/LOC/ORG/MISC)
_NER_ENTITY_MAPPING: dict[str, str] = {
    "PER": "PERSON",
    "PERSON": "PERSON",
    "LOC": "LOCATION",
    "LOCATION": "LOCATION",
    "ORG": "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION",
    "MISC": "NRP",
}


def _transformers_available() -> bool:
    """Vrai si ``torch`` et ``transformers`` sont installés (backend CamemBERT)."""
    import importlib.util

    return (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("transformers") is not None
    )


def select_backend() -> str:
    """Retourne le backend NER actif : ``"transformers"`` ou ``"spacy"``.

    Piloté par ``ANON_NLP_BACKEND`` (``"spacy"`` ou ``"transformers"``). En
    l'absence de valeur explicite, le choix est automatique :

    - ``spacy`` sur Vercel (variable ``VERCEL`` présente, CamemBERT n'y rentre
      pas) ;
    - ``transformers`` (CamemBERT) ailleurs **si** ``torch`` + ``transformers``
      sont installés, sinon repli sur ``spacy``.

    Un choix explicite via ``ANON_NLP_BACKEND`` est toujours respecté.
    """
    backend = os.environ.get("ANON_NLP_BACKEND", "").strip().lower()
    if backend in {"spacy", "transformers"}:
        return backend
    if os.environ.get("VERCEL"):
        return "spacy"
    return "transformers" if _transformers_available() else "spacy"


def _nlp_configuration() -> dict:
    """Construit la configuration ``NlpEngineProvider`` selon le backend actif."""
    if select_backend() == "transformers":
        return {
            "nlp_engine_name": "transformers",
            "models": [
                {
                    "lang_code": "fr",
                    "model_name": {
                        "spacy": _SPACY_MODEL,
                        "transformers": _TRANSFORMERS_MODEL,
                    },
                }
            ],
            "ner_model_configuration": {
                "labels_to_ignore": ["O"],
                "aggregation_strategy": "simple",
                "alignment_mode": "expand",
                "model_to_presidio_entity_mapping": _NER_ENTITY_MAPPING,
            },
        }
    return {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "fr", "model_name": _SPACY_MODEL}],
        "ner_model_configuration": {
            "labels_to_ignore": ["O"],
            "model_to_presidio_entity_mapping": _NER_ENTITY_MAPPING,
        },
    }


# ---------------------------------------------------------------------------
# Construction de l'AnalyzerEngine
# ---------------------------------------------------------------------------

def _build_analyzer() -> AnalyzerEngine:
    """Construit un ``AnalyzerEngine`` configuré pour le français juridique.

    Inclut les recognizers prédéfinis de Presidio **plus** des recognizers
    custom pour les entités françaises courantes (NIR, IBAN, téléphone,
    code postal, date de naissance, numéro de rôle, e-mail).

    Le moteur NER (spaCy ou CamemBERT) est choisi par ``select_backend()``.
    """
    # --- Moteur NLP (spaCy ou Transformers/CamemBERT) -----------------------
    provider = NlpEngineProvider(nlp_configuration=_nlp_configuration())
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
        # Carte bancaire — le recognizer prédéfini de Presidio (avec
        # validation Luhn) n'est chargé qu'en anglais par défaut ; on
        # l'enregistre explicitement pour le français.
        CreditCardRecognizer(supported_language="fr"),
        # N° de TVA intracommunautaire français (FR + clé + 9 chiffres SIREN)
        PatternRecognizer(
            supported_entity="FR_TVA",
            name="French VAT Recognizer",
            patterns=[
                Pattern(
                    name="tva_pattern",
                    regex=r"\bFR\s?[0-9A-HJ-NP-Z]{2}\s?\d{3}\s?\d{3}\s?\d{3}\b",
                    score=0.8,
                ),
            ],
            supported_language="fr",
        ),
        # SIRET (14 chiffres) — score modéré, exige un contexte d'immatriculation
        PatternRecognizer(
            supported_entity="FR_SIRET",
            name="French SIRET Recognizer",
            patterns=[
                Pattern(
                    name="siret_pattern",
                    regex=r"\b\d{3}\s?\d{3}\s?\d{3}\s?\d{5}\b",
                    score=0.4,
                ),
            ],
            supported_language="fr",
            context=["siret", "rcs", "immatricul", "établissement", "siège"],
        ),
        # SIREN (9 chiffres) — score faible, n'est masqué qu'avec contexte
        PatternRecognizer(
            supported_entity="FR_SIREN",
            name="French SIREN Recognizer",
            patterns=[
                Pattern(
                    name="siren_pattern",
                    regex=r"\b\d{3}\s?\d{3}\s?\d{3}\b",
                    score=0.35,
                ),
            ],
            supported_language="fr",
            context=["siren", "rcs", "immatricul", "société", "registre"],
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

SCORE_THRESHOLD: float = float(os.environ.get("ANON_SCORE_THRESHOLD", "0.5"))
"""Seuil de confiance minimal pour qu'une entité soit anonymisée.

Évite la sur-anonymisation : les détections à faible score (ex : un nombre
à 5 chiffres pris pour un code postal sans contexte d'adresse, score 0.4)
sont ignorées sous ce seuil."""


# ---------------------------------------------------------------------------
# Post-traitement : remplacement des noms propres en capitales
# ---------------------------------------------------------------------------

_UPPERCASE_NAME_RE = re.compile(
    r"\b[A-ZÀ-Ú]{2,}(?:\s+[A-ZÀ-Ú]{2,})+\b"
)
"""Regex capturant 2+ mots consécutifs entièrement en majuscules (min 2 car.)"""

# Termes juridiques / structurels fréquemment écrits en capitales et qui ne
# sont PAS des noms de personnes. Si une séquence en majuscules contient l'un
# de ces mots, elle n'est pas remplacée par [Nom propre] (évite de masquer
# « TRIBUNAL DE COMMERCE », « COUR D'APPEL », « PAR CES MOTIFS », etc.).
_LEGAL_UPPERCASE_STOPWORDS: set[str] = {
    "TRIBUNAL", "COUR", "APPEL", "CASSATION", "JUGEMENT", "ARRÊT", "ARRET",
    "ORDONNANCE", "ARTICLE", "ARTICLES", "REQUÊTE", "REQUETE", "AUDIENCE",
    "GREFFE", "CHAMBRE", "SECTION", "CONCLUSIONS", "INSTANCE", "GRANDE",
    "JUDICIAIRE", "ADMINISTRATIF", "CONSEIL", "COMMERCE", "RÉPUBLIQUE",
    "REPUBLIQUE", "FRANÇAISE", "FRANCAISE", "MINISTÈRE", "MINISTERE",
    "PUBLIC", "PROCÈS", "PROCES", "VERBAL", "ATTENDU", "PAR", "CES",
    "MOTIFS", "VU", "STATUANT", "CONTRADICTOIREMENT", "PRÉSENT", "PRESENT",
}


def _is_legal_header(matched: str) -> bool:
    """Vrai si ``matched`` est un en-tête entièrement en majuscules contenant
    un terme juridique/structurel (ex : « TRIBUNAL DE COMMERCE », « COUR D
    APPEL »).

    Ne renvoie ``True`` que si le texte ne contient **aucune minuscule** :
    ainsi un vrai nom de personne (« Jean DUPONT ») reste masqué, tandis que
    les intitulés de juridiction en capitales sont préservés.
    """
    if not matched or matched != matched.upper():
        return False
    if not any(ch.isalpha() for ch in matched):
        return False
    return any(tok in _LEGAL_UPPERCASE_STOPWORDS for tok in matched.split())


def filter_legal_headers(text: str, results: list) -> list:
    """Retire des résultats les en-têtes juridiques en majuscules.

    Évite de masquer « TRIBUNAL DE COMMERCE DE PARIS » ou « COUR D APPEL »
    même lorsque le moteur NER les détecte à tort comme PERSON / NRP.
    """
    return [r for r in results if not _is_legal_header(text[r.start : r.end])]


def override_uppercase_entities(text: str, results: list) -> list:
    """Remplace le type d'entité des résultats dont le texte est en capitales.

    Si le texte capturé par Presidio correspond à une séquence de mots
    entièrement en majuscules (≥2 mots de ≥2 caractères), son
    ``entity_type`` est forcé à ``FR_NOM_PROPRE`` afin d'obtenir le
    label ``[Nom propre]`` plutôt qu'un label incorrect (ex : NATIONALITÉ).

    Les séquences contenant un terme juridique/structurel (ex : « TRIBUNAL
    DE COMMERCE ») sont laissées intactes pour éviter la sur-anonymisation.
    """
    for r in results:
        matched = text[r.start : r.end]
        if _UPPERCASE_NAME_RE.fullmatch(matched) and not _is_legal_header(matched):
            r.entity_type = "FR_NOM_PROPRE"
    return results


def replace_uppercase_names(text: str) -> str:
    """Remplace les séquences de mots en majuscules par ``[Nom propre]``.

    Appelée *après* l'anonymisation Presidio pour capturer les séquences
    que Presidio n'a pas détectées du tout. Les intitulés juridiques en
    capitales (ex : « COUR D'APPEL ») sont préservés.
    """
    def _sub(match: re.Match) -> str:
        seq = match.group(0)
        return seq if _is_legal_header(seq) else "[Nom propre]"

    return _UPPERCASE_NAME_RE.sub(_sub, text)


# ---------------------------------------------------------------------------
# Fonction utilitaire d'anonymisation de texte brut
# ---------------------------------------------------------------------------

def anonymize_text(text: str) -> str:
    """Analyse et anonymise un texte français.

    Chaque entité détectée est remplacée par un label entre crochets,
    par exemple ``[PERSONNE]``, ``[LIEU]``, ``[DATE_NAISSANCE]``, etc.
    Les noms propres en capitales (ex : « RENAUD TECH COMPANY ») sont
    remplacés par ``[Nom propre]``.

    Parameters
    ----------
    text : str
        Texte brut en français à anonymiser.

    Returns
    -------
    str
        Texte anonymisé avec les labels de remplacement.
    """
    results = analyzer.analyze(
        text=text, language="fr", score_threshold=SCORE_THRESHOLD,
    )
    # Filtrer les entités à conserver (ex : dates)
    results = [r for r in results if r.entity_type not in ENTITIES_TO_SKIP]
    # Préserver les en-têtes juridiques en majuscules (TRIBUNAL, COUR, etc.)
    results = filter_legal_headers(text, results)
    # Reclasser les entités en capitales comme noms propres
    results = override_uppercase_entities(text, results)
    operators = {
        entity_type: OperatorConfig(
            "replace", {"new_value": f"[{get_label(entity_type)}]"}
        )
        for entity_type in {r.entity_type for r in results}
    }
    anonymized = anonymizer_engine.anonymize(
        text=text, analyzer_results=results, operators=operators,
    )
    # Post-traitement : noms propres en capitales non détectés par Presidio
    return replace_uppercase_names(anonymized.text)


# ---------------------------------------------------------------------------
# Analyse détaillée (couche de révision interactive)
# ---------------------------------------------------------------------------

def _detection_dict(text: str, start: int, end: int, entity_type: str,
                    score: float) -> dict:
    """Construit un dictionnaire de détection sérialisable en JSON."""
    return {
        "start": start,
        "end": end,
        "type": entity_type,
        "label": get_label(entity_type),
        "score": round(float(score), 3),
        "value": text[start:end],
    }


def _normalize(s: str) -> str:
    """Normalise une chaîne pour comparaison (minuscule, espaces réduits)."""
    return " ".join(s.lower().split())


def _add_missing_uppercase_names(text: str, detections: list[dict]) -> list[dict]:
    """Ajoute les séquences de noms en majuscules non détectées par Presidio.

    Reproduit, sous forme de détections, ce que ``replace_uppercase_names``
    masquerait, afin qu'elles soient visibles et révisables dans l'interface.
    """
    occupied = [(d["start"], d["end"]) for d in detections]
    for m in _UPPERCASE_NAME_RE.finditer(text):
        if _is_legal_header(m.group(0)):
            continue
        if any(m.start() < e and s < m.end() for s, e in occupied):
            continue  # chevauche une détection existante
        detections.append(
            _detection_dict(text, m.start(), m.end(), "FR_NOM_PROPRE", 1.0)
        )
    return detections


def _apply_whitelist(detections: list[dict], whitelist: list[str]) -> list[dict]:
    """Retire les détections correspondant à un terme de la liste blanche.

    Une détection est conservée (= non masquée, donc retirée des détections)
    si sa valeur contient un terme de la liste blanche, ou inversement.
    """
    terms = [_normalize(w) for w in (whitelist or []) if w.strip()]
    if not terms:
        return detections
    kept = []
    for d in detections:
        val = _normalize(d["value"])
        if any(t in val or val in t for t in terms):
            continue  # à garder en clair → on retire la détection
        kept.append(d)
    return kept


def _add_blocklist(text: str, detections: list[dict],
                   blocklist: list[str]) -> list[dict]:
    """Force le masquage des occurrences des termes de la liste noire.

    Chaque occurrence (insensible à la casse) d'un terme de la liste noire
    devient une détection ``CUSTOM`` → ``[CONFIDENTIEL]``, sauf si elle
    chevauche une détection existante.
    """
    terms = [t.strip() for t in (blocklist or []) if t.strip()]
    if not terms:
        return detections
    occupied = [(d["start"], d["end"]) for d in detections]
    for term in terms:
        for m in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            if any(m.start() < e and s < m.end() for s, e in occupied):
                continue
            det = _detection_dict(text, m.start(), m.end(), "CUSTOM", 1.0)
            detections.append(det)
            occupied.append((m.start(), m.end()))
    return detections


def _resolve_overlaps(detections: list[dict]) -> list[dict]:
    """Trie les détections et retire les chevauchements (garde le meilleur score)."""
    ordered = sorted(detections, key=lambda d: (d["start"], -d["score"]))
    result: list[dict] = []
    last_end = -1
    for d in ordered:
        if d["start"] >= last_end:
            result.append(d)
            last_end = d["end"]
    return result


def analyze_text_detailed(
    text: str,
    whitelist: list[str] | None = None,
    blocklist: list[str] | None = None,
) -> dict:
    """Analyse un texte et renvoie les détections structurées (sans masquer).

    Destinée à l'interface de révision : le frontend reçoit le texte original
    et la liste des entités détectées, à valider ou refuser une par une.

    Parameters
    ----------
    text : str
        Texte brut en français à analyser.
    whitelist : list[str], optional
        Termes à toujours conserver en clair (retirés des détections).
    blocklist : list[str], optional
        Termes à toujours masquer (ajoutés comme ``[CONFIDENTIEL]``).

    Returns
    -------
    dict
        ``{"text": <original>, "detections": [{start, end, type, label,
        score, value}, ...]}`` (détections triées, sans chevauchement).
    """
    results = analyzer.analyze(
        text=text, language="fr", score_threshold=SCORE_THRESHOLD,
    )
    results = [r for r in results if r.entity_type not in ENTITIES_TO_SKIP]
    results = filter_legal_headers(text, results)
    results = override_uppercase_entities(text, results)

    detections = [
        _detection_dict(text, r.start, r.end, r.entity_type, r.score)
        for r in results
    ]
    detections = _add_missing_uppercase_names(text, detections)
    detections = _apply_whitelist(detections, whitelist)
    detections = _add_blocklist(text, detections, blocklist)
    detections = _resolve_overlaps(detections)

    return {"text": text, "detections": detections}


def apply_detections(text: str, detections: list[dict]) -> str:
    """Reconstruit le texte en remplaçant chaque détection par son label.

    Applique les remplacements de droite à gauche pour préserver les indices.
    Utilisé côté serveur (tests, fichiers) ; le frontend de révision fait
    le même calcul côté client pour l'interactivité.
    """
    for d in sorted(detections, key=lambda d: d["start"], reverse=True):
        text = text[: d["start"]] + f'[{d["label"]}]' + text[d["end"]:]
    return text
