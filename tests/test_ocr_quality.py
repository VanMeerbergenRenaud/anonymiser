"""Filtre de qualité de l'OCR : aucun texte inventé sur un logo, un sceau, une
signature ou une photo, mais les vrais textes courts (tampon, numéro) sont lus.

Les tests marqués ``needs_ocr`` sont ignorés si Tesseract est absent ; ils
sont à exécuter aussi avec Tesseract 4.1.1 (version du serveur) :
``TESSERACT_CMD=/chemin/tesseract-4.1.1 pytest tests/test_ocr_quality.py``.
"""

import io
import os
import random
import subprocess
import sys

import fitz
import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "corpus"))

from build_corpus import _emblem, _font, _signature, logo_png, signature_png  # noqa: E402

from api import ocr  # noqa: E402

needs_ocr = pytest.mark.skipif(not ocr.is_available(), reason="Tesseract non installé")

CORPUS = os.path.join(os.path.dirname(__file__), "corpus", "documents")


def _png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def blason_png() -> bytes:
    """Blason de la page 1 de l'arrêt de la Cour constitutionnelle (bruit OCR réel)."""
    pdf = fitz.open(os.path.join(CORPUS, "arret_cc_2024_001.pdf"))
    page = pdf[0]
    bbox = page.get_image_info()[0]["bbox"]
    return page.get_pixmap(dpi=400, clip=fitz.Rect(bbox), colorspace=fitz.csGRAY).tobytes("png")


def noise_png() -> bytes:
    rng = random.Random(3)
    img = Image.new("L", (600, 400))
    img.putdata([rng.randint(0, 255) for _ in range(600 * 400)])
    return _png(img)


def shapes_png() -> bytes:
    rng = random.Random(3)
    img = Image.new("L", (800, 600), 200)
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rng.randint(0, 780), rng.randint(0, 580)
        draw.ellipse((x, y, x + rng.randint(10, 120), y + rng.randint(10, 120)), fill=rng.randint(0, 255))
    return _png(img)


def seal_with_signature_png() -> bytes:
    img = Image.new("L", (900, 500), 255)
    draw = ImageDraw.Draw(img)
    _emblem(draw, 220, 250, 200)
    _signature(draw, 450, 220, scale=0.9)
    return _png(img)


def stamp_png(pale: bool = False) -> bytes:
    img = Image.new("L", (520, 220), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((10, 10, 510, 210), outline=40, width=6)
    draw.text((40, 30), "GREFFE", font=_font(40, bold=True), fill=40)
    draw.text((40, 100), "RG 21/123/A", font=_font(56, bold=True), fill=40)
    if pale:
        img = img.rotate(8, expand=True, fillcolor=255).point(lambda p: 255 - (255 - p) // 2)
    return _png(img)


def line_png(text: str, size=(600, 90), font_size=48) -> bytes:
    img = Image.new("L", size, 240)
    ImageDraw.Draw(img).text((20, 15), text, font=_font(font_size, bold=True), fill=10)
    return _png(img)


@needs_ocr
@pytest.mark.parametrize("name, image", [
    ("blason de la Cour constitutionnelle", blason_png),
    ("logo", logo_png),
    ("signature", signature_png),
    ("sceau et signature", seal_with_signature_png),
    ("texture (photo)", noise_png),
    ("formes", shapes_png),
])
def test_images_without_text_produce_nothing(name, image):
    result = ocr.ocr_png_bytes(image())
    assert not result.has_text and result.text == "", f"{name} : {result.text!r}"


@needs_ocr
@pytest.mark.parametrize("image, expected", [
    (lambda: stamp_png(), ["GREFFE", "RG 21/123/A"]),
    (lambda: stamp_png(pale=True), ["RG 21/123/"]),
    (lambda: line_png("592-1234567-89"), ["592-1234567-89"]),
    (lambda: line_png("Annexe 3", size=(300, 100), font_size=40), ["Annexe 3"]),
    (lambda: open(os.path.join(CORPUS, "carte_identite.png"), "rb").read(),
     ["PEETERS", "592-1234567-89", "85.07.30-033.28"]),
])
def test_short_real_texts_are_kept(image, expected):
    result = ocr.ocr_png_bytes(image())
    assert result.has_text
    for fragment in expected:
        assert fragment in result.text, (fragment, result.text)


def _tsv(rows: list[tuple[int, int, str, float]]) -> str:
    """TSV Tesseract minimal : (bloc, ligne, mot, confiance)."""
    lines = ["level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"]
    for i, (block, line, word, conf) in enumerate(rows):
        lines.append(f"5\t1\t{block}\t1\t{line}\t{i}\t0\t0\t10\t10\t{conf}\t{word}")
    return "\n".join(lines) + "\n"


def test_noise_lines_are_dropped():
    tsv = _tsv([
        (1, 1, "se", 70), (2, 1, "LA.", 52), (3, 1, "hek", 39), (4, 1, "{Al", 72),
        (5, 1, "#", 92), (6, 1, "Les", 63), (7, 1, "Mart", 44), (8, 1, "e®", 85),
    ])
    result = ocr._tsv_to_text(tsv)
    assert result.text == "" and not result.has_text


def test_real_lines_are_kept_with_short_tokens():
    tsv = _tsv([
        (1, 1, "Nom", 89), (1, 1, "/", 89), (1, 1, "Name", 96),
        (1, 2, "PEETERS", 91),
        (2, 1, "RG", 96), (2, 1, "21/123/A", 90),
        (3, 1, "An", 96), (3, 1, "Li", 93),
        (4, 1, "ae", 29), (4, 1, "ER", 40),  # bruit dans une page lisible
    ])
    result = ocr._tsv_to_text(tsv)
    assert result.text.splitlines() == ["Nom / Name", "PEETERS", "", "RG 21/123/A", "", "An Li"]
    assert result.has_text


def test_tsv_output_is_requested_without_config_file(monkeypatch):
    """« tsv » est un fichier de configuration absent de certaines installations
    (TESSDATA_PREFIX personnalisé) : Tesseract renvoie alors du texte brut et
    l'OCR échouait en silence. La sortie TSV est demandée par paramètre."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=b"level\tpage_num\n", stderr=b"")

    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    ocr._run_tsv(["-l", "fra", "--psm", "3"], b"png")
    assert "tessedit_create_tsv=1" in calls[0] and "tsv" not in calls[0][-1:]


def test_plain_text_output_is_an_error_not_an_empty_result(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout=b"Monsieur Jean DUPONT\n", stderr=b"")

    monkeypatch.setattr(ocr, "tesseract_cmd", lambda: "/usr/bin/tesseract")
    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        ocr._run_tsv(["-l", "fra"], b"png")


def test_tesseract_installed_after_startup_is_detected(monkeypatch, tmp_path):
    """L'API ne doit plus exiger un redémarrage quand Tesseract est installé
    après son lancement : l'absence est revérifiée régulièrement."""
    monkeypatch.delenv("TESSERACT_CMD", raising=False)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    monkeypatch.setattr(ocr, "_FALLBACK_PATHS", ())
    ocr.reset_detection()
    assert ocr.tesseract_cmd() is None
    binary = tmp_path / "tesseract"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    monkeypatch.setenv("TESSERACT_CMD", str(binary))
    monkeypatch.setattr(ocr, "_RECHECK_SECONDS", 0.0)
    assert ocr.tesseract_cmd() == str(binary)
    ocr.reset_detection()
