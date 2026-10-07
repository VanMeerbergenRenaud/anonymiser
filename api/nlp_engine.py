"""
nlp_engine — Moteur d'anonymisation partagé (textes français / belges).

Ce module centralise toute la configuration Presidio / NER utilisée par les
endpoints ``anonymize_text`` et ``anonymize_file``. Il est chargé une seule
fois au démarrage du serveur et les instances sont ensuite réutilisées
(singletons au niveau du module).

Chaîne de traitement
--------------------
1. **Détection** — Presidio combine :

   - le NER (CamemBERT ou spaCy) pour les personnes, lieux et organisations ;
   - les recognizers sur mesure de ``api.recognizers`` (registre national
     belge, IBAN, téléphones, adresses, noms précédés d'une civilité,
     champs de formulaire, dates de naissance…) ;
   - quelques recognizers Presidio (carte bancaire, URL, adresse IP).

2. **Nettoyage** — suppression des faux positifs : intitulés juridiques en
   capitales (« PAR CES MOTIFS »), juridictions et institutions (« Tribunal
   de première instance », « Code civil »), fonctions (« Directrice
   Générale »), civilités incluses par erreur dans un nom…

3. **Compléments** — séquences de noms en capitales non détectées
   (« RENAUD TECH SOLUTIONS » → ``[Nom propre]``) et **propagation** : un nom
   détecté une fois est masqué partout dans le document (« Monsieur
   BERNARD », « Bernard », « BERNARD »).

4. **Fusion** — les détections qui se chevauchent sont réunies (rien ne
   fuit entre deux détections), les morceaux d'adresse contigus aussi.

5. **Pseudonymes** — chaque personne reçoit un numéro stable dans tout le
   document (``[PERSONNE_1]``, ``[PERSONNE_2]``…), ce qui garde le texte
   compréhensible (« qui a fait quoi »). Désactivable via
   ``ANON_PSEUDONYMS=0`` (toutes les personnes deviennent ``[PERSONNE]``).

Texte issu d'images (OCR)
-------------------------
Les passages encadrés par les marqueurs de ``api.ocr`` sont anonymisés avec
un seuil de confiance plus bas (``ANON_OCR_SCORE_THRESHOLD``, défaut 0.4) :
l'OCR introduit des erreurs qui font baisser les scores. Les lignes de
marqueurs elles-mêmes ne sont jamais modifiées.

Moteur NER (backend)
--------------------
Sélectionnable via ``ANON_NLP_BACKEND`` :

- ``transformers`` → CamemBERT-NER (``Jean-Baptiste/camembert-ner``), plus
  précis sur le français, recommandé. Nécessite ``torch`` + ``transformers``
  (voir ``requirements-local.txt``).
- ``spacy`` → modèle spaCy ``fr_core_news_md`` (léger, utilisé sur Vercel).

Par défaut : ``spacy`` sur Vercel (variable ``VERCEL`` présente), sinon
``transformers`` si installé.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass

from presidio_analyzer import AnalyzerEngine, RecognizerRegistry, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider
from presidio_analyzer.predefined_recognizers import (
    CreditCardRecognizer,
    IpRecognizer,
    UrlRecognizer,
)

from api.ocr import MARKERS, OCR_END, OCR_START
from api.recognizers import (
    NAME_PARTICLES,
    NAME_STOP_WORDS,
    ROLE_WORDS,
    TITLE_BEFORE_RE,
    TITLE_WORDS,
    UPPER_WORD,
    build_recognizers,
    fold,
    trim_person_name,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Mapping entité → label français lisible
# ---------------------------------------------------------------------------

ENTITY_LABELS: dict[str, str] = {
    "PERSON": "PERSONNE",
    "LOCATION": "LIEU",
    "ADDRESS": "ADRESSE",
    "ORGANIZATION": "SOCIÉTÉ",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "TÉLÉPHONE",
    "IBAN_CODE": "IBAN",
    "BANK_ACCOUNT": "COMPTE_BANCAIRE",
    "CREDIT_CARD": "CARTE_BANCAIRE",
    "BE_NATIONAL_NUMBER": "REGISTRE_NATIONAL",
    "BE_ID_CARD": "CARTE_IDENTITÉ",
    "PASSPORT": "PASSEPORT",
    "BE_ENTERPRISE": "NUMÉRO_ENTREPRISE",
    "VAT_NUMBER": "TVA",
    "FR_NIR": "NIR",
    "FR_POSTAL_CODE": "CODE_POSTAL",
    "NRP": "NATIONALITÉ",
    "IP_ADDRESS": "ADRESSE_IP",
    "URL": "URL",
    "BIRTH_DATE": "DATE_NAISSANCE",
    "FR_DATE_NAISSANCE": "DATE_NAISSANCE",
    "FR_NUM_ROLE": "ROLE",
    "FR_NOM_PROPRE": "Nom propre",
    "FR_TVA": "TVA",
    "FR_SIRET": "SIRET",
    "FR_SIREN": "SIREN",
    "LICENSE_PLATE": "PLAQUE",
    "CUSTOM": "CONFIDENTIEL",
}

# Entités détectées par l'analyseur mais conservées dans le texte final
ENTITIES_TO_SKIP: set[str] = {"DATE_TIME"}

# Priorité en cas d'égalité lors de la fusion de détections superposées.
_PRIORITY: dict[str, int] = {
    "BE_NATIONAL_NUMBER": 100, "FR_NIR": 95, "IBAN_CODE": 90, "CREDIT_CARD": 90,
    "BANK_ACCOUNT": 85, "BE_ID_CARD": 85, "PASSPORT": 85, "EMAIL_ADDRESS": 80,
    "BIRTH_DATE": 75, "PHONE_NUMBER": 70, "ADDRESS": 60, "PERSON": 55,
    "LICENSE_PLATE": 50, "VAT_NUMBER": 50, "BE_ENTERPRISE": 50, "FR_SIRET": 45,
    "FR_SIREN": 40, "URL": 40, "IP_ADDRESS": 40, "FR_NUM_ROLE": 40,
    "LOCATION": 30, "ORGANIZATION": 25, "FR_NOM_PROPRE": 20, "NRP": 15,
    "FR_POSTAL_CODE": 10, "CUSTOM": 5,
}


def get_label(entity_type: str) -> str:
    """Retourne le label français pour un type d'entité Presidio."""
    return ENTITY_LABELS.get(entity_type, entity_type)


def _env_flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


SCORE_THRESHOLD: float = float(os.environ.get("ANON_SCORE_THRESHOLD", "0.5"))
"""Seuil de confiance minimal pour qu'une entité soit anonymisée.

Évite la sur-anonymisation : les détections à faible score (ex : un nombre
à 5 chiffres pris pour un code postal sans contexte d'adresse) sont ignorées
sous ce seuil."""

OCR_SCORE_THRESHOLD: float = float(os.environ.get("ANON_OCR_SCORE_THRESHOLD", "0.4"))
"""Seuil (plus permissif) appliqué au texte extrait d'images par OCR."""

PSEUDONYMS: bool = _env_flag("ANON_PSEUDONYMS", True)
"""Numérote les personnes (``[PERSONNE_1]``…) plutôt que ``[PERSONNE]``."""

_MIN_THRESHOLD = min(SCORE_THRESHOLD, OCR_SCORE_THRESHOLD)

# ---------------------------------------------------------------------------
# Sélection et configuration du backend NER
# ---------------------------------------------------------------------------

_SPACY_MODEL = "fr_core_news_md"
"""Modèle spaCy utilisé (NER en backend spaCy, tokenisation en backend transformers)."""

_TRANSFORMERS_MODEL = "Jean-Baptiste/camembert-ner"
"""Modèle CamemBERT-NER HuggingFace utilisé en backend ``transformers``."""

# Mapping labels NER (spaCy ET CamemBERT utilisent PER/LOC/ORG/MISC).
# MISC (« Code civil », événements, œuvres…) est ignoré : trop bruité dans
# les documents juridiques ; la nationalité est captée par des règles dédiées.
_NER_ENTITY_MAPPING: dict[str, str] = {
    "PER": "PERSON",
    "PERSON": "PERSON",
    "LOC": "LOCATION",
    "LOCATION": "LOCATION",
    "ORG": "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION",
}
_NER_LABELS_TO_IGNORE = ["O", "MISC"]


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
    ner_configuration = {
        "labels_to_ignore": _NER_LABELS_TO_IGNORE,
        "model_to_presidio_entity_mapping": _NER_ENTITY_MAPPING,
        "low_score_entity_names": [],
    }
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
                **ner_configuration,
                "aggregation_strategy": "simple",
                "alignment_mode": "expand",
            },
        }
    return {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "fr", "model_name": _SPACY_MODEL}],
        "ner_model_configuration": ner_configuration,
    }


# ---------------------------------------------------------------------------
# Construction de l'AnalyzerEngine
# ---------------------------------------------------------------------------

def _build_analyzer() -> AnalyzerEngine:
    """Construit un ``AnalyzerEngine`` configuré pour le français juridique.

    Le registre ne contient que des recognizers pertinents pour la Belgique et
    la France (les recognizers américains, indiens… de Presidio sont exclus :
    ils produisaient des faux positifs).
    """
    provider = NlpEngineProvider(nlp_configuration=_nlp_configuration())
    nlp_engine = provider.create_engine()

    registry = RecognizerRegistry(supported_languages=["fr"])
    registry.add_nlp_recognizer(nlp_engine)
    registry.add_recognizer(CreditCardRecognizer(supported_language="fr"))
    registry.add_recognizer(UrlRecognizer(supported_language="fr"))
    registry.add_recognizer(IpRecognizer(supported_language="fr"))
    for recognizer in build_recognizers("fr"):
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


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------

@dataclass
class Detection:
    """Une entité à masquer : position dans le texte, type, score, label."""

    start: int
    end: int
    entity: str
    score: float
    label: str = ""
    from_ner: bool = False
    """Vrai si la détection provient du modèle NER (et non d'une règle)."""

    def overlaps(self, start: int, end: int) -> bool:
        return self.start < end and start < self.end


# ---------------------------------------------------------------------------
# Listes de mots (forme « fold » : majuscules sans accents)
# ---------------------------------------------------------------------------

_HEADING_WORDS: frozenset[str] = frozenset({
    # Mots-outils : un nom de personne n'en contient pas.
    "ET", "OU", "LES", "AU", "AUX", "SUR", "POUR", "PAR", "DANS", "AVEC", "CES",
    "CE", "CET", "CETTE", "UN", "UNE", "EN", "A", "QUE", "QUI", "NE", "PAS",
    "SON", "SA", "SES", "LEUR", "LEURS", "NOTRE", "VOTRE", "NOS", "VOS", "TOUT",
    "TOUS", "TOUTE", "TOUTES", "ENTRE", "CONTRE", "SANS", "SOUS", "VERS", "CHEZ",
    "APRES", "AVANT", "DONT", "IL", "ELLE", "ILS", "ELLES", "NOUS", "VOUS",
    "EST", "SONT", "ETRE", "AVOIR", "PLUS", "MOINS", "TRES", "AINSI", "COMME",
    "SI", "NON", "OUI", "LORS", "DEVANT", "SELON", "SUITE", "FIN", "DEBUT",
    # Vocabulaire juridique / structurel des intitulés.
    "TRIBUNAL", "TRIBUNAUX", "COUR", "APPEL", "CASSATION", "JUGEMENT", "ARRET",
    "ORDONNANCE", "ARTICLE", "ARTICLES", "REQUETE", "AUDIENCE", "GREFFE",
    "CHAMBRE", "SECTION", "CONCLUSIONS", "CONCLUSION", "INSTANCE", "GRANDE",
    "JUDICIAIRE", "ADMINISTRATIF", "ADMINISTRATIVE", "CONSEIL", "COMMERCE",
    "REPUBLIQUE", "FRANCAISE", "ROYAUME", "BELGIQUE", "MINISTERE", "PUBLIC",
    "PROCES", "VERBAL", "PROCES-VERBAL", "ATTENDU", "ATTENDUS", "MOTIFS", "VU",
    "STATUANT", "CONTRADICTOIREMENT", "PRESENT", "PRESENTE", "PRESENTS",
    "OBJET", "DOSSIER", "AFFAIRE", "PARTIES", "PARTIE", "DEMANDEUR",
    "DEFENDEUR", "DEFENDERESSE", "DEMANDERESSE", "REQUERANT", "REQUERANTE",
    "CITATION", "ASSIGNATION", "EXPOSE", "FAITS", "FAIT", "PROCEDURE",
    "DISCUSSION", "DISPOSITIF", "DECISION", "DECIDE", "CONDAMNE", "CONDAMNER",
    "CONSTATER", "DIRE", "JUGER", "DECLARER", "ORDONNER", "DEBOUTER",
    "RECEVOIR", "PLAISE", "ELEMENTS", "PREUVE", "PREUVES", "TEMOIGNAGES",
    "TEMOIGNAGE", "CONSEQUENCES", "MEDICALES", "FINANCIERES", "PIECES",
    "PIECE", "INVENTAIRE", "ANNEXE", "ANNEXES", "BORDEREAU", "RAPPEL",
    "PREAMBULE", "CONTRAT", "CONVENTION", "ACCORD", "AVENANT", "CHAPITRE",
    "TITRE", "LIVRE", "PAGE", "TABLE", "MATIERES", "SOMMAIRE", "INTRODUCTION",
    "RESUME", "NOTE", "NOTES", "REMARQUE", "ATTENTION", "IMPORTANT",
    "CONFIDENTIEL", "URGENT", "COPIE", "ORIGINAL", "MONITEUR", "BELGE",
    "JUSTICE", "PAIX", "PREMIERE", "TRAVAIL", "ENTREPRISE", "FAMILLE",
    "JEUNESSE", "POLICE", "CORRECTIONNEL", "CORRECTIONNELLE", "CIVIL",
    "CIVILE", "PENAL", "PENALE", "SOCIAL", "SOCIALE", "ASSISES", "PARQUET",
    "PROCUREUR", "ROI", "GENERAL", "GENERALE", "GENERALES", "AUDITORAT",
    "SERVICE", "FEDERAL", "SPF", "FINANCES", "CODE", "LOI", "DROIT", "DROITS",
    "OBLIGATIONS", "RESPONSABILITE", "DOMMAGES", "INTERETS", "PREJUDICE",
    "INDEMNITE", "INDEMNITES", "SALAIRE", "SALAIRES", "MONTANT", "TOTAL",
    "EUROS", "EUR", "TVA", "HTVA", "TTC", "HT", "FACTURE", "DEVIS", "DATE",
    "LIEU", "SIGNATURE", "NOM", "PRENOM", "PRENOMS", "ADRESSE", "TELEPHONE",
    "EMAIL", "CI-APRES", "DENOMME", "DENOMMEE", "PROPRIETAIRE", "LOCATAIRE",
    "BAILLEUR", "PRENEUR", "VENDEUR", "ACHETEUR", "ACQUEREUR", "EMPLOYEUR",
    "TRAVAILLEUR", "SALARIE", "ACTE", "ACTES", "NOTAIRE", "VENTE", "BAIL",
    "LOCATION", "MANDAT", "PROCURATION", "STATUTS", "ASSEMBLEE",
    "EXTRAORDINAIRE", "ORDINAIRE", "RAPPORT", "EXPERTISE", "EXPERT", "AVIS",
    "LETTRE", "COURRIER", "RECOMMANDE", "MISE", "DEMEURE", "REFERENCE",
    "REFERENCES", "CONCERNE", "CONCERNANT", "REF", "CONFIDENTIALITE",
    "CONDITIONS", "PARTICULIERES", "CLAUSE", "CLAUSES", "DEFINITIONS", "DUREE",
    "RESILIATION", "PRIX", "PAIEMENT", "LIVRAISON", "GARANTIE", "GARANTIES",
    "ASSURANCE", "SINISTRE", "CONSTATE", "DECLARE", "CERTIFIE", "ATTESTE",
    "ATTESTATION", "CERTIFICAT", "MEDICAL", "MEDICALE", "INCAPACITE",
    "MALADIE", "ACCIDENT", "SECURITE", "MUTUELLE", "MUTUALITE", "CHOMAGE",
    "PENSION", "ALLOCATIONS", "FAMILIALES", "IMPOTS", "CONTRIBUTIONS",
    "AVERTISSEMENT", "EXTRAIT", "ROLE", "NUMERO", "REGISTRE", "NATIONAL",
    "COMMUNE", "VILLE", "PROVINCE", "REGION", "ETAT", "TEXTE", "DOCUMENT",
    "INFORMATIONS", "COORDONNEES", "IDENTITE", "CARTE", "DEMANDE", "REPONSE",
    "CONTRE-PARTIE", "HONORAIRES", "FRAIS", "DEPENS", "ETAT", "JUGE",
    "PRESIDENT", "PRESIDENTE", "AVOCAT", "AVOCATS", "BARREAU", "ORDRE",
})
"""Mots signalant un intitulé (« PAR CES MOTIFS ») et non un nom propre."""

_HEADING_PARTICLES = frozenset({"DE", "DU", "DES", "LA", "LE", "L", "D"})

_INSTITUTION_HEADS: frozenset[str] = frozenset({
    "TRIBUNAL", "TRIBUNAUX", "COUR", "CONSEIL", "CODE", "LOI", "LOIS",
    "PARQUET", "MINISTERE", "GREFFE", "CHAMBRE", "BARREAU", "JUSTICE",
    "JURIDICTION", "COMMISSION", "CONSTITUTION", "CONVENTION", "REGLEMENT",
    "DIRECTIVE", "MONITEUR", "GOUVERNEMENT", "PARLEMENT", "SENAT", "ETAT",
    "SPF", "SPW", "AUDITORAT", "AUDITEUR", "PROCUREUR", "ORDRE", "ARTICLE",
    "ARRETE", "DECRET", "ORDONNANCE", "TRAITE", "CHARTE", "CONFERENCE",
    "ROYAUME", "REPUBLIQUE", "ASSEMBLEE", "CAISSE", "ONSS", "INAMI", "ONEM",
    "FOREM", "ACTIRIS", "URSSAF", "CPAM", "SERVICE", "SERVICES", "DIRECTION",
    "DEPARTEMENT", "RESSOURCES", "COMPTABILITE", "SECRETARIAT", "ADMINISTRATION",
    "OFFICE", "INSTITUT", "AGENCE", "RGPD", "GDPR", "TVA", "BCE", "RCS", "SA",
    "SRL", "SPRL", "SAS", "SARL", "ASBL",
})
"""Premier mot d'une juridiction / institution / texte légal : jamais masqué."""

_KEPT_LOCATIONS: frozenset[str] = frozenset({
    "BELGIQUE", "BELGIE", "BELGIUM", "FRANCE", "EUROPE", "UNION EUROPEENNE",
    "WALLONIE", "FLANDRE", "FLANDRES", "REGION WALLONNE", "REGION FLAMANDE",
    "FEDERATION WALLONIE-BRUXELLES", "ROYAUME DE BELGIQUE",
})
"""Pays / régions de juridiction : conservés (aucune valeur identifiante)."""

# ---------------------------------------------------------------------------
# Outils texte
# ---------------------------------------------------------------------------

_SAME_LENGTH_SPACES = str.maketrans({
    "\u00a0": " ", "\u202f": " ", "\u2007": " ", "\u2009": " ", "\u200a": " ",
    "\u2002": " ", "\u2003": " ", "\u2010": "-", "\u2011": "-",
})
"""Espaces insécables / fines et tirets Unicode → équivalents ASCII (même
longueur : les positions des détections restent valables sur le texte
d'origine)."""

_UPPERCASE_SEQ_RE = re.compile(
    rf"(?<![\w-]){UPPER_WORD}(?:[ \t]+{UPPER_WORD})+(?![\w-])"
)
"""2+ mots consécutifs entièrement en majuscules (sur une même ligne)."""


def _fold_char(ch: str) -> str:
    base = unicodedata.normalize("NFD", ch)[0]
    upper = base.upper()
    return upper if len(upper) == 1 else base


def fold_aligned(text: str) -> str:
    """Comme :func:`fold`, mais conserve la longueur (index identiques)."""
    return "".join(_fold_char(ch) for ch in text)


def _words(value: str) -> list[str]:
    return [fold(t).strip(".,;:()[]\"'’") for t in re.split(r"[\s]+", value) if t.strip()]


def _is_heading(sequence: str) -> bool:
    """Vrai si une séquence en majuscules est un intitulé, pas un nom propre.

    Ex : « EXPOSÉ DES FAITS ET DE LA PROCÉDURE », « TRIBUNAL DE COMMERCE DE
    PARIS », « PAR CES MOTIFS ». Un nom (« JEAN DUPONT », « RENAUD TECH
    SOLUTIONS ») renvoie ``False``.
    """
    if not sequence or sequence != sequence.upper():
        return False
    parts: list[str] = []
    for word in _words(sequence):
        parts.extend(p for p in re.split(r"['’]", word) if p)
    if not parts:
        return False
    if any(p in _HEADING_WORDS for p in parts):
        return True
    if sum(p in _HEADING_PARTICLES for p in parts) >= 2:
        return True
    # Uniquement des sigles courts (« SPF BCE », « TVA HT ») : pas un nom.
    return all(len(p) <= 3 for p in parts)


def _strip_edges(text: str, start: int, end: int) -> tuple[int, int]:
    """Retire espaces et ponctuation en bordure d'une détection NER."""
    junk = " \t\r\n,;:()[]{}\"«»“”'’-–—"
    while start < end and text[start] in junk:
        start += 1
    while end > start and text[end - 1] in junk and not (
        text[end - 1] == "." and end - start <= 3  # initiale « J. »
    ):
        end -= 1
    return start, end


# ---------------------------------------------------------------------------
# Étape 1 : détection brute (Presidio), par morceaux
# ---------------------------------------------------------------------------

_NER_RECOGNIZERS = {"TransformersRecognizer", "SpacyRecognizer"}

_CHUNK_SIZE = 20_000
"""Longueur maximale d'un morceau analysé (mémoire / limites spaCy)."""


def _chunks(text: str):
    """Découpe le texte en morceaux d'au plus ``_CHUNK_SIZE`` caractères,
    coupés de préférence entre deux paragraphes (aucune entité coupée)."""
    pos, length = 0, len(text)
    while pos < length:
        end = min(length, pos + _CHUNK_SIZE)
        if end < length:
            floor = pos + _CHUNK_SIZE // 2
            for sep in ("\n\n", "\n", " "):
                cut = text.rfind(sep, floor, end)
                if cut != -1:
                    end = cut + len(sep)
                    break
        yield pos, text[pos:end]
        pos = end


def _raw_detections(text: str) -> list[Detection]:
    detections: list[Detection] = []
    for offset, chunk in _chunks(text):
        if not chunk.strip():
            continue
        results = analyzer.analyze(text=chunk, language="fr", score_threshold=_MIN_THRESHOLD)
        detections.extend(
            Detection(
                r.start + offset, r.end + offset, r.entity_type, float(r.score),
                from_ner=(r.recognition_metadata or {}).get(
                    RecognizerResult.RECOGNIZER_NAME_KEY) in _NER_RECOGNIZERS,
            )
            for r in results
        )
    return detections


# ---------------------------------------------------------------------------
# Étape 2 : zones (marqueurs protégés, passages OCR)
# ---------------------------------------------------------------------------

_OCR_BLOCK_RE = re.compile(re.escape(OCR_START) + r"(.*?)" + re.escape(OCR_END), re.S)


def _protected_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    for marker in MARKERS:
        spans.extend(m.span() for m in re.finditer(re.escape(marker), text))
    return sorted(spans)


def _ocr_spans(text: str) -> list[tuple[int, int]]:
    return [m.span(1) for m in _OCR_BLOCK_RE.finditer(text)]


def _apply_thresholds(detections: list[Detection],
                      ocr_spans: list[tuple[int, int]]) -> list[Detection]:
    kept = []
    for d in detections:
        in_ocr = any(s <= d.start and d.end <= e for s, e in ocr_spans)
        if d.score >= (OCR_SCORE_THRESHOLD if in_ocr else SCORE_THRESHOLD):
            kept.append(d)
    return kept


def _trim_protected(text: str, detections: list[Detection],
                    protected: list[tuple[int, int]]) -> list[Detection]:
    """Découpe les détections qui chevauchent une ligne de marqueur."""
    if not protected:
        return detections
    result: list[Detection] = []
    for d in detections:
        pieces = [(d.start, d.end)]
        for ps, pe in protected:
            next_pieces = []
            for s, e in pieces:
                if e <= ps or s >= pe:
                    next_pieces.append((s, e))
                    continue
                if s < ps:
                    next_pieces.append((s, ps))
                if e > pe:
                    next_pieces.append((pe, e))
            pieces = next_pieces
        for s, e in pieces:
            s, e = _strip_edges(text, s, e)
            if e > s and any(ch.isalnum() for ch in text[s:e]):
                result.append(Detection(s, e, d.entity, d.score, from_ner=d.from_ner))
    return result


# ---------------------------------------------------------------------------
# Étape 3 : nettoyage des faux positifs
# ---------------------------------------------------------------------------

_NER_TYPES = {"PERSON", "LOCATION", "ORGANIZATION"}
_LEADING_PARTICLE_RE = re.compile(
    r"(?:de|du|des|la|le|les|à|au|aux|en|d['’]|l['’])[ \t]*(?=[A-ZÀ-Þ])"
)


def _clean(text: str, detections: list[Detection]) -> list[Detection]:
    cleaned: list[Detection] = []
    for d in detections:
        if d.entity in ENTITIES_TO_SKIP:
            continue
        if d.from_ner and d.entity in _NER_TYPES:
            d.start, d.end = _strip_edges(text, d.start, d.end)
            value = text[d.start:d.end]
            if len(value) < 2 or not any(ch.isupper() for ch in value):
                continue  # mot commun en minuscules : pas un nom propre
            if _is_heading(value):
                continue
            words = _words(value)
            if not words:
                continue
            if d.entity == "PERSON":
                titled = TITLE_BEFORE_RE.search(text, max(0, d.start - 15), d.start)
                span = trim_person_name(text, d.start, d.end, keep_initials=bool(titled))
                if span is None:
                    continue
                d.start, d.end = span
            else:
                # « de Paris » → « Paris » (l'article reste lisible).
                lead = _LEADING_PARTICLE_RE.match(text, d.start, d.end)
                if lead:
                    d.start = lead.end()
                if words[0] in _INSTITUTION_HEADS:
                    continue
                if " ".join(words) in _KEPT_LOCATIONS:
                    continue
                if all(w in ROLE_WORDS or w in NAME_STOP_WORDS or w in _HEADING_WORDS
                       for w in words):
                    continue
        cleaned.append(d)
    return cleaned


# ---------------------------------------------------------------------------
# Étape 4 : compléments (noms en capitales, propagation)
# ---------------------------------------------------------------------------

def _add_uppercase_names(text: str, detections: list[Detection]) -> list[Detection]:
    """Ajoute les séquences en capitales (hors intitulés) non encore détectées."""
    for m in _UPPERCASE_SEQ_RE.finditer(text):
        if any(d.overlaps(m.start(), m.end()) for d in detections):
            continue
        tokens = list(re.finditer(r"\S+", m.group(0)))
        # « MONSIEUR JEAN DUPONT » : la civilité n'est pas masquée.
        while tokens and fold(tokens[0].group(0)).strip(".") in TITLE_WORDS:
            tokens.pop(0)
        if not tokens:
            continue
        start, end = m.start() + tokens[0].start(), m.start() + tokens[-1].end()
        seq = text[start:end]
        if len(tokens) < 2 or _is_heading(seq):
            continue
        detections.append(Detection(start, end, "FR_NOM_PROPRE", 0.6))
    return detections


_COMMON_CAPITALIZED: frozenset[str] = frozenset({
    "SAINT", "SAINTE", "SAINTS", "MONT", "PONT", "BOIS", "VAL", "GRAND",
    "GRANDE", "PETIT", "PETITE", "NOUVEAU", "VIEUX", "HAUT", "BAS",
})
"""Mots trop courants pour être propagés seuls comme nom de personne."""


def _person_terms(text: str, detections: list[Detection]) -> set[str]:
    terms: set[str] = set()
    for d in detections:
        if d.entity != "PERSON":
            continue
        for token in re.split(r"[\s,;:()]+", text[d.start:d.end]):
            if not token or not token[:1].isupper():
                continue
            key = fold_aligned(token).strip(".'’")
            if (len(key) < 3 or key in NAME_PARTICLES or key in NAME_STOP_WORDS
                    or key in _HEADING_WORDS or key in _COMMON_CAPITALIZED):
                continue
            terms.add(key)
    return terms


def _propagate_persons(text: str, detections: list[Detection]) -> list[Detection]:
    """Masque toutes les autres mentions des noms de personnes détectés.

    « Thomas BERNARD » détecté une fois → « BERNARD », « Bernard » et
    « Thomas » sont masqués partout (mais pas « bernard » en minuscules, qui
    peut être un mot courant).
    """
    terms = _person_terms(text, detections)
    if not terms:
        return detections
    folded = fold_aligned(text)
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    pattern = re.compile(rf"(?<![\w'’-])(?:{alternation})(?![\w-])")
    occupied = sorted((d.start, d.end) for d in detections)
    added: list[Detection] = []
    for m in pattern.finditer(folded):
        if not text[m.start()].isupper():
            continue
        if any(s < m.end() and m.start() < e for s, e in occupied):
            continue
        added.append(Detection(m.start(), m.end(), "PERSON", 0.8))
    return detections + added


# ---------------------------------------------------------------------------
# Étape 5 : fusion des détections
# ---------------------------------------------------------------------------

def _merge_overlaps(detections: list[Detection]) -> list[Detection]:
    """Réunit les détections qui se chevauchent (union des positions).

    Le type retenu est celui d'une règle (validée : clé de contrôle, format)
    plutôt que d'une supposition du NER, puis celui de la détection couvrant
    le plus de caractères (une adresse complète l'emporte sur la ville qu'elle
    contient), puis du meilleur score, puis de la priorité du type.
    """
    ordered = sorted(detections, key=lambda d: (d.start, -d.end))
    groups: list[list[Detection]] = []
    group_end = -1
    for d in ordered:
        if groups and d.start < group_end:
            groups[-1].append(d)
            group_end = max(group_end, d.end)
        else:
            groups.append([d])
            group_end = d.end
    merged: list[Detection] = []
    for members in groups:
        best = max(members, key=lambda d: (
            not d.from_ner, d.end - d.start, d.score, _PRIORITY.get(d.entity, 0),
        ))
        merged.append(Detection(
            min(d.start for d in members), max(d.end for d in members),
            best.entity, max(d.score for d in members),
        ))
    return merged


_ADDRESS_PARTS = {"ADDRESS", "LOCATION", "FR_POSTAL_CODE"}
_HOUSE_NUMBER_TAIL_RE = re.compile(
    r"[ \t]*,?[ \t]*(?:n°[ \t]*)?\d{1,4}[A-Za-z]?(?![\d\w])"
    r"(?:[ \t]*,?[ \t]*(?i:bte|bo[îi]te|bus|b)\.?[ \t]*[A-Za-z]?\d{1,4})?"
    r"(?=[ \t]*(?:[,.;)\n]|$))"
)
_ADDRESS_GAP_RE = re.compile(r"[ \t]*[,–-]?[ \t]*\n?[ \t]*")


def _merge_adjacent(text: str, detections: list[Detection]) -> list[Detection]:
    """Fusionne les détections contiguës qui forment une même entité.

    - morceaux d'adresse : « [ADRESSE], [LIEU] » → « [ADRESSE] » ;
    - fragments d'un même nom séparés par une espace : « [PERSONNE]
      [PERSONNE] » → « [PERSONNE] ».
    """
    result: list[Detection] = []
    for d in sorted(detections, key=lambda d: d.start):
        if result:
            prev = result[-1]
            gap = text[prev.end:d.start]
            address_join = (
                prev.entity in _ADDRESS_PARTS and d.entity in _ADDRESS_PARTS
                and "ADDRESS" in (prev.entity, d.entity)
                and len(gap) <= 6 and _ADDRESS_GAP_RE.fullmatch(gap)
            )
            same_join = (
                prev.entity == d.entity and d.entity in _NER_TYPES | {"FR_NOM_PROPRE"}
                and gap in ("", " ")
            )
            if address_join or same_join:
                result[-1] = Detection(
                    prev.start, d.end, "ADDRESS" if address_join else d.entity,
                    max(prev.score, d.score),
                )
                continue
        result.append(d)

    # Numéro de maison resté seul après une adresse (« [ADRESSE] 3 »).
    for i, d in enumerate(result):
        if d.entity != "ADDRESS":
            continue
        tail = _HOUSE_NUMBER_TAIL_RE.match(text, d.end)
        limit = result[i + 1].start if i + 1 < len(result) else len(text)
        if tail and d.end < tail.end() <= limit:
            d.end = tail.end()
    return result


# ---------------------------------------------------------------------------
# Étape 6 : labels (pseudonymes numérotés pour les personnes)
# ---------------------------------------------------------------------------

def _name_parts(value: str) -> tuple[set[str], set[str]]:
    """Sépare prénoms et noms de famille (« Jean DUPONT » → {JEAN}, {DUPONT})."""
    tokens = []
    for raw in re.split(r"[\s,;:()]+", value):
        key = fold(raw).strip(".'’")
        if key and key not in NAME_PARTICLES and len(key) >= 2:
            tokens.append((raw, key))
    if not tokens:
        return set(), set()
    uppers = [k for raw, k in tokens if raw.isupper()]
    if uppers and len(uppers) < len(tokens):
        return {k for raw, k in tokens if not raw.isupper()}, set(uppers)
    if len(tokens) == 1:
        return set(), {tokens[0][1]}
    return {k for _, k in tokens[:-1]}, {tokens[-1][1]}


def _assign_labels(text: str, detections: list[Detection]) -> list[Detection]:
    """Attribue les labels ; un numéro stable par personne si ``PSEUDONYMS``."""
    persons = [d for d in detections if d.entity == "PERSON"]
    for d in detections:
        d.label = get_label(d.entity)
    if not PSEUDONYMS or not persons:
        return detections

    clusters: list[dict] = []  # {"firsts", "surnames", "first_pos"}
    assignment: dict[int, int] = {}
    parts = {id(d): _name_parts(text[d.start:d.end]) for d in persons}

    # 1) Mentions complètes (prénom + nom) : elles définissent les personnes.
    for d in persons:
        firsts, surnames = parts[id(d)]
        if not firsts:
            continue
        target = None
        for i, c in enumerate(clusters):
            if c["surnames"] & surnames and (not c["firsts"] or c["firsts"] & firsts):
                target = i
                break
        if target is None:
            clusters.append({"firsts": set(firsts), "surnames": set(surnames), "first_pos": d.start})
            target = len(clusters) - 1
        else:
            clusters[target]["firsts"] |= firsts
            clusters[target]["first_pos"] = min(clusters[target]["first_pos"], d.start)
        assignment[id(d)] = target

    # 2) Mentions partielles (« BERNARD », « Thomas ») : rattachées à la
    #    personne correspondante la plus proche qui précède.
    for d in persons:
        if id(d) in assignment:
            continue
        _, keys = parts[id(d)]
        candidates = [i for i, c in enumerate(clusters) if c["surnames"] & keys]
        if not candidates:
            candidates = [i for i, c in enumerate(clusters) if c["firsts"] & keys]
        if candidates:
            before = [i for i in candidates if clusters[i]["first_pos"] <= d.start]
            target = max(before or candidates, key=lambda i: clusters[i]["first_pos"])
            clusters[target]["first_pos"] = min(clusters[target]["first_pos"], d.start)
        else:
            clusters.append({"firsts": set(), "surnames": set(keys), "first_pos": d.start})
            target = len(clusters) - 1
        assignment[id(d)] = target

    order = sorted(range(len(clusters)), key=lambda i: clusters[i]["first_pos"])
    number = {cluster: n for n, cluster in enumerate(order, start=1)}
    base = get_label("PERSON")
    for d in persons:
        d.label = f"{base}_{number[assignment[id(d)]]}"
    return detections


# ---------------------------------------------------------------------------
# Listes blanche / noire (révision interactive)
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    """Normalise une chaîne pour comparaison (minuscule, espaces réduits)."""
    return " ".join(s.lower().split())


def _apply_whitelist(text: str, detections: list[Detection],
                     whitelist: list[str] | None) -> list[Detection]:
    """Retire les détections correspondant à un terme de la liste blanche."""
    terms = [_normalize(w) for w in (whitelist or []) if w.strip()]
    if not terms:
        return detections
    kept = []
    for d in detections:
        val = _normalize(text[d.start:d.end])
        if any(t in val or val in t for t in terms):
            continue  # à garder en clair
        kept.append(d)
    return kept


def _add_blocklist(text: str, detections: list[Detection],
                   blocklist: list[str] | None) -> list[Detection]:
    """Force le masquage (``[CONFIDENTIEL]``) des termes de la liste noire."""
    terms = [t.strip() for t in (blocklist or []) if t.strip()]
    for term in terms:
        for m in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            detections.append(Detection(m.start(), m.end(), "CUSTOM", 1.0))
    return detections


# ---------------------------------------------------------------------------
# Pipeline complet
# ---------------------------------------------------------------------------

def detect_entities(
    text: str,
    whitelist: list[str] | None = None,
    blocklist: list[str] | None = None,
) -> list[Detection]:
    """Détecte toutes les entités à masquer dans ``text``.

    Renvoie des détections triées, sans chevauchement, avec leur label final.
    """
    if not text or not text.strip():
        return []
    analysis_text = text.translate(_SAME_LENGTH_SPACES)
    protected = _protected_spans(analysis_text)
    ocr_spans = _ocr_spans(analysis_text)

    detections = _raw_detections(analysis_text)
    detections = _apply_thresholds(detections, ocr_spans)
    detections = _trim_protected(analysis_text, detections, protected)
    detections = _clean(analysis_text, detections)
    detections = _add_uppercase_names(analysis_text, detections)
    detections = _propagate_persons(analysis_text, detections)
    detections = _trim_protected(analysis_text, detections, protected)
    detections = _apply_whitelist(analysis_text, detections, whitelist)
    detections = _add_blocklist(analysis_text, detections, blocklist)
    detections = _merge_overlaps(detections)
    detections = _merge_adjacent(analysis_text, detections)
    detections = _trim_protected(analysis_text, detections, protected)
    return _assign_labels(analysis_text, detections)


def _replace(text: str, detections: list[Detection]) -> str:
    parts: list[str] = []
    last = 0
    for d in sorted(detections, key=lambda d: d.start):
        parts.append(text[last:d.start])
        parts.append(f"[{d.label}]")
        last = d.end
    parts.append(text[last:])
    return "".join(parts)


def anonymize_text(text: str) -> str:
    """Analyse et anonymise un texte français / belge.

    Chaque entité détectée est remplacée par un label entre crochets, par
    exemple ``[PERSONNE_1]``, ``[ADRESSE]``, ``[REGISTRE_NATIONAL]``,
    ``[DATE_NAISSANCE]``… Les noms propres en capitales non identifiés (ex :
    « RENAUD TECH COMPANY ») sont remplacés par ``[Nom propre]``.

    Parameters
    ----------
    text : str
        Texte brut à anonymiser.

    Returns
    -------
    str
        Texte anonymisé avec les labels de remplacement.
    """
    return _replace(text, detect_entities(text))


# ---------------------------------------------------------------------------
# Analyse détaillée (couche de révision interactive)
# ---------------------------------------------------------------------------

def analyze_text_detailed(
    text: str,
    whitelist: list[str] | None = None,
    blocklist: list[str] | None = None,
) -> dict:
    """Analyse un texte et renvoie les détections structurées (sans masquer).

    Destinée à une interface de révision : le frontend reçoit le texte
    original et la liste des entités détectées, à valider ou refuser une par
    une.

    Parameters
    ----------
    text : str
        Texte brut à analyser.
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
    detections = detect_entities(text, whitelist, blocklist)
    return {
        "text": text,
        "detections": [
            {
                "start": d.start,
                "end": d.end,
                "type": d.entity,
                "label": d.label,
                "score": round(float(d.score), 3),
                "value": text[d.start:d.end],
            }
            for d in detections
        ],
    }


def apply_detections(text: str, detections: list[dict]) -> str:
    """Reconstruit le texte en remplaçant chaque détection par son label.

    Applique les remplacements de droite à gauche pour préserver les indices.
    Utilisé côté serveur (tests, fichiers) ; le frontend de révision fait
    le même calcul côté client pour l'interactivité.
    """
    for d in sorted(detections, key=lambda d: d["start"], reverse=True):
        text = text[: d["start"]] + f'[{d["label"]}]' + text[d["end"]:]
    return text
