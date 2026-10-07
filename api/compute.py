"""
compute — Coordination des calculs lourds (NER et OCR) dans un processus.

Le NER (PyTorch) et l'OCR (processus Tesseract) utilisent chacun tous les
cœurs. Les exécuter en même temps (deux fichiers traités simultanément) est
désastreux : les threads de PyTorch se synchronisent en permanence et
deviennent jusqu'à 50 fois plus lents quand des processus OCR leur prennent
les cœurs.

:data:`COMPUTE` est un verrou « lecteurs / rédacteur » :

- ``shared()`` : une image en cours d'OCR. Plusieurs OCR peuvent tourner
  ensemble (processus mono-thread indépendants) ;
- ``exclusive()`` : un morceau de texte en cours d'analyse NER, seul sur la
  machine. Il est prioritaire : dès qu'une analyse attend, aucune nouvelle
  image n'entre en OCR, ce qui borne son attente à la durée d'un OCR.

Le verrou est pris à grain fin (une image, un morceau de ~20 000
caractères) : deux fichiers traités en parallèle avancent tous les deux.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


class ComputeLock:
    """Verrou lecteurs / rédacteur, prioritaire au rédacteur."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._shared = 0
        self._exclusive = False
        self._waiting_exclusive = 0

    @contextmanager
    def shared(self) -> Iterator[None]:
        with self._cond:
            while self._exclusive or self._waiting_exclusive:
                self._cond.wait()
            self._shared += 1
        try:
            yield
        finally:
            with self._cond:
                self._shared -= 1
                if self._shared == 0:
                    self._cond.notify_all()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        with self._cond:
            self._waiting_exclusive += 1
            try:
                while self._exclusive or self._shared:
                    self._cond.wait()
            finally:
                self._waiting_exclusive -= 1
            self._exclusive = True
        try:
            yield
        finally:
            with self._cond:
                self._exclusive = False
                self._cond.notify_all()


COMPUTE = ComputeLock()
"""Verrou partagé par ``api.nlp_engine`` (NER) et ``api.ocr`` (OCR)."""
