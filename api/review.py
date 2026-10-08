"""
review — Mots à relire après l'anonymisation.

Aucune détection automatique n'est parfaite : la liste renvoyée avec le
document signale à l'utilisateur ce qui mérite un coup d'œil avant usage,
sans rien modifier au texte :

- ce que le moteur a **choisi de conserver** alors qu'il aurait pu s'agir
  d'une personne : nom d'une personne citée dans une jurisprudence, lieu sans
  lien établi avec une personne, organisation privée ;
- les **mots à majuscule non masqués** qui ressemblent à un nom propre (en
  milieu de phrase, inconnus des listes de vocabulaire juridique, jamais
  écrits en minuscules dans le document) ;
- un mot en minuscules juste après une civilité (« Monsieur dupont »).

Les institutions, pays, sièges de juridiction et références publiques
(ECLI, numéros d'arrêts) ne sont jamais signalés : leur conservation est
sûre.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from api.public import COUNTRIES, INSTITUTION_HEADS, ORGANIZATION_WORDS
from api.recognizers import CAP_WORD, LOWER, NAME_PARTICLES, NAME_STOP_WORDS, UPPER_WORD, fold
from api.spans import Detection, SpanIndex

CASE_LAW_PERSON = "Nom cité dans une jurisprudence (conservé)"
KEPT_PLACE = "Lieu conservé (sans lien établi avec une personne)"
KEPT_ORGANIZATION = "Organisation conservée"
POSSIBLE_NAME = "Nom propre possible, non masqué"
AFTER_TITLE = "Mot en minuscules après une civilité, non masqué"

MAX_TERMS = 150
"""Nombre maximal de termes renvoyés (les premiers dans l'ordre du texte)."""

_CAPITALIZED_RE = re.compile(rf"(?<![\w'’-])(?:{UPPER_WORD}|{CAP_WORD})(?![\w-])")
_AFTER_TITLE_RE = re.compile(
    rf"(?<![\w-])(?:Monsieur|Madame|Mademoiselle|Mme|Mlle|M\.|Me|Ma[îi]tre)[ \t]+"
    rf"(?P<word>[{LOWER}][{LOWER}'’-]+)"
)
_SENTENCE_START_RE = re.compile(r"(?:^|[.!?:;«»\"“”(\[\n•–—-]|\d[.)])[ \t]*\Z")


def _vocabulary() -> frozenset[str]:
    """Mots (forme « fold ») qui ne sont jamais à signaler."""
    from api import nlp_engine  # import différé : nlp_engine importe ce module

    words = set(NAME_STOP_WORDS | NAME_PARTICLES | INSTITUTION_HEADS | ORGANIZATION_WORDS)
    words |= nlp_engine._HEADING_WORDS | nlp_engine._COMMON_FRENCH_WORDS | nlp_engine._COMMON_WORDS
    words |= nlp_engine._ID_MENTIONS | {"CAUSE", "CONTRE", "ENTRE", "POUR"}
    for country in COUNTRIES:
        words.update(country.split())
    return frozenset(words)


_VOCABULARY: Optional[frozenset[str]] = None


def _is_vocabulary(word: str) -> bool:
    global _VOCABULARY
    if _VOCABULARY is None:
        _VOCABULARY = _vocabulary()
    key = fold(word).strip(".")
    parts = [p for p in re.split(r"['’.\s-]+", key) if len(p) > 1]
    return key in _VOCABULARY or all(part in _VOCABULARY for part in parts)


def review_terms(text: str, detections: Iterable[Detection],
                 kept: Iterable[tuple[int, int, Optional[str]]],
                 excluded: Iterable[tuple[int, int]]) -> list[dict]:
    """Mots à relire : ``[{"term": …, "count": n, "reason": …}]``, dans
    l'ordre de leur première apparition.

    ``kept`` : zones conservées par le moteur, avec le motif à signaler
    (``None`` : conservation sûre). ``excluded`` : zones publiques (ECLI,
    jurisprudence, marqueurs) où rien n'est à signaler.
    """
    masked = SpanIndex((d.start, d.end) for d in detections)
    safe = SpanIndex(list(excluded) + [(s, e) for s, e, reason in kept if reason is None])
    found: dict[str, list] = {}  # terme → [première position, nombre, motif]

    def add(start: int, end: int, reason: str) -> None:
        term = " ".join(text[start:end].split())
        entry = found.get(term)
        if entry is None:
            found[term] = [start, 1, reason]
        else:
            entry[1] += 1

    flagged_spans = []
    for start, end, reason in kept:
        if reason is None or masked.overlaps(start, end):
            continue  # partie d'une adresse finalement masquée
        words = re.findall(r"[^\W\d_]+", text[start:end])
        if not any(len(w) > 3 and not _is_vocabulary(w) for w in words):
            continue  # « eur. D.H. », sigles : pas un nom
        add(start, end, reason)
        flagged_spans.append((start, end))
    flagged = SpanIndex(flagged_spans)

    lowercase_words = {w.lower() for w in re.findall(r"\b[^\W\d_]+\b", text) if w.islower()}
    for m in _CAPITALIZED_RE.finditer(text):
        start, end = m.span()
        word = m.group(0)
        if len(word) < 3 or masked.overlaps(start, end) or safe.overlaps(start, end) \
                or flagged.overlaps(start, end):
            continue
        line_start = text.rfind("\n", 0, start) + 1
        if _SENTENCE_START_RE.search(text, max(line_start - 1, 0), start):
            continue  # majuscule de début de phrase ou de ligne
        if word.isupper() and len(word) <= 3:
            continue  # sigle (« CIR », « CEE »)
        if word.isupper():
            line_end = text.find("\n", end)
            line = text[line_start:line_end if line_end != -1 else len(text)]
            if not any(ch.islower() for ch in line):
                continue  # ligne en capitales : intitulé
        if word.lower() in lowercase_words or _is_vocabulary(word):
            continue
        add(start, end, POSSIBLE_NAME)

    for m in _AFTER_TITLE_RE.finditer(text):
        start, end = m.span("word")
        if masked.overlaps(start, end) or _is_vocabulary(m.group("word")):
            continue
        add(start, end, AFTER_TITLE)

    ordered = sorted(found.items(), key=lambda item: item[1][0])[:MAX_TERMS]
    return [{"term": term, "count": count, "reason": reason}
            for term, (_, count, reason) in ordered]
