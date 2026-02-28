🏗️ Le Plan d'Architecture (Ce que l'IA va construire)
Frontend (Next.js + Tailwind CSS)
Une interface très minimaliste avec deux onglets : "Fichiers" et "Texte".
Zone Fichiers : Un espace de Drag & Drop limitant à 5 fichiers (.txt, .pdf, .docx). Dès le dépôt, une barre de chargement s'affiche, le fichier est envoyé au backend, et le fichier anonymisé se télécharge automatiquement.
Zone Texte : Un grand textarea pour coller du texte, un bouton "Anonymiser", et le résultat s'affiche en dessous avec un bouton "Copier".
Backend (Vercel Python API)
Moteur d'anonymisation : presidio-analyzer et presidio-anonymizer (avec le modèle linguistique fr_core_news_sm).
Règles de détection : Noms, prénoms, adresses, codes postaux, dates de naissance, numéros de téléphone, emails, numéros de sécurité sociale, comptes bancaires.
Traitement des formats :
TXT : Remplacement direct.
DOCX : Utilisation de python-docx pour parcourir les paragraphes, remplacer le texte et sauvegarder en gardant la mise en page.
PDF : Utilisation de PyMuPDF (fitz). Au lieu de casser la mise en page, il va détecter les coordonnées des mots sensibles et dessiner des rectangles noirs dessus (la vraie méthode de caviardage juridique), ou extraire le texte brut selon ce qui est le plus stable.