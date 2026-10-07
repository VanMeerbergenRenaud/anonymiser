"""Tests rapides des règles de détection (sans modèle NER)."""

import pytest

from api.recognizers import build_recognizers

RECOGNIZER = build_recognizers()[0]


def found(text: str, entity: str, min_score: float = 0.5) -> list[str]:
    """Valeurs détectées pour un type donné (score ≥ ``min_score``)."""
    return [
        text[r.start:r.end]
        for r in RECOGNIZER.analyze(text, [])
        if r.entity_type == entity and r.score >= min_score
    ]


@pytest.mark.parametrize("text, expected", [
    ("Registre national : 85.07.30-033.28", "85.07.30-033.28"),       # clé valide
    ("NN 03.02.14-125.46 (né en 2003)", "03.02.14-125.46"),           # né après 2000
    ("numéro national 850730 033 28", "850730 033 28"),
    ("NN : 85073003399", "85073003399"),                              # clé fausse + contexte
])
def test_belgian_national_number(text, expected):
    assert expected in found(text, "BE_NATIONAL_NUMBER")


def test_national_number_needs_context_when_unformatted():
    assert found("Référence de commande 85073003399.", "BE_NATIONAL_NUMBER") == []


@pytest.mark.parametrize("text, expected", [
    ("IBAN BE68 5390 0754 7034.", "BE68 5390 0754 7034"),
    ("compte FR76 3000 4028 1539 4587 1102 345 ouvert", "FR76 3000 4028 1539 4587 1102 345"),
])
def test_iban(text, expected):
    assert expected in found(text, "IBAN_CODE")


@pytest.mark.parametrize("text, expected", [
    ("domicilié Rue Petit Bioleux 18, 4120 Neupré.", "Rue Petit Bioleux 18, 4120 Neupré"),
    ("au 45 rue de la République, Bâtiment C, 69002 Lyon.", "45 rue de la République, Bâtiment C, 69002 Lyon"),
    ("Avenue Louise 54 bte 3, 1050 Ixelles", "Avenue Louise 54 bte 3, 1050 Ixelles"),
    ("Kerkstraat 12 bus 3, 9000 Gent", "Kerkstraat 12 bus 3, 9000 Gent"),
    ("établi à 4000 Liège, Mont Saint-Martin 3.", "Mont Saint-Martin 3"),
])
def test_addresses(text, expected):
    assert expected in found(text, "ADDRESS")


@pytest.mark.parametrize("text", [
    "au cours de l'audience du 15 mars 2024",
    "la mise en place de la Commission",
    "vu l'article 1240 du Code civil",
    "la somme de 4000 euros",
])
def test_no_false_address(text):
    assert found(text, "ADDRESS") == []


@pytest.mark.parametrize("text, expected", [
    ("Monsieur Jean DUPONT, né", ["Jean DUPONT"]),
    ("Mme M. LEJEUNE, juge", ["M. LEJEUNE"]),
    ("le Docteur Van der Linden a attesté", ["Van der Linden"]),
    ("Monsieur le Juge Dupont", ["Dupont"]),
    ("Madame la Présidente", []),
    ("Nom : DUPONT   Prénom : Jean", ["DUPONT", "Jean"]),
])
def test_titled_persons(text, expected):
    assert sorted(set(found(text, "PERSON"))) == sorted(expected)


def test_birth_date_masks_only_the_date():
    assert found("né à Liège le 31 mai 2001", "BIRTH_DATE") == ["31 mai 2001"]


@pytest.mark.parametrize("text, expected", [
    ("GSM 0475/12.34.56", "0475/12.34.56"),
    ("tél. 04 223 45 67", "04 223 45 67"),
    ("+32 475 12 34 56", "+32 475 12 34 56"),
    ("06 12 34 56 78", "06 12 34 56 78"),
])
def test_phones(text, expected):
    assert expected in found(text, "PHONE_NUMBER")


@pytest.mark.parametrize("text", ["le 01.02.2024 10:30", "en 2025", "le 3 mars 2025"])
def test_dates_are_not_phones(text):
    assert found(text, "PHONE_NUMBER", min_score=0.3) == []


def test_vat_and_enterprise_number():
    assert "BE 0123.456.749" in found("TVA BE 0123.456.749", "VAT_NUMBER")
