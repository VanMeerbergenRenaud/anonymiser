"""Défauts trouvés sur des documents inédits (jeu « inédit » du corpus).

Chaque test isole une cause racine : fuite, collision de pseudonymes, mot
courant masqué à tort.
"""

import pytest

from api.nlp_engine import anonymize_text
from tests.test_persons import labels_of


# ---------------------------------------------------------------------------
# Fuites
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("phone", [
    "0032 476 123456", "0032 476 12 34 56", "0032 4 223 45 67", "0033 6 12 34 56 78",
])
def test_phone_with_00_prefix(phone):
    out = anonymize_text(f"Joignable au {phone}, en soirée.")
    assert phone not in out and "[TÉLÉPHONE]" in out


@pytest.mark.parametrize("text, name", [
    ("Entendu par l'INP Lambert, matricule 12345.", "Lambert"),
    ("Rédigé par l'inspecteur principal Lambert.", "Lambert"),
    ("Le commissaire Leroy a dirigé l'enquête.", "Leroy"),
    ("L'agent de quartier Simon s'est présenté.", "Simon"),
    ("Le premier inspecteur Martin et l'INPP Petit sont intervenus.", "Petit"),
])
def test_police_rank_before_name(text, name):
    assert name not in anonymize_text(text)


def test_police_matricule_is_masked():
    out = anonymize_text("Entendu par l'INP Lambert, matricule 12345.")
    assert "12345" not in out and "matricule" in out


@pytest.mark.parametrize("text, place", [
    ("Lieu de naissance : Seraing\nNationalité : belge", "Seraing"),
    ("Lieu de naissance: Ottignies-Louvain-la-Neuve", "Ottignies-Louvain-la-Neuve"),
    ("Né(e) à : Huy", "Huy"),
])
def test_birthplace_field(text, place):
    out = anonymize_text(text)
    assert place not in out


@pytest.mark.parametrize("text, leaked", [
    ("Monsieur jean dupont, domicilié à Namur.", "dupont"),
    ("Madame marie-claire van damme est présente.", "damme"),
    ("Mme sophie lemaire déclare.", "lemaire"),
])
def test_lowercase_name_after_civility(text, leaked):
    out = anonymize_text(text)
    assert leaked not in out.lower().replace("[personne", "")


def test_lowercase_after_civility_keeps_common_words():
    out = anonymize_text("Monsieur le juge a statué. Madame la présidente et Monsieur est absent.")
    assert out == "Monsieur le juge a statué. Madame la présidente et Monsieur est absent."


def test_first_name_in_parentheses_same_person():
    text = "Mme MARTIN-LEGRAND (Sophie) est intervenue. Peeters aussi."
    assert len(labels_of(text, "MARTIN-LEGRAND") | labels_of(text, "({Sophie})")) == 1


# ---------------------------------------------------------------------------
# Pseudonymes
# ---------------------------------------------------------------------------

def test_arabic_particle_surname_consistent():
    text = ("Madame Fatima BEN SAÏD, demanderesse, est assistée. Madame Ben Saïd expose. "
            "Mme BEN SAÏD conteste.")
    found = labels_of(text, "Fatima BEN SAÏD") | labels_of(text, "Madame {Ben Saïd}") \
        | labels_of(text, "Mme {BEN SAÏD}")
    assert len(found) == 1 and None not in found


def test_initial_m_before_particle_surname():
    text = "Monsieur Mohamed EL AMRANI, intimé. M. El Amrani a été victime d'un accident."
    out = anonymize_text(text)
    assert "M. [" not in out
    assert labels_of(text, "Mohamed EL AMRANI") == labels_of(text, "M. El Amrani")


def test_gender_from_first_name_prevents_collision():
    text = ("Me Anne Leroy, avocate. Me A. Leroy plaide. Monsieur Leroy, le père de l'avocate, "
            "est témoin.")
    assert labels_of(text, "Me {Anne Leroy}") == labels_of(text, "Me {A. Leroy}")
    assert not labels_of(text, "Me {Anne Leroy}") & labels_of(text, "Monsieur {Leroy}")


def test_gender_from_agreement_prevents_collision():
    text = ("Madame Claude Dubois, avocate, plaide. Monsieur Paul Dubois, son frère, témoigne. "
            "Le juge Dubois préside ; il est assisté de la greffière Dubois.")
    assert labels_of(text, "Madame {Claude Dubois}") != labels_of(text, "Monsieur {Paul Dubois}")
    assert not labels_of(text, "Madame {Claude Dubois}") & labels_of(text, "Le juge {Dubois}")


# ---------------------------------------------------------------------------
# Sur-anonymisation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("formula", [
    "L'AN DEUX MILLE VINGT-QUATRE, le quinze mars,",
    "Par-devant Nous, Maître",
    "ONT COMPARU :",
    "A COMPARU :",
    "LESQUELS COMPARANTS ONT REQUIS LE NOTAIRE",
    "DONT ACTE.",
    "POUR EXPÉDITION CONFORME",
])
def test_notarial_formulas_kept(formula):
    assert formula in anonymize_text(f"{formula} Jean-Philippe LAGAE, notaire à Bruxelles.")


@pytest.mark.parametrize("text, kept", [
    ("Représentée par Maître Pierre Lebrun loco Maître Anne Lejeune.", "loco"),
    ("L'enfant Lucas a été entendu.", "L'enfant"),
    ("Voir C. trav. Liège, 15 janvier 2019, J.T.T., 2019, p. 145.", "C. trav. Liège"),
    ("Voir C. trav. Liège, 15 janvier 2019, J.T.T., 2019, p. 145.", "J.T.T."),
])
def test_common_words_not_swallowed(text, kept):
    assert kept in anonymize_text(text)


# ---------------------------------------------------------------------------
# Références de dossier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, value", [
    ("Dossier R.R. 22/1234/A du tribunal de la famille.", "22/1234/A"),
    ("Réf. greffe : 2024/1234.", "2024/1234"),
    ("Notre réf. : DUP/2024/015.", "DUP/2024/015"),
    ("Votre référence : 23/0456/MV.", "23/0456/MV"),
    ("V/Réf. : 2023-118-MD", "2023-118-MD"),
    ("Requête n° 2024/RQ/12 déposée au greffe.", "2024/RQ/12"),
    ("Affaires jointes n° 8001 et 8002.", "8002"),
    ("Affaires jointes n° 8001 et 8002.", "8001"),
])
def test_case_references_masked(text, value):
    assert value not in anonymize_text(text)


@pytest.mark.parametrize("text", [
    "Arrêt n° 103/2022 de la Cour constitutionnelle.",
    "C.E., n° 245.123, 12 octobre 2019.",
    "M.B. 13 mars 2007, p. 12345.",
])
def test_public_references_kept(text):
    assert anonymize_text(text) == text
