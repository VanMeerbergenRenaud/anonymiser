"""
public — Ce qui reste lisible : références publiques, institutions, pays,
jurisprudence citée, lieux sans lien avec une personne.

Le NER étiquette volontiers comme « personne », « lieu » ou « organisation »
des éléments publics : un fragment d'ECLI (« GHCC:2024 »), un point d'arrêt
(« B.64 »), « l'Union », « États membres », un ordre d'avocats, le nom d'une
affaire citée (« Michaud c. France »). Ce module fournit les zones et les
listes qui permettent à ``api.nlp_engine`` de les conserver, quel que soit
le contexte de la phrase (même décision dans tout le document).
"""

from __future__ import annotations

import re

from api.recognizers import fold

# ---------------------------------------------------------------------------
# Références publiques (jamais modifiées)
# ---------------------------------------------------------------------------

_PUBLIC_REFERENCE_RE = re.compile(
    r"ECLI:[A-Z]{2}:[A-Z0-9]{1,7}:\d{4}:[A-Z0-9.]{0,25}[A-Z0-9]"           # ECLI:BE:GHCC:2024:ARR.001
    r"|(?<![\w:])(?:EU|CE):[A-Z]{1,6}:\d{4}:[A-Z0-9.]{0,30}[A-Z0-9]"      # EU:C:2021:84, CE:ECHR:…
    r"|(?<![\w-])[CTF]-\d{1,4}/\d{2}(?![\w/])"                             # C-694/20 (CJUE)
    r"|(?<![\w.])[A-Z]\.\d{1,3}(?:\.\d{1,3})*(?![\w])"                     # B.64, A.2.1, B.6.2
    r"|(?<![\w.])[CPSFDH]\.\d{2}\.\d{4}\.[FND](?![\w])"                    # C.18.0453.F (Cassation)
    r"|(?<![\w])n[°ºo˚][ \t]*\d{1,4}/(?:19|20)\d{2}(?![\w/])"             # arrêt n° 103/2022
    r"|(?<![\w])DOC[ \t]+\d{2}[ \t]*-[ \t]*\d{3,4}/\d{3}(?![\w])"          # DOC 55-0791/001
    r"|(?<![\w])(?:req\.|requête)[ \t]+n[°ºo˚]s?[ \t]*\d{3,6}/\d{2}(?![\w/])"  # req. n° 12323/11 (CEDH)
)


def public_reference_spans(text: str) -> list[tuple[int, int]]:
    """Identifiants publics : ECLI, n° d'affaire de la CJUE, points d'arrêt…"""
    return [m.span() for m in _PUBLIC_REFERENCE_RE.finditer(text)]


# ---------------------------------------------------------------------------
# Jurisprudence citée (« Michaud c. France », « Consob, C-481/19 »)
# ---------------------------------------------------------------------------

_CASE_NUMBER = r"(?:[CTF]-\d{1,4}/\d{2}|\d{1,4}/\d{2}(?=,?[ \t]*(?:EU|ECLI):))"
_NAME_START = r"(?:(?<=[,;(\[])|(?<=en cause de)|(?<=arrêt)|(?<=affaire))"
_CJEU_CASE_RE = re.compile(
    # « arrêt du 2 février 2021, Consob, C-481/19 », « en cause de X e.a. (C-694/20 »
    rf"{_NAME_START}[ \t]*(?P<name>[^,;()\[\]\n]{{2,140}}?)"
    rf"(?:[ \t]*\([^()\n]{{0,160}}\))?[ \t]*[,(][ \t]*(?:aff\.[ \t]*)?{_CASE_NUMBER}"
)
_ECHR_CASE_RE = re.compile(
    # « Michaud c. France », « arrêt Marckx c. Belgique du 13 juin 1979 »
    rf"{_NAME_START}[ \t]*(?P<name>[A-ZÀ-Þ][^,;()\[\]\n]{{0,80}}?[ \t]+(?:c\.|v\.)[ \t]+"
    r"[A-ZÀ-Þ][\w’'\- ]{0,60}?)(?=[ \t]*(?:\(|,|\)|\]|;|\.(?:\s|$)|$|du[ \t]+\d))"
)
_COURT_CONTEXT_RE = re.compile(
    r"(?i:cour[ \t]+(?:eur\.?|europ[ée]enne)|cour[ \t]+de[ \t]+justice|requ[êe]te[ \t]+n)"
    r"|CEDH|C\.E\.D\.H\.|Cour[ \t]+EDH|ECHR|req\.|CJUE|C\.J\.U\.E\.|ECLI|EU:C:|Cass\.|C\.[ \t]?const\."
)
"""Juridiction citée : condition pour lire « X c. Y » comme une affaire publique."""


def case_law_spans(text: str) -> list[tuple[int, int]]:
    """Noms d'affaires de jurisprudence publique citées (zones de texte)."""
    spans = []
    for m in _CJEU_CASE_RE.finditer(text):
        spans.append(m.span("name"))
    for m in _ECHR_CASE_RE.finditer(text):
        window = text[max(0, m.start() - 80):m.end() + 100]
        if _COURT_CONTEXT_RE.search(window):
            spans.append(m.span("name"))
    cleaned = []
    for start, end in spans:
        value = text[start:end]
        start += len(value) - len(value.lstrip())
        end -= len(value) - len(value.rstrip())
        if end > start:
            cleaned.append((start, end))
    return sorted(set(cleaned))


# ---------------------------------------------------------------------------
# Institutions et pays
# ---------------------------------------------------------------------------

INSTITUTION_HEADS: frozenset[str] = frozenset({
    "TRIBUNAL", "TRIBUNAUX", "COUR", "CONSEIL", "CODE", "LOI", "LOIS", "PARQUET", "MINISTERE",
    "MINISTRE", "GREFFE", "CHAMBRE", "BARREAU", "BARREAUX", "JUSTICE", "JURIDICTION",
    "COMMISSION", "CONSTITUTION", "CONVENTION", "REGLEMENT", "DIRECTIVE", "MONITEUR",
    "GOUVERNEMENT", "PARLEMENT", "SENAT", "ETAT", "ETATS", "SPF", "SPW", "SPP", "SPRB",
    "AUDITORAT", "AUDITEUR", "PROCUREUR", "ORDRE", "ORDRES", "ORDE", "ARTICLE", "ARRETE",
    "DECRET", "ORDONNANCE", "TRAITE", "CHARTE", "CONFERENCE", "ROYAUME", "REPUBLIQUE",
    "ASSEMBLEE", "CAISSE", "ONSS", "INAMI", "ONEM", "FOREM", "ACTIRIS", "VDAB", "URSSAF", "CPAM",
    "CPAS", "OCMW", "SERVICE", "SERVICES", "DIRECTION", "DEPARTEMENT", "ADMINISTRATION", "OFFICE",
    "INSTITUT", "AGENCE", "AUTORITE", "UNION", "COMMUNAUTE", "FEDERATION", "REGION", "PROVINCE",
    "COMMUNE", "ZONE", "POLICE", "CENTRE", "FONDS", "BANQUE", "COLLEGE", "UNIVERSITE", "ECOLE",
    "HOPITAL", "MUTUALITE", "SYNDICAT", "LIGUE", "CNIL", "APD", "GBA", "RGPD", "GDPR", "TVA",
    "BCE", "KBO", "RCS", "ONU", "OCDE", "OTAN", "BENELUX", "EUROPOL", "EUROJUST", "INTERPOL",
    "CEDH", "CJUE", "TFUE", "TUE", "UE", "ECLI", "VLAAMSE", "RAAD", "HOF", "RECHTBANK",
    "DIVISION", "CANTON", "ARRONDISSEMENT", "SECTION", "CHAPITRE", "TITRE", "ANNEXE", "ANNEXES",
    "PIECE", "PIECES", "PAGE", "PARAGRAPHE", "ALINEA", "INVENTAIRE", "BORDEREAU", "SOMMAIRE",
})
"""Premier mot d'une institution, d'un texte légal ou d'un élément de
structure du document : jamais masqué (« Cour de justice », « l'Autorité de
protection des données », « Orde van Vlaamse balies », « États membres »)."""

COUNTRIES: frozenset[str] = frozenset({
    "AFGHANISTAN", "AFRIQUE DU SUD", "ALBANIE", "ALGERIE", "ALLEMAGNE", "ANDORRE", "ANGOLA",
    "ARABIE SAOUDITE", "ARGENTINE", "ARMENIE", "AUSTRALIE", "AUTRICHE", "AZERBAIDJAN", "BAHREIN",
    "BANGLADESH", "BELARUS", "BIELORUSSIE", "BELGIQUE", "BELGIE", "BELGIEN", "BELGIUM", "BENIN",
    "BIRMANIE", "BOLIVIE", "BOSNIE", "BOSNIE-HERZEGOVINE", "BOTSWANA", "BRESIL", "BULGARIE",
    "BURKINA FASO", "BURUNDI", "CAMBODGE", "CAMEROUN", "CANADA", "CAP-VERT", "CHILI", "CHINE",
    "CHYPRE", "COLOMBIE", "COMORES", "CONGO", "COREE", "COREE DU SUD", "COREE DU NORD",
    "COSTA RICA", "COTE D'IVOIRE", "CROATIE", "CUBA", "DANEMARK", "DJIBOUTI", "EGYPTE",
    "EMIRATS ARABES UNIS", "EQUATEUR", "ERYTHREE", "ESPAGNE", "ESTONIE", "ETATS-UNIS",
    "ETATS-UNIS D'AMERIQUE", "ETHIOPIE", "FINLANDE", "FRANCE", "GABON", "GAMBIE", "GEORGIE",
    "GHANA", "GRECE", "GUATEMALA", "GUINEE", "HAITI", "HONDURAS", "HONGRIE", "INDE", "INDONESIE",
    "IRAK", "IRAN", "IRLANDE", "ISLANDE", "ISRAEL", "ITALIE", "JAMAIQUE", "JAPON", "JORDANIE",
    "KAZAKHSTAN", "KENYA", "KOSOVO", "KOWEIT", "LAOS", "LETTONIE", "LIBAN", "LIBERIA", "LIBYE",
    "LIECHTENSTEIN", "LITUANIE", "LUXEMBOURG", "MACEDOINE", "MACEDOINE DU NORD", "MADAGASCAR",
    "MALAISIE", "MALI", "MALTE", "MAROC", "MAURICE", "MAURITANIE", "MEXIQUE", "MOLDAVIE",
    "MONACO", "MONGOLIE", "MONTENEGRO", "MOZAMBIQUE", "NAMIBIE", "NEPAL", "NICARAGUA", "NIGER",
    "NIGERIA", "NORVEGE", "NOUVELLE-ZELANDE", "OMAN", "OUGANDA", "OUZBEKISTAN", "PAKISTAN",
    "PALESTINE", "PANAMA", "PARAGUAY", "PAYS-BAS", "PEROU", "PHILIPPINES", "POLOGNE",
    "PORTUGAL", "QATAR", "REPUBLIQUE TCHEQUE", "TCHEQUIE", "REPUBLIQUE DEMOCRATIQUE DU CONGO",
    "RDC", "ROUMANIE", "ROYAUME-UNI", "RUSSIE", "RWANDA", "SAINT-MARIN", "SENEGAL", "SERBIE",
    "SIERRA LEONE", "SINGAPOUR", "SLOVAQUIE", "SLOVENIE", "SOMALIE", "SOUDAN", "SRI LANKA",
    "SUEDE", "SUISSE", "SYRIE", "TADJIKISTAN", "TAIWAN", "TANZANIE", "TCHAD", "THAILANDE",
    "TOGO", "TUNISIE", "TURKMENISTAN", "TURQUIE", "UKRAINE", "URUGUAY", "VATICAN", "VENEZUELA",
    "VIETNAM", "YEMEN", "ZAMBIE", "ZIMBABWE", "ANGLETERRE", "ECOSSE", "PAYS DE GALLES",
    "NEDERLAND", "NETHERLANDS", "DUITSLAND", "GERMANY", "FRANKRIJK", "SPAIN", "ITALY",
    "EUROPE", "AFRIQUE", "ASIE", "AMERIQUE", "AMERIQUE DU NORD", "AMERIQUE DU SUD", "OCEANIE",
    "UNION EUROPEENNE", "WALLONIE", "FLANDRE", "FLANDRES", "VLAANDEREN", "WALLONIE-BRUXELLES",
    "REGION WALLONNE", "REGION FLAMANDE", "REGION DE BRUXELLES-CAPITALE", "BRUXELLES-CAPITALE",
    "FEDERATION WALLONIE-BRUXELLES", "COMMUNAUTE FRANCAISE", "COMMUNAUTE FLAMANDE",
    "COMMUNAUTE GERMANOPHONE", "ROYAUME DE BELGIQUE",
})
"""Pays, continents et entités fédérées : toujours lisibles."""

_LEADING_ARTICLE_RE = re.compile(r"^(?:L['’]|LE |LA |LES |DE |DU |DES |D['’]|AU |AUX )+")
_TRAILING_ET_AL_RE = re.compile(r"(?:[ ,]+(?:E\.A\.?|ET AUTRES|ET CONSORTS|C\.S\.?))+$")


def normalize(value: str) -> str:
    """Forme de comparaison : capitales sans accents, sans guillemets, sans
    article initial ni « e.a. » final (« l’« Orde van Vlaamse balies » »
    → « ORDE VAN VLAAMSE BALIES »)."""
    key = fold(value)
    key = re.sub(r"[«»\"“”]", " ", key)
    key = " ".join(key.replace("’", "'").split())
    key = _LEADING_ARTICLE_RE.sub("", key).strip()
    return _TRAILING_ET_AL_RE.sub("", key).strip(" ,.")


def is_institution(value: str) -> bool:
    key = normalize(value)
    words = re.split(r"[ '\-]+", key)
    return bool(words and words[0] in INSTITUTION_HEADS) or key in COUNTRIES


def is_country(value: str) -> bool:
    return normalize(value) in COUNTRIES


# ---------------------------------------------------------------------------
# Lieux liés à une personne (domicile, naissance, résidence)
# ---------------------------------------------------------------------------

_PERSON_PLACE_BEFORE_RE = re.compile(
    r"(?i)(?<![\w])(?:domicili[ée]e?s?|demeurant|r[ée]sid(?:ant|ante|ants|antes|e|ent|ait|aient)|"
    r"habit(?:ant|ante|ants|e|ent|ait|aient)|n[ée]e?s?|d[ée]c[ée]d[ée]e?s?|originaires?|"
    r"install[ée]e?s?|domicile|r[ée]sidence|lieu[ \t]+de[ \t]+naissance|wonende|woonachtig|geboren)"
    r"(?![\w])[^,;.\n]{0,45}?(?:[ \t]|^)(?:à|au|aux|en|te|in)[ \t]+$"
)


_RESIDENCE_BEFORE_RE = re.compile(
    # La résidence peut être séparée du lieu par une apposition : « réside chez
    # sa sœur, Madame X, à Ath ».
    r"(?i)(?<![\w])(?:domicili[ée]e?s?|demeur\w*|r[ée]sid\w*|habit\w*|h[ée]berg[ée]e?s?|vit|vivent|"
    r"vivait|install[ée]e?s?)(?![\w])[^.;\n]{0,80}?(?:[ \t]|^)(?:à|au|aux|en|te|in)[ \t]+$"
)


def person_linked_place(text: str, start: int) -> bool:
    """Vrai si le lieu commençant à ``start`` est lié à une personne :
    « domicilié à Namur », « née à Ciney le… », « né le 3 mars 1980 à
    Dinant », « réside chez sa sœur, Madame X, à Ath »."""
    line_start = text.rfind("\n", 0, start) + 1
    window = max(line_start, start - 100)
    return (_PERSON_PLACE_BEFORE_RE.search(text, window, start) is not None
            or _RESIDENCE_BEFORE_RE.search(text, window, start) is not None)


_SEAT_BEFORE_RE = re.compile(
    r"(?i)(?<![\w])(?:si[èe]ge(?:[ \t]+social)?(?:[ \t]+(?:est|sont))?(?:[ \t]+(?:[ée]tabli|fix[ée]|situ[ée])e?s?)?"
    r"|[ée]tablie?s?|situ[ée]e?s?|sise?s?|implant[ée]e?s?|localis[ée]e?s?)[ \t]+(?:à|au|aux|en)[ \t]+$"
)


def place_context(text: str, start: int) -> bool:
    """Vrai si ``start`` suit « dont le siège est établi à », « situé à »… :
    ce qui suit est un lieu, même si le NER y voit une personne."""
    return _SEAT_BEFORE_RE.search(text, max(0, start - 60), start) is not None


# ---------------------------------------------------------------------------
# Organisations
# ---------------------------------------------------------------------------

ORGANIZATION_WORDS: frozenset[str] = frozenset({
    "SA", "SRL", "SPRL", "SCRL", "SC", "SCS", "SNC", "ASBL", "AISBL", "NV", "BV", "BVBA", "VZW",
    "SAS", "SARL", "SASU", "EURL", "GIE", "SE", "GMBH", "AG", "LTD", "LLC", "INC", "PLC", "CO",
    "SOCIETE", "ASSOCIATION", "GROUPE", "GROUP", "HOLDING", "CONSULTING", "SOLUTIONS",
    "SERVICES", "INTERNATIONAL", "BUSINESS", "REGISTERS", "LAWYERS", "TAX", "PRIVACY",
    "COMPANY", "CORPORATION", "FONDATION", "FOUNDATION", "BANK", "BANQUE", "ASSURANCES",
    "INSURANCE", "IMMO", "IMMOBILIERE", "CONSTRUCTION", "CONSTRUCTIONS", "TRANSPORT",
    "TRANSPORTS", "INVEST", "INVESTMENTS", "PARTNERS", "ASSOCIES", "CABINET", "ETUDE", "BUREAU",
    "&", "ET",
})
"""Mots révélant une personne morale (forme juridique, secteur)."""


def looks_like_organization(value: str) -> bool:
    words = [w.strip(".,") for w in re.split(r"[\s/]+", fold(value)) if w.strip(".,")]
    return any(w.replace(".", "") in ORGANIZATION_WORDS for w in words) or "&" in value
