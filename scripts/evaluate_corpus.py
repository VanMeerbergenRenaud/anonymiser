#!/usr/bin/env python3
"""Mesure la qualité d'anonymisation sur le corpus d'évaluation.

Utilisation (depuis la racine du dépôt) ::

    PYTHONPATH=. ./venv/bin/python scripts/evaluate_corpus.py
    PYTHONPATH=. ./venv/bin/python scripts/evaluate_corpus.py --json resultats.json
    PYTHONPATH=. ./venv/bin/python scripts/evaluate_corpus.py jugement_famille.pdf

Moteur NER : ``ANON_NLP_BACKEND=spacy`` ou ``transformers`` (défaut).
Voir ``tests/corpus/corpus_eval.py`` pour les indicateurs et le format des
annotations. Le code de sortie vaut 1 si une fuite ou une erreur est trouvée.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "corpus"))

from corpus_eval import evaluate_corpus, format_report  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("documents", nargs="*", help="documents à évaluer (défaut : tous)")
    parser.add_argument("--json", help="écrit les résultats détaillés dans ce fichier")
    parser.add_argument("--no-determinism", action="store_true",
                        help="ne traite chaque document qu'une fois")
    parser.add_argument("--quiet", action="store_true", help="tableau seul, sans détails")
    args = parser.parse_args()

    from api.nlp_engine import select_backend

    results = evaluate_corpus(args.documents or None, check_determinism=not args.no_determinism)
    print(f"Moteur NER : {select_backend()}\n")
    print(format_report(results, verbose=not args.quiet))
    if args.json:
        Path(args.json).write_text(
            json.dumps([r.as_dict() for r in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
