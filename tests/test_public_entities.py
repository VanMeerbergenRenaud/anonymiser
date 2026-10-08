"""Sur-anonymisation : références publiques, institutions, pays, lieux,
organisations et jurisprudence restent lisibles ; les décisions sont les
mêmes dans tout le document. Doit passer avec les deux moteurs NER.
"""

import re

import pytest

from api import nlp_engine
from api.nlp_engine import anonymize_text


def kept(text: str, *fragments: str) -> str:
    result = anonymize_text(text)
    for fragment in fragments:
        assert fragment in result, (fragment, result)
    return result


@pytest.mark.parametrize("text, fragments", [
    ("Par l’arrêt n° 168/2020 du 17 décembre 2020 (ECLI:BE:GHCC:2020:ARR.168), la Cour a rejeté "
     "la demande.\nECLI:BE:GHCC:2024:ARR.001",
     ["arrêt n° 168/2020", "ECLI:BE:GHCC:2020:ARR.168", "ECLI:BE:GHCC:2024:ARR.001"]),
    ("Par arrêt du 8 décembre 2022 (C-694/20, ECLI:EU:C:2022:963), la Cour de justice a répondu.",
     ["C-694/20", "ECLI:EU:C:2022:963"]),
    ("sur les griefs mentionnés en B.64 et B.87 de l’arrêt de la Cour n° 103/2022. Voir A.2.1 "
     "et B.6.2.", ["B.64", "B.87", "n° 103/2022", "A.2.1", "B.6.2"]),
    ("(Cour EDH, arrêt du 6 décembre 2012, CE:ECHR:2012:1206JUD001232311, §§ 117 et 118)",
     ["CE:ECHR:2012:1206JUD001232311"]),
    ("Cass., 18 octobre 2019, C.18.0453.F ; C. const., 12 mai 2016, n° 70/2016.",
     ["C.18.0453.F", "n° 70/2016"]),
    ("Arrêt n° 103/2022 (C-694/20).", ["Arrêt n° 103/2022 (C-694/20)."]),
])
def test_public_references_are_never_altered(text, fragments):
    kept(text, *fragments)


INSTITUTIONS = (
    "L’article 52 de la Charte vise les objectifs d’intérêt général reconnus par l’Union. "
    "Chaque État membre prend les mesures nécessaires ; les États membres échangent des "
    "informations. L’Autorité de protection des données a rendu un avis. Le recours a été "
    "introduit par l’Ordre des barreaux francophones et germanophone et par l’« Orde van Vlaamse "
    "balies ». La Commission et le Conseil des ministres, représenté par le SPF Finances, ont "
    "répondu. La Cour de justice de l’Union européenne a été saisie dans l’affaire Orde van "
    "Vlaamse Balies e.a."
)


def test_institutions_stay_readable():
    kept(INSTITUTIONS, "l’Union.", "État membre", "États membres", "Autorité de protection des données",
         "Ordre des barreaux francophones et germanophone", "Orde van Vlaamse balies",
         "Orde van Vlaamse Balies e.a.", "Commission", "Conseil des ministres", "SPF Finances",
         "Cour de justice de l’Union européenne")


def test_countries_and_unlinked_places_stay_readable():
    kept("La convention européenne des droits de l’homme, signée à Rome le 4 novembre 1950, "
         "s’applique en France et en Turquie. Les faits se sont produits à Wavre. "
         "Fait à Bruxelles, le 1er septembre 2023.",
         "signée à Rome", "en France", "en Turquie", "à Wavre", "Fait à Bruxelles")


@pytest.mark.parametrize("text, hidden", [
    ("Monsieur Paul HENRY, domicilié à Namur, a comparu.", "Namur"),
    ("Madame Sophie MARCHAL, née à Ciney le 21 août 1983, a comparu.", "Ciney"),
    ("Monsieur Paul HENRY, né le 3 mars 1980 à Dinant, a comparu.", "Dinant"),
    ("Depuis la séparation, l'enfant réside chez sa mère à Liège.", "Liège"),
])
def test_places_linked_to_a_person_are_masked(text, hidden):
    assert hidden not in anonymize_text(text)


CASE_LAW = (
    "La Cour se réfère à sa jurisprudence (voir, en ce sens, Cour EDH, arrêt du 6 décembre 2012, "
    "Michaud c. France, CE:ECHR:2012:1206JUD001232311, §§ 117 et 118 ; arrêt du 2 février 2021, "
    "Consob, C-481/19, EU:C:2021:84, points 36 et 37 ; arrêt du 22 novembre 2022, Luxembourg "
    "Business Registers et Sovim, C-37/20 et C-601/20, EU:C:2022:912 ; Cour eur. D.H., arrêt "
    "Marckx c. Belgique du 13 juin 1979 ; [Cour EDH, arrêt du 9 avril 2019, Altay c. Turquie "
    "(N° 2), CE:ECHR:2019:0409JUD001123609, § 49]). Par arrêt du 8 décembre 2022 en cause de "
    "Orde van Vlaamse Balies e.a. (C-694/20, ECLI:EU:C:2022:963), la Cour de justice a répondu."
)


def test_cited_case_law_stays_readable():
    kept(CASE_LAW, "Michaud c. France", "Consob, C-481/19", "Luxembourg Business Registers et Sovim",
         "Marckx c. Belgique", "Altay c. Turquie", "en cause de Orde van Vlaamse Balies e.a.")


def test_cited_case_law_can_be_masked(monkeypatch):
    monkeypatch.setattr(nlp_engine, "MASK_CASE_LAW", True)
    result = anonymize_text(CASE_LAW)
    for name in ("Michaud", "Consob", "Marckx", "Altay", "Sovim"):
        assert name not in result, name
    assert "CE:ECHR:2012:1206JUD001232311" in result and "C-481/19" in result


def test_party_named_like_a_cited_case_is_masked_everywhere():
    result = anonymize_text(
        "Monsieur Jean MICHAUD a introduit une requête. Il invoque l'arrêt Michaud c. France "
        "(Cour EDH, 6 décembre 2012, CE:ECHR:2012:1206JUD001232311)."
    )
    assert "MICHAUD" not in result and "Michaud" not in result


@pytest.mark.parametrize("text, fragment", [
    ("Il travaillait pour la SRL Bati-Meuse jusqu'en 2023.", "SRL Bati-Meuse"),
    ("Monsieur GILSON travaille pour la SA Ethias.", "SA Ethias"),
    ("la S.A. Ethias et la S.P.R.L. Dupuis ont conclu un accord.", "S.A. Ethias"),
    ("introduits par l’association de fait « Belgian Association of Tax Lawyers » et autres",
     "Belgian Association of Tax Lawyers"),
    ("(arrêt du 6 octobre 2020, Privacy International, C-623/17, EU:C:2020:790)", "Privacy International"),
])
def test_legal_persons_stay_readable_by_default(text, fragment):
    kept(text, fragment)


def test_legal_persons_can_be_masked(monkeypatch):
    monkeypatch.setattr(nlp_engine, "MASK_ORGANIZATIONS", True)
    result = anonymize_text("Monsieur GILSON travaille pour la société Ethias Assurances depuis 2010.")
    assert "Ethias" not in result


def test_person_split_by_the_ner_into_organization_and_person_is_fully_masked():
    text = ("introduits par l’« Orde van Vlaamse balies » et Alex Tallon et par l’Institut des "
            "experts-comptables. Le recours de l’« Orde van Vlaamse balies » et Alex Tallon a été "
            "introduit le 30 juin 2020.")
    result = anonymize_text(text)
    assert "Alex" not in result and "Tallon" not in result
    assert len(set(re.findall(r"\[PERSONNE_\d+\]", result))) == 1
    assert result.count("Orde van Vlaamse balies") == 2


def test_person_name_inside_a_company_name_is_masked():
    result = anonymize_text(
        "Cabinet Leclercq & Associés – Avenue de la Toison d'Or 52, 1060 Bruxelles\n"
        "POUR : Madame Véronique HENROTTE, ayant pour conseil Me Isabelle LECLERCQ, avocate."
    )
    assert "Leclercq" not in result and "LECLERCQ" not in result
    assert "Cabinet [PERSONNE_" in result


@pytest.mark.parametrize("text, fragment", [
    ("Partant, il y a lieu d’annuler les dispositions attaquées.", "Partant, il y a lieu"),
    ("Numéros du rôle : 7407 et 7409", "Numéros du rôle"),
])
def test_common_words_are_not_names(text, fragment):
    kept(text, fragment)


def test_same_string_gets_the_same_decision_everywhere():
    contexts = [
        "Le recours de l’« Orde van Vlaamse balies » a été introduit le 30 juin 2020.",
        "L’« Orde van Vlaamse balies » soutient que la loi viole la Constitution.",
        "Selon l’Orde van Vlaamse balies, l’obligation est disproportionnée.",
        "Monsieur Alex Tallon et l’Orde van Vlaamse balies demandent l’annulation.",
    ]
    result = anonymize_text(" ".join(contexts))
    assert result.count("Orde van Vlaamse balies") == 4
    assert "Tallon" not in result


def test_grand_place_addresses_are_masked():
    result = anonymize_text("Madame Gaëlle VERSTRAETEN, domiciliée à 7500 Tournai, Grand-Place 22, appelante.")
    assert "Grand-Place" not in result and "22" not in result


def test_residence_with_an_apposition_is_masked():
    result = anonymize_text(
        "Madame Gaëlle VERSTRAETEN réside actuellement chez sa sœur, Madame Inès VERSTRAETEN, à Ath.")
    assert "Ath" not in result


def test_company_seat_is_not_a_person():
    result = anonymize_text("la SA Clinique Saint-Luc, dont le siège est établi à Bouge, intimée, "
                            "représentée par Me François Vandevelde.")
    assert "[PERSONNE_1]" in result and "établi à [PERSONNE" not in result
