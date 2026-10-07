"""
anonymize_file — Conversion et anonymisation de fichiers (TXT, DOCX, PDF, images).

Chaque fichier est converti en texte brut (UTF-8), puis anonymisé via le
moteur partagé de ``api.nlp_engine``. Le résultat est toujours un fichier
``.txt`` anonymisé.

Formats supportés en entrée
---------------------------
- **.txt** : décodage robuste (UTF-8, UTF-16, Windows-1252…) → anonymisation
- **.docx** : texte du corps (dans l'ordre, tableaux compris), en-têtes et
  pieds de page, zones de texte, notes de bas de page **+ OCR des images**
- **.pdf** : texte de chaque page (lignes recollées, césures retirées),
  champs de formulaire, commentaires **+ OCR des images et des pages
  scannées**
- **images** (.png, .jpg, .jpeg, .tif, .tiff, .bmp, .gif, .webp) : OCR

Le texte lu dans une image est encadré par les marqueurs de ``api.ocr``
(« --- Texte extrait d'une image (attention) --- ») pour signaler qu'il
provient d'une reconnaissance automatique et doit être relu.

Gros fichiers (jusqu'à ``settings.MAX_FILE_MB``)
-------------------------------------------------
- Les pages scannées et images de PDF ne sont rendues qu'au moment d'être
  lues par l'OCR (jamais toutes en mémoire) ; l'OCR des pages précédentes
  se poursuit pendant le rendu de la suivante.
- Chaque étape signale son avancement (``progress``) et peut être annulée
  (voir ``api.progress``).
- Un document dont le texte dépasse ``settings.MAX_TEXT_CHARS`` est refusé
  dès que la limite est atteinte, sans attendre la fin de l'OCR.
"""

from __future__ import annotations

import io
import logging
import os
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

import fitz  # PyMuPDF
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from lxml import etree

from api import ocr, settings
from api.nlp_engine import TextTooLongError, anonymize_text, check_text_length, known_word
from api.progress import ProgressCallback, report

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MAX_FILE_SIZE: int = settings.MAX_FILE_SIZE
"""Taille maximale d'un fichier uploadé (``ANON_MAX_FILE_MB``, défaut 100 Mo)."""

IMAGE_EXTENSIONS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".jfif", ".tif", ".tiff", ".bmp", ".gif", ".webp",
})
"""Images téléversées directement (lues par OCR)."""

SUPPORTED_EXTENSIONS: tuple[str, ...] = (".txt", ".docx", ".pdf", *sorted(IMAGE_EXTENSIONS))

MAX_OCR_PAGES: int = settings.env_int("ANON_OCR_MAX_PAGES", 300, minimum=0)
"""Nombre maximal de pages / images lues par OCR dans un même fichier
(≈ 1,3 s par page A4 scannée avec 4 processus OCR)."""

_PDF_DPI = 300
"""Résolution de rendu des pages scannées (300 dpi = optimum Tesseract)."""

_MIN_IMAGE_PX = 40
"""Images plus petites (icônes, puces, filets) : ignorées."""


NO_TEXT_IN_IMAGE = "--- Aucun texte détecté dans l'image ---"


class FileProcessingError(ValueError):
    """Erreur « utilisateur » (fichier protégé, corrompu, trop long…).

    ``status`` est le code HTTP à renvoyer (400 par défaut).
    """

    status: int = 400


class DocumentTooLongError(FileProcessingError):
    """Le texte du document dépasse ``settings.MAX_TEXT_CHARS``."""

    status = 413


# ---------------------------------------------------------------------------
# Document intermédiaire : texte + images à lire
# ---------------------------------------------------------------------------

ImageSource = Callable[[], Optional[bytes]]
"""Rendu différé d'une image (page de PDF…), appelé au moment de l'OCR."""


@dataclass
class _Image:
    data: bytes | ImageSource | None
    """Image prête pour l'OCR (PNG/JPEG…), fonction qui la produit, ou None
    si le format est illisible."""

    over_limit: bool = False
    """Vrai si l'image dépasse le nombre maximal d'OCR par fichier."""

    native_words: frozenset[str] = frozenset()
    """Mots déjà présents dans la couche texte de la page (PDF) : si l'image
    ne contient que ceux-là, son texte OCR serait un doublon."""


@dataclass
class _Doc:
    """Suite ordonnée de blocs de texte et d'images.

    Les textes consécutifs sont regroupés (un paragraphe par ligne) ; une
    image ou un appel à :meth:`section` (nouvelle page, pied de page…)
    démarre un nouveau bloc, séparé par une ligne vide dans le résultat.
    """

    parts: list[str | _Image] = field(default_factory=list)
    _new_block: bool = True
    text_length: int = 0
    """Nombre de caractères de texte (hors OCR) : garde-fou de longueur."""

    def text(self, value: str) -> None:
        if not value or not value.strip():
            return
        if self.parts and isinstance(self.parts[-1], str) and not self._new_block:
            self.parts[-1] += "\n" + value
        else:
            self.parts.append(value)
        self._new_block = False
        self._count(value)

    def continue_last(self, value: str,
                      join: Optional[Callable[[str, str], str]] = None) -> None:
        """Prolonge le dernier bloc de texte (phrase coupée par un saut de page)."""
        last = self.parts[-1] if self.parts else None
        if not isinstance(last, str):
            self.text(value)
            return
        self.parts[-1] = join(last, value) if join else last.rstrip() + " " + value.lstrip()
        self._count(value)

    def _count(self, value: str) -> None:
        self.text_length += len(value) + 1
        if self.text_length > settings.MAX_TEXT_CHARS:
            # Refus immédiat : inutile de lire (et d'OCRiser) la suite.
            raise DocumentTooLongError(str(TextTooLongError()))

    def image(self, image: _Image) -> None:
        self.parts.append(image)
        self._new_block = True

    def section(self) -> None:
        self._new_block = True

    def plain_text(self) -> str:
        return "\n".join(p for p in self.parts if isinstance(p, str))


@dataclass
class ProcessedFile:
    """Résultat du traitement d'un fichier."""

    content: bytes
    """Texte anonymisé, encodé en UTF-8."""

    ocr_images: int = 0
    """Nombre d'images dont du texte a été extrait par OCR."""

    skipped_images: int = 0
    """Nombre d'images non analysées (OCR indisponible, format, limite)."""


def _append(doc: _Doc, items: list[str | _Image]) -> None:
    for item in items:
        if isinstance(item, str):
            doc.text(item)
        else:
            doc.image(item)


def _render(doc: _Doc, progress: Optional[ProgressCallback] = None) -> tuple[str, int, int]:
    """Lance l'OCR des images (en parallèle) et assemble le texte final.

    Renvoie ``(texte, images_lues, images_non_analysées)``.
    """
    available = ocr.is_available()
    to_read: list[_Image] = []
    for part in doc.parts:
        if isinstance(part, _Image) and part.data is not None and not part.over_limit:
            if len(to_read) < MAX_OCR_PAGES:
                to_read.append(part)
            else:
                part.over_limit = True
    results = {}
    if available and to_read:
        texts = ocr.ocr_many([img.data for img in to_read], progress=progress)
        results = dict(zip(map(id, to_read), texts))

    out: list[str] = []
    found = skipped = 0
    notices: set[str] = set()

    def notice(line: str) -> None:
        # Une seule mention par document et par motif suffit.
        if line not in notices:
            notices.add(line)
            out.append(line)

    for part in doc.parts:
        if isinstance(part, str):
            out.append(part.strip("\n"))
            continue
        if part.over_limit:
            skipped += 1
            notice(ocr.OCR_LIMIT_REACHED)
        elif part.data is None:
            skipped += 1
            notice(ocr.OCR_UNSUPPORTED)
        elif not available:
            skipped += 1
            notice(ocr.OCR_UNAVAILABLE)
        else:
            result = results.get(id(part))
            if result is None or not result.has_text:
                continue  # photo, logo, signature : aucun texte lisible
            words = _ocr_words(result.text)
            if part.native_words and words and len(words & part.native_words) >= 0.6 * len(words):
                continue  # texte déjà présent dans la page (PDF « scanné + OCR »)
            out.append(ocr.wrap(result.text))
            found += 1
    return "\n\n".join(p for p in out if p.strip()), found, skipped


def _ocr_words(text: str) -> set[str]:
    """Mots (≥ 3 lettres, minuscules) servant à repérer les doublons OCR."""
    return set(re.findall(r"\w{3,}", text.lower()))


# ---------------------------------------------------------------------------
# Nettoyage du texte extrait
# ---------------------------------------------------------------------------

_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)


def _clean_text(text: str) -> str:
    """Normalise le texte extrait (ligatures, espaces exotiques, blancs)."""
    text = text.translate(_INVISIBLE)
    # NFKC : ligatures « ﬁ » → « fi », espaces insécables → espaces, etc.
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


@dataclass
class Extraction:
    """Texte d'un document, prêt à être anonymisé."""

    text: str
    ocr_images: int = 0
    skipped_images: int = 0


def _finish(text: str, found: int, skipped: int) -> Extraction:
    cleaned = _clean_text(text)
    try:
        check_text_length(cleaned)
    except TextTooLongError as exc:
        raise DocumentTooLongError(str(exc)) from exc
    return Extraction(cleaned, found, skipped)


def _anonymize(extraction: Extraction, progress: Optional[ProgressCallback]) -> ProcessedFile:
    anonymized = anonymize_text(extraction.text, progress=progress)
    return ProcessedFile(anonymized.encode("utf-8"), ocr_images=extraction.ocr_images,
                         skipped_images=extraction.skipped_images)


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------

def _decode_text(content: bytes) -> str:
    """Décode un fichier texte quel que soit son encodage (BOM, UTF-8, ANSI)."""
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace")
    if content.startswith(b"\xef\xbb\xbf"):
        return content[3:].decode("utf-8", errors="replace")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    # Fichiers « Texte brut » Windows (Bloc-notes, Word) : Windows-1252.
    try:
        return content.decode("cp1252")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _extract_txt(content: bytes, progress: Optional[ProgressCallback]) -> tuple[str, int, int]:
    doc = _Doc()
    doc.text(_decode_text(content))
    return _render(doc, progress)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

_NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "v": "urn:schemas-microsoft-com:vml",
    "mc": "http://schemas.openxmlformats.org/markup-compatibility/2006",
}


def _q(prefix: str, tag: str) -> str:
    return f"{{{_NS[prefix]}}}{tag}"


_W_P, _W_TBL, _W_TR, _W_TC = _q("w", "p"), _q("w", "tbl"), _q("w", "tr"), _q("w", "tc")
_W_T, _W_TAB, _W_BR, _W_CR = _q("w", "t"), _q("w", "tab"), _q("w", "br"), _q("w", "cr")
_W_NBHYPHEN, _W_SOFTHYPHEN = _q("w", "noBreakHyphen"), _q("w", "softHyphen")
_W_SKIP = {_q("w", "delText"), _q("w", "instrText"), _q("w", "delInstrText"),
           _q("w", "rPr"), _q("w", "pPr"), _q("w", "sectPr")}
_W_TXBX = _q("w", "txbxContent")
_A_BLIP = _q("a", "blip")
_V_IMAGEDATA = _q("v", "imagedata")
_MC_FALLBACK = _q("mc", "Fallback")
_R_EMBED, _R_ID = _q("r", "embed"), _q("r", "id")
_W_TYPE = _q("w", "type")
_W_VAL = _q("w", "val")


# --- Numérotation automatique (listes « 1. », « a) », puces) ----------------

_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def _format_number(n: int, fmt: str) -> str:
    """Numéro de liste Word dans le format ``w:numFmt`` donné."""
    if fmt in ("none", "bullet"):
        return ""
    if fmt in ("lowerLetter", "upperLetter"):
        letters = ""
        while n > 0:
            n, rest = divmod(n - 1, 26)
            letters = chr(ord("a") + rest) + letters
        return letters if fmt == "lowerLetter" else letters.upper()
    if fmt in ("lowerRoman", "upperRoman"):
        roman = ""
        for value, digits in _ROMAN:
            while n >= value:
                roman += digits
                n -= value
        return roman if fmt == "upperRoman" else roman.lower()
    if fmt == "decimalZero":
        return f"{n:02d}"
    if fmt == "ordinal":
        return f"{n}er" if n == 1 else f"{n}e"
    return str(n)


def _bullet(symbol: str) -> str:
    """Puce Word → caractère lisible (les polices Symbol / Wingdings utilisent
    la zone d'usage privé : «  » est une puce ronde)."""
    if not symbol or any(0xE000 <= ord(ch) <= 0xF8FF for ch in symbol):
        return "•"
    return "◦" if symbol == "o" else symbol


class _Numbering:
    """Numéros des paragraphes de listes Word (``w:numPr`` direct ou hérité
    du style) : ils ne figurent pas dans le texte et étaient perdus."""

    def __init__(self, document) -> None:
        self.levels: dict[tuple[str, int], tuple[str, str, int]] = {}
        self.abstract: dict[str, str] = {}
        self.restart: dict[str, dict[int, int]] = {}
        self.style_numbering: dict[str, tuple[str, Optional[int]]] = {}
        self.counters: dict[str, dict[int, int]] = {}
        self.started: set[str] = set()
        try:
            root = document.part.numbering_part.element
        except (KeyError, NotImplementedError, AttributeError):
            root = None
        if root is not None:
            abstract_levels: dict[str, dict[int, tuple[str, str, int]]] = {}
            for abstract in root.iter(_q("w", "abstractNum")):
                levels = abstract_levels.setdefault(abstract.get(_q("w", "abstractNumId")), {})
                for lvl in abstract.findall(_q("w", "lvl")):
                    levels[int(lvl.get(_q("w", "ilvl"), "0"))] = (
                        self._val(lvl, "numFmt", "decimal"), self._val(lvl, "lvlText", ""),
                        int(self._val(lvl, "start", "1") or 1),
                    )
            for num in root.iter(_q("w", "num")):
                num_id = num.get(_q("w", "numId"))
                abstract_id = self._val(num, "abstractNumId", "")
                self.abstract[num_id] = abstract_id
                for ilvl, level in abstract_levels.get(abstract_id, {}).items():
                    self.levels[(num_id, ilvl)] = level
                for override in num.findall(_q("w", "lvlOverride")):
                    start = override.find(_q("w", "startOverride"))
                    if start is not None:
                        self.restart.setdefault(num_id, {})[
                            int(override.get(_q("w", "ilvl"), "0"))] = int(start.get(_W_VAL, "1"))
        try:
            styles = document.styles.element
        except AttributeError:
            styles = None
        if styles is not None:
            raw: dict[str, tuple[Optional[str], Optional[int], Optional[str]]] = {}
            for style in styles.iter(_q("w", "style")):
                num_pr = style.find(f"{_q('w', 'pPr')}/{_q('w', 'numPr')}")
                num_id = ilvl = None
                if num_pr is not None:
                    num_id = self._val(num_pr, "numId", None)
                    level = self._val(num_pr, "ilvl", None)
                    ilvl = int(level) if level is not None else None
                based_on = self._val(style, "basedOn", None)
                raw[style.get(_q("w", "styleId"))] = (num_id, ilvl, based_on)
            for style_id in raw:
                seen, current = set(), style_id
                while current in raw and current not in seen:
                    seen.add(current)
                    num_id, ilvl, based_on = raw[current]
                    if num_id is not None:
                        self.style_numbering[style_id] = (num_id, ilvl)
                        break
                    current = based_on

    @staticmethod
    def _val(element, tag: str, default):
        child = element.find(_q("w", tag))
        return child.get(_W_VAL, default) if child is not None else default

    def label(self, p) -> str:
        """Numéro (« 2. », « b) », « • ») du paragraphe ``p``, ou ""."""
        ppr = p.find(_q("w", "pPr"))
        num_id = ilvl = None
        if ppr is not None:
            num_pr = ppr.find(_q("w", "numPr"))
            if num_pr is not None:
                num_id = self._val(num_pr, "numId", None)
                level = self._val(num_pr, "ilvl", None)
                ilvl = int(level) if level is not None else None
            if num_id is None:
                style = self._val(ppr, "pStyle", None)
                if style in self.style_numbering:
                    num_id, style_level = self.style_numbering[style]
                    ilvl = ilvl if ilvl is not None else style_level
        if not num_id or num_id == "0":
            return ""
        ilvl = ilvl or 0
        level = self.levels.get((num_id, ilvl))
        if level is None:
            return ""
        fmt, text, start = level
        if fmt == "bullet":
            return _bullet(text)
        key = self.abstract.get(num_id, num_id)
        counters = self.counters.setdefault(key, {})
        if num_id not in self.started and num_id in self.restart:
            for lvl, value in self.restart[num_id].items():
                counters[lvl] = value - 1
        self.started.add(num_id)
        counters[ilvl] = counters.get(ilvl, start - 1) + 1
        for deeper in [lvl for lvl in counters if lvl > ilvl]:
            del counters[deeper]  # sous-niveaux : recommencent à leur début

        def number(match: re.Match) -> str:
            lvl = int(match.group(1)) - 1
            lvl_fmt, _, lvl_start = self.levels.get((num_id, lvl), ("decimal", "", 1))
            return _format_number(counters.get(lvl, lvl_start), lvl_fmt)

        return re.sub(r"%(\d)", number, text).strip()


class _DocxReader:
    """Parcourt le XML d'un DOCX dans l'ordre de lecture."""

    def __init__(self, document) -> None:
        self.document = document
        self.seen_images: set[str] = set()
        self.numbering = _Numbering(document)

    # --- Images -------------------------------------------------------------

    def _image_bytes(self, part, rel_id: str | None) -> bytes | None | bool:
        """Octets d'une image liée ; False = à ignorer (déjà vue, minuscule…)."""
        if not rel_id:
            return False
        try:
            image_part = part.related_parts[rel_id]
        except KeyError:
            return False  # image externe (lien) : rien à lire
        name = str(getattr(image_part, "partname", rel_id))
        if name in self.seen_images:
            return False  # même image répétée (logo) : lue une seule fois
        self.seen_images.add(name)
        blob = image_part.blob
        img = ocr.load_image(blob)
        if img is None:
            return None  # EMF/WMF… : format non lisible
        if min(img.size) < _MIN_IMAGE_PX // 2 or max(img.size) < _MIN_IMAGE_PX:
            return False
        return blob

    # --- Parcours -----------------------------------------------------------

    def blocks(self, element, part, doc: _Doc) -> None:
        """Ajoute au document les paragraphes/tableaux de ``element``."""
        for child in element:
            if child.tag == _W_P:
                self._paragraph(child, part, doc)
            elif child.tag == _W_TBL:
                self._table(child, part, doc)
            elif child.tag in _W_SKIP or child.tag == _MC_FALLBACK:
                continue
            else:  # w:sdt, w:customXml, w:sdtContent… : conteneurs
                self.blocks(child, part, doc)

    def _paragraph(self, p, part, doc: _Doc) -> None:
        texts: list[str] = []
        images: list[bytes | None] = []
        nested: list = []

        def visit(el) -> None:
            for c in el:
                tag = c.tag
                if tag in _W_SKIP or tag == _MC_FALLBACK:
                    continue
                if tag == _W_T:
                    texts.append(c.text or "")
                elif tag == _W_TAB:
                    texts.append("\t")
                elif tag in (_W_BR, _W_CR):
                    texts.append("\n")
                elif tag == _W_NBHYPHEN:
                    texts.append("-")
                elif tag == _W_SOFTHYPHEN:
                    continue
                elif tag == _W_TXBX:
                    nested.append(c)  # zone de texte : traitée après le paragraphe
                elif tag in (_A_BLIP, _V_IMAGEDATA):
                    data = self._image_bytes(part, c.get(_R_EMBED) or c.get(_R_ID))
                    if data is not False:
                        images.append(data)
                else:
                    visit(c)

        visit(p)
        text = "".join(texts)
        label = self.numbering.label(p) if text.strip() else ""
        doc.text(f"{label} {text.lstrip()}" if label else text)
        for box in nested:
            self.blocks(box, part, doc)
        for data in images:
            doc.image(_Image(data))

    def _table(self, tbl, part, doc: _Doc) -> None:
        for tr in tbl.iter(_W_TR):
            if tr.getparent() is not tbl:
                continue  # lignes d'un tableau imbriqué : traitées via la cellule
            cells: list[str] = []
            row_images: list[_Image] = []
            for tc in tr.findall(_W_TC):
                sub = _Doc()
                self.blocks(tc, part, sub)
                cell_text = " ".join(sub.plain_text().split())
                if cell_text:
                    cells.append(cell_text)
                row_images.extend(p for p in sub.parts if isinstance(p, _Image))
            doc.text(" | ".join(cells))
            for image in row_images:
                doc.image(image)

    # --- Parties annexes (en-têtes, pieds, notes) ---------------------------

    def related_parts(self, reltypes: tuple[str, ...]) -> list:
        parts, seen = [], set()
        for rel in self.document.part.rels.values():
            if rel.is_external or rel.reltype not in reltypes:
                continue
            target = rel.target_part
            if id(target) in seen:
                continue
            seen.add(id(target))
            parts.append(target)
        return parts

    @staticmethod
    def xml_root(part):
        element = getattr(part, "element", None)
        if element is not None:
            return element
        return etree.fromstring(part.blob)


def _docx_to_doc(content: bytes) -> _Doc:
    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise FileProcessingError(
            "Fichier Word illisible ou corrompu (seul le format .docx est pris en charge)."
        ) from exc

    reader = _DocxReader(document)
    doc = _Doc()
    seen_margins: set[str] = set()

    def margin(reltype: str) -> None:
        """En-têtes / pieds de page, sans répéter un contenu identique
        (première page, pages paires et impaires partagent souvent le même)."""
        for part in reader.related_parts((reltype,)):
            sub = _Doc()
            reader.blocks(reader.xml_root(part), part, sub)
            key = " ".join(sub.plain_text().split())
            if key in seen_margins and not any(isinstance(p, _Image) for p in sub.parts):
                continue
            seen_margins.add(key)
            doc.section()
            _append(doc, sub.parts)

    # En-têtes (souvent : coordonnées de l'expéditeur, références).
    margin(RT.HEADER)

    # Corps du document, dans l'ordre (paragraphes et tableaux mêlés).
    doc.section()
    reader.blocks(document.element.body, document.part, doc)

    # Notes de bas de page / de fin.
    for notes in reader.related_parts((RT.FOOTNOTES, RT.ENDNOTES)):
        doc.section()
        for note in reader.xml_root(notes):
            if note.get(_W_TYPE) in ("separator", "continuationSeparator", "continuationNotice"):
                continue
            reader.blocks(note, notes, doc)

    # Pieds de page.
    margin(RT.FOOTER)
    return doc


def _extract_docx(content: bytes, progress: Optional[ProgressCallback]) -> tuple[str, int, int]:
    return _render(_docx_to_doc(content), progress)


# ---------------------------------------------------------------------------
# PDF : reconstruction de la mise en page
# ---------------------------------------------------------------------------
#
# Un PDF ne contient pas de paragraphes, seulement des fragments de texte
# positionnés. Les PDF exportés de Word (la plupart des décisions et
# conclusions) posent même chaque mot d'une ligne justifiée séparément, et
# PyMuPDF range alors une même ligne dans plusieurs « lignes », voire chaque
# ligne d'un passage en double interligne dans un bloc distinct.
#
# Le texte est donc reconstruit à partir des **lignes visuelles** de la page
# (fragments à la même hauteur), puis regroupé en paragraphes :
#
# - une ligne qui atteint la marge droite se poursuit à la ligne suivante
#   (même si elle finit par « : » : « (ci-après : » + « la directive ») ;
# - une ligne vide (exports Word), une puce ou un numéro de liste après une
#   fin de phrase, un retrait de première ligne, un titre centré ou en gras
#   commencent un nouveau paragraphe ;
# - deux colonnes sur une même ligne (signatures « Le greffier, … Le
#   président, ») sont séparées par une tabulation, jamais recollées ;
# - un trait d'union en fin de ligne est conservé (« avocat-intermédiaire »,
#   « eux-mêmes », « 2019-2020 »), sauf césure avérée (« conven-tionnelles ») ;
# - les en-têtes et pieds de page répétés ne sont gardés qu'une fois et les
#   numéros de page sont retirés : ils ne coupent plus les phrases qui se
#   poursuivent sur la page suivante.

_PDF_TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
_SENTENCE_END = (".", ":", ";", "!", "?", "»", '"', "”")

_MARGIN_ZONE = 0.09
"""Part de la hauteur de page où se trouvent en-têtes et pieds de page."""

_LIST_MARKER_RE = re.compile(
    r"[«\"“(]?[ \t]*(?:"
    r"[-–—•·▪◦●○■□➢►✓*][ \t]"                       # puces
    r"|(?:[a-z]|\d{1,3})[.)°][ \t]"                    # a.  1.  b)  2°
    r"|(?:II|III|IV|VI|VII|VIII|IX|XI|XII|XIII|XIV|XV|XVI)\.[ \t]"  # II.  IV. (pas « I. », « V. » : initiales)
    r"|\((?:[a-z]|\d{1,3}|[ivx]{1,5})\)[ \t]"          # (a)  (1)  (iv)
    r"|[A-Z]\.\d{1,3}(?:\.\d{1,3})*\.[ \t]"            # A.1.  B.6.2.
    r"|\d{1,3}(?:\.\d{1,3})+\.?[ \t]"                  # 1.2  3.1.4.
    r")"
)
"""Début de ligne marquant un élément de liste ou un paragraphe numéroté."""

_PAGE_NUMBER_RE = re.compile(
    r"(?i)(?:page|p\.)?[ \t]*[-–—]?[ \t]*\d{1,4}[ \t]*(?:(?:/|sur|de|of)[ \t]*\d{1,4})?[ \t]*[-–—]?"
)


@dataclass
class _Segment:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    bold: bool


@dataclass
class _Row:
    """Ligne visuelle d'une page (fragments alignés sur une même hauteur)."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    bold: bool = False
    columns: bool = False
    """Vrai si la ligne contient plusieurs colonnes (séparées par une tabulation)."""

    @property
    def blank(self) -> bool:
        return not self.text.strip()


@dataclass
class _PageLayout:
    rows: list[_Row]
    width: float
    height: float
    left: float = 0.0
    right: float = 0.0
    garbled: bool = False
    """Couche texte illisible (police sans table de caractères) : page à lire par OCR."""


@dataclass
class _Paragraph:
    text: str
    first: _Row
    last: _Row


def _segments(page) -> list[_Segment]:
    data = page.get_text("dict", flags=_PDF_TEXT_FLAGS)
    segments: list[_Segment] = []
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text")]
            if not spans:
                continue
            inked = [s for s in spans if s["text"].strip()]
            x0, y0, x1, y1 = line["bbox"]
            segments.append(_Segment(
                x0, y0, x1, y1, "".join(s["text"] for s in spans),
                max((s["size"] for s in inked), default=spans[0]["size"]),
                bool(inked) and all(s.get("flags", 0) & 16 for s in inked),
            ))
    return segments


def _make_row(segments: list[_Segment], column_gap: float) -> _Row:
    """Assemble les fragments d'une ligne visuelle (de gauche à droite)."""
    segments = sorted(segments, key=lambda s: s.x0)
    inked = [s for s in segments if s.text.strip()]
    if not inked:
        s = segments[0]
        return _Row(s.x0, s.y0, s.x1, s.y1, "", s.size)
    gaps = [b.x0 - a.x1 for a, b in zip(inked, inked[1:])]
    # Ligne justifiée (mots posés un par un) : espaces réguliers, pas des colonnes.
    ordered = sorted(gaps)
    justified = len(gaps) >= 2 and ordered[-1] <= 2.5 * max(ordered[len(ordered) // 2], 1.0)
    text = inked[0].text.rstrip()
    columns = False
    for prev, seg, gap in zip(inked, inked[1:], gaps):
        piece = seg.text.rstrip()
        if gap > column_gap and not justified:
            text = text.rstrip() + "\t" + piece.lstrip()
            columns = True
        elif gap > 0.15 * max(prev.size, seg.size) and not text.endswith((" ", "\t")) \
                and not piece.startswith(" "):
            text += " " + piece
        else:
            text += piece
    return _Row(
        min(s.x0 for s in inked), min(s.y0 for s in inked), max(s.x1 for s in inked),
        max(s.y1 for s in inked), text, max(s.size for s in inked),
        all(s.bold for s in inked), columns,
    )


def _garbled(text: str) -> bool:
    """Vrai si la couche texte est illisible (caractères privés, de contrôle…)."""
    chars = [ch for ch in text if not ch.isspace()]
    if len(chars) < 20:
        return False
    bad = sum(1 for ch in chars
              if ch == "�" or unicodedata.category(ch) in ("Co", "Cc", "Cn", "Cs"))
    return bad > 0.2 * len(chars)


def _page_layout(page) -> _PageLayout:
    """Lignes visuelles d'une page et marges du texte."""
    width, height = page.rect.width, page.rect.height
    column_gap = max(48.0, 0.1 * width)
    groups: list[list[_Segment]] = []
    for seg in sorted(_segments(page), key=lambda s: ((s.y0 + s.y1) / 2, s.x0)):
        if groups:
            row = groups[-1]
            top, bottom = min(s.y0 for s in row), max(s.y1 for s in row)
            overlap = min(bottom, seg.y1) - max(top, seg.y0)
            if overlap >= 0.5 * max(1.0, min(bottom - top, seg.y1 - seg.y0)):
                row.append(seg)
                continue
        groups.append([seg])
    rows = [_make_row(group, column_gap) for group in groups]
    layout = _PageLayout(rows, width, height)
    inked = [r for r in rows if not r.blank]
    layout.garbled = _garbled("".join(r.text for r in inked))
    if inked:
        starts = Counter(round(r.x0) for r in inked)
        layout.left = float(min(x for x, n in starts.items() if n == max(starts.values())))
        ends = sorted(r.x1 for r in inked if len(r.text) >= 20) or sorted(r.x1 for r in inked)
        layout.right = max(ends[int(0.9 * (len(ends) - 1))], layout.left + 0.55 * width)
    return layout


def _margin_zone(row: _Row, layout: _PageLayout) -> Optional[str]:
    if row.y1 <= _MARGIN_ZONE * layout.height:
        return "top"
    if row.y0 >= (1 - _MARGIN_ZONE) * layout.height:
        return "bottom"
    return None


def _is_centered(row: _Row, layout: _PageLayout) -> bool:
    width = max(layout.right - layout.left, 1.0)
    if row.x0 <= layout.left + 0.08 * width or row.x1 - row.x0 >= 0.8 * width:
        return False
    return abs((row.x0 + row.x1) / 2 - (layout.left + layout.right) / 2) <= 0.06 * width


def _wraps(prev: _Row, cur: _Row, layout: _PageLayout) -> bool:
    """Vrai si le premier mot de ``cur`` n'aurait pas tenu au bout de ``prev``
    (la ligne a donc été coupée par la mise en page, pas par l'auteur)."""
    first = cur.text.split()[0] if cur.text.split() else ""
    char_width = (prev.x1 - prev.x0) / max(len(prev.text.strip()), 1)
    return layout.right - prev.x1 < 0.9 * (len(first) + 1) * char_width


def _joins(prev: _Row, cur: _Row, layout: _PageLayout) -> bool:
    """Vrai si ``cur`` poursuit le paragraphe terminé par ``prev``."""
    if prev.columns or cur.columns or _is_centered(prev, layout) or _is_centered(cur, layout):
        return False
    if prev.bold and not cur.bold:
        return False  # titre en gras suivi du texte
    if abs(prev.size - cur.size) > 1.0:
        return False
    last, first = prev.text.rstrip(), cur.text.lstrip()
    ends = last.endswith(_SENTENCE_END)
    marker = _LIST_MARKER_RE.match(first) is not None
    wraps = _wraps(prev, cur, layout)
    if marker and (ends or not wraps):
        return False
    if ends and cur.x0 > layout.left + max(6.0, 0.6 * cur.size):
        return False  # retrait de première ligne : nouveau paragraphe
    if wraps:
        return True
    # Ligne courte : suite de phrase seulement si elle commence en minuscule.
    return not ends and not marker and first[:1].islower()


_WORD_RE = re.compile(r"[^\W\d_]+(?:-[^\W\d_]+)*")


def _word_counts(text: str) -> Counter:
    return Counter(_WORD_RE.findall(text.lower()))


def _dehyphenate(left: str, right: str, vocabulary: Counter) -> bool:
    """Vrai si « left- » + « right » est une césure (mot coupé) et non un mot
    composé : on regarde d'abord le reste du document, puis le lexique."""
    joined, hyphenated = (left + right).lower(), f"{left}-{right}".lower()
    if vocabulary[hyphenated]:
        return False
    if vocabulary[joined]:
        return True
    return known_word(joined) and not (known_word(left) and known_word(right))


def _join_text(previous: str, following: str, vocabulary: Counter) -> str:
    """Recolle deux lignes d'un même paragraphe."""
    prev, cur = previous.rstrip(), following.lstrip()
    if prev.endswith("-") and len(prev) >= 2 and prev[-2].isalnum() and cur[:1].isalnum():
        left = re.search(r"([^\W\d_]+)-$", prev)
        right = re.match(r"[^\W\d_]+", cur)
        if left and right and cur[:1].islower() and _dehyphenate(left.group(1), right.group(0), vocabulary):
            return prev[:-1] + cur  # césure : « conven-tionnelles »
        return prev + cur  # trait d'union : « avocat-intermédiaire », « 2019-2020 »
    return prev + " " + cur


def _paragraphs(rows: list[_Row], layout: _PageLayout,
                vocabulary: Counter) -> tuple[list[_Paragraph], bool]:
    """Regroupe les lignes en paragraphes. Renvoie aussi « la page se termine
    par une ligne vide » (le dernier paragraphe ne se poursuit pas)."""
    paragraphs: list[_Paragraph] = []
    blank_before = False
    for row in rows:
        if row.blank:
            blank_before = True
            continue
        current = paragraphs[-1] if paragraphs else None
        if current and not blank_before and _joins(current.last, row, layout):
            current.text = _join_text(current.text, row.text, vocabulary)
            current.last = row
        else:
            paragraphs.append(_Paragraph(row.text.strip(), row, row))
        blank_before = False
    return paragraphs, blank_before


def _visible_text_ratio(page) -> float:
    """Part du texte réellement visible (un PDF « scanné + OCR » a une couche
    de texte invisible posée sur l'image : inutile de relire l'image)."""
    try:
        trace = page.get_texttrace()
    except Exception:  # noqa: BLE001
        return 1.0
    total = sum(len(span.get("chars", ())) for span in trace)
    if not total:
        return 0.0
    invisible = sum(len(span.get("chars", ())) for span in trace if span.get("type") == 3)
    return 1 - invisible / total


def _render_png(pdf, page_number: int, clip=None, dpi: int = _PDF_DPI) -> bytes:
    """Rend (une zone d') une page en PNG niveaux de gris pour l'OCR."""
    page = pdf[page_number]
    rect = clip or page.rect
    # Limite la taille en pixels (pages A3, images géantes).
    max_side_px = 6000
    dpi = min(dpi, int(max_side_px * 72 / max(rect.width, rect.height, 1)))
    pix = page.get_pixmap(dpi=max(dpi, 72), clip=clip, colorspace=fitz.csGRAY, alpha=False)
    return pix.tobytes("png")


def _page_renderer(pdf, page_number: int, clip=None, dpi: int = _PDF_DPI) -> ImageSource:
    """Rendu différé : la page n'est rendue qu'au moment de son OCR.

    PyMuPDF n'est pas utilisable depuis plusieurs threads : le rendu a lieu
    dans le thread du traitement (voir ``ocr.ocr_many``), seul l'OCR est
    parallélisé.
    """
    rect = fitz.Rect(clip) if clip is not None else None

    def render() -> Optional[bytes]:
        try:
            return _render_png(pdf, page_number, clip=rect, dpi=dpi)
        except Exception as exc:  # noqa: BLE001 — page endommagée : ignorée
            logger.warning("Rendu impossible (page %s) : %s", page_number + 1, type(exc).__name__)
            return None

    return render


def _pdf_extras(page) -> list[str]:
    """Valeurs des champs de formulaire et contenu des commentaires."""
    extras: list[str] = []
    try:
        for widget in page.widgets() or []:
            value = widget.field_value
            if isinstance(value, str) and value.strip() and value not in ("Off", "Yes"):
                label = (widget.field_label or widget.field_name or "").strip()
                extras.append(f"{label} : {value.strip()}" if label else value.strip())
    except Exception:  # noqa: BLE001 — formulaire mal formé : ignoré
        pass
    try:
        for annot in page.annots() or []:
            content = (annot.info or {}).get("content", "").strip()
            if content:
                extras.append(content)
    except Exception:  # noqa: BLE001
        pass
    return extras


def _open_pdf(content: bytes):
    try:
        pdf = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise FileProcessingError("Fichier PDF illisible ou corrompu.") from exc
    if pdf.needs_pass and not pdf.authenticate(""):
        pdf.close()
        raise FileProcessingError(
            "PDF protégé par mot de passe : retirez la protection puis réessayez."
        )
    return pdf


@dataclass
class _PdfState:
    """État partagé entre les pages d'un PDF."""

    ocr_budget: int = field(default_factory=lambda: MAX_OCR_PAGES)
    seen_xrefs: set[int] = field(default_factory=set)
    layouts: dict[int, Optional[_PageLayout]] = field(default_factory=dict)
    vocabulary: Counter = field(default_factory=Counter)
    """Mots du document (minuscules) : tranche les césures de fin de ligne."""
    margin_keep: dict[tuple[int, int], str] = field(default_factory=dict)
    """En-têtes / pieds de page gardés (première occurrence) : (page, ligne) → zone."""
    margin_drop: set[tuple[int, int]] = field(default_factory=set)
    """Répétitions d'en-têtes / pieds de page et numéros de page, retirés."""

    def take_ocr_slot(self) -> bool:
        if self.ocr_budget <= 0:
            return False
        self.ocr_budget -= 1
        return True


def _margin_key(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.split()))


def _read_layouts(pdf, state: _PdfState, progress: Optional[ProgressCallback]) -> None:
    """Première lecture : lignes de chaque page, vocabulaire, en-têtes répétés.

    Un document trop long est refusé dès que la limite est franchie.
    """
    page_count = pdf.page_count
    report(progress, "extract", 0, page_count)
    length = 0
    for number in range(page_count):
        try:
            layout = _page_layout(pdf[number])
        except Exception as exc:  # noqa: BLE001 — page corrompue : signalée plus loin
            logger.warning("Page %s illisible : %s", number + 1, type(exc).__name__)
            layout = None
        state.layouts[number] = layout
        if layout is not None:
            text = "\n".join(row.text for row in layout.rows if not row.blank)
            length += len(text) + 1
            if length > settings.MAX_TEXT_CHARS:
                raise DocumentTooLongError(str(TextTooLongError()))
            state.vocabulary.update(_word_counts(text))
        report(progress, "extract", number + 1, page_count)

    # En-têtes et pieds de page : texte identique (aux chiffres près) dans la
    # marge haute ou basse d'au moins deux pages.
    pages_by_key: dict[str, set[int]] = {}
    candidates: list[tuple[int, int, str, str]] = []
    for number, layout in state.layouts.items():
        if layout is None:
            continue
        for index, row in enumerate(layout.rows):
            zone = None if row.blank else _margin_zone(row, layout)
            if zone is None:
                continue
            if _PAGE_NUMBER_RE.fullmatch(row.text.strip()):
                state.margin_drop.add((number, index))
                continue
            key = _margin_key(row.text)
            pages_by_key.setdefault(key, set()).add(number)
            candidates.append((number, index, key, zone))
    first_seen: set[str] = set()
    for number, index, key, zone in candidates:
        if len(pages_by_key[key]) < 2:
            continue  # texte propre à la page : contenu normal
        if key in first_seen:
            state.margin_drop.add((number, index))
        else:
            first_seen.add(key)
            state.margin_keep[(number, index)] = zone


@dataclass
class _PageContent:
    """Contenu d'une page, dans l'ordre de lecture."""

    items: list[str | _Image] = field(default_factory=list)
    header: list[str] = field(default_factory=list)
    footer: list[str] = field(default_factory=list)
    """En-têtes / pieds de page conservés : placés après le paragraphe en cours."""
    first_row: Optional[_Row] = None
    """Première ligne de la page si elle commence par un paragraphe."""
    last_row: Optional[_Row] = None
    """Dernière ligne de la page si elle finit par un paragraphe non clos."""
    layout: Optional[_PageLayout] = None


def _page_items(pdf, page, state: _PdfState) -> _PageContent:
    """Paragraphes et images d'une page, dans l'ordre de lecture."""
    number = page.number
    layout = state.layouts.get(number)
    if layout is None:
        layout = _page_layout(page)
    content = _PageContent(layout=layout)
    body: list[_Row] = []
    for index, row in enumerate(layout.rows):
        if (number, index) in state.margin_drop:
            continue
        zone = state.margin_keep.get((number, index))
        if zone == "top":
            content.header.append(row.text.strip())
        elif zone == "bottom":
            content.footer.append(row.text.strip())
        else:
            body.append(row)
    paragraphs, ends_blank = _paragraphs(body, layout, state.vocabulary)
    native = "\n".join(p.text for p in paragraphs)
    alnum = sum(ch.isalnum() for ch in native)
    page_area = max(page.rect.width * page.rect.height, 1)
    images = [
        info for info in page.get_image_info(xrefs=True)
        if fitz.Rect(info["bbox"]).intersects(page.rect)
    ]
    items: list[tuple[float, float, str | _Image]] = [
        (p.first.y0, p.first.x0, p) for p in paragraphs
    ]

    if layout.garbled or (alnum < 25 and (images or page.get_drawings())):
        # Page scannée, texte vectorisé ou couche texte illisible : OCR de la
        # page entière. Le peu de texte natif lisible (légende, n° de page)
        # sert à écarter un OCR qui ne ferait que le répéter.
        if layout.garbled:
            items, native = [], ""
        if state.take_ocr_slot():
            native_words = frozenset(_ocr_words(native))
            items.append((0.0, 0.0, _Image(_page_renderer(pdf, number), native_words=native_words)))
        else:
            items.append((0.0, 0.0, _Image(None, over_limit=True)))
    elif images:
        visible = _visible_text_ratio(page)
        native_words = frozenset(_ocr_words(native))
        for info in images:
            rect = fitz.Rect(info["bbox"]) & page.rect
            if rect.width < 30 or rect.height < 12:
                continue  # puce, filet, icône
            xref = info.get("xref", 0)
            if xref and xref in state.seen_xrefs:
                continue  # logo répété sur chaque page
            if xref:
                state.seen_xrefs.add(xref)
            if visible < 0.5 and rect.width * rect.height > 0.5 * page_area:
                continue  # scan déjà doté d'une couche texte (OCR existant)
            if not state.take_ocr_slot():
                items.append((rect.y0, rect.x0, _Image(None, over_limit=True)))
                continue
            dpi = 400 if rect.width < 200 else _PDF_DPI
            renderer = _page_renderer(pdf, number, clip=rect, dpi=dpi)
            items.append((rect.y0, rect.x0, _Image(renderer, native_words=native_words)))

    items.sort(key=lambda it: (round(it[0]), it[1]))
    ordered = [item for _, _, item in items]
    if ordered and isinstance(ordered[0], _Paragraph):
        content.first_row = ordered[0].first
    if ordered and isinstance(ordered[-1], _Paragraph) and not ends_blank:
        content.last_row = ordered[-1].last
    content.items = [p.text if isinstance(p, _Paragraph) else p for p in ordered]
    extras = _pdf_extras(page)
    if extras:
        content.items += extras
        content.last_row = None
    return content


def _pdf_to_doc(pdf, progress: Optional[ProgressCallback] = None) -> _Doc:
    """Texte de chaque page + images à lire (rendues plus tard, à la demande).

    Une page endommagée n'interrompt pas le document : elle est signalée
    (``ocr.UNREADABLE_PAGE``) et les autres pages sont traitées.
    """
    doc = _Doc()
    state = _PdfState()
    _read_layouts(pdf, state, progress)
    previous: Optional[_PageContent] = None
    pending: list[str] = []  # pieds de page conservés, placés après le paragraphe en cours
    for number in range(pdf.page_count):
        try:
            content = _page_items(pdf, pdf[number], state)
        except Exception as exc:  # noqa: BLE001 — page corrompue : signalée, ignorée
            logger.warning("Page %s illisible : %s", number + 1, type(exc).__name__)
            content = _PageContent(items=[ocr.UNREADABLE_PAGE])
        items = list(content.items)
        if (previous is not None and previous.last_row is not None
                and content.first_row is not None and previous.layout is not None
                and doc.parts and isinstance(doc.parts[-1], str)
                and _joins(previous.last_row, content.first_row, previous.layout)):
            # Phrase coupée par un saut de page (« né le 10 mai | 1988 ») :
            # recollée pour que la date ou le nom restent détectables.
            doc.continue_last(items.pop(0),
                              join=lambda a, b: _join_text(a, b, state.vocabulary))
        for line in pending + content.header:
            doc.section()
            doc.text(line)
        pending = list(content.footer)
        doc.section()  # une page = un bloc
        _append(doc, items)
        previous = content
    for line in pending:
        doc.section()
        doc.text(line)
    return doc


def _extract_pdf(content: bytes, progress: Optional[ProgressCallback]) -> tuple[str, int, int]:
    pdf = _open_pdf(content)
    try:
        # Le PDF reste ouvert pendant l'OCR : les pages y sont rendues à la demande.
        return _render(_pdf_to_doc(pdf, progress), progress)
    finally:
        pdf.close()


# ---------------------------------------------------------------------------
# Images téléversées
# ---------------------------------------------------------------------------

def _frame_renderer(img, index: int) -> ImageSource:
    """Rendu différé d'une page de TIFF multipage (PNG)."""

    def render() -> Optional[bytes]:
        try:
            img.seek(index)
            frame = img if img.mode in ("1", "L", "LA", "P", "RGB", "RGBA") else img.convert("RGB")
            buffer = io.BytesIO()
            frame.save(buffer, format="PNG")
            return buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 — page endommagée : ignorée
            logger.warning("Page %s de l'image illisible : %s", index + 1, exc)
            return None

    return render


def _image_to_doc(content: bytes) -> _Doc:
    img = ocr.load_image(content)
    if img is None:
        raise FileProcessingError("Image illisible ou format non pris en charge.")
    doc = _Doc()
    frames = getattr(img, "n_frames", 1)
    if frames <= 1 or img.format == "GIF":  # GIF animé : la 1re image suffit
        doc.image(_Image(content))
        return doc
    # TIFF multipage (scan de plusieurs pages) : une image par page, rendue
    # au moment de l'OCR ; une de plus que la limite pour la signaler.
    for index in range(min(frames, MAX_OCR_PAGES + 1)):
        doc.image(_Image(_frame_renderer(img, index)))
    return doc


def _extract_image(content: bytes, progress: Optional[ProgressCallback]) -> tuple[str, int, int]:
    text, found, skipped = _render(_image_to_doc(content), progress)
    return (text if text.strip() else NO_TEXT_IN_IMAGE), found, skipped


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

def extract(filename: str, content: bytes,
            progress: Optional[ProgressCallback] = None) -> Extraction:
    """Convertit un fichier en texte (extraction, OCR), selon son extension.

    Raises
    ------
    FileProcessingError
        Format non supporté, fichier protégé, illisible ou trop long.
    api.progress.Cancelled
        Levée par ``progress`` pour interrompre le traitement.
    """
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".txt":
        return _finish(*_extract_txt(content, progress))
    if ext == ".docx":
        return _finish(*_extract_docx(content, progress))
    if ext == ".pdf":
        return _finish(*_extract_pdf(content, progress))
    if ext in IMAGE_EXTENSIONS:
        return _finish(*_extract_image(content, progress))
    if ext in (".doc", ".odt", ".rtf", ".pages"):
        raise FileProcessingError(
            f"Format {ext} non supporté : enregistrez le document en .docx ou .pdf."
        )
    raise FileProcessingError(
        f"Format non supporté : {ext or '(aucune extension)'}. "
        "Formats acceptés : .txt, .docx, .pdf, images (.png, .jpg…)."
    )


def extract_text(filename: str, content: bytes,
                 progress: Optional[ProgressCallback] = None) -> str:
    """Texte du document tel qu'il est anonymisé (voir :func:`extract`)."""
    return extract(filename, content, progress).text


def process_file(filename: str, content: bytes,
                 progress: Optional[ProgressCallback] = None) -> ProcessedFile:
    """Convertit puis anonymise un fichier selon son extension.

    Parameters
    ----------
    filename : str
        Nom d'origine (seule l'extension est utilisée).
    content : bytes
        Contenu du fichier.
    progress : callable, optional
        Suivi d'avancement / annulation, voir ``api.progress``.

    Raises
    ------
    FileProcessingError
        Format non supporté, fichier protégé, illisible ou trop long.
    api.progress.Cancelled
        Levée par ``progress`` pour interrompre le traitement.
    """
    return _anonymize(extract(filename, content, progress), progress)
