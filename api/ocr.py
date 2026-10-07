"""
ocr — Reconnaissance de texte (OCR) dans les images, via Tesseract.

Ce module est utilisé par ``api.anonymize_file`` pour lire le texte contenu
dans les images d'un document (images intégrées à un PDF ou à un DOCX, pages
scannées, photos/captures téléversées directement).

Le texte reconnu est ensuite inséré dans le document, encadré par des
marqueurs (``OCR_START`` / ``OCR_END``) afin que l'utilisateur sache qu'il
provient d'une image et doit être relu avec attention, puis anonymisé comme
le reste du texte.

Moteur
------
Tesseract (https://github.com/tesseract-ocr/tesseract) est appelé
directement en sous-processus (sans dépendance Python supplémentaire).
Installation :

- Ubuntu / Debian (Forge) : ``sudo apt-get install -y tesseract-ocr
  tesseract-ocr-fra tesseract-ocr-nld``
- macOS : ``brew install tesseract tesseract-lang``

Si Tesseract n'est pas installé, l'OCR est simplement désactivé : les images
sont signalées dans le document (``OCR_UNAVAILABLE``) mais ne sont pas lues.

Variables d'environnement
-------------------------
- ``TESSERACT_CMD`` : chemin du binaire (défaut : recherche dans le PATH).
- ``ANON_OCR_LANGS`` : langues Tesseract (défaut ``fra+nld+eng``, filtrées
  selon les langues réellement installées).
- ``ANON_OCR_WORKERS`` : nombre d'OCR exécutés en parallèle (défaut : 4 max).
- ``ANON_OCR_TIMEOUT`` : délai maximal par image, en secondes (défaut 90).
- ``ANON_OCR_ENABLED`` : ``0`` pour désactiver complètement l'OCR.
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import subprocess
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Optional, Sequence, Union

from PIL import Image, ImageOps

from api import settings
from api.compute import COMPUTE
from api.progress import ProgressCallback, report

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Marqueurs insérés dans le texte de sortie
# ---------------------------------------------------------------------------

OCR_START = "--- Texte extrait d'une image (attention) ---"
"""Ligne insérée avant chaque texte reconnu dans une image."""

OCR_END = "--- Fin du texte extrait de l'image ---"
"""Ligne insérée après chaque texte reconnu dans une image."""

OCR_UNAVAILABLE = "--- Image non analysée : OCR indisponible sur le serveur ---"
"""Ligne insérée à la place d'une image quand Tesseract n'est pas installé."""

OCR_UNSUPPORTED = "--- Image non analysée : format d'image non pris en charge ---"
"""Ligne insérée à la place d'une image dont le format ne peut être lu."""

OCR_LIMIT_REACHED = "--- Images suivantes non analysées : limite d'OCR atteinte ---"
"""Ligne insérée quand le nombre maximal d'images/pages à lire est atteint."""

UNREADABLE_PAGE = "--- Page illisible (fichier endommagé) : contenu non repris ---"
"""Ligne insérée à la place d'une page de PDF impossible à lire."""

MARKERS: tuple[str, ...] = (
    OCR_START, OCR_END, OCR_UNAVAILABLE, OCR_UNSUPPORTED, OCR_LIMIT_REACHED,
    UNREADABLE_PAGE,
)
"""Lignes techniques à ne jamais anonymiser (voir ``api.nlp_engine``)."""

# ---------------------------------------------------------------------------
# Paramètres
# ---------------------------------------------------------------------------

_DEFAULT_LANGS = "fra+nld+eng"
_TIMEOUT = settings.env_int("ANON_OCR_TIMEOUT", 90, minimum=5)
_MIN_WORD_CONFIDENCE = 30.0
"""Les mots reconnus avec une confiance inférieure (bruit, dessins, logos
illisibles) sont écartés."""

_MAX_PIXELS = 24_000_000
"""Au-delà, l'image est réduite avant OCR (mémoire et temps de calcul)."""

_TARGET_MIN_SIDE = 1600
"""Les petites images sont agrandies : Tesseract lit mal le texte < ~20 px."""

_SUPPORTED_FORMATS = {"PNG", "JPEG", "MPO", "GIF", "BMP", "TIFF", "WEBP", "PPM", "PCX", "ICO"}


@dataclass
class OcrResult:
    """Résultat de la lecture d'une image."""

    text: str
    """Texte reconnu (vide si aucun texte lisible)."""

    confidence: float
    """Confiance moyenne Tesseract des mots retenus (0–100)."""

    @property
    def has_text(self) -> bool:
        return sum(ch.isalnum() for ch in self.text) >= 3


EMPTY_RESULT = OcrResult(text="", confidence=0.0)


# ---------------------------------------------------------------------------
# Disponibilité de Tesseract
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def tesseract_cmd() -> str | None:
    """Chemin du binaire Tesseract, ou ``None`` s'il est introuvable."""
    if not settings.env_flag("ANON_OCR_ENABLED", True):
        return None
    candidates = [
        os.environ.get("TESSERACT_CMD", ""),
        shutil.which("tesseract") or "",
        "/opt/homebrew/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/usr/bin/tesseract",
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return None


@lru_cache(maxsize=1)
def installed_languages() -> frozenset[str]:
    """Langues Tesseract installées (``tesseract --list-langs``)."""
    cmd = tesseract_cmd()
    if not cmd:
        return frozenset()
    try:
        proc = subprocess.run(
            [cmd, "--list-langs"], capture_output=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Tesseract inutilisable : %s", exc)
        return frozenset()
    output = (proc.stdout or b"").decode("utf-8", "replace")
    langs = {
        line.strip() for line in output.splitlines()[1:] if line.strip()
    }
    return frozenset(langs)


@lru_cache(maxsize=1)
def ocr_languages() -> str:
    """Langues effectivement utilisées, au format Tesseract (``fra+eng``)."""
    wanted = os.environ.get("ANON_OCR_LANGS", _DEFAULT_LANGS)
    available = installed_languages()
    langs = [lang for lang in re.split(r"[+,\s]+", wanted) if lang in available]
    if not langs:
        # Repli sur une langue installée quelconque (eng est toujours fourni).
        fallback = [lang for lang in ("fra", "eng") if lang in available]
        langs = fallback or sorted(lang for lang in available if lang != "osd")[:1]
        if langs:
            logger.warning(
                "Langues OCR %r non installées, repli sur %s", wanted, langs,
            )
    return "+".join(langs)


def is_available() -> bool:
    """Vrai si l'OCR est opérationnel (Tesseract + au moins une langue)."""
    return bool(tesseract_cmd() and ocr_languages())


def status() -> dict:
    """État de l'OCR (exposé par ``/api/health``)."""
    return {
        "available": is_available(),
        "engine": "tesseract" if tesseract_cmd() else None,
        "languages": ocr_languages().split("+") if is_available() else [],
    }


def _workers() -> int:
    return settings.env_int("ANON_OCR_WORKERS", max(1, min(4, os.cpu_count() or 1)), minimum=1)


# Chaque Tesseract est limité à un thread : le parallélisme est géré ici
# (plusieurs images à la fois), ce qui est bien plus efficace.
_SUBPROCESS_ENV = {**os.environ, "OMP_THREAD_LIMIT": "1"}


# ---------------------------------------------------------------------------
# Préparation de l'image
# ---------------------------------------------------------------------------

def load_image(data: bytes) -> Image.Image | None:
    """Ouvre une image (octets) avec Pillow ; ``None`` si format illisible.

    Les images démesurées (> ~179 Mpx, « bombes de décompression ») sont
    refusées par Pillow et donc traitées comme illisibles.
    """
    try:
        img = Image.open(io.BytesIO(data))
        if img.format and img.format.upper() not in _SUPPORTED_FORMATS:
            return None
        img.load()
        return img
    except Exception:  # noqa: BLE001 — tout format illisible est ignoré
        return None


def _prepare(img: Image.Image) -> Image.Image:
    """Normalise une image pour Tesseract (orientation, gris, taille, contraste)."""
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001 — EXIF corrompu : on garde l'image telle quelle
        pass

    # Transparence → fond blanc (sinon le texte noir sur fond transparent
    # devient noir sur noir).
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, img)
    img = img.convert("L")

    width, height = img.size
    if width * height > _MAX_PIXELS:
        scale = (_MAX_PIXELS / (width * height)) ** 0.5
        img = img.resize((max(1, int(width * scale)), max(1, int(height * scale))),
                         Image.LANCZOS)
    elif max(width, height) < _TARGET_MIN_SIDE:
        scale = min(3.0, _TARGET_MIN_SIDE / max(width, height, 1))
        if scale > 1.2:
            img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    # Étire l'histogramme : améliore nettement les scans pâles ou les photos.
    img = ImageOps.autocontrast(img, cutoff=1)

    # Texte clair sur fond sombre → inversion (Tesseract attend du texte sombre).
    hist = img.histogram()
    dark = sum(hist[:96])
    if dark > 0.6 * sum(hist):
        img = ImageOps.invert(img)
    return img


def _to_png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", dpi=(300, 300))
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Appel de Tesseract
# ---------------------------------------------------------------------------

def _run(args: list[str], png: bytes) -> str:
    cmd = tesseract_cmd()
    if not cmd:
        return ""
    proc = subprocess.run(
        [cmd, "stdin", "stdout", *args],
        input=png, capture_output=True, timeout=_TIMEOUT,
        env=_SUBPROCESS_ENV, check=False,
    )
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"Tesseract a échoué ({proc.returncode}) : {err[-300:]}")
    return (proc.stdout or b"").decode("utf-8", "replace")


def _tsv_to_text(tsv: str) -> OcrResult:
    """Reconstruit le texte (lignes/paragraphes) depuis la sortie TSV."""
    lines: dict[tuple[int, int, int], list[str]] = {}
    order: list[tuple[int, int, int]] = []
    confidences: list[float] = []

    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        word = cols[11].strip()
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if not word or conf < _MIN_WORD_CONFIDENCE:
            continue
        key = (int(cols[2]), int(cols[3]), int(cols[4]))  # bloc, paragraphe, ligne
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)
        confidences.append(conf)

    out: list[str] = []
    previous_block = None
    for key in order:
        line = " ".join(lines[key]).strip()
        # Ligne sans contenu exploitable (bruit graphique) → ignorée.
        if sum(ch.isalnum() for ch in line) < 2:
            continue
        if previous_block is not None and key[0] != previous_block:
            out.append("")
        out.append(line)
        previous_block = key[0]

    text = "\n".join(out).strip()
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrResult(text=_fix_ocr_digits(text), confidence=confidence)


_DIGIT_TOKEN_RE = re.compile(r"\b[\dOoIl|]{2,}(?:[./\- ][\dOoIl|]{2,})*\b")


def _fix_ocr_digits(text: str) -> str:
    """Corrige les confusions OCR classiques dans les suites de chiffres.

    Ex. : ``85.O7.3O-O33.28`` → ``85.07.30-033.28``. Indispensable pour que
    les numéros (registre national, IBAN, téléphone…) soient bien détectés
    puis anonymisés.
    """
    def _fix(match: re.Match) -> str:
        token = match.group(0)
        digits = sum(ch.isdigit() for ch in token)
        letters = sum(ch in "OoIl|" for ch in token)
        if digits < 4 or letters == 0 or letters > digits / 2:
            return token
        return token.translate(str.maketrans({"O": "0", "o": "0", "I": "1",
                                              "l": "1", "|": "1"}))

    return _DIGIT_TOKEN_RE.sub(_fix, text)


def _detect_rotation(png: bytes) -> int:
    """Angle de rotation (0/90/180/270) détecté par l'OSD de Tesseract."""
    if "osd" not in installed_languages():
        return 0
    try:
        out = _run(["--psm", "0", "-l", "osd"], png)
    except (RuntimeError, OSError, subprocess.SubprocessError):
        return 0  # trop peu de texte pour décider : fréquent, sans gravité
    rotate = re.search(r"Rotate:\s*(\d+)", out)
    confidence = re.search(r"Orientation confidence:\s*([\d.]+)", out)
    if not rotate or not confidence or float(confidence.group(1)) < 1.5:
        return 0
    return int(rotate.group(1)) % 360


def ocr_image(img: Image.Image) -> OcrResult:
    """Lit le texte d'une image (Pillow). Ne lève jamais d'exception."""
    if not is_available():
        return EMPTY_RESULT
    try:
        prepared = _prepare(img)
        if min(prepared.size) < 12:
            return EMPTY_RESULT
        png = _to_png(prepared)
        lang = ocr_languages()
        result = _tsv_to_text(_run(["-l", lang, "--psm", "3", "tsv"], png))

        # Page/scan mal orienté(e) : on redresse et on relit.
        if result.confidence < 70 and min(prepared.size) >= 300:
            angle = _detect_rotation(png)
            if angle:
                rotated = prepared.rotate(-angle, expand=True, fillcolor=255)
                retry = _tsv_to_text(_run(["-l", lang, "--psm", "3", "tsv"],
                                          _to_png(rotated)))
                if len(retry.text) and retry.confidence > result.confidence:
                    result = retry

        # Texte épars (carte d'identité, schéma, capture…) : mode « sparse ».
        if not result.has_text:
            sparse = _tsv_to_text(_run(["-l", lang, "--psm", "11", "tsv"], png))
            if sparse.has_text:
                result = sparse
        return result if result.has_text else EMPTY_RESULT
    except Exception as exc:  # noqa: BLE001 — une image illisible ne bloque pas le document
        logger.warning("OCR impossible sur une image : %s", exc)
        return EMPTY_RESULT


def ocr_png_bytes(data: bytes) -> OcrResult:
    """Variante de :func:`ocr_image` prenant des octets d'image."""
    img = load_image(data)
    return ocr_image(img) if img is not None else EMPTY_RESULT


def _ocr_shared(data: bytes) -> OcrResult:
    """OCR d'une image, jamais en même temps qu'une analyse NER (voir ``api.compute``)."""
    with COMPUTE.shared():
        return ocr_png_bytes(data)


ImageInput = Union[bytes, Callable[[], Optional[bytes]]]
"""Octets d'une image, ou fonction qui la produit (rendu différé)."""


def ocr_many(images: Sequence[ImageInput],
             progress: Optional[ProgressCallback] = None) -> list[OcrResult]:
    """Lit plusieurs images en parallèle (ordre des résultats conservé).

    Une image peut être fournie sous forme de fonction (page de PDF à
    rendre) : elle est appelée dans le thread appelant — le rendu PyMuPDF
    n'est pas « thread-safe » — juste avant d'être confiée à l'OCR, avec au
    plus deux images en attente par processus OCR. La mémoire reste ainsi
    bornée quel que soit le nombre de pages, et le rendu de la page suivante
    se fait pendant l'OCR des précédentes.

    ``progress("ocr", lues, total)`` est appelé après chaque image. S'il lève
    une exception (annulation), les images non commencées sont abandonnées
    et l'exception est propagée.
    """
    if not images:
        return []
    if not is_available():
        return [EMPTY_RESULT] * len(images)
    total = len(images)
    workers = min(_workers(), total)
    max_in_flight = 2 * workers
    results: list[OcrResult] = [EMPTY_RESULT] * total
    pending: dict = {}
    done_count = 0
    report(progress, "ocr", 0, total)

    def collect(block_until_one: bool) -> None:
        nonlocal done_count
        if not pending:
            return
        finished, _ = wait(pending, timeout=None if block_until_one else 0,
                           return_when=FIRST_COMPLETED)
        for future in finished:
            results[pending.pop(future)] = future.result()
            done_count += 1
        if finished:
            report(progress, "ocr", done_count, total)

    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ocr")
    try:
        for index, image in enumerate(images):
            while len(pending) >= max_in_flight:
                collect(block_until_one=True)
            data = image() if callable(image) else image
            if data is None:
                done_count += 1  # rendu impossible : aucun texte
                continue
            pending[pool.submit(_ocr_shared, data)] = index
            collect(block_until_one=False)
        while pending:
            collect(block_until_one=True)
    finally:
        # Annulation : les images en file d'attente ne sont jamais lues.
        pool.shutdown(wait=True, cancel_futures=True)
    return results


def wrap(text: str) -> str:
    """Encadre un texte OCR par les marqueurs d'avertissement."""
    return f"{OCR_START}\n{text.strip()}\n{OCR_END}"
