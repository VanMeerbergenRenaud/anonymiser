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


# ---------------------------------------------------------------------------
# Numéros de rôle (RG / FA) et références de dossier
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("R.G. n° 24/1234/A — audience", "24/1234/A"),
    ("RG 2024/1234", "2024/1234"),
    ("R.G. : 2023/AL/123", "2023/AL/123"),
    ("N° RG 22/00123 - N° Portalis DBVX-V-B7G-NXYZ", "22/00123"),
    ("N° de rôle : 23/4567/A", "23/4567/A"),
    ("Numéro de rôle général 21/00987/B", "21/00987/B"),
    ("inscrite au rôle n° 19/555/A", "19/555/A"),
    ("RG n°s 19/1111/A, 19/2222/A et 19/3333/A", "19/1111/A, 19/2222/A et 19/3333/A"),
    ("Affaire FA 25/456", "25/456"),
    ("Le numéro 2023/789 RG a été joint.", "2023/789"),
    ("Le dossier 21/1234/A (R.G.) est fixé.", "21/1234/A"),
    ("Dossier 22/321/FA", "22/321/FA"),
    ("RG 2024/FA/123", "2024/FA/123"),
    ("Réf. FA/2021/123", "FA/2021/123"),
    ("rolnummer 2023/1234/A", "2023/1234/A"),
])
def test_role_numbers(text, expected):
    assert expected in found(text, "FR_NUM_ROLE")


@pytest.mark.parametrize("text", [
    "le RG du tribunal",                       # mention sans numéro
    "la famille FA a été entendue",            # pas de chiffres
    "article 12 du RGPD",                      # RGPD ≠ RG
    "le rôle de la mère est essentiel",        # « rôle » sans « numéro de »
    "page 12/45 du rapport",                   # aucune mention RG / FA
])
def test_no_false_role_number(text):
    assert found(text, "FR_NUM_ROLE") == []


def test_role_number_does_not_swallow_a_time():
    assert found("RG 21/123 à 14 h 30", "FR_NUM_ROLE") == ["21/123"]


def test_role_number_keeps_the_mention():
    assert found("R.G. n° 24/1234/A", "FR_NUM_ROLE") == ["24/1234/A"]


@pytest.mark.parametrize("text, expected", [
    ("Rép. n° 2025/5678", "2025/5678"),
    ("répertoire général n° 2024/12345", "2024/12345"),
    ("notice du parquet LI.55.L1.012345/2023", "LI.55.L1.012345/2023"),
    ("PV n° BR.12.L3.004567/2024", "BR.12.L3.004567/2024"),
    ("N° Portalis DBVX-V-B7G-NXYZ", "DBVX-V-B7G-NXYZ"),
])
def test_case_references(text, expected):
    assert expected in found(text, "CASE_REFERENCE")


# ---------------------------------------------------------------------------
# Registre national annoncé par RN / NN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("RN 85.04.03-123.45", "85.04.03-123.45"),
    ("R.N. 820115-123-45", "820115-123-45"),
    ("NN 82011512345", "82011512345"),
    ("N.N. : 82.01.15-123.46", "82.01.15-123.46"),
    ("(RN : 82011512346)", "82011512346"),
    ("son numéro national (NN) est le 82.01.15 123 45.", "82.01.15 123 45"),
    ("85.04.03-123.45 (RN)", "85.04.03-123.45"),
    ("NISS 85.04.03-123.45", "85.04.03-123.45"),
    # Date impossible et clé fausse : masqué malgré tout grâce à la mention.
    ("RN 85.13.45-123.45", "85.13.45-123.45"),
    ("NN 8513451234", "8513451234"),                                   # chiffre manquant
])
def test_national_number_after_mention(text, expected):
    assert expected in found(text, "BE_NATIONAL_NUMBER")


@pytest.mark.parametrize("text", [
    "la RN 4 vers Namur",                     # route nationale
    "sortie de la RN25",
    "NN : voir annexe 3",
])
def test_no_false_national_number(text):
    assert found(text, "BE_NATIONAL_NUMBER") == []


# ---------------------------------------------------------------------------
# Téléphones belges / français (formats supplémentaires)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("tél. (081) 22 33 44", "(081) 22 33 44"),
    ("(02) 512.34.56", "(02) 512.34.56"),
    ("+32 (0)4 223 45 68", "+32 (0)4 223 45 68"),
    ("+32(0)2/512.34.56", "+32(0)2/512.34.56"),
    ("0032 4 223 45 69", "0032 4 223 45 69"),
    ("+32471234567", "+32471234567"),
    ("+33612345678", "+33612345678"),
    ("0033612345678", "0033612345678"),
    ("0471/234.567", "0471/234.567"),
    ("0471-23-45-67", "0471-23-45-67"),
    ("tél.0475 12 34 56", "0475 12 34 56"),
    ("02/512.34.56", "02/512.34.56"),
    ("010 45 67 89", "010 45 67 89"),
    ("06.12.34.56.78", "06.12.34.56.78"),
    ("+33 (0)6 12 34 56 78", "+33 (0)6 12 34 56 78"),
])
def test_more_phone_formats(text, expected):
    assert expected in found(text, "PHONE_NUMBER")


@pytest.mark.parametrize("text", [
    "TVA BE 0123.456.749",                    # numéro d'entreprise valide
    "montant de 1.234,56 EUR",
    "le 01.02.2024",
    "réf. 001234567890",                      # 00 + indicatif inconnu
])
def test_no_false_phone(text):
    assert found(text, "PHONE_NUMBER", min_score=0.3) == []


def test_phone_field_keeps_only_the_number():
    text = "GSM : 0475/12.34.56 — fax 081/22.33.45"
    phones = found(text, "PHONE_NUMBER")
    assert "0475/12.34.56" in phones and "081/22.33.45" in phones
    assert all("fax" not in p for p in phones)


# ---------------------------------------------------------------------------
# Numéros de rôle : pluriel, listes de nombres, « du rôle » après, « affaire n° »
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("Numéros du rôle : 7407, 7409, 7410 et 7412", "7407, 7409, 7410 et 7412"),
    ("Numéro du rôle : 7407", "7407"),
    ("n°s du rôle 2023/123 et 2023/456", "2023/123 et 2023/456"),
    ("Ces affaires, inscrites sous les numéros 7407, 7409, 7410 et 7412 du rôle de la Cour",
     "7407, 7409, 7410 et 7412"),
    ("L'affaire inscrite sous le numéro 7407 du rôle", "7407"),
    ("inscrite au rôle général sous le n° 24/1234/A", "24/1234/A"),
    ("les parties requérantes dans l’affaire n° 7407;", "7407"),
    ("dans les affaires nos 7409 et 7410", "7409 et 7410"),
    ("rolnummers 7407 en 7409", "7407 en 7409"),
])
def test_role_number_lists_and_mentions(text, expected):
    assert expected in found(text, "FR_NUM_ROLE")


@pytest.mark.parametrize("text", [
    "dans l'affaire C-694/20, la Cour de justice",       # n° d'affaire de la CJUE : public
    "dans l'affaire n° C-694/20",
    "Par l’arrêt n° 103/2022 du 15 septembre 2022",     # n° d'arrêt public
    "le rôle de l'avocat est essentiel",
    "numéros de téléphone 0475 12 34 56",
])
def test_no_false_role_number_in_public_references(text):
    assert found(text, "FR_NUM_ROLE", min_score=0.3) == []


def test_role_list_does_not_swallow_small_numbers():
    assert found("Numéros du rôle : 7407 et 2 autres affaires", "FR_NUM_ROLE") == ["7407"]
