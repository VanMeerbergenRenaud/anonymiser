"""
spans — Détections (entités à masquer) et index d'intervalles.

Structures partagées par ``api.nlp_engine`` (chaîne d'anonymisation) et
``api.persons`` (noms de personnes et pseudonymes).
"""

from __future__ import annotations

import bisect
import itertools
from dataclasses import dataclass
from typing import Iterable


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
    kind: str = ""
    """Précision sur l'origine : ``"initials"`` (personne désignée par ses
    seules initiales, « P.V. »)."""


class SpanIndex:
    """Index (statique) d'intervalles ``[start, end)``.

    Répond en O(log n) à « ``[start, end)`` chevauche-t-il un intervalle
    indexé ? », là où un parcours de la liste coûterait O(n) — soit O(n²)
    pour un document entier.
    """

    def __init__(self, spans: Iterable[tuple[int, int]]) -> None:
        ordered = sorted(spans)
        self._starts = [s for s, _ in ordered]
        # _max_end[i] = plus grande fin parmi les i+1 premiers intervalles.
        self._max_end = list(itertools.accumulate((e for _, e in ordered), max))

    def overlaps(self, start: int, end: int) -> bool:
        count = bisect.bisect_left(self._starts, end)  # intervalles débutant avant `end`
        return count > 0 and self._max_end[count - 1] > start
