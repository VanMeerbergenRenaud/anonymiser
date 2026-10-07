"""
recognizers — Détecteurs (« recognizers ») Presidio sur mesure.

Spécialisés pour les documents juridiques **belges** et **français** :

- numéro de registre national belge (avec validation de la clé modulo 97),
  numéro BIS, carte d'identité, passeport ;
- NIR français (n° de sécurité sociale, clé vérifiée) ;
- IBAN de tous pays (longueur + clé), anciens n° de compte belges ;
- numéros d'entreprise BCE/KBO et TVA belges, TVA/SIRET/SIREN français ;
- numéros de rôle (« R.G. n° 24/1234/A », « 2024/FA/123 », « 21/456 FA »…)
  et références de dossier (répertoire, notice du parquet, Portalis, PV) ;
- numéro de registre national annoncé par « RN », « NN », « NISS »… quel
  que soit son format (même avec une faute de frappe) ;
- téléphones belges / français / internationaux (« 0475/12.34.56 »,
  « (081) 22 33 44 », « +32 (0)2 512 34 56 », « +33612345678 »…) ;
- adresses postales (« Rue Petit Bioleux 18, 4120 Neupré », « 24 rue des
  Acacias, 69003 Lyon », « Kerkstraat 12 bus 3, 9000 Gent »…) ;
- noms de personnes introduits par une civilité ou une fonction
  (« Monsieur », « Me », « Maître », « Docteur », « la dame », « née »…) ;
- champs de formulaire (« Nom : … », « Adresse : … », « N° national : … ») ;
- dates de naissance (« né à Liège le 31 mai 2001 »), nationalité, plaques
  d'immatriculation, e-mails.

Chaque règle est une expression régulière éventuellement accompagnée d'un
validateur (clé de contrôle, cohérence de date…) et de mots de contexte qui
augmentent le score quand ils apparaissent juste avant la valeur.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional

from presidio_analyzer import EntityRecognizer, RecognizerResult

# ---------------------------------------------------------------------------
# Briques de base des expressions régulières
# ---------------------------------------------------------------------------

UPPER = "A-ZÀ-ÖØ-ÞŒŸ"
LOWER = "a-zß-öø-ÿœ"

CAP_WORD = rf"[{UPPER}][{LOWER}][{UPPER}{LOWER}]*(?:[-'’][{UPPER}{LOWER}]+)*"
"""Mot à initiale majuscule : Jean, Jean-Pierre, Saint-Exupéry, McDonald."""

UPPER_WORD = rf"[{UPPER}]{{2,}}(?:[-'’][{UPPER}]+)*"
"""Mot entièrement en capitales : DUPONT, LE-GALL, D'ORMESSON."""

_INITIAL = rf"[{UPPER}]\.(?:-[{UPPER}]\.)?"
_NAME_WORD = rf"(?:{CAP_WORD}|{UPPER_WORD}|{_INITIAL})"
_NAME_PARTICLE = (
    r"(?:(?:de|du|des|la|le|van|von|der|den|ter|ten|vanden|vander|vande|"
    r"di|da|del|della|dos|das|zu|op|het)[ \t]+|[dD]['’])"
)
PERSON_NAME = (
    rf"(?:{_NAME_PARTICLE})*{_NAME_WORD}"
    rf"(?:[ \t]+(?:{_NAME_PARTICLE})*{_NAME_WORD}){{0,4}}"
)
"""Nom de personne : 1 à 5 mots (particules « de », « van der »… comprises)."""

_MONTHS = (
    r"janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|"
    r"octobre|novembre|d[ée]cembre|janv|f[ée]vr?|avr|juil|sept|oct|nov|d[ée]c|"
    r"januari|februari|maart|mei|juni|juli|augustus|oktober"
)
DATE = (
    rf"(?:\d{{1,2}}(?:er|e)?[ \t]+(?i:{_MONTHS})\.?[ \t]+\d{{4}}"
    r"|\d{1,2}[/.-]\d{1,2}[/.-](?:\d{4}|\d{2})(?!\d)"
    r"|\d{1,2}[ \t]+\d{1,2}[ \t]+\d{4}"
    r"|\d{4}-\d{2}-\d{2})"
)


def fold(text: str) -> str:
    """Majuscules sans accents (comparaisons robustes : « Émilie » = « EMILIE »)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()


# ---------------------------------------------------------------------------
# Listes de mots
# ---------------------------------------------------------------------------

TITLE_WORDS: frozenset[str] = frozenset({
    "MONSIEUR", "MESSIEURS", "MADAME", "MESDAMES", "MADEMOISELLE",
    "MESDEMOISELLES", "M", "MM", "MR", "MRS", "MS", "MME", "MMES", "MLLE",
    "MLLES", "ME", "MES", "MAITRE", "MAITRES", "DOCTEUR", "DR", "DRE", "PR",
    "PROFESSEUR", "SIEUR", "DAME", "VEUVE", "VVE", "EPOUSE", "EPOUX", "NEE",
    "NE", "SOUSSIGNE", "SOUSSIGNEE", "DHR", "MEVR", "MEVROUW", "MEESTER",
    "MENEER", "MIJNHEER", "HEER",
})
"""Civilités (forme « fold »)."""

ROLE_WORDS: frozenset[str] = frozenset({
    "PRESIDENT", "PRESIDENTE", "JUGE", "JUGES", "PROCUREUR", "MINISTRE",
    "DIRECTEUR", "DIRECTRICE", "GERANT", "GERANTE", "ADMINISTRATEUR",
    "ADMINISTRATRICE", "AVOCAT", "AVOCATE", "AVOCATS", "GENERAL", "GENERALE",
    "NOTAIRE", "HUISSIER", "GREFFIER", "GREFFIERE", "CONSEILLER",
    "CONSEILLERE", "SUBSTITUT", "BATONNIER", "EXPERT", "EXPERTE", "PREMIER",
    "PREMIERE", "VICE", "SECRETAIRE", "TRESORIER", "CURATEUR", "CURATRICE",
    "LIQUIDATEUR", "MEDIATEUR", "MEDIATRICE", "DEMANDEUR", "DEMANDERESSE",
    "DEFENDEUR", "DEFENDERESSE", "REQUERANT", "REQUERANTE", "APPELANT",
    "APPELANTE", "INTIME", "INTIMEE", "PARTIE", "PARTIES", "CONCLUANT",
    "CONCLUANTE", "CLIENT", "CLIENTE", "TEMOIN", "PATIENT", "PATIENTE",
    "SALARIE", "SALARIEE", "EMPLOYEUR", "TRAVAILLEUR", "BOURGMESTRE",
    "ECHEVIN", "ECHEVINE", "MAIRE", "GOUVERNEUR", "ROI", "REINE",
    "COMMISSAIRE", "INSPECTEUR", "INSPECTRICE", "AGENT", "OFFICIER",
    "CONFRERE", "CONSOEUR", "MEDECIN", "INFIRMIER", "INFIRMIERE", "JUSTICE",
    "MAGISTRAT", "TUTEUR", "TUTRICE", "ADMINISTRATEUR", "MANDATAIRE",
    "REPRESENTANT", "REPRESENTANTE", "COLLEGUE", "DOYEN", "RECTEUR",
})
"""Fonctions / qualités : jamais un nom de personne."""

STREET_TYPE_WORDS: frozenset[str] = frozenset({
    "RUE", "RUELLE", "AVENUE", "AV", "BOULEVARD", "BD", "CHAUSSEE", "CHEE",
    "PLACE", "PL", "QUAI", "ALLEE", "CHEMIN", "IMPASSE", "SQUARE", "CLOS",
    "DREVE", "SENTIER", "ROUTE", "VOIE", "PARVIS", "GALERIE", "ROND-POINT",
    "VENELLE", "COUR", "COURS", "PASSAGE", "CITE", "RESIDENCE", "HAMEAU",
    "FAUBOURG", "ESPLANADE", "PROMENADE", "RAMPE", "MONTEE", "THIER",
    "STRAAT", "LAAN", "STEENWEG", "PLEIN",
})

MISC_STOP_WORDS: frozenset[str] = frozenset({
    "TEL", "TELEPHONE", "GSM", "FAX", "EMAIL", "E-MAIL", "MAIL", "COURRIEL",
    "ADRESSE", "DOMICILIE", "DOMICILIEE", "DEMEURANT", "NE", "NEE", "ET",
    "OU", "SA", "SPRL", "SRL", "ASBL", "SAS", "SARL", "SNC", "SCRL", "SC",
    "SCS", "NV", "BV", "BVBA", "VZW", "BELGIQUE", "FRANCE", "LUNDI", "MARDI",
    "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE", "JANVIER",
    "FEVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOUT", "SEPTEMBRE",
    "OCTOBRE", "NOVEMBRE", "DECEMBRE", "X", "Y", "Z", "XX", "XXX", "N",
    "TOUS", "TOUTES", "CI-DESSUS", "CI-APRES", "PAR", "POUR", "CONTRE",
    "AFFAIRE", "OBJET", "REF", "DOSSIER",
})

NAME_STOP_WORDS: frozenset[str] = TITLE_WORDS | ROLE_WORDS | STREET_TYPE_WORDS | MISC_STOP_WORDS
"""Mots qui ne peuvent pas faire partie d'un nom de personne."""

NAME_PARTICLES: frozenset[str] = frozenset({
    "DE", "DU", "DES", "LA", "LE", "VAN", "VON", "DER", "DEN", "TER", "TEN",
    "VANDEN", "VANDER", "VANDE", "DI", "DA", "DEL", "DELLA", "DOS", "DAS",
    "ZU", "OP", "HET", "D'", "D’",
})


def _token_key(token: str) -> str:
    return fold(token.strip(".,;:()[]\"'’"))


# ---------------------------------------------------------------------------
# Cadre générique : règle = regex + validateur + contexte
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Match:
    """Résultat d'un validateur : position, score et type éventuellement révisés."""

    start: int
    end: int
    score: float
    entity: Optional[str] = None


Validator = Callable[[re.Match, str, float], Optional[Match]]


@dataclass
class Rule:
    """Une expression régulière produisant des entités d'un type donné."""

    entity: str
    regex: re.Pattern
    score: float
    group: str | int = 0
    validator: Optional[Validator] = None
    context: Optional[re.Pattern] = None
    """Mots dont la présence juste avant la valeur augmente le score."""
    context_boost: float = 0.35
    context_window: int = 60
    entities: tuple[str, ...] = field(default_factory=tuple)
    """Types supplémentaires que le validateur peut renvoyer."""


class RegexRecognizer(EntityRecognizer):
    """Recognizer Presidio piloté par une liste de :class:`Rule`."""

    def __init__(self, name: str, rules: list[Rule], supported_language: str = "fr"):
        self.rules = rules
        entities = sorted({r.entity for r in rules} | {e for r in rules for e in r.entities})
        super().__init__(
            supported_entities=entities,
            name=name,
            supported_language=supported_language,
        )

    def load(self) -> None:  # noqa: D102 — rien à charger
        pass

    def analyze(self, text: str, entities: list[str], nlp_artifacts=None) -> list[RecognizerResult]:  # noqa: D102
        results: list[RecognizerResult] = []
        for rule in self.rules:
            for m in rule.regex.finditer(text):
                start, end = m.span(rule.group)
                if start < 0 or end <= start:
                    continue
                found = Match(start, end, rule.score, rule.entity)
                if rule.validator is not None:
                    found = rule.validator(m, text, rule.score)
                    if found is None:
                        continue
                entity = found.entity or rule.entity
                if entities and entity not in entities:
                    continue
                score = found.score
                if rule.context is not None and score < 1.0:
                    window = text[max(0, found.start - rule.context_window):found.start]
                    if rule.context.search(window):
                        score = min(1.0, score + rule.context_boost)
                if score <= 0:
                    continue
                results.append(RecognizerResult(
                    entity_type=entity,
                    start=found.start,
                    end=found.end,
                    score=score,
                    recognition_metadata={
                        RecognizerResult.RECOGNIZER_NAME_KEY: self.name,
                        RecognizerResult.RECOGNIZER_IDENTIFIER_KEY: self.id,
                    },
                ))
        return results


def _ctx(*words: str) -> re.Pattern:
    """Compile une liste de mots de contexte (insensible à la casse)."""
    return re.compile(r"(?i)(?<![\w])(?:" + "|".join(words) + r")(?![\w])")


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


# ---------------------------------------------------------------------------
# Identifiants nationaux
# ---------------------------------------------------------------------------

_NN_CONTEXT = _ctx(
    r"registre\s+national", r"num[ée]ro\s+national", r"n°\s*national",
    r"num[ée]ro\s+de\s+registre", r"NN", r"N\.N\.?", r"RRN", r"NISS", r"INSZ",
    r"rijksregister\w*", r"identifiant\s+national", r"num[ée]ro\s+d['’]identification",
    r"n°\s*d['’]identification", r"BIS", r"num[ée]ro\s+BIS", r"national\s+number",
)


def _validate_be_national_number(m: re.Match, text: str, score: float) -> Optional[Match]:
    yy, mm, dd, seq, cc = m.group("yy"), m.group("mm"), m.group("dd"), m.group("seq"), m.group("cc")
    month, day = int(mm), int(dd)
    if day > 31 or not (month <= 12 or 20 <= month <= 32 or 40 <= month <= 52):
        return None
    base = f"{yy}{mm}{dd}{seq}"
    check = int(cc)
    checksum_ok = check in {97 - int(base) % 97, 97 - int("2" + base) % 97}
    formatted = re.fullmatch(r"\d{2}\.\d{2}\.\d{2}[-.\s]\d{3}[.\s-]\d{2}", m.group(0)) is not None
    if checksum_ok:
        score = 1.0
    elif formatted:
        score = 0.75  # format typique : masqué même si la clé est fausse (faute de frappe, OCR)
    else:
        score = 0.3   # 11 chiffres sans format ni clé : uniquement avec contexte
    return Match(m.start(), m.end(), score)


_BE_NN_RE = re.compile(
    r"(?<![\d.,/+-])(?P<yy>\d{2})(?P<sep>[. -]?)(?P<mm>\d{2})(?P=sep)(?P<dd>\d{2})"
    r"[. -]?(?P<seq>\d{3})[. -]?(?P<cc>\d{2})(?![\d])"
)

# Numéro annoncé explicitement (« RN 85.07.30-033.28 », « NN : 85073003328 »,
# « 85073003328 (RN) ») : masqué quel que soit son format ou sa clé — une
# faute de frappe dans le numéro ne doit jamais le faire fuiter.
_NN_MENTION = (
    r"(?<![\w.])(?:R\.[ \t]?N\.?|RN|N\.[ \t]?N\.?|NN|NISS|INSZ|RRN)(?![\w])"
)
_NN_LABEL = (
    rf"{_NN_MENTION}"
    r"|(?<![\w])(?i:(?:num[ée]ro|n[°ºo˚])\.?[ \t]+(?:(?:de|du)[ \t]+)?(?:registre[ \t]+national|national|BIS)"
    r"|registre[ \t]+national|rijksregister(?:nummer|nr)?"
    r"|identificatienummer(?:[ \t]+van[ \t]+het[ \t]+rijksregister)?"
    r"|num[ée]ro[ \t]+d['’]identification(?:[ \t]+(?:au|du)[ \t]+registre[ \t]+national)?)(?![\w])"
)
_NN_VALUE = (
    r"\d{2}[ .\-/]?\d{2}[ .\-/]?\d{2}[ .\-/]?\d{3}[ .\-/]?\d{2}(?![\d])"
    r"|\d(?:[ .\-/]?\d){8,12}(?![\d])"
)
"""9 à 13 chiffres (11 attendus) : tolère un chiffre en trop ou en moins."""

_NN_MENTION_BEFORE_RE = re.compile(
    rf"(?:{_NN_LABEL})[^\d\n]{{0,25}}?(?P<num>{_NN_VALUE})"
)
_NN_MENTION_AFTER_RE = re.compile(
    rf"(?<![\d.,/-])(?P<num>{_NN_VALUE})(?=[ \t]*\(?[ \t]*(?:{_NN_MENTION}))"
)


def _validate_fr_nir(m: re.Match, text: str, score: float) -> Optional[Match]:
    raw = re.sub(r"\s", "", m.group(0)).upper()
    body, key = raw[:13], raw[13:]
    numeric = body.replace("2A", "19").replace("2B", "18")
    if not numeric.isdigit():
        return None
    valid = 97 - int(numeric) % 97 == int(key)
    spaced = " " in m.group(0)
    return Match(m.start(), m.end(), 1.0 if valid else (0.7 if spaced else 0.4))


_FR_NIR_RE = re.compile(
    r"(?<![\d])[12]\s?\d{2}\s?\d{2}\s?(?:\d{2}|2[AB])\s?\d{3}\s?\d{3}\s?\d{2}(?!\d)"
)

_IBAN_LENGTHS: dict[str, int] = {
    "AD": 24, "AT": 20, "BA": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28,
    "CZ": 24, "DE": 22, "DK": 18, "DZ": 26, "EE": 20, "ES": 24, "FI": 18,
    "FR": 27, "GB": 22, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26,
    "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MA": 28, "MC": 27,
    "MT": 31, "NL": 18, "NO": 15, "PL": 28, "PT": 25, "RO": 24, "SE": 24,
    "SI": 19, "SK": 24, "SM": 27, "TN": 24, "TR": 26,
}


def _iban_checksum_ok(iban: str) -> bool:
    rearranged = iban[4:] + iban[:4]
    try:
        number = "".join(str(int(ch, 36)) for ch in rearranged)
    except ValueError:
        return False
    return int(number) % 97 == 1


def _validate_iban(m: re.Match, text: str, score: float) -> Optional[Match]:
    raw = m.group(0)
    country = raw[:2]
    expected = _IBAN_LENGTHS.get(country)
    compact = re.sub(r"[ \t]", "", raw)
    if expected is None:
        if 15 <= len(compact) <= 34 and _iban_checksum_ok(compact):
            return Match(m.start(), m.end(), 0.9)
        return None
    if len(compact) < expected:
        return None
    # Coupe exactement à la longueur du pays (évite d'avaler le mot suivant).
    count, end = 0, m.start()
    for i, ch in enumerate(raw):
        if ch not in " \t":
            count += 1
        if count == expected:
            end = m.start() + i + 1
            break
    iban = compact[:expected]
    return Match(m.start(), end, 1.0 if _iban_checksum_ok(iban) else 0.65)


_IBAN_RE = re.compile(
    r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:[ \t]?[A-Z0-9]{4}){2,7}(?:[ \t]?[A-Z0-9]{1,3})?(?![A-Za-z0-9])"
)

_BCE_CONTEXT = _ctx(
    r"TVA", r"BTW", r"VAT", r"BCE", r"KBO", r"entreprise", r"ondernemings\w*",
    r"n°\s*d['’]entreprise", r"num[ée]ro\s+d['’]entreprise", r"RPM", r"RPR",
    r"immatricul\w+", r"inscrite?", r"enterprise",
)
_VAT_CONTEXT = re.compile(r"(?i)(?:TVA|BTW|VAT)\W{0,15}$")


def _is_valid_bce(digits: str) -> bool:
    """Clé modulo 97 d'un numéro d'entreprise belge (10 chiffres)."""
    return len(digits) == 10 and 97 - int(digits[:8]) % 97 == int(digits[8:])


def _validate_be_enterprise(m: re.Match, text: str, score: float) -> Optional[Match]:
    digits = _digits(m.group("num"))
    if len(digits) == 9:
        digits = "0" + digits
    if len(digits) != 10:
        return None
    valid = _is_valid_bce(digits)
    has_prefix = bool(m.group("prefix"))
    formatted = "." in m.group("num") or " " in m.group("num")
    if valid and (has_prefix or formatted):
        score = 0.9
    elif valid or has_prefix:
        score = 0.6
    elif "." in m.group("num"):
        score = 0.4   # format BCE canonique « 0123.456.789 » : masqué avec contexte
    else:
        score = 0.25
    before = text[max(0, m.start() - 25):m.start()]
    entity = "VAT_NUMBER" if has_prefix or _VAT_CONTEXT.search(before) else "BE_ENTERPRISE"
    return Match(m.start(), m.end(), score, entity)


_BE_ENTERPRISE_RE = re.compile(
    r"(?<![\w.])(?P<prefix>BE[ \t]?)?(?P<num>[01]\d{3}(?P<s>[. ]?)\d{3}(?P=s)\d{3}|\d{3}\.\d{3}\.\d{3})(?![\d]|\.\d)"
)


def _validate_be_id_card(m: re.Match, text: str, score: float) -> Optional[Match]:
    digits = _digits(m.group(0))
    if len(digits) != 12:
        return None
    check = int(digits[:10]) % 97 or 97
    valid = check == int(digits[10:])
    before = text[max(0, m.start() - 60):m.start()].lower()
    is_account = any(w in before for w in ("compte", "rekening", "account", "cpte", "virement"))
    entity = "BANK_ACCOUNT" if is_account else "BE_ID_CARD"
    return Match(m.start(), m.end(), 0.9 if valid else 0.6, entity)


# ---------------------------------------------------------------------------
# Téléphones
# ---------------------------------------------------------------------------

_PHONE_SEP = r"(?:[ \t]?[./-][ \t]?|[ \t])"
_PHONE_CONTEXT = _ctx(
    r"t[ée]l[ée]?phone", r"t[ée]l\.?", r"gsm", r"portable", r"mobile", r"fax",
    r"contact", r"joignable", r"ligne\s+directe", r"phone", r"appel\w*",
    r"t[ée]l[ée]copie", r"secr[ée]tariat", r"tel\.?",
)


_BCE_LIKE_RE = re.compile(r"[01]\d{3}\.\d{3}\.\d{3}")


def _validate_phone(m: re.Match, text: str, score: float) -> Optional[Match]:
    raw = m.group(0)
    digits = _digits(re.sub(r"\(0\)", "", raw))
    if raw.startswith(("+", "00")):
        # Indicatif + numéro national : 9 à 15 chiffres (norme E.164).
        if raw.startswith("00"):
            digits = digits[2:]
        if not 9 <= len(digits) <= 15:
            return None
        return Match(m.start(), m.end(), 0.85)
    if len(digits) not in (9, 10):
        return None
    # « 0123.456.749 » : numéro d'entreprise (BCE) valide, pas un téléphone.
    if _BCE_LIKE_RE.fullmatch(raw) and _is_valid_bce(digits):
        return None
    return Match(m.start(), m.end(), score)


# Pas de détection au milieu d'un nombre (« 1.0475… », « 12/0475… »), mais
# bien après une abréviation collée (« tél.0475 12 34 56 »).
_PHONE_BEFORE = r"(?<![\w+/,-])(?<!\d\.)"
_PHONE_NATIONAL_RE = re.compile(
    rf"{_PHONE_BEFORE}0\d{{1,3}}(?!\d)(?:{_PHONE_SEP}\d{{2,3}}(?!\d)){{2,4}}(?![./-]?\d)"
)
_PHONE_NATIONAL_COMPACT_RE = re.compile(rf"{_PHONE_BEFORE}0[1-9]\d{{7,8}}(?![\w])")
_PHONE_AREA_CODE_RE = re.compile(
    # Indicatif de zone entre parenthèses : « (081) 22 33 44 », « (02) 512.34.56 ».
    rf"{_PHONE_BEFORE}\(0\d{{1,3}}\)[ \t]?\d{{2,4}}(?:{_PHONE_SEP}\d{{2,3}}(?!\d)){{1,3}}(?![./-]?\d)"
)
_PHONE_INTL_RE = re.compile(
    r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}[ \t]?(?:\(0\)[ \t]?)?"
    rf"\d{{1,4}}(?:{_PHONE_SEP}\d{{1,4}}(?!\d)){{1,5}}(?![\d])"
)
_PHONE_INTL_COMPACT_RE = re.compile(
    # « +32471234567 », « 0033612345678 » (sans séparateur). Avec « 00 »,
    # seulement les indicatifs européens / maghrébins courants : une longue
    # suite de chiffres commençant par 00 est sinon souvent une référence.
    r"(?<![\w+])(?:\+[1-9]\d{8,14}"
    r"|00(?:32|33|31|352|49|41|44|39|34|351|377|212|213|216|90|48|40)\d{7,12})(?![\d])"
)

# ---------------------------------------------------------------------------
# Numéros de rôle (RG / FA) et références de dossier
# ---------------------------------------------------------------------------
#
# Un numéro de rôle identifie l'affaire, donc les parties : tout numéro
# placé juste avant ou juste après la mention « RG » / « FA » est masqué.
# La mention elle-même reste lisible (« R.G. n° [NUMÉRO_RÔLE] »), sauf
# quand elle est soudée au numéro (« 2024/FA/123 » → « [NUMÉRO_RÔLE] »).

_REF_SEP = r"(?:[ \t]?/[ \t]?|[.\-])"
_REF = (
    rf"(?:[A-Z]{{1,4}}{_REF_SEP})*\d[A-Z0-9]{{0,9}}"
    rf"(?:{_REF_SEP}[A-Z0-9]{{1,10}}){{0,6}}(?![\w])"
)
"""Référence : segments séparés par « / », « . » ou « - » (« 24/1234/A »,
« 2023/AL/123 », « C/21/00123 », « LI.55.L1.012345/2023 »)."""

_REF_SHAPED = (
    rf"(?:[A-Z]{{1,4}}{_REF_SEP})*\d[A-Z0-9]{{0,9}}"
    rf"(?:{_REF_SEP}[A-Z0-9]{{1,10}}){{1,6}}(?![\w])"
)
"""Référence à au moins deux segments : seule admise dans une liste ou
devant « RG » (« 2023/789 RG »), pour ne pas masquer une heure ou un
nombre isolé (« RG 21/123 à 14 h »)."""

_REF_LIST = rf"{_REF}(?:[ \t]*(?:[,;&–—-]|et|à)[ \t]*{_REF_SHAPED})*"
_REF_GLUE = (
    r"[ \t]*(?:[:.][ \t]*)?"
    r"(?:(?i:n[°ºo˚]s?|nrs?|nos?|num[ée]ros?)\.?[ \t]*)?(?:[:][ \t]*)?"
)
"""Entre la mention et le numéro : « R.G. n° », « RG : », « RG n°s »…"""

_PLAIN_ROLE = r"\d{3,6}(?![\w/]|[.,]\d)"
"""Numéro de rôle sans séparateur (« 7407 ») : admis dans une liste annoncée
par « numéros du rôle » ; au moins 3 chiffres (« et 2 autres » n'en est pas)."""

_ROLE_LIST = (
    rf"{_REF}(?:[ \t]*(?:[,;&–—-]|et|à)[ \t]*{_REF_SHAPED}"
    rf"|[ \t]*(?:[,;&]|et|en)[ \t]*{_PLAIN_ROLE})*"
)
"""Liste de numéros de rôle : « 7407, 7409, 7410 et 7412 », « 19/1111/A et 19/2222/A »."""

_ROLE_MENTION = r"(?<![\w.])(?:R\.[ \t]?G\.?|RG|F\.[ \t]?A\.?|FA)(?![\w])"
_ROLE_LABEL = (
    rf"{_ROLE_MENTION}"
    r"|(?<![\w])(?i:(?:num[ée]ros?|n[°ºo˚]s?|nrs?)\.?[ \t]+(?:(?:de|du|des)[ \t]+)?r[ôo]les?(?:[ \t]+g[ée]n[ée]ral)?"
    r"|r[ôo]le[ \t]+g[ée]n[ée]ral|r[ôo]le(?=[ \t]+n[°ºo˚])|rolnummers?|algemene[ \t]+rol"
    r"|inscrite?s?[ \t]+au[ \t]+r[ôo]le(?:[ \t]+g[ée]n[ée]ral)?[ \t]+sous[ \t]+(?:le|les)"
    r"|affaires?(?=[ \t]+(?:n[°ºo˚]s?|nos?|num[ée]ros?)\.?[ \t]*\d))(?![\w])"
)
"""Mentions annonçant un numéro de rôle (« RG », « Numéros du rôle », « inscrite
au rôle général sous le n° », « l'affaire n° 7407 »)."""

_ROLE_BEFORE_RE = re.compile(rf"(?:{_ROLE_LABEL}){_REF_GLUE}(?P<num>{_ROLE_LIST})")
_ROLE_AFTER_RE = re.compile(
    rf"(?<![\w/.-])(?P<num>{_REF_SHAPED})(?=[ \t]*\(?[ \t]*(?:{_ROLE_MENTION}))"
)
_ROLE_NUMBERS_BEFORE_ROLE_RE = re.compile(
    # « inscrites sous les numéros 7407, 7409 et 7412 du rôle de la Cour »
    rf"(?<![\w])(?i:n[°ºo˚]s?|nos?|num[ée]ros?)\.?[ \t]*(?P<num>{_ROLE_LIST})"
    r"(?=[ \t]+(?i:du|au)[ \t]+r[ôo]le(?![\w]))"
)
_ROLE_GLUED_RE = re.compile(
    # « 22/321/FA », « 2024/FA/123 », « FA/2021/123 »
    r"(?<![\w/.-])(?P<num>(?:[A-Z0-9]{1,10}/)*(?:FA|RG)(?:/[A-Z0-9]{1,10})+"
    r"|(?:[A-Z0-9]{1,10}/)+(?:FA|RG))(?![\w/])"
)

_PUBLIC_CASE_NUMBER_RE = re.compile(r"[CTF]-\d{1,4}/\d{2}(?:[ \t]*(?:,|et)[ \t]*[CTF]-\d{1,4}/\d{2})*")
"""Numéro d'affaire de la Cour de justice de l'UE (« C-694/20 ») : public."""


_CASE_REF_LABEL = (
    r"(?<![\w])(?:R[ée]p\.|(?i:r[ée]pertoire)(?:[ \t]+(?i:g[ée]n[ée]ral))?"
    r"|(?i:notice(?:[ \t]+(?:du[ \t]+)?parquet)?|not\.[ \t]*parq\.?|n[°ºo˚][ \t]*(?:de[ \t]+)?notice)"
    r"|PV|P\.V\.|(?i:proc[èe]s[- ]verbal))(?![\w])"
)
_CASE_REF_RE = re.compile(rf"(?:{_CASE_REF_LABEL}){_REF_GLUE}(?P<num>{_REF_LIST})")
_PORTALIS_RE = re.compile(
    r"(?i:portalis)[ \t]*(?:n[°ºo˚]\.?[ \t]*)?:?[ \t]*(?P<num>[A-Z0-9]{2,6}(?:-[A-Z0-9]{1,6}){2,5})(?![\w-])"
)


def _reference_validator(min_digits: int = 2) -> Validator:
    """Valide le groupe ``num`` s'il contient assez de chiffres (« RG A » ou
    « RN 4 » ne sont pas des numéros) et n'est pas un numéro d'affaire public
    de la Cour de justice (« affaire C-694/20 »)."""
    def _validate(m: re.Match, text: str, score: float) -> Optional[Match]:
        start, end = m.span("num")
        if sum(ch.isdigit() for ch in text[start:end]) < min_digits:
            return None
        if _PUBLIC_CASE_NUMBER_RE.fullmatch(text[start:end]):
            return None
        return Match(start, end, score)
    return _validate


_validate_reference = _reference_validator()


# ---------------------------------------------------------------------------
# Adresses postales
# ---------------------------------------------------------------------------

_STRICT_STREET_TYPES = (
    r"rue|ruelle|avenue|boulevard|chauss[ée]e|quai|all[ée]e|impasse|dr[èe]ve|"
    r"faubourg|esplanade|venelle|sentier|square|rond-point"
)
_LOOSE_STREET_TYPES = (
    r"place|route|chemin|voie|passage|promenade|clos|cit[ée]|r[ée]sidence|"
    r"galerie|parvis|rampe|mont[ée]e|hameau|lotissement|villa"
)
_RISKY_STREET_TYPES = (
    r"cours|parc|domaine|zoning|carrefour|pav[ée]|tienne|trieu|fond|val|berge|jardins?|port|"
    r"mont|montagne|degr[ée]s?|vin[âa]ve|thier"
)
_STREET_ABBREVIATIONS = r"av|bd|bld|blvd|pl|ch[ée]e|imp|rte|fbg|sq"
_STREET_TYPE = (
    rf"(?P<type>(?i:{_STRICT_STREET_TYPES}|{_LOOSE_STREET_TYPES}|{_RISKY_STREET_TYPES})(?![\w-])"
    rf"|(?i:{_STREET_ABBREVIATIONS})\.?(?=[ \t]))"
)
_STREET_PARTICLE = (
    r"(?:(?:de|du|des|la|le|les|aux|au|à|sous|sur|lez|lès|en|van|von|der|den|"
    r"het|ter|ten|op|aan|'t)[ \t]+|[dlDL]['’])"
)
_STREET_TOKEN = (
    rf"(?:{_STREET_PARTICLE}+(?:{CAP_WORD}|{UPPER_WORD}|[{LOWER}]{{3,}})"
    rf"|{CAP_WORD}|{UPPER_WORD})"
)
_STREET_NAME = rf"{_STREET_TOKEN}(?:[ \t]+{_STREET_TOKEN}){{0,5}}"
_HOUSE_NUMBER = (
    r"(?:(?i:n°|no|nr)\.?[ \t]*)?\d{1,4}(?!\d)"
    r"(?:[ \t]?(?i:bis|ter|quater)(?![\w])|[A-Za-z](?![\w]))?"
    r"(?:[ \t]*[-/][ \t]*\d{1,4}(?!\d)[A-Za-z]?)?"
)
_BOX = (
    r"(?:[ \t]*,?[ \t]*(?:"
    r"(?i:bte|bo[îi]te|bus|bt)\.?[ \t]*[A-Za-z]?\d{1,4}[A-Za-z]?(?![\w])"
    rf"|(?i:b[âa]timent|b[âa]t|[ée]tage|appartement|appt|app|apt|escalier|esc|entr[ée]e|porte)\.?[ \t]*(?:[A-Z0-9]{{1,4}}|{CAP_WORD})(?![\w])"
    r"))*"
)
_CITY_WORD = rf"(?:{CAP_WORD}|{UPPER_WORD})"
_CITY = (
    rf"(?:(?:La|Le|Les|L['’]|De|Den|Sint|Saint|Sainte|Mont|Bois|Pont|Braine|Ville)[ \t]+)?"
    rf"{_CITY_WORD}"
    rf"(?:[ \t]+(?:sur|sous|lez|lès|en|aan|am|bei)[ \t]+{_CITY_WORD})?"
    r"(?:[ \t]+(?i:cedex)(?:[ \t]+\d{1,2}(?!\d))?)?"
)
_POSTAL_CITY = (
    r"(?:(?:B|F|L|D|CH)[ \t]?[-–][ \t]?)?(?P<pc>\d{4,5})(?!\d)[ \t]+(?P<city>" + _CITY + ")"
)
_JOIN = r"(?:[ \t]*[,–-][ \t]*|[ \t]+(?:à|a|te|in)[ \t]+|[ \t]*\n[ \t]*|[ \t]+)"

_NL_STREET = (
    rf"(?:(?:Grote|Kleine|Oude|Nieuwe|Lange|Korte|Hoge|Sint|Onze-Lieve-Vrouw)[ \t-]+)?"
    rf"(?:[{UPPER}][\w'’-]*?)?(?i:straat|laan|steenweg|plein|dreef|lei|weg|kaai|markt|"
    r"singel|vest|baan|dijk|kade|wal|gracht|steeg|hof|plaats|veld|berg|dal|hoek|wijk)(?![\w])"
)

_ADDRESS_FR_RE = re.compile(
    rf"(?<![\w,.-])(?P<street>{_HOUSE_NUMBER}[ \t]*,?[ \t]*{_STREET_TYPE}[ \t]+{_STREET_NAME}{_BOX})"
    rf"(?P<tail>{_JOIN}{_POSTAL_CITY})?"
)
_ADDRESS_BE_RE = re.compile(
    rf"(?<![\w-])(?P<street>{_STREET_TYPE}[ \t]+{_STREET_NAME}"
    rf"(?P<num>[ \t]*,?[ \t]*{_HOUSE_NUMBER})?{_BOX})"
    rf"(?P<tail>{_JOIN}{_POSTAL_CITY})?"
)
_ADDRESS_NL_RE = re.compile(
    rf"(?<![\w-])(?P<street>{_NL_STREET}[ \t]+{_HOUSE_NUMBER}{_BOX})"
    rf"(?P<tail>{_JOIN}{_POSTAL_CITY})?"
)
_POSTAL_CITY_RE = re.compile(rf"(?<![\w.,/-]){_POSTAL_CITY}")

CITY_STOP_WORDS: frozenset[str] = frozenset({
    "CODE", "ARTICLE", "ARTICLES", "ART", "LOI", "EUROS", "EURO", "EUR",
    "FAIT", "MONSIEUR", "MADAME", "MAITRE", "ME", "TEL", "TELEPHONE", "GSM",
    "FAX", "EMAIL", "PAGE", "PAGES", "ANNEXE", "PIECE", "PIECES", "TOTAL",
    "JANVIER", "FEVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOUT",
    "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DECEMBRE", "LE", "LA", "LES",
    "TVA", "BTW", "BCE", "RCS", "IBAN", "BIC", "SA", "SRL", "SPRL", "ASBL",
    "AU", "DU", "DES", "ET", "OU", "EN", "PAR", "POUR", "SUR", "VU",
})


def _city_is_valid(m: re.Match) -> bool:
    city = m.group("city")
    if not city:
        return False
    first = _token_key(city.split()[0])
    return first not in CITY_STOP_WORDS


def _validate_address(m: re.Match, text: str, score: float) -> Optional[Match]:
    street = m.group("street")
    has_number = bool(re.search(r"\d", street))
    tail_ok = m.group("tail") is not None and _city_is_valid(m)
    end = m.end() if tail_ok else m.end("street")
    street_type = m.groupdict().get("type") or ""

    # Types ambigus (« cours », « parc », « port »…) : seulement avec majuscule
    # (« Cours Mirabeau 3 », pas « au cours de l'audience 2024 »).
    if (re.fullmatch(rf"(?i:{_RISKY_STREET_TYPES})", street_type)
            and not street_type[:1].isupper()):
        return None
    if not has_number and not tail_ok:
        # Rue sans numéro ni code postal : uniquement pour les types non ambigus
        # (« rue des Acacias ») ou un type ambigu écrit avec majuscule
        # (« Place Saint-Lambert », mais pas « mise en place de… »).
        strict = re.fullmatch(rf"(?i:{_STRICT_STREET_TYPES})", street_type) is not None
        if not strict and not street_type[:1].isupper():
            return None
        score = 0.6 if strict else 0.55
    elif tail_ok:
        score = 0.9
    # Ne pas terminer sur un séparateur ou un espace.
    while end > m.start() and text[end - 1] in " \t,–-":
        end -= 1
    return Match(m.start(), end, score)


def _validate_postal_city(m: re.Match, text: str, score: float) -> Optional[Match]:
    if not _city_is_valid(m):
        return None
    before = text[max(0, m.start() - 20):m.start()]
    # Code postal + localité isolés : seulement en début de ligne, après une
    # virgule/parenthèse/tiret ou « à » (« domicilié à 4120 Neupré »).
    if not re.search(r"(?:^|[\n,(;:–-]|\b(?:à|a|te|in)\s)\s*$", before) and m.start() != 0:
        return None
    return Match(m.start(), m.end(), score)


def _validate_postal_in_parentheses(m: re.Match, text: str, score: float) -> Optional[Match]:
    """« Bordeaux (33000) » → code postal ; « Loi (2016) » → année, ignorée."""
    code = m.group("pc")
    if len(code) == 4 and 1800 <= int(code) <= 2100:
        return None
    return Match(m.start("pc"), m.end("pc"), score)


# ---------------------------------------------------------------------------
# Noms de personnes introduits par une civilité / une fonction
# ---------------------------------------------------------------------------

_TITLES = (
    r"Monsieur|Messieurs|Madame|Mesdames|Mademoiselle|Mesdemoiselles|MONSIEUR|MADAME|MADEMOISELLE"
    r"|M\.|MM\.|Mr\.?|Mrs\.?|Ms\.?|Mme\.?|Mmes\.?|Mlle\.?|Mlles\.?|Me\.?|Mes\.?"
    r"|Ma[îi]tres?|MA[ÎI]TRE|Docteur|Dr\.?|Dre\.?|Pr\.?|Professeur"
    r"|[Ss]ieur|[Dd]ame|[Vv]euve|Vve\.?|[ÉéEe]pou(?:se|x)|[Nn][ée]e"
    r"|[Ss]oussign[ée](?:e|\(e\))?|[Oo]ndergetekende|[Dd]e heer|[Mm]evrouw|Mevr\.|Dhr\.|[Mm]eester|Meneer|Mijnheer"
    r"|[Jj]uge|[Nn]otaire|[Hh]uissier(?:[ \t]+de[ \t]+justice)?|[Gg]reffi(?:er|ère)|[Aa]vocate?|[Cc]onfrère|[Cc]onsœur"
    r"|[Bb]âtonnier|[Ee]xperte?|[Mm]édecin|[Ii]nfirmi(?:er|ère)|[Tt]émoin|[Cc]ollègue|[Pp]atiente?"
    r"|[Ee]nfants?|[Ff]ils|[Ff]ille|[Pp]ère|[Mm]ère|[Ff]rère|[Ss]œur|[Cc]onjointe?|[Cc]ompagne?|[Ee]x-[ée]pou(?:se|x)"
)
_TITLED_PERSON_RE = re.compile(
    rf"(?<![\w-])(?:{_TITLES})(?:[ \t]*,)?[ \t]+(?P<name>{PERSON_NAME})"
)
_SOUSSIGNE_RE = re.compile(
    rf"(?i:je[ \t]+soussign[ée](?:e|\(e\))?s?|nous[ \t]+soussign[ée]s?)[ \t]*,?[ \t]+"
    rf"(?:(?:{_TITLES})[ \t]+)?(?P<name>{PERSON_NAME})"
)


TITLE_BEFORE_RE = re.compile(
    r"(?:Monsieur|Madame|Mademoiselle|Ma[îi]tre|Docteur|Mme|Mlle|Me|Dr|Mr|M)\.?[ \t]*$"
)
"""Civilité juste avant un nom (« Mme » + « M. LEJEUNE »)."""

_ARTICLES = frozenset({"le", "la", "les", "l'", "l’", "de", "du", "des", "d'", "d’"})
_INITIAL_RE = re.compile(rf"[{UPPER}]\.(?:-[{UPPER}]\.)?")


def trim_person_name(text: str, start: int, end: int,
                     keep_initials: bool = False) -> Optional[tuple[int, int]]:
    """Retire civilités, fonctions et articles en bordure d'un nom.

    Renvoie la nouvelle position du nom, ou ``None`` s'il ne reste aucun mot
    pouvant être un nom de personne (ex : « le Président »).

    ``keep_initials`` : la civilité a déjà été reconnue (« Mme M. LEJEUNE »),
    une lettre suivie d'un point est donc une initiale et non « M. ».
    """
    tokens = [(t.start() + start, t.end() + start, t.group(0))
              for t in re.finditer(r"\S+", text[start:end])]
    # Civilités, fonctions et articles en minuscules en tête : ignorés
    # (« le Président », « la Juge Dupont »). Les particules avec majuscule
    # font partie du nom (« Van der Linden », « De Smet »).
    while tokens:
        word = tokens[0][2]
        if keep_initials and _INITIAL_RE.fullmatch(word):
            break
        if _token_key(word) in NAME_STOP_WORDS or word in _ARTICLES:
            tokens.pop(0)
            # Après « Mme », « M. » est une initiale et non une civilité.
            keep_initials = keep_initials or _token_key(word) in TITLE_WORDS
            continue
        break
    # Coupe au premier mot qui ne peut pas appartenir à un nom.
    for i, tok in enumerate(tokens):
        if keep_initials and _INITIAL_RE.fullmatch(tok[2]):
            continue
        if _token_key(tok[2]) in NAME_STOP_WORDS:
            tokens = tokens[:i]
            break
    while tokens and _token_key(tokens[-1][2]) in NAME_PARTICLES:
        tokens.pop()
    meaningful = [
        t for t in tokens
        if _token_key(t[2]) not in NAME_PARTICLES and len(t[2].rstrip(".")) >= 2
    ]
    if not meaningful:
        return None
    return tokens[0][0], tokens[-1][1]


def _validate_titled_person(m: re.Match, text: str, score: float) -> Optional[Match]:
    span = trim_person_name(text, m.start("name"), m.end("name"), keep_initials=True)
    if span is None:
        return None
    return Match(span[0], span[1], score)


# ---------------------------------------------------------------------------
# Champs de formulaire (« Nom : Dupont », « Adresse : … »)
# ---------------------------------------------------------------------------

_FIELD_VALUE = r"[ \t]*(?:[:：]|\|)[ \t]*(?P<value>(?:(?![ ]{3}|\t)[^\n|;]){1,160})"
_FIELD_PREFIX = r"(?:^|(?<=[|;(\t])|(?<=  ))[ \t]*(?:[-•*·][ \t]*)?"
_LABEL_CUT_RE = re.compile(
    r"\t|[ ]{3,}|[ \t]+[A-ZÀ-Þa-zà-ÿ'’.° ]{2,30}[ \t]*[:：][ \t]"
)


def _field(labels: str) -> re.Pattern:
    return re.compile(rf"(?im){_FIELD_PREFIX}(?:{labels})\.?{_FIELD_VALUE}")


_PHONE_VALUE_RE = re.compile(r"[ \t]*(?:\+|\(0)?\d[\d \t./()\-]*\d")
"""Numéro en tête d'un champ « Tél. : … » (s'arrête au premier mot)."""


def _field_validator(min_alnum: int = 2, need_digits: int = 0,
                     person: bool = False, phone: bool = False) -> Validator:
    def _validate(m: re.Match, text: str, score: float) -> Optional[Match]:
        start, end = m.span("value")
        value = text[start:end]
        cut = _LABEL_CUT_RE.search(value)
        if cut:
            end = start + cut.start()
            value = text[start:end]
        if phone:
            # « GSM : 0475/12.34.56 — fax 081/22.33.45 » : seul le premier
            # numéro appartient au champ (le second est détecté seul).
            number = _PHONE_VALUE_RE.match(value)
            if number is None:
                return None
            end = start + number.end()
            value = text[start:end]
        stripped = value.rstrip(" \t.,;:")
        if stripped.endswith(")") and "(" not in stripped:
            stripped = stripped[:-1].rstrip(" \t.,;:")  # « (GSM : 0475…) »
        end = start + len(stripped)
        lead = len(value) - len(value.lstrip(" \t"))
        start += lead
        value = text[start:end]
        if sum(ch.isalnum() for ch in value) < min_alnum:
            return None
        if re.fullmatch(r"[\s._…-]*", value):
            return None
        if need_digits and sum(ch.isdigit() for ch in value) < need_digits:
            return None
        if person:
            if not re.search(rf"[{UPPER}]", value):
                return None
            span = trim_person_name(text, start, end)
            if span is None:
                return None
            start, end = span
        return Match(start, end, score)
    return _validate


_NAME_FIELD_RE = _field(
    r"nom(?:[ \t]+(?:de[ \t]+famille|d['’]usage|de[ \t]+jeune[ \t]+fille|de[ \t]+naissance|complet|"
    r"et[ \t]+pr[ée]noms?|marital|patronymique))?|pr[ée]noms?|nom[ \t]*/[ \t]*pr[ée]nom|"
    r"naam|voornaam|voornamen|familienaam|achternaam|surname|first[ \t]+name|last[ \t]+name|"
    r"full[ \t]+name|given[ \t]+names?|signataire|titulaire(?:[ \t]+du[ \t]+compte)?|"
    r"b[ée]n[ée]ficiaire|nom[ \t]+du[ \t]+(?:patient|client|titulaire|d[ée]funt|conjoint|p[èe]re|"
    r"de[ \t]+la[ \t]+m[èe]re)"
)
_ADDRESS_FIELD_RE = _field(
    r"adresse(?![ \t]+(?:e-?mail|[ée]lectronique|mail|ip|internet|web|url))"
    r"(?:[ \t]+(?:postale|du[ \t]+domicile|de[ \t]+(?:facturation|livraison|correspondance)|"
    r"priv[ée]e|personnelle|compl[èe]te|actuelle))?|domicile|domicili[ée]e?(?:[ \t]+(?:à|au))?|"
    r"r[ée]sidence|lieu[ \t]+de[ \t]+r[ée]sidence|adres|woonplaats|woonadres|address|"
    r"demeurant(?:[ \t]+(?:à|au))?"
)
_BIRTHPLACE_FIELD_RE = _field(r"lieu[ \t]+de[ \t]+naissance|n[ée]e?[ \t]+à|geboorteplaats|place[ \t]+of[ \t]+birth")
_BIRTHDATE_FIELD_RE = _field(r"date[ \t]+de[ \t]+naissance|n[ée]\(?e?\)?[ \t]+le|geboortedatum|date[ \t]+of[ \t]+birth|DOB")
_NATIONALITY_FIELD_RE = _field(r"nationalit[ée]|nationaliteit|nationality")
_NN_FIELD_RE = _field(
    r"(?:n°|num[ée]ro|no)?[ \t]*(?:de[ \t]+)?(?:registre[ \t]+national|national)|"
    r"n°[ \t]*NN|NN|N\.N\.|rijksregisternummer|INSZ|NISS|num[ée]ro[ \t]+BIS"
)
_ID_FIELD_RE = _field(
    r"(?:n°|num[ée]ro|no)[ \t]*(?:de[ \t]+(?:la[ \t]+)?)?(?:carte[ \t]+d['’]identit[ée]|CI|passeport|"
    r"titre[ \t]+de[ \t]+s[ée]jour|permis[ \t]+de[ \t]+conduire)|passeport|identiteitskaart"
)
_PHONE_FIELD_RE = _field(r"t[ée]l(?:[ée]phone)?(?:[ \t]+(?:portable|fixe|mobile|priv[ée]|bureau))?|gsm|mobile|portable|fax")

# ---------------------------------------------------------------------------
# Divers
# ---------------------------------------------------------------------------

_BIRTH_DATE_RE = re.compile(
    rf"(?i:\bn[ée](?:e|\(e\))?s?)[ \t]+(?:(?i:à|a|en|au)[ \t]+[^\n,;()]{{1,40}}?,?[ \t]+)?"
    rf"(?i:le)[ \t]+(?P<date>{DATE})"
)
_BIRTH_DATE_LABEL_RE = re.compile(
    rf"(?i:date[ \t]+de[ \t]+naissance|geboortedatum|geboren[ \t]+op|date[ \t]+of[ \t]+birth)"
    rf"[ \t]*:?[ \t]*(?P<date>{DATE})"
)
_BIRTH_DATE_NL_RE = re.compile(
    rf"(?i:geboren)[ \t]+te[ \t]+\S+(?:[ \t]+\S+)?[ \t]+op[ \t]+(?P<date>{DATE})"
)
_BIRTH_DATE_DEGREE_RE = re.compile(rf"(?<![nN\w])°[ \t]*(?P<date>{DATE})")

_NATIONALITY_RE = re.compile(
    r"(?i:de[ \t]+nationalit[ée])[ \t]+(?P<value>[a-zà-ÿ]+(?:-[a-zà-ÿ]+)?)"
)

_EMAIL_RE = re.compile(
    r"(?<![\w.%+-])[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,}(?![\w])"
)

_PLATE_CONTEXT = _ctx(r"plaque", r"immatricul\w*", r"v[ée]hicule", r"voiture",
                      r"nummerplaat", r"kenteken", r"chssis", r"camionnette")


def _build_rules() -> list[Rule]:
    """Toutes les règles regex, regroupées par recognizer."""
    return [
        # --- Identifiants -----------------------------------------------------
        Rule("BE_NATIONAL_NUMBER", _BE_NN_RE, 0.75, validator=_validate_be_national_number,
             context=_NN_CONTEXT, context_boost=0.45),
        Rule("FR_NIR", _FR_NIR_RE, 0.7, validator=_validate_fr_nir,
             context=_ctx(r"s[ée]curit[ée]\s+sociale", r"NIR", r"INSEE", r"s[ée]cu", r"matricule"),
             context_boost=0.4),
        Rule("IBAN_CODE", _IBAN_RE, 0.9, validator=_validate_iban),
        Rule("BE_ENTERPRISE", _BE_ENTERPRISE_RE, 0.6, validator=_validate_be_enterprise,
             context=_BCE_CONTEXT, context_boost=0.4, entities=("VAT_NUMBER",)),
        Rule("BE_ID_CARD", re.compile(r"(?<![\d-])\d{3}-\d{7}-\d{2}(?![\d-])"), 0.6,
             validator=_validate_be_id_card, entities=("BANK_ACCOUNT",)),
        Rule("BE_ID_CARD", re.compile(r"(?<![\d-])\d{12}(?![\d-])"), 0.3,
             context=_ctx(r"carte\s+d['’]identit[ée]", r"eID", r"identiteitskaart", r"CI"),
             context_boost=0.45),
        Rule("PASSPORT", re.compile(r"(?<![\w])(?:[A-Z]{2}\d{6,7}|\d{2}[A-Z]{2}\d{5})(?![\w])"), 0.2,
             context=_ctx(r"passeport", r"passport", r"paspoort"), context_boost=0.5),
        Rule("VAT_NUMBER", re.compile(r"(?<![\w])FR[ \t]?[0-9A-HJ-NP-Z]{2}[ \t]?\d{3}[ \t]?\d{3}[ \t]?\d{3}(?![\d])"), 0.8),
        Rule("FR_SIRET", re.compile(r"(?<![\d])\d{3}[ \t]?\d{3}[ \t]?\d{3}[ \t]?\d{5}(?![\d])"), 0.4,
             context=_ctx(r"siret", r"rcs", r"immatricul\w*", r"[ée]tablissement", r"si[èe]ge")),
        Rule("FR_SIREN", re.compile(r"(?<![\d.])\d{3}[ \t]\d{3}[ \t]\d{3}(?![\d])|(?<![\d.])\d{9}(?![\d])"), 0.35,
             context=_ctx(r"siren", r"rcs", r"immatricul\w*", r"soci[ée]t[ée]", r"registre", r"num[ée]ro")),
        Rule("BE_NATIONAL_NUMBER", _NN_MENTION_BEFORE_RE, 0.95, validator=_validate_reference),
        Rule("BE_NATIONAL_NUMBER", _NN_MENTION_AFTER_RE, 0.95, validator=_validate_reference),
        # --- Numéros de rôle et références de dossier ---------------------------
        Rule("FR_NUM_ROLE", _ROLE_BEFORE_RE, 0.95, validator=_validate_reference),
        Rule("FR_NUM_ROLE", _ROLE_AFTER_RE, 0.9, validator=_validate_reference),
        Rule("FR_NUM_ROLE", _ROLE_GLUED_RE, 0.9, validator=_validate_reference),
        Rule("FR_NUM_ROLE", _ROLE_NUMBERS_BEFORE_ROLE_RE, 0.9, validator=_validate_reference),
        Rule("CASE_REFERENCE", _CASE_REF_RE, 0.85, validator=_validate_reference),
        Rule("CASE_REFERENCE", _PORTALIS_RE, 0.9, validator=_reference_validator(min_digits=1)),
        # --- Coordonnées ------------------------------------------------------
        Rule("EMAIL_ADDRESS", _EMAIL_RE, 1.0),
        Rule("PHONE_NUMBER", _PHONE_NATIONAL_RE, 0.75, validator=_validate_phone,
             context=_PHONE_CONTEXT, context_boost=0.2),
        Rule("PHONE_NUMBER", _PHONE_NATIONAL_COMPACT_RE, 0.55, validator=_validate_phone,
             context=_PHONE_CONTEXT, context_boost=0.35),
        Rule("PHONE_NUMBER", _PHONE_AREA_CODE_RE, 0.8, validator=_validate_phone),
        Rule("PHONE_NUMBER", _PHONE_INTL_RE, 0.85, validator=_validate_phone),
        Rule("PHONE_NUMBER", _PHONE_INTL_COMPACT_RE, 0.85, validator=_validate_phone),
        # --- Adresses ---------------------------------------------------------
        Rule("ADDRESS", _ADDRESS_FR_RE, 0.85, validator=_validate_address),
        Rule("ADDRESS", _ADDRESS_BE_RE, 0.85, validator=_validate_address),
        Rule("ADDRESS", _ADDRESS_NL_RE, 0.85, validator=_validate_address),
        Rule("ADDRESS", _POSTAL_CITY_RE, 0.75, validator=_validate_postal_city),
        Rule("FR_POSTAL_CODE", re.compile(rf"(?<=[{UPPER}{LOWER}])[ \t]*\((?P<pc>\d{{4,5}})\)"), 0.6,
             validator=_validate_postal_in_parentheses),
        Rule("FR_POSTAL_CODE", re.compile(r"(?<![\d.,])\d{4,5}(?![\d.,])"), 0.2,
             context=_ctx(r"code\s+postal", r"CP", r"postcode", r"zip"), context_boost=0.5,
             context_window=25),
        Rule("ADDRESS", _ADDRESS_FIELD_RE, 0.85, group="value", validator=_field_validator(min_alnum=3)),
        # --- Personnes --------------------------------------------------------
        Rule("PERSON", _TITLED_PERSON_RE, 0.9, group="name", validator=_validate_titled_person),
        Rule("PERSON", _SOUSSIGNE_RE, 0.9, group="name", validator=_validate_titled_person),
        Rule("PERSON", _NAME_FIELD_RE, 0.85, group="value", validator=_field_validator(person=True)),
        # --- Naissance, nationalité ---------------------------------------------
        Rule("BIRTH_DATE", _BIRTH_DATE_RE, 0.95, group="date"),
        Rule("BIRTH_DATE", _BIRTH_DATE_LABEL_RE, 0.95, group="date"),
        Rule("BIRTH_DATE", _BIRTH_DATE_NL_RE, 0.95, group="date"),
        Rule("BIRTH_DATE", _BIRTH_DATE_DEGREE_RE, 0.85, group="date"),
        Rule("BIRTH_DATE", _BIRTHDATE_FIELD_RE, 0.9, group="value", validator=_field_validator()),
        Rule("LOCATION", _BIRTHPLACE_FIELD_RE, 0.8, group="value", validator=_field_validator()),
        Rule("NRP", _NATIONALITY_RE, 0.85, group="value"),
        Rule("NRP", _NATIONALITY_FIELD_RE, 0.8, group="value", validator=_field_validator(min_alnum=3)),
        Rule("BE_NATIONAL_NUMBER", _NN_FIELD_RE, 0.95, group="value",
             validator=_field_validator(min_alnum=6, need_digits=6)),
        Rule("BE_ID_CARD", _ID_FIELD_RE, 0.9, group="value",
             validator=_field_validator(min_alnum=5, need_digits=3)),
        Rule("PHONE_NUMBER", _PHONE_FIELD_RE, 0.85, group="value",
             validator=_field_validator(min_alnum=8, need_digits=8, phone=True)),
        # --- Véhicules --------------------------------------------------------
        Rule("LICENSE_PLATE", re.compile(r"(?<![\w-])[1-9]-[A-Z]{3}-\d{3}(?![\w-])"), 0.8),
        Rule("LICENSE_PLATE", re.compile(r"(?<![\w-])[A-Z]{2}-\d{3}-[A-Z]{2}(?![\w-])"), 0.75),
        Rule("LICENSE_PLATE", re.compile(r"(?<![\w-])[A-Z]{3}-\d{3}(?![\w-])"), 0.3,
             context=_PLATE_CONTEXT, context_boost=0.45),
    ]


def build_recognizers(language: str = "fr") -> list[EntityRecognizer]:
    """Renvoie les recognizers sur mesure, prêts à être ajoutés au registre."""
    return [RegexRecognizer("BelgianFrenchRegexRecognizer", _build_rules(), language)]
