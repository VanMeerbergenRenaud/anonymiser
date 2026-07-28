"""
anonymize_text — Point d'entrée pour l'anonymisation de texte brut.

Ce module expose simplement la fonction ``anonymize_text()`` du moteur NLP
partagé. Il est importé par ``server.py`` pour l'endpoint ``/api/anonymize_text``.
"""

from api.nlp_engine import (  # noqa: F401 — ré-export intentionnel
    anonymize_text,
    analyze_text_detailed,
    apply_detections,
)

__all__ = ["anonymize_text", "analyze_text_detailed", "apply_detections"]
