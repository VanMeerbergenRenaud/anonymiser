"""
anonymize_text — Point d'entrée pour l'anonymisation de texte brut.

Ce module expose les fonctions du moteur NLP partagé utilisées par les
endpoints ``/api/anonymize_text`` et ``/api/analyze_text`` (``api.index``).
"""

from api.nlp_engine import (  # noqa: F401 — ré-export intentionnel
    anonymize_text,
    analyze_text_detailed,
    apply_detections,
)

__all__ = ["anonymize_text", "analyze_text_detailed", "apply_detections"]
