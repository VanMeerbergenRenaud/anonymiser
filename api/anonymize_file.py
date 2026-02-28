"""
anonymize_file — Traitement et anonymisation de fichiers (TXT, DOCX, PDF).

Ce module contient les fonctions de traitement spécifiques à chaque format
de fichier. Il utilise le moteur NLP partagé de ``api.nlp_engine`` pour
la détection et le remplacement des entités sensibles.

Formats supportés
-----------------
- **.txt** : remplacement textuel simple
- **.docx** : remplacement dans les paragraphes et tableaux, suppression des images
- **.pdf** : rédaction avec rectangles noirs par-dessus les entités, suppression des images
"""

from __future__ import annotations

import io

import fitz  # PyMuPDF
from docx import Document

from api.nlp_engine import analyzer, anonymizer_engine, get_label
from presidio_anonymizer.entities import OperatorConfig


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

MAX_FILE_SIZE: int = int(4.5 * 1024 * 1024)
"""Taille maximale d'un fichier uploadé (4.5 Mo)."""


# ---------------------------------------------------------------------------
# TXT
# ---------------------------------------------------------------------------

def _process_txt(content: bytes) -> bytes:
    """Anonymise un fichier texte brut.

    Parameters
    ----------
    content : bytes
        Contenu brut du fichier ``.txt``.

    Returns
    -------
    bytes
        Contenu anonymisé encodé en UTF-8.
    """
    text = content.decode("utf-8", errors="replace")
    results = analyzer.analyze(text=text, language="fr")
    operators = {
        entity_type: OperatorConfig(
            "replace", {"new_value": f"[{get_label(entity_type)}]"}
        )
        for entity_type in {r.entity_type for r in results}
    }
    anonymized = anonymizer_engine.anonymize(
        text=text, analyzer_results=results, operators=operators,
    )
    return anonymized.text.encode("utf-8")


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------

def _remove_images_from_paragraph(paragraph) -> None:
    """Supprime toutes les images inline d'un paragraphe Word.

    Gère à la fois les éléments ``<w:drawing>`` (images modernes) et
    ``<w:pict>`` (images VML héritées).
    """
    nsmap = {
        "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
        "pic": "http://schemas.openxmlformats.org/drawingml/2006/picture",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    for drawing in paragraph._element.findall(".//w:drawing", nsmap):
        drawing.getparent().remove(drawing)
    for pict in paragraph._element.findall(".//w:pict", nsmap):
        pict.getparent().remove(pict)


def _anonymize_paragraph(paragraph) -> None:
    """Détecte et remplace les entités sensibles dans un paragraphe Word.

    Le texte complet du paragraphe est analysé, puis les remplacements sont
    effectués dans le premier *run* pour conserver le formatage de base.
    """
    full_text = paragraph.text
    if not full_text.strip():
        return

    results = analyzer.analyze(text=full_text, language="fr")
    if not results:
        return

    # Trier de la fin vers le début pour préserver les indices
    results = sorted(results, key=lambda r: r.start, reverse=True)
    new_text = full_text
    for result in results:
        label = get_label(result.entity_type)
        new_text = new_text[: result.start] + f"[{label}]" + new_text[result.end :]

    # Remplacer les runs : tout le texte dans le premier, vider les suivants
    if paragraph.runs:
        paragraph.runs[0].text = new_text
        for run in paragraph.runs[1:]:
            run.text = ""


def _process_docx(content: bytes) -> bytes:
    """Anonymise un fichier DOCX en préservant la structure du document.

    - Supprime les images des paragraphes et cellules de tableaux.
    - Remplace les entités sensibles par des labels ``[TYPE]``.
    - Conserve le formatage du premier *run* de chaque paragraphe.

    Parameters
    ----------
    content : bytes
        Contenu brut du fichier ``.docx``.

    Returns
    -------
    bytes
        Contenu du fichier DOCX anonymisé.
    """
    doc = Document(io.BytesIO(content))

    # Paragraphes principaux
    for paragraph in doc.paragraphs:
        _remove_images_from_paragraph(paragraph)
        _anonymize_paragraph(paragraph)

    # Cellules de tableaux
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    _remove_images_from_paragraph(paragraph)
                    _anonymize_paragraph(paragraph)

    # Nettoyer les relations d'images orphelines
    try:
        part = doc.part
        rels_to_remove = [
            rel_id
            for rel_id, rel in part.rels.items()
            if "image" in rel.reltype
        ]
        for rel_id in rels_to_remove:
            del part.rels[rel_id]
    except Exception:
        pass  # Les éléments XML ont déjà été supprimés

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

def _process_pdf(content: bytes) -> bytes:
    """Anonymise un fichier PDF par rédaction (rectangles noirs).

    - Supprime toutes les images de chaque page.
    - Détecte les entités sensibles et les couvre avec des annotations
      de rédaction noires.

    Parameters
    ----------
    content : bytes
        Contenu brut du fichier ``.pdf``.

    Returns
    -------
    bytes
        Contenu du fichier PDF anonymisé.
    """
    doc = fitz.open(stream=content, filetype="pdf")

    for page in doc:
        # Supprimer les images
        for img in page.get_images(full=True):
            xref = img[0]
            try:
                page.delete_image(xref)
            except Exception:
                # Fallback : couvrir l'image avec un rectangle blanc
                for img_rect in page.get_image_rects(xref):
                    page.draw_rect(img_rect, color=(1, 1, 1), fill=(1, 1, 1))

        page.clean_contents()

        # Analyser le texte de la page
        text_page = page.get_text("text")
        if not text_page.strip():
            continue

        results = analyzer.analyze(text=text_page, language="fr")
        if not results:
            continue

        # Rédiger les entités sensibles (remplacer le texte)
        for result in results:
            label = get_label(result.entity_type)
            sensitive_text = text_page[result.start : result.end]
            for inst in page.search_for(sensitive_text):
                # Ajoute une annotation qui supprimera le texte original
                # et dessinera le [LABEL] dessus (fond blanc, texte noir)
                page.add_redact_annot(
                    inst, 
                    text=f"[{label}]",
                    fill=(1, 1, 1),
                    text_color=(0, 0, 0),
                    fontsize=10
                )

        page.apply_redactions()

    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    doc.close()
    return buf.getvalue()
