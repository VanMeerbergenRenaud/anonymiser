"""
persons — Noms de personnes : initiales, regroupement, pseudonymes.

Politique appliquée
-------------------
- L'initiale du prénom accolée au nom fait partie du masque : « N. Dupont »
  → « [PERSONNE_2] », jamais « N. [PERSONNE_2] ». « M. » devant un nom seul
  (« M. Pâques ») est traité comme une initiale ; devant un prénom et un nom
  (« M. Jean DUPONT »), comme « Monsieur », qui reste lisible.
- Les initiales seules qui désignent une personne (« P.V. et G.G., assistés
  de leur avocat », « Madame F.M. ») sont masquées comme une personne ; les
  abréviations (« le P.V. », « P.V. n° 12 », « J.T., 2017 », « S.A. ») non.
- Une personne = un seul ``[PERSONNE_n]`` dans tout le document ; deux
  personnes ne partagent jamais une étiquette : deux mentions ne sont
  réunies que si leurs noms de famille coïncident et que prénoms, initiales
  et civilités (Monsieur / Madame) sont compatibles. « P. Dupont » et
  « N. Dupont » sont deux personnes ; « Madame DUPONT » ne désigne pas
  « Jean DUPONT ».

Les fonctions travaillent sur des :class:`api.spans.Detection` de type
``PERSON`` et sont appelées par ``api.nlp_engine``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Optional

from api.first_names import first_name_gender
from api.recognizers import NAME_PARTICLES, UPPER, fold
from api.spans import Detection, SpanIndex

PERSON = "PERSON"

# ---------------------------------------------------------------------------
# Initiales accolées au nom
# ---------------------------------------------------------------------------

_PARTICLE = (
    r"(?:de|du|des|la|le|van|von|der|den|ter|ten|vanden|vander|vande|di|da|del|della|"
    r"dos|das|zu|op|het|De|Du|Des|La|Le|Van|Von|Der|Den|Ter|Ten|Vanden|Vander|Vande|Di|Da|Del)"
)
_INITIAL = rf"[{UPPER}]\.(?:-[{UPPER}]\.)?"
"""Initiale de prénom : « N. », « J.-P. »."""

_INITIAL_TOKEN_RE = re.compile(rf"[{UPPER}]\.(?:-?[{UPPER}]\.)*")
_INITIALS_BEFORE_RE = re.compile(
    rf"(?<![\w.'’-])(?:{_INITIAL}[ \t]+)+(?:{_PARTICLE}[ \t]+|[dD]['’])*\Z"
)


def _name_words(value: str) -> list[str]:
    """Mots du nom (hors initiales et particules)."""
    words = []
    for raw in re.split(r"[\s,;:()«»\"]+", value):
        if not raw or _INITIAL_TOKEN_RE.fullmatch(raw):
            continue
        key = fold(raw).strip(".'’-")
        if key and key not in NAME_PARTICLES and len(key) >= 2:
            words.append(raw)
    return words


def attach_initials(text: str, detections: list[Detection]) -> list[Detection]:
    """Étend chaque nom de personne à l'initiale (et la particule) qui le précède.

    Le NER et la propagation des noms ne retiennent souvent que « Dupont »
    dans « N. Dupont », ou retirent « M. », « N. » et « Y. » pris pour une
    civilité ou un mot vide : l'initiale restait visible.
    """
    occupied = SpanIndex((d.start, d.end) for d in detections)
    for d in detections:
        if d.entity != PERSON or d.kind == "initials":
            continue
        window_start = max(0, d.start - 40)
        m = _INITIALS_BEFORE_RE.search(text, window_start, d.start)
        if m is None:
            continue
        initials = re.findall(_INITIAL, m.group(0))
        if initials == ["M."] and len(_name_words(text[d.start:d.end])) >= 2:
            continue  # « M. Jean DUPONT » : « M. » est « Monsieur »
        if occupied.overlaps(m.start(), m.start() + 1):
            continue
        d.start = m.start()
    return detections


def split_persons(text: str, detections: list[Detection]) -> list[Detection]:
    """Sépare deux personnes réunies en une seule détection par le NER.

    « Dupont P. Nihoul » (signatures sur deux colonnes, liste de juges) : une
    initiale qui suit un nom ouvre un nouveau nom.
    """
    result: list[Detection] = []
    for d in detections:
        if d.entity != PERSON:
            result.append(d)
            continue
        pending = d
        while True:
            tokens = list(re.finditer(r"\S+", text[pending.start:pending.end]))
            cut = None
            for i in range(1, len(tokens) - 1):
                word, before = tokens[i].group(0), tokens[i - 1].group(0).rstrip(",")
                if (_INITIAL_TOKEN_RE.fullmatch(word) and not _INITIAL_TOKEN_RE.fullmatch(before)
                        and before[:1].isupper() and fold(before) not in NAME_PARTICLES):
                    cut = i
                    break
            if cut is None:
                result.append(pending)
                break
            left_end = pending.start + len(tokens[cut - 1].group(0).rstrip(",")) + tokens[cut - 1].start()
            right_start = pending.start + tokens[cut].start()
            result.append(replace(pending, end=left_end))
            pending = replace(pending, start=right_start)
    return result


# ---------------------------------------------------------------------------
# Initiales seules désignant une personne (« P.V. et G.G. »)
# ---------------------------------------------------------------------------

_INITIALS_GROUP_RE = re.compile(
    rf"(?<![\w.'’-])[{UPPER}]\.(?:-?[{UPPER}]\.){{1,3}}(?![\w]|[ \t]*[{UPPER}][\w.])"
)

ABBREVIATIONS: frozenset[str] = frozenset({
    "RG", "FA", "NN", "RN", "SA", "NV", "BV", "UE", "EU", "CE", "CEE", "JT", "MB", "AR", "AM",
    "CC", "CP", "CJ", "CIR", "DH", "TVA", "PS", "MR", "ONU", "USA", "UK", "ASBL", "SPRL",
    "SRL", "SCRL", "SNC", "SAS", "SARL", "SC", "SCS", "PM", "JO", "JL", "RW", "TBP", "RGDC",
    "RDC", "JLMB", "TFUE", "TUE", "CEDH", "CJUE", "NB", "CA", "CPAS", "ONEM", "INAMI", "ONSS",
    "SPF", "BCE", "TPI", "CPC", "CPP", "CIC", "SS", "AG", "PJ", "CPI", "TGI", "JAF", "DIP",
    "CDE", "TA", "HT", "TTC", "HTVA", "EA", "CAD", "IE", "EG", "VS", "PO", "QC", "RTBF", "OK",
    "AJ", "AH", "SP", "CO", "CJCE", "TPICE", "CEDH", "RJ", "RCJB", "RPDB", "JJP", "JDJ",
})
"""Abréviations courantes écrites avec des points (« J.T. », « S.A. », « R.G. »)."""

_DETERMINER_BEFORE_RE = re.compile(
    r"(?i)(?<![\w])(?:le|la|les|l['’]|un|une|du|des|au|aux|ce|cet|cette|ces|son|sa|ses|"
    r"leur|leurs|notre|votre|chaque|tout|toute)[ \t]*\Z"
)
_PERSON_TITLE_BEFORE_RE = re.compile(
    r"(?<![\w])(?:Monsieur|Madame|Mademoiselle|Messieurs|Mesdames|M\.|Mme|Mlle|Me|Ma[îi]tre|"
    r"Dr|Docteur|(?i:l['’]enfant|les enfants|enfants?|mineure?s?|t[ée]moins?|[ée]poux|[ée]pouse|"
    r"veuve|fils|fille|fr[èe]re|s[œo]e?ur|p[èe]re|m[èe]re|conjointe?|requ[ée]rante?|appelante?|"
    r"intim[ée]e?|d[ée]fendeur|d[ée]fenderesse|demandeur|demanderesse|pr[ée]venue?|victime|"
    r"pr[ée]nomm[ée]e?|d[ée]nomm[ée]e?|sieur|dame))[ \t]*\Z"
)
_PERSON_CONTEXT_AFTER_RE = re.compile(
    r"[ \t]*,?[ \t]*\(?[ \t]*(?i:n[ée]e?s?|domicili[ée]e?s?|demeurant|r[ée]sidant|assist[ée]e?s?|"
    r"repr[ée]sent[ée]e?s?|[âa]g[ée]e?s?|[ée]pouse|[ée]poux|veuve|a[ \t]+d[ée]clar[ée]|d[ée]clare|"
    r"expose|soutient|sollicite|conteste|requ[ée]rante?s?|parties?[ \t]+requ[ée]rantes?)(?![\w])"
)
_COORDINATION_RE = re.compile(r"[ \t]*(?:,|et)[ \t]*")


def _letters(value: str) -> str:
    return "".join(ch for ch in fold(value) if ch.isalpha())


def find_initials_persons(text: str, occupied: SpanIndex) -> list[Detection]:
    """Groupes d'initiales désignant une personne (``kind="initials"``).

    Retenus seulement si le contexte désigne une personne : civilité ou
    qualité juste avant (« Madame F.M. », « l'enfant J.D. »), énumération
    avec d'autres initiales (« P.V. et G.G. ») ou suite de type « né »,
    « domicilié », « assisté », « représenté »… Jamais après un déterminant
    (« le P.V. ») ni devant un numéro (« P.V. n° 12 »).
    """
    matches = list(_INITIALS_GROUP_RE.finditer(text))
    found: list[Detection] = []
    for k, m in enumerate(matches):
        before = text[max(0, m.start() - 40):m.start()]
        after = text[m.end():m.end() + 60]
        if _DETERMINER_BEFORE_RE.search(before) or re.match(r"[ \t]*(?:n[°ºo˚]|\d)", after):
            continue
        titled = _PERSON_TITLE_BEFORE_RE.search(before) is not None
        if _letters(m.group(0)) in ABBREVIATIONS and not titled:
            continue
        coordinated = any(
            0 <= j < len(matches)
            and _letters(matches[j].group(0)) not in ABBREVIATIONS
            and _COORDINATION_RE.fullmatch(
                text[min(m.end(), matches[j].end()):max(m.start(), matches[j].start())])
            for j in (k - 1, k + 1) if j != k
        )
        if not (titled or coordinated or _PERSON_CONTEXT_AFTER_RE.match(after)):
            continue
        if not occupied.overlaps(m.start(), m.end()):
            found.append(Detection(m.start(), m.end(), PERSON, 0.85, kind="initials"))
    return found


# ---------------------------------------------------------------------------
# Regroupement des mentions et pseudonymes
# ---------------------------------------------------------------------------

_MALE_BEFORE_RE = re.compile(
    r"(?<![\w])(?:Monsieur|Messieurs|Mr|MM|Dhr|[Dd]e[ \t]+heer|Meneer|Mijnheer|[Ss]ieur|"
    r"[ée]poux|[Ff]ils|[Ff]r[èe]re|[Pp][èe]re|[Nn]eveu|[Oo]ncle)\.?[ \t,]*\Z"
)
_FEMALE_BEFORE_RE = re.compile(
    r"(?<![\w])(?:Madame|Mesdames|Mme|Mmes|Mlle|Mlles|Mademoiselle|Mevr|Mevrouw|[Dd]ame|"
    r"[ée]pouse|[Vv]euve|[Nn][ée]e|[Ff]ille|[Ss][œo]e?ur|[Mm][èe]re|[Nn]i[èe]ce|[Tt]ante)\.?[ \t,]*\Z"
)
_ROLES_F = (r"juge|pr[ée]sidente|greffi[èe]re|avocate|conseill[èe]re|substitute|experte|notaire|"
            r"t[ée]moin|demanderesse|d[ée]fenderesse|requ[ée]rante|appelante|intim[ée]e|pr[ée]venue|"
            r"victime|patiente|cliente|coll[èe]gue|m[èe]re|fille|s[œo]e?ur|[ée]pouse|veuve|tante|"
            r"ni[èe]ce|inspectrice|commissaire|agente|m[ée]diatrice|curatrice|tutrice|gérante")
_ROLES_M = (r"juge|pr[ée]sident|greffier|avocat|conseiller|substitut|expert|notaire|t[ée]moin|"
            r"demandeur|d[ée]fendeur|requ[ée]rant|appelant|intim[ée]|pr[ée]venu|patient|client|"
            r"coll[èe]gue|p[èe]re|fils|fr[èe]re|[ée]poux|veuf|oncle|neveu|inspecteur|commissaire|"
            r"agent|m[ée]diateur|curateur|tuteur|g[ée]rant")
_FEMALE_ROLE_BEFORE_RE = re.compile(rf"(?<![\w])(?:[Ll]a|[Uu]ne|[Ll]['’]|[Ss]a)[ \t]*(?:{_ROLES_F})[ \t,]*\Z")
_MALE_ROLE_BEFORE_RE = re.compile(rf"(?<![\w])(?:[Ll]e|[Uu]n|[Ss]on)[ \t]+(?:{_ROLES_M})[ \t,]*\Z")
_FEMALE_AFTER_RE = re.compile(
    r"[ \t]*,[ \t]*(?:(?:la|sa|une|l['’])[ \t]*)?(?:avocate|pr[ée]sidente|greffi[èe]re|conseill[èe]re|"
    r"experte|demanderesse|d[ée]fenderesse|requ[ée]rante|appelante|intim[ée]e|pr[ée]venue|n[ée]e|"
    r"[ée]pouse|veuve|m[èe]re|fille|s[œo]e?ur|inspectrice|agente|m[ée]diatrice|curatrice|tutrice)(?![\w])"
)
_MALE_AFTER_RE = re.compile(
    r"[ \t]*,[ \t]*(?:(?:le|son|un)[ \t]+)?(?:avocat|pr[ée]sident|greffier|conseiller|expert|demandeur|"
    r"d[ée]fendeur|requ[ée]rant|appelant|intim[ée]|pr[ée]venu|n[ée]|[ée]poux|veuf|p[èe]re|fils|fr[èe]re|"
    r"inspecteur|m[ée]diateur|curateur|tuteur)(?![\w])"
)
_TITLED_BEFORE_RE = re.compile(
    r"(?<![\w])(?:Monsieur|Madame|Mademoiselle|Mme|Mlle|M|Me|Ma[îi]tre|Dr|Mr|Docteur)\.?[ \t]*\Z"
)
_PREFIX_PARTICLES = frozenset({"BEN", "BOU", "BENT"})
"""Particules (« Ben Saïd ») qui sont aussi des prénoms (« Ben ») : particules
seulement après une civilité, un prénom, ou en capitales devant un nom."""


def _gender(text: str, d: Detection, before: str, firsts: list[str]) -> Optional[str]:
    """Genre d'une mention : civilité, accord d'une qualité (« la greffière
    Dubois », « Dubois, avocate »), sinon prénom (« Anne » → F)."""
    if _MALE_BEFORE_RE.search(before):
        return "M"
    if _FEMALE_BEFORE_RE.search(before) or _FEMALE_ROLE_BEFORE_RE.search(before):
        return "F"
    if _MALE_ROLE_BEFORE_RE.search(before):
        return "M"
    after = text[d.end:d.end + 40]
    if _FEMALE_AFTER_RE.match(after):
        return "F"
    if _MALE_AFTER_RE.match(after):
        return "M"
    genders = {first_name_gender(f) for f in firsts} - {None}
    return genders.pop() if len(genders) == 1 else None


_MAIDEN_NAME_BEFORE_RE = re.compile(r",?[ \t]*(?:n[ée]e|[ée]pouse|veuve|[ée]p\.)[ \t]+\Z")
_PAREN_FIRST_NAME_RE = re.compile(r"[ \t]*\([ \t]*")
"""« Mme MARTIN-LEGRAND (Sophie) » : le prénom entre parenthèses suit le nom."""
_FIRST_NAME_FIELD_RE = re.compile(r"(?i)pr[ée]noms?[ \t]*[:：][ \t]*\Z")
_SURNAME_FIELD_RE = re.compile(r"(?i)(?<![\w])nom(?:[ \t]+de[ \t]+famille)?[ \t]*[:：][ \t]*\Z")


@dataclass
class _Mention:
    det: Detection
    firsts: set[str]
    initials: set[str]
    surnames: set[str]
    gender: Optional[str]
    wildcard: bool
    """« M. » : initiale M ou « Monsieur » (compatible avec tout prénom)."""

    @property
    def full(self) -> bool:
        return bool(self.surnames) and bool(self.firsts or self.initials or self.wildcard)


@dataclass
class _Cluster:
    first_pos: int
    surnames: set[str] = field(default_factory=set)
    firsts: set[str] = field(default_factory=set)
    initials: set[str] = field(default_factory=set)
    gender: Optional[str] = None
    mentions: int = 0
    key: Optional[str] = None
    """Initiales seules (« PV ») : ces mentions forment leur propre personne."""

    def add(self, m: _Mention) -> None:
        self.surnames |= m.surnames
        self.firsts |= m.firsts
        if not m.wildcard:
            self.initials |= m.initials
        self.gender = self.gender or m.gender
        self.first_pos = min(self.first_pos, m.det.start)
        self.mentions += 1


def _initials_of(firsts: set[str]) -> set[str]:
    result = set()
    for first in firsts:
        parts = [p for p in re.split(r"[-\s]+", first) if p]
        if parts:
            result.add(parts[0][0])
            result.add("".join(p[0] for p in parts))
    return result


def _parse(text: str, d: Detection) -> _Mention:
    value = text[d.start:d.end]
    before = text[max(0, d.start - 30):d.start]
    titled = _TITLED_BEFORE_RE.search(before) is not None
    initials: set[str] = set()
    wildcard = False
    names: list[tuple[str, str]] = []
    tokens = [t for t in re.split(r"[\s,;:()«»\"]+", value) if t]
    for index, raw in enumerate(tokens):
        if (fold(raw) in _PREFIX_PARTICLES and index + 1 < len(tokens)
                and (titled or names or initials or raw.isupper())):
            continue  # « Madame Ben Saïd », « Fatima BEN SAÏD » : particule du nom
        if _INITIAL_TOKEN_RE.fullmatch(raw):
            if raw == "M." and not names and not initials:
                wildcard = True
            else:
                initials.add(_letters(raw))
            continue
        key = fold(raw).strip(".'’-")
        if key and key not in NAME_PARTICLES and len(key) >= 2:
            names.append((raw, key))
    firsts: set[str] = set()
    surnames: set[str] = set()
    uppers = [k for raw, k in names if raw.isupper()]
    if uppers and len(uppers) < len(names):
        firsts = {k for raw, k in names if not raw.isupper()}
        surnames = set(uppers)
    elif len(names) == 1:
        surnames = {names[0][1]}
    elif names:
        firsts = {k for _, k in names[:-1]}
        surnames = {names[-1][1]}
    first_raw = [raw for raw, k in names if k in firsts]
    gender = _gender(text, d, before, first_raw)
    return _Mention(d, firsts, initials, surnames, gender, wildcard)


def _first_name_score(m: _Mention, c: _Cluster) -> int:
    """3 : même prénom ; 2 : initiale concordante ; 1 : aucune information ;
    0 : incompatible (autre prénom, autre initiale)."""
    if m.firsts and c.firsts:
        return 3 if m.firsts & c.firsts else 0
    if m.wildcard:
        return 1
    mine, theirs = m.initials | _initials_of(m.firsts), c.initials | _initials_of(c.firsts)
    if mine and theirs:
        return 2 if mine & theirs else 0
    return 1


def _gender_ok(m: _Mention, c: _Cluster) -> bool:
    return not (m.gender and c.gender and m.gender != c.gender)


def _best(m: _Mention, candidates: list[_Cluster], score=lambda c: 0) -> _Cluster:
    """Le groupe le plus probable : meilleur accord des prénoms, même civilité,
    personne la plus citée, puis la plus proche qui précède."""
    pos = m.det.start
    return max(candidates, key=lambda c: (
        score(c),
        bool(m.gender) and c.gender == m.gender,
        c.mentions,
        c.first_pos <= pos,
        c.first_pos if c.first_pos <= pos else -c.first_pos,
    ))


def assign_person_labels(text: str, persons: list[Detection], base: str,
                         numbered: bool = True) -> None:
    """Attribue ``[PERSONNE_n]`` (``base`` + numéro) à chaque détection ``PERSON``.

    Les mentions complètes (prénom ou initiale + nom) définissent les
    personnes ; les mentions partielles (« DUPONT », « Lucas ») sont ensuite
    rattachées à la personne compatible la plus probable. Numérotation dans
    l'ordre d'apparition : le résultat ne dépend que du texte (déterministe).
    """
    if not numbered:
        for d in persons:
            d.label = base
        return
    ordered = sorted(persons, key=lambda d: (d.start, d.end))
    mentions = [_parse(text, d) for d in ordered]
    clusters: list[_Cluster] = []
    assignment: dict[int, _Cluster] = {}

    # 0) Initiales seules : une personne par groupe d'initiales.
    by_key: dict[str, _Cluster] = {}
    for m in mentions:
        if m.det.kind != "initials":
            continue
        key = _letters(text[m.det.start:m.det.end])
        cluster = by_key.get(key)
        if cluster is None:
            cluster = by_key[key] = _Cluster(m.det.start, key=key)
            clusters.append(cluster)
        cluster.add(m)
        assignment[id(m.det)] = cluster

    # 1) Mentions complètes.
    for m in mentions:
        if id(m.det) in assignment or not m.full:
            continue
        candidates = [c for c in clusters if c.key is None and c.surnames & m.surnames
                      and _gender_ok(m, c) and _first_name_score(m, c) > 0]
        if candidates:
            cluster = _best(m, candidates, lambda c: _first_name_score(m, c))
        else:
            cluster = _Cluster(m.det.start)
            clusters.append(cluster)
        cluster.add(m)
        assignment[id(m.det)] = cluster

    # 2) Mentions partielles (un seul mot).
    previous: Optional[Detection] = None
    for index, m in enumerate(mentions):
        if id(m.det) in assignment:
            previous = m.det
            continue
        word = next(iter(m.surnames), "")
        before = text[max(0, m.det.start - 30):m.det.start]
        cluster = None
        if previous is not None and _MAIDEN_NAME_BEFORE_RE.fullmatch(text[previous.end:m.det.start]):
            cluster = assignment[id(previous)]  # « Marie DUPONT, née MARTIN »
        elif (previous is not None and _PAREN_FIRST_NAME_RE.fullmatch(text[previous.end:m.det.start])
              and re.match(r"[ \t]*\)", text[m.det.end:])):
            cluster = assignment[id(previous)]  # « MARTIN-LEGRAND (Sophie) »
            cluster.firsts.add(word)
        elif _FIRST_NAME_FIELD_RE.search(before):
            for other in reversed(mentions[:index]):
                if m.det.start - other.det.end > 120:
                    break
                if _SURNAME_FIELD_RE.search(text[max(0, other.det.start - 30):other.det.start]):
                    cluster = assignment[id(other.det)]  # « Nom : X » puis « Prénom : Y »
                    break
        if cluster is None and word:
            candidates = [c for c in clusters if c.key is None and word in c.surnames and _gender_ok(m, c)]
            if not candidates:
                candidates = [c for c in clusters if c.key is None and word in c.firsts and _gender_ok(m, c)]
            if candidates:
                cluster = _best(m, candidates)
        if cluster is None:
            cluster = _Cluster(m.det.start)
            clusters.append(cluster)
        if word and word not in cluster.surnames | cluster.firsts:
            # Nouvelle personne, nom de naissance ou prénom d'un formulaire.
            (cluster.firsts if _FIRST_NAME_FIELD_RE.search(before) else cluster.surnames).add(word)
        cluster.gender = cluster.gender or m.gender
        cluster.first_pos = min(cluster.first_pos, m.det.start)
        cluster.mentions += 1
        assignment[id(m.det)] = cluster
        previous = m.det

    number = {id(c): n for n, c in enumerate(sorted(clusters, key=lambda c: c.first_pos), start=1)}
    for d in ordered:
        d.label = f"{base}_{number[id(assignment[id(d)])]}"
