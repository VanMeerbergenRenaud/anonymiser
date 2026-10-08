"""Mesure sur le corpus d'évaluation (``tests/corpus``) : aucune fuite tolérée.

Chaque document est traité deux fois (déterminisme). Pour le tableau
complet des indicateurs : ``scripts/evaluate_corpus.py``.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "corpus"))

from corpus_eval import evaluate_document, load_annotations  # noqa: E402

ANNOTATIONS = load_annotations()


@pytest.mark.parametrize("annotation", ANNOTATIONS, ids=[a["document"] for a in ANNOTATIONS])
def test_corpus_document(annotation):
    result = evaluate_document(annotation)
    if result.skipped:
        pytest.skip(result.skipped)
    assert not result.leaks, f"fuites : {result.leaks}"
    assert not result.keep_missing, f"sur-anonymisation : {result.keep_missing}"
    assert not result.label_errors, result.label_errors
    assert not result.fidelity_errors, result.fidelity_errors
    assert not result.words_lost, f"mots perdus : {result.words_lost}"
    assert not result.words_altered, f"mots altérés : {result.words_altered}"
    assert not result.swallowed, f"mots avalés dans un nom : {result.swallowed}"
    assert result.deterministic, "deux traitements du même fichier diffèrent"
