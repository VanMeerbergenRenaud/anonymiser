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
from dataclasses import dataclass, field
from typing import Callable, Optional

import fitz  # PyMuPDF
from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from lxml import etree

from api import ocr, settings
from api.nlp_engine import TextTooLongError, anonymize_text, check_text_length
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

    def continue_last(self, value: str) -> None:
        """Prolonge le dernier bloc de texte (phrase coupée par un saut de page)."""
        last = self.parts[-1] if self.parts else None
        if not isinstance(last, str):
            self.text(value)
            return
        self.parts[-1] = last.rstrip() + " " + value.lstrip()
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


def _anonymize(text: str, found: int, skipped: int,
               progress: Optional[ProgressCallback]) -> ProcessedFile:
    cleaned = _clean_text(text)
    try:
        check_text_length(cleaned)
    except TextTooLongError as exc:
        raise DocumentTooLongError(str(exc)) from exc
    anonymized = anonymize_text(cleaned, progress=progress)
    return ProcessedFile(anonymized.encode("utf-8"), ocr_images=found, skipped_images=skipped)


def _process_doc(doc: _Doc, progress: Optional[ProgressCallback]) -> ProcessedFile:
    text, found, skipped = _render(doc, progress)
    return _anonymize(text, found, skipped, progress)


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


def process_txt(content: bytes, progress: Optional[ProgressCallback] = None) -> ProcessedFile:
    doc = _Doc()
    doc.text(_decode_text(content))
    return _process_doc(doc, progress)


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


class _DocxReader:
    """Parcourt le XML d'un DOCX dans l'ordre de lecture."""

    def __init__(self, document) -> None:
        self.document = document
        self.seen_images: set[str] = set()

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
        doc.text("".join(texts))
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


def process_docx(content: bytes, progress: Optional[ProgressCallback] = None) -> ProcessedFile:
    return _process_doc(_docx_to_doc(content), progress)


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

_PDF_TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
_SENTENCE_END = (".", ":", ";", "!", "?", "»", '"')


def _join_lines(lines: list[tuple[str, tuple]]) -> str:
    """Recolle les lignes d'un bloc PDF coupées par la mise en page.

    Une ligne qui atteint (presque) la marge droite du bloc et ne se termine
    pas par une ponctuation forte est la suite du même paragraphe : elle est
    recollée (avec suppression de la césure « conven-/tionnelles »). Les
    lignes courtes (adresses, listes) restent séparées.
    """
    if not lines:
        return ""
    x0 = min(b[0] for _, b in lines)
    x1 = max(b[2] for _, b in lines)
    width = max(x1 - x0, 1.0)
    out = lines[0][0].rstrip()
    for (prev, pbox), (cur, cbox) in zip(lines, lines[1:]):
        prev, cur = prev.rstrip(), cur.strip()
        if not cur:
            continue
        same_row = abs(cbox[1] - pbox[1]) < 2
        wraps = (pbox[2] - x0) >= 0.8 * width and not prev.endswith(_SENTENCE_END)
        continues = cur[:1].islower() and not prev.endswith(_SENTENCE_END)
        if same_row:
            out += " " + cur
        elif wraps or continues:
            last_token = prev.split()[-1] if prev.split() else ""
            if prev.endswith("-") and len(prev) > 1 and prev[-2].isalpha():
                if cur[:1].islower() and not re.search(r"[@/.]|www", last_token):
                    out = out[:-1] + cur  # césure : « conven-tionnelles »
                else:
                    out += cur  # trait d'union réel : « Jean-Pierre », e-mail
            else:
                out += " " + cur
        else:
            out += "\n" + cur
    return out


def _continues_paragraph(previous: str, following: str) -> bool:
    """Vrai si ``following`` (début de page) prolonge la phrase ``previous``."""
    last, first = previous.rstrip(), following.lstrip()
    return bool(last and first) and not last.endswith(_SENTENCE_END) and (
        first[:1].islower() or first[:1].isdigit()
    )


def _page_text_blocks(page) -> list[tuple[tuple, str]]:
    """Blocs de texte (bbox, texte) d'une page, dans l'ordre de lecture."""
    data = page.get_text("dict", flags=_PDF_TEXT_FLAGS, sort=True)
    blocks = []
    for block in data.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        lines = []
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", []))
            if text.strip():
                lines.append((text, line["bbox"]))
        text = _join_lines(lines)
        if text.strip():
            blocks.append((tuple(block["bbox"]), text))
    return blocks


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
            logger.warning("Rendu impossible (page %s) : %s", page_number + 1, exc)
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

    def take_ocr_slot(self) -> bool:
        if self.ocr_budget <= 0:
            return False
        self.ocr_budget -= 1
        return True


def _page_items(pdf, page, state: _PdfState) -> list[str | _Image]:
    """Blocs de texte et images d'une page, dans l'ordre de lecture."""
    number = page.number
    blocks = _page_text_blocks(page)
    native = "\n".join(text for _, text in blocks)
    alnum = sum(ch.isalnum() for ch in native)
    page_area = max(page.rect.width * page.rect.height, 1)
    images = [
        info for info in page.get_image_info(xrefs=True)
        if fitz.Rect(info["bbox"]).intersects(page.rect)
    ]
    items: list[tuple[float, float, str | _Image]] = [
        (bbox[1], bbox[0], text) for bbox, text in blocks
    ]

    if alnum < 25 and (images or page.get_drawings()):
        # Page scannée (ou texte vectorisé) : OCR de la page entière. Le peu
        # de texte natif (légende, n° de page) sert à écarter un OCR qui ne
        # ferait que le répéter.
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
    return [item for _, _, item in items] + _pdf_extras(page)


def _pdf_to_doc(pdf, progress: Optional[ProgressCallback] = None) -> _Doc:
    """Texte de chaque page + images à lire (rendues plus tard, à la demande).

    Une page endommagée n'interrompt pas le document : elle est signalée
    (``ocr.UNREADABLE_PAGE``) et les autres pages sont traitées.
    """
    doc = _Doc()
    state = _PdfState()
    page_count = pdf.page_count
    report(progress, "extract", 0, page_count)
    for number in range(page_count):
        try:
            ordered = _page_items(pdf, pdf[number], state)
        except Exception as exc:  # noqa: BLE001 — page corrompue : signalée, ignorée
            logger.warning("Page %s illisible : %s", number + 1, exc)
            ordered = [ocr.UNREADABLE_PAGE]
        previous = doc.parts[-1] if doc.parts else None
        if (ordered and isinstance(ordered[0], str) and isinstance(previous, str)
                and _continues_paragraph(previous, ordered[0])):
            # Phrase coupée par un saut de page (« né le 10 mai | 1988 ») :
            # recollée pour que la date / le nom restent détectables.
            doc.continue_last(ordered.pop(0))
        doc.section()  # une page = un bloc
        _append(doc, ordered)
        report(progress, "extract", number + 1, page_count)
    return doc


def process_pdf(content: bytes, progress: Optional[ProgressCallback] = None) -> ProcessedFile:
    pdf = _open_pdf(content)
    try:
        # Le PDF reste ouvert pendant l'OCR : les pages y sont rendues à la demande.
        doc = _pdf_to_doc(pdf, progress)
        text, found, skipped = _render(doc, progress)
    finally:
        pdf.close()
    return _anonymize(text, found, skipped, progress)


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


def process_image(content: bytes, progress: Optional[ProgressCallback] = None) -> ProcessedFile:
    doc = _image_to_doc(content)
    text, found, skipped = _render(doc, progress)
    if not text.strip():
        text = NO_TEXT_IN_IMAGE
    return _anonymize(text, found, skipped, progress)


# ---------------------------------------------------------------------------
# Point d'entrée
# ---------------------------------------------------------------------------

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
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".txt":
        return process_txt(content, progress)
    if ext == ".docx":
        return process_docx(content, progress)
    if ext == ".pdf":
        return process_pdf(content, progress)
    if ext in IMAGE_EXTENSIONS:
        return process_image(content, progress)
    if ext in (".doc", ".odt", ".rtf", ".pages"):
        raise FileProcessingError(
            f"Format {ext} non supporté : enregistrez le document en .docx ou .pdf."
        )
    raise FileProcessingError(
        f"Format non supporté : {ext or '(aucune extension)'}. "
        "Formats acceptés : .txt, .docx, .pdf, images (.png, .jpg…)."
    )
