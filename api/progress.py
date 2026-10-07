"""
progress — Suivi d'avancement et annulation des traitements longs.

Un traitement (OCR de 200 pages, analyse d'un jugement de 500 pages…) peut
durer plusieurs minutes. Les étapes longues appellent régulièrement un
« callback » de progression ``progress(stage, done, total)`` :

- l'API le relaie au navigateur (barre de progression, maintien de la
  connexion ouverte) ;
- il sert aussi de point d'annulation : s'il lève :class:`Cancelled` (le
  navigateur s'est déconnecté), le traitement s'arrête au plus tôt au lieu
  de consommer le serveur pour rien.

Étapes (``stage``)
------------------
- ``extract`` : lecture du document (pages d'un PDF) ;
- ``ocr`` : lecture des images par OCR ;
- ``analyze`` : détection des données personnelles (NER + règles).
"""

from __future__ import annotations

from typing import Callable, Optional

ProgressCallback = Callable[[str, int, int], None]
"""``progress(stage, done, total)`` — voir la documentation du module."""


class Cancelled(Exception):
    """Le traitement a été annulé (client déconnecté)."""


def report(progress: Optional[ProgressCallback], stage: str, done: int, total: int) -> None:
    """Appelle ``progress`` s'il est défini (raccourci pour les appelants)."""
    if progress is not None:
        progress(stage, done, total)
