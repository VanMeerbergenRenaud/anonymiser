"""Mesure de la qualité d'anonymisation sur le corpus (``tests/corpus``).

Pour chaque document de ``documents/`` décrit par ``annotations/<nom>.json`` :

- **fuites** : occurrences de valeurs à masquer encore présentes dans le
  résultat (objectif : 0) ;
- **sur-anonymisation** : occurrences de termes à conserver qui ont disparu ;
- **pseudonymes** : mentions qui doivent partager une étiquette
  (``same_label``) ou porter des étiquettes différentes (``distinct_labels``) ;
- **fidélité** : fragments attendus (``present``) ou interdits (``absent``)
  dans le texte extrait (avant anonymisation), et mots du document source
  perdus ou altérés par l'extraction (PDF et Word) ;
- **déterminisme** : deux traitements du même fichier donnent le même octet
  pour octet.

Format des annotations (JSON) ::

    {
      "document": "jugement.pdf",
      "needs_ocr": false,
      "mask": {"PERSONNE": ["DUPONT", "re:\\\\bN\\\\. Lambert"], ...},
      "keep": ["Tribunal de la famille", ...],
      "same_label": [["Jean DUPONT", "Monsieur DUPONT"]],
      "distinct_labels": [["N. Lambert", "A. Gérard"]],
      "present": ["avocat-intermédiaire", "re:\\\\nI\\\\. Procédure\\\\n"],
      "absent": ["avocatintermédiaire"],
      "absent_output": ["re:\\\\b[A-Z]\\\\. \\\\[PERSONNE"],
      "ignore_lost": ["re:^\\\\d{1,2}$"]
    }

Une valeur préfixée par ``re:`` est une expression régulière ; sinon c'est
un texte exact, recherché comme mot entier (sensible à la casse).

Utilisation : ``PYTHONPATH=. ./venv/bin/python scripts/evaluate_corpus.py``.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

CORPUS = Path(__file__).resolve().parent
DOCUMENTS = CORPUS / "documents"
ANNOTATIONS = CORPUS / "annotations"


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

def load_annotations(names: Optional[Iterable[str]] = None) -> list[dict]:
    wanted = set(names or [])
    result = []
    for path in sorted(ANNOTATIONS.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if wanted and data["document"] not in wanted and path.stem not in wanted:
            continue
        result.append(data)
    return result


def _pattern(value: str) -> re.Pattern:
    if value.startswith("re:"):
        return re.compile(value[3:])
    return re.compile(rf"(?<![\w]){re.escape(value)}(?![\w])")


def _count(value: str, text: str) -> int:
    return sum(1 for _ in _pattern(value).finditer(text))


# ---------------------------------------------------------------------------
# Accès au moteur (compatible avec les versions antérieures du code)
# ---------------------------------------------------------------------------

def extract_text(filename: str, content: bytes) -> str:
    """Texte du document tel qu'il est anonymisé (après extraction et OCR)."""
    import api.anonymize_file as af

    if hasattr(af, "extract_text"):
        return af.extract_text(filename, content)
    # Versions antérieures : même enchaînement que process_file.
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        pdf = af._open_pdf(content)
        try:
            text, _, _ = af._render(af._pdf_to_doc(pdf))
        finally:
            pdf.close()
    elif ext == ".docx":
        text, _, _ = af._render(af._docx_to_doc(content))
    elif ext == ".txt":
        doc = af._Doc()
        doc.text(af._decode_text(content))
        text, _, _ = af._render(doc)
    else:
        text, _, _ = af._render(af._image_to_doc(content))
        if not text.strip():
            text = af.NO_TEXT_IN_IMAGE
    return af._clean_text(text)


def _detections(text: str):
    from api.nlp_engine import detect_entities

    return detect_entities(text)


def _apply(text: str, detections) -> str:
    parts, last = [], 0
    for d in sorted(detections, key=lambda d: d.start):
        parts.append(text[last:d.start])
        parts.append(f"[{d.label}]")
        last = d.end
    parts.append(text[last:])
    return "".join(parts)


# ---------------------------------------------------------------------------
# Fidélité du texte : mots du document source
# ---------------------------------------------------------------------------

_PIECE_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _pieces(text: str) -> list[str]:
    return _PIECE_RE.findall(text)


def source_words(filename: str, content: bytes, ignore_lines: Iterable[str] = (),
                 repeated_lines: Iterable[str] = ()) -> Optional[list[str]]:
    """Mots du document d'origine (couche texte PDF, texte Word), dans l'ordre.

    PDF : les lignes visuelles correspondant à ``ignore_lines`` (n° de page)
    sont écartées, de même que les répétitions (toutes sauf la première) des
    lignes correspondant à ``repeated_lines`` (en-têtes, pieds de page).
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        import fitz

        ignored = [_pattern(v) for v in ignore_lines]
        repeated = [_pattern(v) for v in repeated_lines]
        seen: set[int] = set()
        pdf = fitz.open(stream=content, filetype="pdf")
        words: list[str] = []
        for page in pdf:
            rows: dict[int, list[tuple[float, str]]] = {}
            for w in page.get_text("words", sort=True):
                rows.setdefault(round(w[3] / 3), []).append((w[0], w[4]))
            for key in sorted(rows):
                row = [word for _, word in sorted(rows[key])]
                line = " ".join(row)
                if any(p.search(line) for p in ignored):
                    continue
                hit = next((i for i, p in enumerate(repeated) if p.search(line)), None)
                if hit is not None:
                    if hit in seen:
                        continue
                    seen.add(hit)
                words.extend(row)
        pdf.close()
        return words
    if ext == ".docx":
        import io
        import zipfile

        from lxml import etree

        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        words = []
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            for name in archive.namelist():
                if re.fullmatch(r"word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml", name):
                    root = etree.fromstring(archive.read(name))
                    for p in root.iter(f"{ns}p"):
                        parts = [
                            (el.text or "") if el.tag == f"{ns}t" else " "
                            for el in p.iter(f"{ns}t", f"{ns}br", f"{ns}cr", f"{ns}tab")
                        ]
                        words.extend("".join(parts).split())
        return words
    return None


def word_fidelity(source: list[str], extracted: str,
                  ignore: Iterable[str] = ()) -> tuple[list[str], list[str]]:
    """Mots perdus et mots altérés par l'extraction.

    Les mots sont comparés par morceaux alphanumériques (« eux-mêmes » →
    « eux », « mêmes ») ; une césure correctement recollée (« conven- » +
    « tionnelles » → « conventionnelles ») n'est comptée ni perdue ni altérée.
    """
    ignore_patterns = [_pattern(v) for v in ignore]
    raw: list[str] = []
    hyphenated: set[int] = set()  # index du dernier morceau d'un mot coupé (« conven- »)
    for word in source:
        if any(p.search(word) for p in ignore_patterns):
            continue
        raw.extend(_pieces(word))
        if word.endswith("-") and raw:
            hyphenated.add(len(raw) - 1)
    got = Counter(_pieces(extracted))
    want = Counter(raw)
    lost = want - got
    extra = got - want
    # Césures recollées : « conven- » + « tionnelles » = « conventionnelles ».
    for index, (a, b) in enumerate(zip(raw, raw[1:])):
        if index not in hyphenated:
            continue  # deux mots soudés (« déposéeau ») restent une erreur
        joined = a + b
        if lost[a] and lost[b] and extra[joined]:
            lost[a] -= 1
            lost[b] -= 1
            extra[joined] -= 1
    # Numéros de liste restitués (numérotation automatique Word : « 1. », « a) »).
    for piece in list(extra):
        if re.fullmatch(r"\d{1,3}|[a-zA-Z]", piece):
            del extra[piece]
    return sorted((+lost).elements()), sorted((+extra).elements())


# ---------------------------------------------------------------------------
# Évaluation d'un document
# ---------------------------------------------------------------------------

@dataclass
class DocumentResult:
    name: str
    skipped: str = ""
    seconds: float = 0.0
    mask_total: int = 0
    leaks: list[tuple[str, str, int]] = field(default_factory=list)
    keep_total: int = 0
    keep_missing: list[tuple[str, int]] = field(default_factory=list)
    label_checks: int = 0
    label_errors: list[str] = field(default_factory=list)
    fidelity_checks: int = 0
    fidelity_errors: list[str] = field(default_factory=list)
    words_total: int = 0
    words_lost: list[str] = field(default_factory=list)
    words_altered: list[str] = field(default_factory=list)
    deterministic: Optional[bool] = None

    @property
    def leaked(self) -> int:
        return sum(n for _, _, n in self.leaks)

    @property
    def missing(self) -> int:
        return sum(n for _, n in self.keep_missing)

    @property
    def ok(self) -> bool:
        return bool(self.skipped) or not (
            self.leaks or self.keep_missing or self.label_errors
            or self.fidelity_errors or self.deterministic is False
        )

    def as_dict(self) -> dict:
        return {
            "document": self.name, "skipped": self.skipped, "seconds": round(self.seconds, 2),
            "mask_total": self.mask_total, "leaked": self.leaked, "leaks": self.leaks,
            "keep_total": self.keep_total, "missing": self.missing,
            "keep_missing": self.keep_missing, "label_checks": self.label_checks,
            "label_errors": self.label_errors, "fidelity_checks": self.fidelity_checks,
            "fidelity_errors": self.fidelity_errors, "words_total": self.words_total,
            "words_lost": self.words_lost, "words_altered": self.words_altered,
            "deterministic": self.deterministic,
        }


def _mention_pattern(mention: str) -> re.Pattern:
    """Motif d'une mention ; la partie à vérifier est entre accolades
    (« Monsieur {DUPONT} ») ou dans le groupe 1 d'une expression ``re:``."""
    if mention.startswith("re:"):
        return re.compile(mention[3:])
    if "{" in mention:
        before, rest = mention.split("{", 1)
        target, after = rest.split("}", 1)
        return re.compile(rf"(?<![\w]){re.escape(before)}({re.escape(target)}){re.escape(after)}(?![\w])")
    return re.compile(rf"(?<![\w])({re.escape(mention)})(?![\w])")


def _labels_for(mention: str, text: str, detections) -> list[Optional[str]]:
    """Étiquette couvrant chaque occurrence de ``mention`` (None si non masquée)."""
    labels = []
    for m in _mention_pattern(mention).finditer(text):
        start, end = m.span(1) if m.re.groups else m.span()
        label = None
        for d in detections:
            if d.start <= start and end <= d.end:
                label = d.label
                break
        labels.append(label)
    return labels


def evaluate_document(annotation: dict, check_determinism: bool = True) -> DocumentResult:
    from api import ocr
    from api.anonymize_file import process_file

    name = annotation["document"]
    result = DocumentResult(name)
    if annotation.get("needs_ocr") and not ocr.is_available():
        result.skipped = "OCR indisponible"
        return result
    content = (DOCUMENTS / name).read_bytes()

    started = time.perf_counter()
    output = process_file(name, content).content.decode("utf-8")
    result.seconds = time.perf_counter() - started

    # Second traitement, indépendant : sert au contrôle des étiquettes et du
    # déterminisme (la sortie reconstruite doit être identique à l'octet près).
    extracted = extract_text(name, content)
    detections = _detections(extracted)
    if check_determinism:
        result.deterministic = _apply(extracted, detections) == output

    # Fuites
    for category, values in annotation.get("mask", {}).items():
        for value in values:
            total = _count(value, extracted)
            result.mask_total += max(total, 1)
            remaining = _count(value, output)
            if remaining:
                result.leaks.append((category, value, remaining))
    for value in annotation.get("absent_output", []):
        result.mask_total += 1
        remaining = _count(value, output)
        if remaining:
            result.leaks.append(("FORME", value, remaining))

    # Sur-anonymisation
    for value in annotation.get("keep", []):
        total = _count(value, extracted)
        result.keep_total += max(total, 1)
        kept = _count(value, output)
        if kept < max(total, 1):
            result.keep_missing.append((value, max(total, 1) - kept))

    # Pseudonymes
    groups_same = annotation.get("same_label", [])
    groups_distinct = annotation.get("distinct_labels", [])
    if groups_same or groups_distinct:
        for group in groups_same:
            result.label_checks += 1
            labels = [lab for mention in group for lab in _labels_for(mention, extracted, detections)]
            found = set(labels)
            if None in found or len(found) != 1:
                result.label_errors.append(f"même personne : {group} → {sorted(map(str, found))}")
        for group in groups_distinct:
            result.label_checks += 1
            sets = [set(_labels_for(mention, extracted, detections)) for mention in group]
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    common = (sets[i] & sets[j]) - {None}
                    if common:
                        result.label_errors.append(
                            f"personnes distinctes : {group[i]!r} et {group[j]!r} → {sorted(common)}")

    # Fidélité
    for value in annotation.get("present", []):
        result.fidelity_checks += 1
        if not _pattern(value).search(extracted):
            result.fidelity_errors.append(f"absent du texte extrait : {value!r}")
    for value in annotation.get("absent", []):
        result.fidelity_checks += 1
        if _pattern(value).search(extracted):
            result.fidelity_errors.append(f"présent à tort : {value!r}")
    words = source_words(name, content, annotation.get("ignore_lines", []),
                         annotation.get("repeated_lines", []))
    if words:  # PDF scanné (sans couche texte), image, texte brut : non mesuré
        lost, altered = word_fidelity(words, extracted)
        result.words_total = len(words)
        result.words_lost = lost
        result.words_altered = altered
    return result


def evaluate_corpus(names: Optional[Iterable[str]] = None,
                    check_determinism: bool = True) -> list[DocumentResult]:
    return [evaluate_document(a, check_determinism) for a in load_annotations(names)]


# ---------------------------------------------------------------------------
# Rapport
# ---------------------------------------------------------------------------

def _pct(part: int, total: int) -> str:
    return f"{100 * part / total:.1f} %" if total else "—"


def format_report(results: list[DocumentResult], verbose: bool = True) -> str:
    lines = [
        "| Document | Fuites | Sur-anonymisation | Pseudonymes (erreurs) | Fidélité (erreurs) "
        "| Mots perdus / altérés | Déterministe | Durée |",
        "|---|---|---|---|---|---|---|---|",
    ]
    totals = Counter()
    for r in results:
        if r.skipped:
            lines.append(f"| {r.name} | ignoré ({r.skipped}) | | | | | | |")
            continue
        totals.update(mask=r.mask_total, leaked=r.leaked, keep=r.keep_total, missing=r.missing,
                      labels=r.label_checks, label_errors=len(r.label_errors),
                      fidelity=r.fidelity_checks, fidelity_errors=len(r.fidelity_errors),
                      words=r.words_total, lost=len(r.words_lost), altered=len(r.words_altered),
                      seconds=r.seconds)
        det = "—" if r.deterministic is None else ("oui" if r.deterministic else "NON")
        words = f"{len(r.words_lost)} / {len(r.words_altered)}" if r.words_total else "—"
        lines.append(
            f"| {r.name} | {r.leaked}/{r.mask_total} ({_pct(r.leaked, r.mask_total)}) "
            f"| {r.missing}/{r.keep_total} ({_pct(r.missing, r.keep_total)}) "
            f"| {len(r.label_errors)}/{r.label_checks} | {len(r.fidelity_errors)}/{r.fidelity_checks} "
            f"| {words} | {det} | {r.seconds:.1f} s |"
        )
    lines.append(
        f"| **Total** | **{totals['leaked']}/{totals['mask']} ({_pct(totals['leaked'], totals['mask'])})** "
        f"| **{totals['missing']}/{totals['keep']} ({_pct(totals['missing'], totals['keep'])})** "
        f"| **{totals['label_errors']}/{totals['labels']}** "
        f"| **{totals['fidelity_errors']}/{totals['fidelity']}** "
        f"| **{totals['lost']} / {totals['altered']}** | | **{totals['seconds']:.1f} s** |"
    )
    if verbose:
        for r in results:
            details = []
            details += [f"  - fuite [{c}] {v!r} ×{n}" for c, v, n in r.leaks]
            details += [f"  - disparu {v!r} ×{n}" for v, n in r.keep_missing]
            details += [f"  - {e}" for e in r.label_errors + r.fidelity_errors]
            if r.words_lost:
                details.append(f"  - mots perdus : {r.words_lost[:40]}")
            if r.words_altered:
                details.append(f"  - mots altérés : {r.words_altered[:40]}")
            if details:
                lines.append("")
                lines.append(f"**{r.name}**")
                lines.extend(details)
    return "\n".join(lines)
