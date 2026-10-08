"""Personnes : initiales, « M. », initiales seules, pseudonymes sans collision.

Politique : une personne = un seul [PERSONNE_n] dans tout le document ; deux
personnes ne partagent jamais une étiquette ; l'initiale accolée au nom fait
partie du masque (« N. Dupont » → « [PERSONNE_2] »).
"""

import re

import pytest

from api import nlp_engine
from api.nlp_engine import anonymize_text, detect_entities


def labels_of(text: str, mention: str) -> set[str]:
    """Étiquettes couvrant chaque occurrence de ``mention`` (None si visible).

    Seule la partie entre accolades est vérifiée : « Madame {DUPONT} expose ».
    """
    detections = detect_entities(text)
    if "{" in mention:
        before, rest = mention.split("{", 1)
        target, after = rest.split("}", 1)
        pattern = re.escape(before) + "(" + re.escape(target) + ")" + re.escape(after)
    else:
        pattern = "(" + re.escape(mention) + ")"
    found = set()
    for m in re.finditer(pattern, text):
        start, end = m.span(1)
        found.add(next((d.label for d in detections if d.start <= start and end <= d.end), None))
    return found


COMPOSITION = (
    "La Cour constitutionnelle, composée des présidents P. Nihoul et L. Lavrysen, des juges "
    "T. Giet, J. Moerman, M. Pâques, Y. Kherbache, D. Pieters, S. de Bethune, E. Bribosia et "
    "K. Jadin, assistée du greffier N. Dupont, présidée par le président P. Nihoul, rend l'arrêt "
    "suivant.\nLe greffier,\tLe président,\nN. Dupont\tP. Nihoul"
)


def test_initials_are_part_of_the_mask():
    result = anonymize_text(COMPOSITION)
    assert not re.search(r"(?<![\w.])[A-Z]\.(?:-[A-Z]\.)? ?\[PERSONNE", result), result
    for name in ("Nihoul", "Lavrysen", "Giet", "Moerman", "Pâques", "Kherbache", "Pieters",
                 "Bethune", "Bribosia", "Jadin", "Dupont"):
        assert name not in result, name


def test_signature_columns_do_not_merge_two_persons():
    dupont, nihoul = labels_of(COMPOSITION, "N. Dupont"), labels_of(COMPOSITION, "P. Nihoul")
    assert len(dupont) == 1 and len(nihoul) == 1 and None not in dupont | nihoul
    assert dupont != nihoul
    result = anonymize_text(COMPOSITION)
    assert re.search(r"\[PERSONNE_\d+\]\t\[PERSONNE_\d+\]$", result), result


def test_two_persons_on_one_line_are_not_merged():
    text = "Le greffier, Le président,\nN. Dupont P. Nihoul\nLe président P. Nihoul a signé."
    assert labels_of(text, "N. Dupont").isdisjoint(labels_of(text, "P. Nihoul"))


def test_same_surname_different_initials_are_different_persons():
    text = "Ont comparu P. Dupont, demandeur, et N. Dupont, défendeur. P. Dupont a ensuite répliqué."
    assert labels_of(text, "P. Dupont").isdisjoint(labels_of(text, "N. Dupont"))
    assert len(labels_of(text, "P. Dupont")) == 1


def test_family_sharing_a_surname():
    text = (
        "Monsieur Jean DUPONT et Madame Marie DUPONT se sont mariés en 2010. Monsieur Jean DUPONT "
        "travaille à Namur. Monsieur Thomas DUPONT, frère du demandeur, a témoigné. "
        "Madame DUPONT expose que les enfants résident chez elle. Monsieur DUPONT conteste."
    )
    jean, marie, thomas = (labels_of(text, n) for n in ("Jean DUPONT", "Marie DUPONT", "Thomas DUPONT"))
    assert len({frozenset(jean), frozenset(marie), frozenset(thomas)}) == 3
    assert labels_of(text, "Madame {DUPONT} expose") == marie
    assert labels_of(text, "Monsieur {DUPONT} conteste") == jean  # le plus cité


def test_monsieur_abbreviation_before_first_and_last_name_stays_visible():
    result = anonymize_text("M. Jean DUPONT a déposé une requête. M. Pâques, juge, a siégé.")
    assert result.startswith("M. [PERSONNE_1] a déposé")
    assert "M. [PERSONNE" not in result[20:]  # « M. Pâques » : l'initiale est masquée


def test_initials_only_designating_persons_are_masked():
    text = ("Le recours a été introduit par l'association de fait « Belgian Association of Tax "
            "Lawyers », P.V. et G.G., assistés et représentés par Me P. Malherbe. "
            "L'attestation de Madame F.M., voisine, est produite.")
    result = anonymize_text(text)
    for initials in ("P.V.", "G.G.", "F.M."):
        assert initials not in result, initials
    assert labels_of(text, "P.V.").isdisjoint(labels_of(text, "G.G."))


@pytest.mark.parametrize("text, kept", [
    ("Un P.V. a été dressé le 3 mai 2024.", "P.V."),
    ("Voir le P.V. n° 123/2024 et le R.G. de la cause.", "P.V."),
    ("(J.T., 2017, p. 345)", "J.T."),
])
def test_abbreviations_are_not_initials(text, kept):
    assert kept in anonymize_text(text)


def test_initials_masking_can_be_disabled(monkeypatch):
    monkeypatch.setattr(nlp_engine, "MASK_INITIALS", False)
    result = anonymize_text("Le recours a été introduit par P.V. et G.G., assistés de leur avocat.")
    assert "P.V." in result and "G.G." in result


def test_pseudonyms_are_deterministic():
    first = anonymize_text(COMPOSITION)
    assert all(anonymize_text(COMPOSITION) == first for _ in range(2))
