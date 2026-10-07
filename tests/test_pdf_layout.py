"""Fidélité du texte extrait des PDF : paragraphes, césures, colonnes, en-têtes.

Les PDF sont produits par le générateur du corpus (``tests/corpus``), qui
reproduit les particularités des PDF exportés de Word.
"""

import os
import sys

import fitz
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "corpus"))

from build_corpus import Typesetter  # noqa: E402

from api.anonymize_file import extract_text  # noqa: E402


def pdf_text(source: str, **kwargs) -> str:
    return extract_text("doc.pdf", Typesetter(**kwargs).render(source))


LONG = (
    "Par requête adressée à la Cour par lettre recommandée à la poste le 29 juin 2020 et parvenue "
    "au greffe le 30 juin 2020, un recours en annulation de la loi du 20 décembre 2019 a été introduit."
)


def test_blank_lines_separate_headings_and_list_items():
    text = pdf_text(f"I. Objet des recours et procédure\na. {LONG}\nb. {LONG}")
    assert "\nI. Objet des recours et procédure\na. Par requête" in "\n" + text
    assert "\nb. Par requête" in text
    assert "procédure a." not in text


def test_justified_lines_are_joined_even_in_double_spacing():
    text = pdf_text(f"@interligne 2\n> {LONG} {LONG}")
    assert "\n" not in text.strip()
    assert "parvenue au greffe" in text


def test_line_end_compound_hyphens_are_kept():
    text = pdf_text(
        "> Les parties requérantes soutiennent que la loi impose à l'avocat-¦intermédiaire de "
        "notifier eux-¦mêmes les autres intermédiaires (Doc. parl., Parlement flamand, 2019-¦2020, "
        "arrêt C-¦694/20) dans tous les cas visés."
    )
    for expected in ("avocat-intermédiaire", "eux-mêmes", "2019-2020", "C-694/20"):
        assert expected in text, expected
    for wrong in ("avocatintermédiaire", "euxmêmes", "2019- 2020", "C- 694"):
        assert wrong not in text, wrong


def test_syllabic_hyphenation_is_removed():
    text = pdf_text(
        "> Les modalités conven¬tionnelles proposées par les parties tiennent compte de l'horaire "
        "scolaire des enfants et des contraintes professionnelles de chacun."
    )
    assert "conventionnelles" in text and "conven-" not in text


def test_full_width_line_ending_with_colon_is_joined():
    text = pdf_text(
        "> La directive 2011/16/UE du Conseil relative à la coopération fiscale (ci-après :"
        "¦la directive 2011/16/UE) est modifiée par la directive (UE) 2018/822 du Conseil."
    )
    assert "(ci-après : la directive 2011/16/UE)" in text


def test_signature_columns_are_separated():
    text = pdf_text(f"> {LONG}\n|| Le greffier, || Le président,\n|| N. Dupont || P. Nihoul")
    assert "Le greffier,\tLe président," in text
    assert "N. Dupont\tP. Nihoul" in text


def test_list_items_without_blank_lines_stay_separate():
    pdf = fitz.open()
    page = pdf.new_page()
    y = 72
    for line in ("Ont comparu :", "- Me P. Malherbe, pour les parties requérantes ;",
                 "- Me S. Scarnà, pour la partie requérante ;", "- les parties ont été entendues."):
        page.insert_text((72, y), line, fontsize=11)
        y += 14
    text = extract_text("liste.pdf", pdf.tobytes())
    assert text.splitlines() == [
        "Ont comparu :", "- Me P. Malherbe, pour les parties requérantes ;",
        "- Me S. Scarnà, pour la partie requérante ;", "- les parties ont été entendues.",
    ]


def test_running_header_and_footer_are_kept_once_and_sentences_cross_pages():
    sentence = " ".join(["Le tribunal examine la situation des parties avec attention."] * 9)
    source = "\n".join(f"> {sentence}" for _ in range(8)) + "\n> Fin du jugement."
    text = pdf_text(source, footer="R.G. n° 23/4521/A - Jugement du 14 mars 2024")
    assert text.count("R.G. n° 23/4521/A - Jugement du 14 mars 2024") == 1
    assert "Jugement du 14 mars 2024 2" not in text
    assert not any(line.strip().isdigit() for line in text.splitlines()), "numéro de page"
    # Aucune phrase coupée par le saut de page : chaque paragraphe est entier.
    paragraphs = [p for p in text.splitlines() if p.startswith("Le tribunal")]
    assert len(paragraphs) == 8 and all(p.endswith("attention.") for p in paragraphs)


def test_page_numbers_alone_are_dropped_but_content_numbers_kept():
    text = pdf_text(f"> {LONG}\n@page\n> Article 12 : {LONG}")
    assert "Article 12 :" in text
    assert "\n2\n" not in f"\n{text}\n"


def test_unreadable_text_layer_is_read_by_ocr_instead(monkeypatch):
    """Police sans table de caractères : le texte extrait n'est que du bruit
    (caractères privés), qui masquerait des noms sans qu'on puisse les lire."""
    import api.anonymize_file as af

    pdf = fitz.open()
    pdf.new_page().insert_text((72, 72), "texte quelconque", fontsize=11)
    garbage = [af._Segment(72, 60 + 14 * i, 500, 72 + 14 * i, "\ue001\ue002\ue003 \ue004\ue005" * 8, 11, False)
               for i in range(5)]
    monkeypatch.setattr(af, "_segments", lambda page: garbage)
    seen = []
    monkeypatch.setattr(af.ocr, "is_available", lambda: True)
    monkeypatch.setattr(af.ocr, "ocr_many", lambda images, progress=None: seen.extend(images) or [
        af.ocr.OcrResult("Monsieur Jean DUPONT", 95.0) for _ in images])
    text = extract_text("illisible.pdf", pdf.tobytes())
    assert seen, "la page aurait dû être lue par OCR"
    assert "\ue001" not in text and "Jean DUPONT" in text


def test_garbled_detection():
    from api.anonymize_file import _garbled

    assert _garbled("\ue001\ue002 \ufffd\ufffd " * 10)
    assert not _garbled("Le tribunal de la famille de Namur, statuant contradictoirement.")


@pytest.mark.parametrize("left, right, expected", [
    ("avocat-", "intermédiaire", "avocat-intermédiaire"),
    ("conven-", "tionnelles", "conventionnelles"),
    ("Jean-", "Pierre", "Jean-Pierre"),
    ("2019-", "2020", "2019-2020"),
    ("ci-", "après", "ci-après"),
    ("irrecevabi-", "lité", "irrecevabilité"),
])
def test_hyphen_decision(left, right, expected):
    from api.anonymize_file import _join_text
    from collections import Counter

    assert _join_text(left, right, Counter()) == expected


def test_words_split_into_fragments_are_not_glued():
    """Fragments d'une ligne justifiée dont l'espace final occupe tout l'écart :
    « déposée » + « au greffe » ne doit pas devenir « déposéeau »."""
    pdf = fitz.open()
    page = pdf.new_page()
    x = 72
    for word in ("la", "requête", "déposée", "au", "greffe", "le", "4", "octobre."):
        page.insert_text((x, 100), word + " ", fontsize=11)
        x += fitz.get_text_length(word + " ", fontsize=11)
    text = extract_text("collé.pdf", pdf.tobytes())
    assert text == "la requête déposée au greffe le 4 octobre."


def test_a_year_alone_in_the_margin_is_not_a_page_number():
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 100), "Rapport annuel de la commission.", fontsize=11)
    page.insert_text((280, 800), "2024", fontsize=11)
    assert "2024" in extract_text("rapport.pdf", pdf.tobytes())
