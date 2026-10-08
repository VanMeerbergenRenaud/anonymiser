# Corpus d'évaluation

Documents juridiques servant à mesurer la qualité de l'anonymisation : taux de
fuite (objectif 0 %), sur-anonymisation, cohérence des pseudonymes, fidélité du
texte et déterminisme.

| Document | Type | Points testés |
|---|---|---|
| `arret_cc_2024_001.pdf` | Arrêt n° 1/2024 de la Cour constitutionnelle (public, 24 pages, [source](https://fr.const-court.be/public/f/2024/2024-001f.pdf)) | numéros de rôle en liste, juges et avocats « N. Dupont », signatures sur deux colonnes, institutions, jurisprudence citée, ECLI, blason (OCR), césures, titres et puces |
| `jugement_famille.pdf` | Jugement du tribunal de la famille (fictif) | famille portant le même nom, enfants, magistrats, double interligne, pied de page répété, colonnes de signature |
| `citation.pdf` | Citation d'huissier (fictive) | société requérante (conservée), adresses, plaque, registre national |
| `conclusions.docx` | Conclusions d'avocat (fictives) | en-tête du cabinet, numérotation automatique Word, puces, tableau, jurisprudence, initiales d'un témoin |
| `acte_notarie.docx` | Acte de vente (fictif) | comparants numérotés, numéros nationaux, lieux de naissance |
| `courrier.docx` | Courrier entre avocats (fictif) | en-tête, TVA, « Concerne : DUPUIS / MERTENS », image de signature |
| `bail.txt` | Bail (fictif) | adresse néerlandophone, téléphones belges et français |
| `pv_police.txt` | Procès-verbal d'audition (fictif) | champs de formulaire, carte d'identité, prénom seul |
| `scan_tampon.pdf` | Page scannée (fictive) | OCR, tampon « RG 21/123/A », blason, signature sur un nom |
| `carte_identite.png` | Photo de carte d'identité (fictive) | texte court par champs |
| `logo.png`, `signature.png` | Images sans texte | l'OCR ne doit rien inventer |

Tous les noms, adresses et numéros des documents fictifs sont inventés. Les
documents fictifs sont générés par `build_corpus.py` (versionnés : il n'est
utile de relancer ce script que pour les modifier).

## Annotations

`annotations/<document>.json` décrit ce qui doit être masqué (`mask`),
conservé (`keep`), les mentions d'une même personne (`same_label`), les
personnes à distinguer (`distinct_labels`) et les contrôles de fidélité du
texte extrait (`present`, `absent`). Le format est détaillé dans
`corpus_eval.py`.

## Mesure

```bash
PYTHONPATH=. ./venv/bin/python scripts/evaluate_corpus.py
TESSERACT_CMD=/chemin/tesseract-4.1.1 PYTHONPATH=. ./venv/bin/python scripts/evaluate_corpus.py
```

`tests/test_corpus.py` exécute la même mesure dans `pytest` et échoue à la
moindre fuite.
