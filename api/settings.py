"""
settings — Réglages partagés du backend (variables d'environnement).

Point d'entrée unique pour les limites de l'application : l'API Flask, le
traitement des fichiers et la page ``/api/health`` lisent les mêmes valeurs,
ce qui évite les incohérences (ex. : une limite à 10 Mo côté serveur et à
100 Mo côté interface).

Une valeur invalide (``ANON_MAX_FILE_MB=abc``) est signalée dans les logs et
remplacée par la valeur par défaut : une faute de frappe dans la
configuration ne doit jamais empêcher le serveur de démarrer.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lecture robuste des variables d'environnement
# ---------------------------------------------------------------------------

def env_flag(name: str, default: bool) -> bool:
    """Booléen : ``0``, ``false``, ``no``, ``off`` ou vide → ``False``."""
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def env_int(name: str, default: int, minimum: int = 0) -> int:
    """Entier ≥ ``minimum`` ; valeur absente ou invalide → ``default``."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("%s=%r invalide (entier attendu) : %s utilisé", name, raw, default)
        return default
    if value < minimum:
        logger.warning("%s=%r trop petit (minimum %s) : %s utilisé", name, raw, minimum, default)
        return default
    return value


def env_float(name: str, default: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Réel dans ``[minimum, maximum]`` ; valeur absente ou invalide → ``default``."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip().replace(",", "."))
    except ValueError:
        logger.warning("%s=%r invalide (nombre attendu) : %s utilisé", name, raw, default)
        return default
    if not minimum <= value <= maximum:
        logger.warning("%s=%r hors de [%s, %s] : %s utilisé", name, raw, minimum, maximum, default)
        return default
    return value


# ---------------------------------------------------------------------------
# Limites
# ---------------------------------------------------------------------------

MAX_FILE_MB: int = env_int("ANON_MAX_FILE_MB", 100, minimum=1)
"""Taille maximale d'un fichier téléversé, en Mo (défaut : 100)."""

MAX_FILE_SIZE: int = MAX_FILE_MB * 1024 * 1024
"""Taille maximale d'un fichier téléversé, en octets."""

MAX_REQUEST_SIZE: int = MAX_FILE_SIZE + 2 * 1024 * 1024
"""Taille maximale d'une requête HTTP : le fichier + l'enveloppe multipart."""

MAX_TEXT_CHARS: int = env_int("ANON_MAX_TEXT_CHARS", 5_000_000, minimum=1_000)
"""Longueur maximale du texte analysé (≈ 2 000 pages).

Au-delà, le document est refusé avec un message clair plutôt que d'occuper
le serveur pendant des heures : le NER CamemBERT traite environ 6 000
caractères par seconde sur 4 cœurs. Un document n'est jamais anonymisé
partiellement."""

MAX_PARALLEL_JOBS: int = env_int("ANON_MAX_PARALLEL_JOBS", 2, minimum=1)
"""Nombre de fichiers traités simultanément par processus.

Les suivants attendent leur tour (l'interface affiche « En attente ») : on
évite ainsi qu'une rafale de fichiers de 100 Mo sature la mémoire et que
les traitements se ralentissent mutuellement (le NER et l'OCR utilisent
déjà tous les cœurs)."""
