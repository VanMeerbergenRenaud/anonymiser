"""Tests de bout en bout : texte, fichiers (TXT, DOCX, PDF, images) et OCR.

Charge le modèle NER (CamemBERT ou spaCy) : plus lent que les tests de règles.
Les tests OCR sont ignorés si Tesseract n'est pas installé.
"""

import io

import fitz
import pytest
from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw, ImageFont

from api import ocr
from api.anonymize_file import FileProcessingError, process_file
from api.nlp_engine import anonymize_text

needs_ocr = pytest.mark.skipif(not ocr.is_available(), reason="Tesseract non installé")

BELGIAN_TEXT = (
    "TRIBUNAL DE PREMIÈRE INSTANCE DE LIÈGE\n"
    "Monsieur Olivier DEPREZ, né à Seraing le 12 octobre 1978 (NN 78.10.12-345.67), "
    "domicilié Rue de la Province 45 boîte 2, 4100 Seraing, GSM 0472 11 22 33, "
    "olivier.deprez@exemple.be, compte BE68 5390 0754 7034.\n"
    "PAR CES MOTIFS, vu l'article 1184 de l'ancien Code civil, le tribunal "
    "condamne la société à payer à Monsieur DEPREZ la somme de 25.000 €."
)

SECRETS = [
    "Olivier", "DEPREZ", "12 octobre 1978", "78.10.12-345.67", "Province",
    "4100", "0472 11 22 33", "olivier.deprez", "BE68",
]


def _font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def text_png(lines: list[str], size=(1100, 300)) -> bytes:
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for i, line in enumerate(lines):
        draw.text((30, 30 + i * 50), line, fill="black", font=_font(32))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def test_belgian_text_is_anonymized():
    result = anonymize_text(BELGIAN_TEXT)
    for secret in SECRETS:
        assert secret not in result, secret
    # Intitulés et références juridiques conservés.
    assert "TRIBUNAL DE PREMIÈRE INSTANCE" in result
    assert "PAR CES MOTIFS" in result
    assert "Code civil" in result
    assert "[REGISTRE_NATIONAL]" in result
    assert "[ADRESSE]" in result


def test_same_person_gets_same_pseudonym():
    result = anonymize_text(
        "Monsieur Jean DUPONT a signé. Madame Sophie MARTIN a refusé. "
        "Plus tard, Monsieur DUPONT a relancé Madame MARTIN."
    )
    assert result.count("[PERSONNE_1]") == 2
    assert result.count("[PERSONNE_2]") == 2


def test_ocr_markers_are_preserved():
    text = f"{ocr.OCR_START}\nMadame Claire DUBOIS\n{ocr.OCR_END}"
    result = anonymize_text(text)
    assert result.startswith(ocr.OCR_START)
    assert result.endswith(ocr.OCR_END)
    assert "DUBOIS" not in result


def test_txt_windows_1252():
    content = "Madame Hélène GOFFIN, née à Huy le 2 août 1990.".encode("cp1252")
    result = process_file("note.txt", content).content.decode("utf-8")
    assert "GOFFIN" not in result and "2 août 1990" not in result
    assert "née à" in result


def test_unsupported_and_corrupted_files():
    with pytest.raises(FileProcessingError):
        process_file("ancien.doc", b"\xd0\xcf\x11\xe0")
    with pytest.raises(FileProcessingError):
        process_file("casse.docx", b"pas un zip")
    with pytest.raises(FileProcessingError):
        process_file("casse.pdf", b"pas un pdf")


def test_password_protected_pdf():
    pdf = fitz.open()
    pdf.new_page().insert_text((50, 50), "Secret")
    data = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="x", owner_pw="y")
    with pytest.raises(FileProcessingError):
        process_file("protege.pdf", data)


@needs_ocr
def test_docx_image_text_is_extracted_and_anonymized():
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = "Étude Maître Isabelle HENRARD"
    doc.add_paragraph("Voir la capture :")
    doc.add_picture(io.BytesIO(text_png([
        "Titulaire : Nathalie VANDENBERGHE",
        "Compte : BE71 0961 2345 6769",
    ])), width=Inches(5))
    buffer = io.BytesIO()
    doc.save(buffer)

    result = process_file("bail.docx", buffer.getvalue())
    text = result.content.decode("utf-8")
    assert result.ocr_images == 1
    assert ocr.OCR_START in text and ocr.OCR_END in text
    assert "VANDENBERGHE" not in text and "BE71" not in text and "HENRARD" not in text


@needs_ocr
def test_scanned_pdf_is_read_by_ocr():
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_image(fitz.Rect(30, 40, 565, 300), stream=text_png([
        "Madame Claire DUBOIS",
        "Rue Saint-Gilles 120, 4000 Liège",
        "Registre national : 85.07.30-033.28",
    ], size=(1600, 400)))
    result = process_file("scan.pdf", pdf.tobytes())
    text = result.content.decode("utf-8")
    assert result.ocr_images == 1
    assert text.startswith(ocr.OCR_START)
    for secret in ("DUBOIS", "Saint-Gilles", "85.07.30-033.28"):
        assert secret not in text


@needs_ocr
def test_rotated_image_upload():
    img = Image.open(io.BytesIO(text_png([
        "Monsieur Pierre DELVAUX",
        "Avenue Louise 54 bte 3, 1050 Ixelles",
    ], size=(900, 200)))).rotate(90, expand=True)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    text = process_file("attestation.png", buffer.getvalue()).content.decode("utf-8")
    assert ocr.OCR_START in text
    assert "DELVAUX" not in text and "Louise" not in text


# ---------------------------------------------------------------------------
# Numéros demandés : rôle (RG / FA), registre national (RN / NN), téléphones
# ---------------------------------------------------------------------------

def test_belgian_numbers_are_removed_but_mentions_kept():
    result = anonymize_text(
        "R.G. n° 24/1234/A — Dossier 22/321/FA — Le numéro 2023/789 RG a été joint.\n"
        "Madame Sophie LAMBERT, RN 85.13.45-123.45, NN 82011512345, "
        "GSM 0475/12.34.56, tél. (081) 22 33 44, +32471234567, +33 6 12 34 56 78."
    )
    for secret in ("24/1234/A", "22/321/FA", "2023/789", "85.13.45", "82011512345",
                   "0475", "(081)", "+32471234567", "+33 6"):
        assert secret not in result, secret
    assert "R.G. n° [NUMÉRO_RÔLE]" in result
    assert "RN [REGISTRE_NATIONAL]" in result and "NN [REGISTRE_NATIONAL]" in result


def test_jurisdiction_seats_stay_readable():
    result = anonymize_text(
        "Tribunal de première instance de Liège, division Namur. Maître Paul HENRY, "
        "avocat au barreau de Bruxelles, pour Monsieur Marc LEROY, domicilié à Namur."
    )
    assert "Tribunal de première instance de Liège" in result
    assert "division Namur" in result and "barreau de Bruxelles" in result
    assert "domicilié à [LIEU]" in result or "domicilié à Namur" not in result
    assert "LEROY" not in result and "HENRY" not in result


# ---------------------------------------------------------------------------
# Gros fichiers : progression, annulation, limite de longueur, rendu différé
# ---------------------------------------------------------------------------

def test_file_progress_stages():
    pdf = fitz.open()
    for _ in range(3):
        pdf.new_page().insert_text((50, 50), "Monsieur Jean DUPONT, GSM 0475/12.34.56")
    stages = []
    process_file("dossier.pdf", pdf.tobytes(), progress=lambda s, d, t: stages.append((s, d, t)))
    assert ("extract", 3, 3) in stages
    assert stages[-1][0] == "analyze" and stages[-1][1] == stages[-1][2]


def test_progress_callback_can_cancel():
    from api.progress import Cancelled

    def cancel(stage, done, total):
        raise Cancelled()

    with pytest.raises(Cancelled):
        process_file("note.txt", "Madame Claire DUBOIS".encode(), progress=cancel)


def test_too_long_document_is_refused_early(monkeypatch):
    from api import settings
    from api.anonymize_file import DocumentTooLongError

    monkeypatch.setattr(settings, "MAX_TEXT_CHARS", 2_000)
    pdf = fitz.open()
    for _ in range(5):
        pdf.new_page().insert_textbox(fitz.Rect(40, 40, 555, 800), "mot " * 150)
    pages_read = []
    with pytest.raises(DocumentTooLongError):
        process_file("long.pdf", pdf.tobytes(),
                     progress=lambda s, d, t: s == "extract" and pages_read.append(d))
    assert max(pages_read) < 5, "la lecture aurait dû s'arrêter dès la limite franchie"


def test_ocr_many_renders_lazily_and_keeps_order():
    calls = []

    def source(i):
        def render():
            calls.append(i)
            return None if i == 1 else text_png([f"Page {i}"], size=(400, 100))
        return render

    results = ocr.ocr_many([source(i) for i in range(4)])
    assert calls == [0, 1, 2, 3]  # rendu dans l'ordre, dans le thread appelant
    assert len(results) == 4 and not results[1].has_text
    if ocr.is_available():
        assert "Page 3" in results[3].text


@needs_ocr
def test_scanned_page_caption_is_not_duplicated():
    pdf = fitz.open()
    page = pdf.new_page(width=595, height=842)
    page.insert_text((40, 40), "Annexe photographique 1.", fontsize=11)
    page.insert_image(fitz.Rect(40, 30, 555, 300), stream=text_png(["Annexe photographique 1."]))
    result = process_file("annexe.pdf", pdf.tobytes())
    text = result.content.decode("utf-8")
    assert result.ocr_images == 0 and ocr.OCR_START not in text
    assert text.count("Annexe photographique") == 1


def test_damaged_pdf_page_does_not_fail_the_document(monkeypatch):
    import api.anonymize_file as file_module

    original = file_module._page_items

    def flaky(pdf, page, state):
        if page.number == 1:
            raise RuntimeError("page endommagée")
        return original(pdf, page, state)

    monkeypatch.setattr(file_module, "_page_items", flaky)
    pdf = fitz.open()
    for name in ("Monsieur Jean DUPONT", "Madame Claire DUBOIS", "Monsieur Paul HENRY"):
        pdf.new_page().insert_text((50, 50), name)
    text = process_file("dossier.pdf", pdf.tobytes()).content.decode("utf-8")
    assert ocr.UNREADABLE_PAGE in text  # signalée telle quelle (marqueur protégé)
    assert text.count("[PERSONNE_") == 2 and "DUPONT" not in text and "HENRY" not in text


@pytest.mark.parametrize("before, expected", [
    ("Tribunal de première instance de ", True),
    ("Liège, division ", True),
    ("Cour d’appel de ", True),
    ("Justice de paix du canton de ", True),
    ("Conseil de prud'hommes de ", True),
    ("avocat au barreau de ", True),
    ("domicilié à ", False),
    ("le tribunal a constaté qu'il habite près de ", False),
    ("les hommes de ", False),
    ("commune de ", False),
])
def test_jurisdiction_seat_detection(before, expected):
    from api.nlp_engine import _is_jurisdiction_seat

    assert _is_jurisdiction_seat(before + "Liège", len(before)) is expected
