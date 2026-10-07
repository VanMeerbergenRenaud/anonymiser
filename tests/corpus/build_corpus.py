"""Génère les documents fictifs du corpus d'évaluation (``tests/corpus/documents``).

Les documents produits sont versionnés : ce script ne sert qu'à les recréer
(ou à en ajouter). Tous les noms, adresses et numéros sont fictifs.

Lancement (depuis la racine du dépôt) ::

    PYTHONPATH=. ./venv/bin/python tests/corpus/build_corpus.py

Les PDF reproduisent les particularités des PDF exportés de Word (comme
l'arrêt de la Cour constitutionnelle) : mots d'une ligne justifiée posés un
par un, lignes vides entre paragraphes, en-tête (n° de page) et pied de page
répétés, passages en double interligne, traits d'union en fin de ligne,
bloc de signatures sur deux colonnes.

Balisage des sources PDF (une ligne = un paragraphe) :

- ``= texte`` : titre centré (gras) ;
- ``## texte`` : titre aligné à gauche (gras) ;
- ``> texte`` : paragraphe justifié avec retrait de première ligne ;
- ``texte`` : paragraphe justifié sans retrait ;
- ``|| gauche || droite`` : ligne sur deux colonnes (signatures) ;
- ``@page`` : saut de page ; ``@interligne 2`` : interligne à partir d'ici ;
- dans un paragraphe, ``¦`` force un retour à la ligne (ligne précédente
  justifiée) et ``¬`` une césure syllabique (« conven¬tionnelles »).
"""

from __future__ import annotations

import io
import math
import os
from pathlib import Path

import fitz
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
OUT = HERE / "documents"

# ---------------------------------------------------------------------------
# Mise en page PDF
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = 595.32, 842.04
LEFT, RIGHT = 70.9, 527.5
TOP, BOTTOM = 72.0, 750.0
FONT, BOLD = "helv", "hebo"
SIZE = 11.5
INDENT = 21.3


class Typesetter:
    """Pose le texte ligne à ligne, à la manière d'un export Word."""

    def __init__(self, header_numbers: bool = True, footer: str | None = None) -> None:
        self.pdf = fitz.open()
        self.header_numbers = header_numbers
        self.footer = footer
        self.spacing = 1.2
        self.page = None
        self.y = 0.0
        self.new_page()

    # --- Pages ---------------------------------------------------------------

    def new_page(self) -> None:
        self.page = self.pdf.new_page(width=PAGE_W, height=PAGE_H)
        number = self.pdf.page_count
        if self.header_numbers and number > 1:
            text = str(number)
            width = fitz.get_text_length(text, FONT, SIZE)
            self.page.insert_text((RIGHT - width, 46), text, fontname=FONT, fontsize=SIZE)
        if self.footer:
            self.page.insert_text((LEFT, 805), self.footer, fontname=FONT, fontsize=10)
        self.y = TOP

    def advance(self, size: float = SIZE) -> None:
        self.y += size * self.spacing
        if self.y > BOTTOM:
            self.new_page()
            self.y += size * self.spacing

    # --- Lignes ----------------------------------------------------------------

    def blank(self) -> None:
        """Ligne vide (un espace, comme dans les exports Word)."""
        self.advance()
        self.page.insert_text((LEFT, self.y), " ", fontname=FONT, fontsize=SIZE)

    def _line(self, words: list[str], x0: float, justify: bool, font: str = FONT) -> None:
        self.advance()
        widths = [fitz.get_text_length(w, font, SIZE) for w in words]
        space = fitz.get_text_length(" ", font, SIZE)
        if justify and len(words) > 1:
            gap = (RIGHT - x0 - sum(widths)) / (len(words) - 1)
            x = x0
            for word, width in zip(words, widths):
                self.page.insert_text((x, self.y), word + " ", fontname=font, fontsize=SIZE)
                x += width + gap
        else:
            self.page.insert_text((x0, self.y), " ".join(words) + " ", fontname=font, fontsize=SIZE)
        del space

    def paragraph(self, text: str, first_indent: float = 0.0, font: str = FONT) -> None:
        """Paragraphe justifié (dernière ligne alignée à gauche)."""
        tokens = text.replace("¦", " ¦ ").split()
        line: list[str] = []
        x0 = LEFT + first_indent

        def width(words: list[str]) -> float:
            return fitz.get_text_length(" ".join(words), font, SIZE)

        for token in tokens:
            if token == "¦":
                self._line(line, x0, justify=True, font=font)
                line, x0 = [], LEFT
                continue
            if "¬" in token:  # césure syllabique forcée
                head, tail = token.split("¬", 1)
                self._line(line + [head + "-"], x0, justify=True, font=font)
                line, x0 = [tail], LEFT
                continue
            if line and width(line + [token]) > RIGHT - x0:
                self._line(line, x0, justify=True, font=font)
                line, x0 = [], LEFT
            line.append(token)
        if line:
            self._line(line, x0, justify=False, font=font)

    def centered(self, text: str, font: str = BOLD) -> None:
        self.advance()
        width = fitz.get_text_length(text, font, SIZE)
        self.page.insert_text(((LEFT + RIGHT - width) / 2, self.y), text + " ", fontname=font, fontsize=SIZE)

    def columns(self, left: str, right: str) -> None:
        self.advance()
        self.page.insert_text((LEFT, self.y), left + " ", fontname=FONT, fontsize=SIZE)
        width = fitz.get_text_length(right, FONT, SIZE)
        self.page.insert_text((RIGHT - width, self.y), right + " ", fontname=FONT, fontsize=SIZE)

    # --- Source balisée ---------------------------------------------------------

    def render(self, source: str) -> bytes:
        first = True
        for raw in source.strip().splitlines():
            line = raw.strip()
            if not line:
                continue
            if line == "@page":
                self.new_page()
                first = True
                continue
            if line.startswith("@interligne"):
                self.spacing = 1.2 * float(line.split()[1])
                continue
            if not first:
                self.blank()
            first = False
            if line.startswith("= "):
                self.centered(line[2:])
            elif line.startswith("## "):
                self.paragraph(line[3:], font=BOLD)
            elif line.startswith("> "):
                self.paragraph(line[2:], first_indent=INDENT)
            elif line.startswith("||"):
                left, right = [p.strip() for p in line.strip("|").split("||")]
                self.columns(left, right)
            else:
                self.paragraph(line)
        return self.pdf.tobytes(garbage=3, deflate=True)


# ---------------------------------------------------------------------------
# Images (scans, carte, logo, signature)
# ---------------------------------------------------------------------------

def _font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for folder in ("/usr/share/fonts/truetype/dejavu", ""):
        try:
            return ImageFont.truetype(os.path.join(folder, name) if folder else name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _emblem(draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
    """Blason fictif : cercles, étoile, lion stylisé (aucun texte)."""
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline="black", width=6)
    draw.ellipse((cx - r + 18, cy - r + 18, cx + r - 18, cy + r - 18), outline="black", width=3)
    points = []
    for k in range(10):
        angle = math.pi / 2 + k * math.pi / 5
        radius = r * (0.55 if k % 2 == 0 else 0.22)
        points.append((cx + radius * math.cos(angle), cy - radius * math.sin(angle)))
    draw.polygon(points, outline="black", fill=None, width=4)
    for k in range(24):
        angle = k * math.pi / 12
        x1, y1 = cx + (r - 30) * math.cos(angle), cy + (r - 30) * math.sin(angle)
        x2, y2 = cx + (r - 8) * math.cos(angle), cy + (r - 8) * math.sin(angle)
        draw.line((x1, y1, x2, y2), fill="black", width=3)
    draw.arc((cx - r // 3, cy + r // 6, cx + r // 3, cy + r // 2), 0, 180, fill="black", width=5)
    draw.pieslice((cx - 25, cy - 20, cx + 25, cy + 30), 200, 340, fill="black")


def _signature(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float = 1.0) -> None:
    """Paraphe manuscrit fictif (courbes)."""
    pts = []
    for k in range(0, 360):
        t = k / 359
        px = x + scale * (420 * t + 40 * math.sin(14 * t * math.pi))
        py = y + scale * (60 * math.sin(6 * t * math.pi) * (1 - t) + 25 * math.cos(22 * t))
        pts.append((px, py))
    draw.line(pts, fill="black", width=max(2, int(4 * scale)))
    draw.line((x + 30 * scale, y + 70 * scale, x + 380 * scale, y + 55 * scale), fill="black", width=3)


def _png(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def logo_png() -> bytes:
    img = Image.new("L", (520, 520), 255)
    _emblem(ImageDraw.Draw(img), 260, 260, 230)
    return _png(img)


def signature_png() -> bytes:
    img = Image.new("L", (560, 200), 255)
    _signature(ImageDraw.Draw(img), 40, 70)
    return _png(img)


def id_card_png() -> bytes:
    img = Image.new("L", (1240, 780), 238)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((8, 8, 1232, 772), radius=40, outline=90, width=5)
    draw.text((60, 40), "BELGIQUE  BELGIË  BELGIEN  BELGIUM", font=_font(40, bold=True), fill=20)
    draw.text((60, 100), "CARTE D'IDENTITÉ", font=_font(36), fill=30)
    draw.rectangle((60, 190, 380, 600), fill=200, outline=120, width=3)
    draw.ellipse((150, 260, 290, 420), fill=170)
    draw.ellipse((110, 430, 330, 600), fill=170)
    rows = [
        ("Nom / Name", "PEETERS"),
        ("Prénoms / Given names", "An Marie"),
        ("Nationalité", "Belge"),
        ("Date de naissance", "30 07 1985"),
        ("N° de carte", "592-1234567-89"),
        ("N° national", "85.07.30-033.28"),
    ]
    y = 190
    for label, value in rows:
        draw.text((430, y), label, font=_font(26), fill=60)
        draw.text((430, y + 32), value, font=_font(40, bold=True), fill=10)
        y += 92
    return _png(img)


def scan_page_png() -> bytes:
    """Page A4 scannée (300 dpi) : logo, texte, tampon du greffe, signature."""
    img = Image.new("L", (2480, 3508), 250)
    draw = ImageDraw.Draw(img)
    _emblem(draw, 330, 330, 200)
    body = _font(46)
    lines = [
        ("JUSTICE DE PAIX DU CANTON DE HUY", _font(54, bold=True)),
        ("", body),
        ("Procès-verbal de conciliation du 6 février 2024", _font(48, bold=True)),
        ("", body),
        ("Comparaissent volontairement :", body),
        ("Monsieur Luc FRANSSEN, domicilié Rue du Pont 8, 4500 Huy,", body),
        ("GSM 0496/55.21.08, et Madame Anne LEBRUN, domiciliée", body),
        ("Avenue des Ardennes 31, 4500 Huy.", body),
        ("", body),
        ("Les parties déclarent se concilier comme suit : Monsieur", body),
        ("FRANSSEN versera à Madame LEBRUN la somme de 1.200 euros", body),
        ("en douze mensualités sur le compte BE50 0682 1234 5601.", body),
        ("", body),
        ("Le greffier,", body),
        ("B. Hardy", body),
    ]
    y = 700
    for text, font in lines:
        if text:
            draw.text((220, y), text, font=font, fill=15)
        y += 78
    # Tampon du greffe (cadre + numéro de rôle)
    draw.rectangle((1500, 2700, 2240, 2990), outline=40, width=8)
    draw.text((1560, 2730), "GREFFE", font=_font(52, bold=True), fill=40)
    draw.text((1560, 2830), "RG 21/123/A", font=_font(66, bold=True), fill=40)
    _signature(draw, 240, 1900, scale=1.6)
    return _png(img)


# ---------------------------------------------------------------------------
# Sources des documents PDF
# ---------------------------------------------------------------------------

JUGEMENT_FAMILLE = """
= TRIBUNAL DE PREMIÈRE INSTANCE DE LIÈGE
= Division Namur - Tribunal de la famille
= 3e chambre
Jugement du 14 mars 2024
R.G. n° 23/4521/A - Rép. n° 2024/1187
## EN CAUSE DE :
Monsieur Jean-Marc DUPONT, né à Dinant le 3 février 1981, RN 81.02.03-123.47, domicilié rue des Glycines 14, 5000 Namur, GSM 0475/12.34.56, jm.dupont@exemple.be,
partie demanderesse, ayant pour conseil Me Isabelle LECLERCQ, avocate au barreau de Namur.
## CONTRE :
Madame Sophie DUPONT-MARCHAL, née à Ciney le 21 août 1983, NN 83082112475, domiciliée avenue Reine Astrid 102 bte 3, 5000 Namur, tél. (081) 22 33 44,
partie défenderesse, ayant pour conseil Me P. Verhoeven, avocat au barreau de Namur.
## EN PRÉSENCE DE :
l'enfant Lucas DUPONT, né le 12 avril 2012, et l'enfant Emma DUPONT, née le 5 septembre 2015.
## I. Procédure
> Le tribunal a pris connaissance des pièces de la procédure, notamment de la requête déposée au greffe le 4 octobre 2023 et des conclusions des parties. Les parties ont été entendues à l'audience publique du 15 février 2024, en présence de Madame la substitut du procureur du Roi C. Wauters, qui a rendu un avis oral.
## II. Faits
> Les parties se sont mariées le 7 juillet 2010. De leur union sont nés deux enfants. Elles se sont séparées le 1er mars 2023. Monsieur DUPONT a quitté le domicile conjugal pour s'installer temporairement chez son frère, Monsieur Thomas DUPONT. Il travaillait comme technicien pour la SRL Bati-Meuse jusqu'à son licenciement en juin 2023.
> Madame DUPONT-MARCHAL expose que les enfants résident principalement auprès d'elle depuis la séparation et que les parties ont elles-mêmes convenu, dans un premier temps, d'un hébergement accessoire chez le père un week-end sur deux. Elle sollicite une contribution alimentaire de 250 euros par mois et par enfant, à verser sur son compte BE97 0012 3456 7890.
@interligne 2
## III. Discussion
a. Quant à l'hébergement des enfants
> En vertu de l'article 374, § 2, de l'ancien Code civil, le tribunal examine prioritairement la possibilité de fixer l'hébergement de manière égalitaire. Les parents eux-¦mêmes reconnaissent que les enfants entretiennent une relation de qualité avec chacun d'eux. Les modalités conven¬tionnelles proposées par Monsieur DUPONT (ci-après : ¦ le père) tiennent compte de l'horaire scolaire.
b. Quant à la contribution alimentaire
1. Les revenus du père ont diminué depuis son licenciement : il perçoit des allocations de chômage de l'ONEM depuis le mois de juillet 2023.
2. Les besoins des enfants, âgés de 11 et 8 ans, sont évalués à 410 euros par mois et par enfant, conformément à la méthode Renard.
> Compte tenu des facultés respectives des parties, il y a lieu de fixer la contribution du père à 180 euros par mois et par enfant, indexée, à partir du 1er avril 2024, et payable avant le 5 de chaque mois.
@interligne 1
@page
## PAR CES MOTIFS,
> LE TRIBUNAL, statuant contradictoirement, en application de la loi du 15 juin 1935 sur l'emploi des langues en matière judiciaire,
> Dit que l'hébergement des enfants Lucas et Emma sera exercé de manière égalitaire, selon le système d'une semaine sur deux, du vendredi à la sortie de l'école au vendredi suivant ;
> Condamne Monsieur Jean-Marc DUPONT à payer à Madame Sophie DUPONT-MARCHAL une contribution alimentaire de 180 euros par mois et par enfant, sur le compte BE97 0012 3456 7890 ;
> Ainsi jugé et prononcé en audience publique de la 3e chambre du tribunal de la famille de Namur, le 14 mars 2024, où étaient présents : Madame A. Gérard, juge, et Madame N. Lambert, greffier.
|| Le greffier, || La juge,
|| N. Lambert || A. Gérard
"""

CITATION = """
= CITATION À COMPARAÎTRE
= devant la Justice de paix du canton de Namur 1
L'an deux mille vingt-quatre, le douze avril,
À LA REQUÊTE DE :
la SRL Immo-Mosane, dont le siège est établi rue Grandgagnage 21, 5000 Namur, inscrite à la Banque-Carrefour des Entreprises sous le numéro 0456.789.133, représentée par son gérant, Monsieur Olivier PIRSON ;
Nous, Christophe LAMBERT, huissier de justice à la résidence de Namur, y demeurant rue Godefroid 5, soussigné,
AVONS CITÉ :
Monsieur Kevin RENARD, né à Charleroi le 30 juillet 1985, de nationalité belge, RN 85.07.30-033.28, domicilié chaussée de Louvain 245, 5004 Bouge, où étant et parlant à sa personne ainsi déclaré,
À COMPARAÎTRE le mardi 21 mai 2024 à 9 heures devant la Justice de paix du canton de Namur 1.
POUR :
> Entendre condamner le cité à payer à la requérante la somme de 3.450 euros à titre d'arriérés de loyers pour le bien situé rue de Fer 18, 5000 Namur, outre les intérêts au taux légal. La requérante expose que Monsieur RENARD a cessé tout paiement depuis le mois de novembre 2023 et qu'un véhicule immatriculé 1-ABC-123 lui appartenant stationne sur l'emplacement privatif de l'immeuble.
> Les paiements pourront être effectués sur le compte BE31 3630 1234 5678 de la requérante.
Dont acte. Coût : 145,67 euros.
|| || C. LAMBERT
"""

# ---------------------------------------------------------------------------
# Documents Word
# ---------------------------------------------------------------------------


def _docx_bytes(doc: Document) -> bytes:
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def conclusions_docx() -> bytes:
    doc = Document()
    section = doc.sections[0]
    section.header.paragraphs[0].text = (
        "Cabinet Leclercq & Associés – Avenue de la Toison d'Or 52, 1060 Bruxelles – "
        "Tél. 02/538.12.34 – i.leclercq@leclercq-avocats.be"
    )
    section.footer.paragraphs[0].text = "Conclusions de synthèse – R.G. 2023/AR/1452"
    for text in ("COUR D'APPEL DE LIÈGE", "Chambre de la famille (10e chambre)", "CONCLUSIONS DE SYNTHÈSE"):
        p = doc.add_paragraph(text)
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(
        "POUR : Madame Véronique HENROTTE, née le 14/06/1975, domiciliée Rue Saint-Gilles 120, "
        "4000 Liège, appelante, ayant pour conseil Me Isabelle LECLERCQ, avocate."
    )
    doc.add_paragraph(
        "CONTRE : Monsieur Patrick GILSON, RN 75.06.14-002.31, intimé, ayant pour conseil "
        "Me Marc-Antoine DE WOLF, avocat, dont le cabinet est établi Boulevard d'Avroy 38, 4000 Liège."
    )
    doc.add_heading("I. Les faits", level=1)
    for text in (
        "Les parties ont vécu ensemble de 2008 à 2021. De leur relation est née Chloé GILSON, "
        "le 12 mai 2010.",
        "Depuis la séparation, l'enfant réside chez sa mère à Liège. Monsieur GILSON travaille "
        "pour la SA Ethias et perçoit un revenu net de 3.150 euros par mois.",
        "Le premier juge a fixé la contribution alimentaire à 220 euros par mois. Madame HENROTTE "
        "a interjeté appel par requête déposée le 3 juillet 2023.",
    ):
        doc.add_paragraph(text, style="List Number")
    doc.add_heading("II. Discussion", level=1)
    doc.add_paragraph(
        "Selon une jurisprudence constante, la contribution est fixée en fonction des facultés "
        "des parents (Cass., 18 octobre 2019, C.18.0453.F ; C. const., 12 mai 2016, n° 70/2016). "
        "La Cour européenne des droits de l'homme rappelle que l'intérêt de l'enfant prime "
        "(Cour eur. D.H., arrêt Marckx c. Belgique du 13 juin 1979 ; voir aussi J.T., 2017, p. 345)."
    )
    doc.add_paragraph(
        "L'attestation de Madame F.M., voisine des parties, confirme que l'enfant passe ses "
        "week-ends chez sa mère. Le CPAS de Liège et le SPF Finances ont été interrogés."
    )
    for text in (
        "les frais scolaires (pièce 4) ;",
        "les frais médicaux non remboursés par la mutualité (pièce 5).",
    ):
        doc.add_paragraph(text, style="List Bullet")
    doc.add_paragraph("Inventaire des pièces :")
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, ("N°", "Pièce", "Date")):
        cell.text = text
    for row in (
        ("1", "Acte de naissance de l'enfant Chloé GILSON", "12.05.2010"),
        ("2", "Fiches de paie de Monsieur GILSON", "2023"),
        ("3", "Extrait de compte BE68 5390 0754 7034", "01.06.2023"),
    ):
        cells = table.add_row().cells
        for cell, text in zip(cells, row):
            cell.text = text
    doc.add_paragraph(
        "PAR CES MOTIFS, plaise à la Cour de réformer le jugement entrepris et de condamner "
        "Monsieur Patrick GILSON à payer une contribution alimentaire de 350 euros par mois."
    )
    doc.add_paragraph("Liège, le 2 février 2024.")
    doc.add_paragraph("Pour l'appelante, son conseil, Me I. Leclercq")
    return _docx_bytes(doc)


def acte_notarie_docx() -> bytes:
    doc = Document()
    doc.add_paragraph("Rép. n° 2024/3567")
    doc.add_paragraph("VENTE").alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(
        "L'AN DEUX MILLE VINGT-QUATRE. Le quinze mars. Par-devant Nous, Maître Anne-Catherine "
        "DEVILLERS, notaire à la résidence de Namur,"
    )
    doc.add_paragraph("ONT COMPARU :")
    doc.add_paragraph(
        "Monsieur Pierre DELVAUX, né à Uccle le 2 décembre 1958, numéro national "
        "58.12.02-131.44, et son épouse Madame Martine GOOSSENS, née à Ixelles le 19 avril 1961, "
        "numéro national 61.04.19-142.15, domiciliés ensemble à 5100 Jambes, rue de Dave 112. "
        "Mariés sous le régime de la séparation de biens aux termes de leur contrat de mariage "
        "reçu par le notaire Jacques MOREAU, le 3 mai 1985. Ci-après dénommés « le vendeur ».",
        style="List Number",
    )
    doc.add_paragraph(
        "Monsieur Benoît NIZET, né à Namur le 8 janvier 1990, numéro national 90.01.08-077.28, "
        "célibataire, domicilié à 5000 Namur, rue Saint-Nicolas 9, GSM +32 471 23 45 67. "
        "Ci-après dénommé « l'acquéreur ».",
        style="List Number",
    )
    doc.add_paragraph(
        "Lesquels comparants Nous ont requis d'acter que le vendeur vend à l'acquéreur, qui "
        "accepte, le bien suivant : une maison d'habitation sise rue de Dave 112, cadastrée "
        "section B, numéro 345 F 2, pour une contenance de 4 ares 20 centiares."
    )
    doc.add_paragraph(
        "PRIX : La vente est consentie et acceptée pour le prix de 285.000 euros, payé par "
        "virement du compte BE06 7512 3456 7890 de l'acquéreur, ouvert auprès de la banque ING."
    )
    doc.add_paragraph(
        "DONT ACTE. Fait et passé à Namur, en l'étude, date que dessus. Après lecture intégrale "
        "et commentée, les comparants ont signé avec Nous, notaire."
    )
    return _docx_bytes(doc)


def courrier_docx() -> bytes:
    doc = Document()
    doc.sections[0].header.paragraphs[0].text = (
        "Maître Sophie RENIER – Avocate – Rue de Bruxelles 61, 5000 Namur – T. 081/22.33.45 – "
        "s.renier@renier-avocats.be – TVA BE 0876.543.270"
    )
    doc.add_paragraph("Namur, le 12 juin 2024")
    doc.add_paragraph("Maître Laurent DECHAMPS\nAvocat\nPlace Saint-Lambert 14\n4000 Liège")
    doc.add_paragraph("Concerne : DUPUIS / MERTENS – R.G. 24/789/A – Tribunal de la famille de Liège")
    doc.add_paragraph("Cher Confrère,")
    doc.add_paragraph(
        "Je fais suite à votre courrier du 3 juin 2024 concernant le dossier de ma cliente, "
        "Madame Claire DUPUIS, que vous savez opposée à votre client, Monsieur Hugo MERTENS."
    )
    doc.add_paragraph(
        "L'audience est fixée au 25 juin 2024 à 9 h 00. Vous trouverez ci-joint l'attestation "
        "de Madame Jeanne DUPUIS, mère de ma cliente, ainsi que le relevé des frais exposés pour "
        "l'enfant Noah MERTENS."
    )
    doc.add_paragraph("Veuillez agréer, Cher Confrère, l'expression de mes sentiments dévoués.")
    doc.add_picture(io.BytesIO(signature_png()), width=Inches(1.8))
    doc.add_paragraph("Sophie RENIER")
    return _docx_bytes(doc)


# ---------------------------------------------------------------------------
# Textes bruts
# ---------------------------------------------------------------------------

BAIL = """CONTRAT DE BAIL DE RÉSIDENCE PRINCIPALE
(décret wallon du 15 mars 2018 relatif au bail d'habitation)

ENTRE :
Madame Nathalie VANDENBERGHE, domiciliée Kerkstraat 12 bus 3, 9000 Gent, tél. 09 223 45 67,
ci-après dénommée « le bailleur »,

ET :
Monsieur Julien MARTIN, né le 4 mars 1994, et Madame Laura PETIT, née le 22 novembre 1995,
ci-après dénommés « le preneur ».

Article 1 – Objet
Le bailleur donne en location au preneur un appartement situé Avenue Louise 54 bte 3, 1050 Ixelles,
comprenant deux chambres, une cuisine équipée et une cave.

Article 2 – Durée
Le bail est conclu pour une durée de trois ans prenant cours le 1er septembre 2023.

Article 3 – Loyer
Le loyer mensuel est fixé à 950 euros, payable par anticipation avant le 5 de chaque mois sur le
compte IBAN BE68 5390 0754 7034 du bailleur.

Article 4 – Garantie locative
Une garantie de deux mois de loyer est constituée sur un compte bloqué ouvert auprès de Belfius.

Article 5 – Contacts
En cas d'urgence, le preneur peut joindre Madame VANDENBERGHE au 0476 98 76 54 ou par
courriel à nathalie.vdb@exemple.be. Monsieur MARTIN est joignable au +33 6 12 34 56 78.

Fait à Bruxelles, le 1er septembre 2023, en trois exemplaires.

Le bailleur,                         Les preneurs,
N. Vandenberghe                      J. Martin        L. Petit
"""

PV_POLICE = """ZONE DE POLICE NAMUR CAPITALE
PROCÈS-VERBAL D'AUDITION
PV n° NA.55.L1.004521/2024

L'an 2024, le 3 mai à 14 h 20, nous, Inspecteur principal Marc VANDAMME, en fonction au
commissariat central, entendons la personne suivante :

Nom : BOUZIANE
Prénom : Karim
Né le : 17/11/1990 à Liège
Nationalité : marocaine
Adresse : Rue Ernest Solvay 56, 4000 Liège
Carte d'identité n° 592-1234567-89
GSM : +32 471 23 45 67
E-mail : k.bouziane@exemple.com

Déclaration :
« Le 2 mai 2024 vers 23 heures, je circulais avec mon véhicule immatriculé 2-XYZ-789 sur
l'autoroute E411 à hauteur de Wavre. Mon ami Yannick était passager. Un véhicule a freiné
brusquement devant moi. L'autre conducteur, une dame que je ne connais pas, est sortie de
son véhicule et m'a insulté. Je n'ai rien d'autre à déclarer. »

Lecture faite, persiste et signe.

K. Bouziane                         Inspecteur principal M. VANDAMME
"""


# ---------------------------------------------------------------------------
# Génération
# ---------------------------------------------------------------------------

def scan_pdf() -> bytes:
    pdf = fitz.open()
    page = pdf.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_image(page.rect, stream=scan_page_png())
    return pdf.tobytes(garbage=3, deflate=True)


def build() -> dict[str, bytes]:
    return {
        "jugement_famille.pdf": Typesetter(
            footer="R.G. n° 23/4521/A - Jugement du 14 mars 2024").render(JUGEMENT_FAMILLE),
        "citation.pdf": Typesetter(header_numbers=False).render(CITATION),
        "conclusions.docx": conclusions_docx(),
        "acte_notarie.docx": acte_notarie_docx(),
        "courrier.docx": courrier_docx(),
        "bail.txt": BAIL.encode("utf-8"),
        "pv_police.txt": PV_POLICE.encode("utf-8"),
        "scan_tampon.pdf": scan_pdf(),
        "carte_identite.png": id_card_png(),
        "logo.png": logo_png(),
        "signature.png": signature_png(),
    }


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, data in build().items():
        (OUT / name).write_bytes(data)
        print(f"{name:24s} {len(data):>8d} octets")
