# Anonymiseur de Documents Juridiques

Une application web locale pour l'anonymisation automatique de documents juridiques et de textes libres, conçue pour les professionnels du droit.

## Fonctionnalités

- **Anonymisation de Fichiers** : `.txt`, `.docx`, `.pdf` et images (`.png`, `.jpg`, `.tif`, `.bmp`, `.gif`, `.webp`).
  - Texte, tableaux, en-têtes et pieds de page, zones de texte, notes de bas de page (Word) ; champs de formulaire et commentaires (PDF).
  - **OCR** : le texte contenu dans les images (captures, cartes d'identité, scans, pages de PDF scannées…) est lu automatiquement puis anonymisé. Il est encadré dans le résultat par
    `--- Texte extrait d'une image (attention) ---` … `--- Fin du texte extrait de l'image ---`, car l'OCR peut contenir des erreurs : à relire.
  - Déposez jusqu'à 8 fichiers simultanément (max 10 Mo chacun). Le résultat est toujours un fichier `.txt`.
- **Anonymisation de Texte Libre** : Collez un texte directement dans l'interface pour une anonymisation instantanée.
- **Entités Détectées** (Belgique et France) :
  - Personnes, numérotées de façon cohérente dans tout le document (`[PERSONNE_1]`, `[PERSONNE_2]`…) : « Monsieur Jean DUPONT », « M. DUPONT », « Dupont » et « Jean » deviennent la même personne. Noms en capitales non identifiés : `[Nom propre]`.
  - Adresses postales belges, françaises et néerlandophones (`[ADRESSE]`) : « Rue Petit Bioleux 18, 4120 Neupré », « 24 rue des Acacias, 69003 Lyon », « Kerkstraat 12 bus 3, 9000 Gent » ; lieux (`[LIEU]`)
  - Numéro de registre national / BIS (`[REGISTRE_NATIONAL]`, clé modulo 97 vérifiée), carte d'identité (`[CARTE_IDENTITÉ]`), passeport (`[PASSEPORT]`), NIR français (`[NIR]`)
  - Dates de naissance (`[DATE_NAISSANCE]`) — les autres dates sont conservées pour garder la chronologie —, nationalité (`[NATIONALITÉ]`)
  - Coordonnées : e-mails, téléphones belges / français / internationaux (`[EMAIL]`, `[TÉLÉPHONE]`)
  - Données bancaires : IBAN de tous pays (`[IBAN]`), anciens n° de compte belges, cartes bancaires (`[CARTE_BANCAIRE]`)
  - Entreprises (`[SOCIÉTÉ]`), n° BCE/TVA (`[NUMÉRO_ENTREPRISE]`, `[TVA]`), SIRET/SIREN, plaques d'immatriculation (`[PLAQUE]`), URL, adresses IP, n° de rôle /FA
  - Champs de formulaire : « Nom : … », « Adresse : … », « N° national : … », « Date de naissance : … »
  - Les intitulés juridiques (« PAR CES MOTIFS », « TRIBUNAL DE PREMIÈRE INSTANCE »), juridictions, codes et lois restent lisibles.

## Architecture technique

- **Frontend** : Next.js (App Router), React, Tailwind CSS
- **Backend NLP** : Python, Flask, Presidio (Microsoft) avec deux moteurs de reconnaissance d'entités au choix :
  - **CamemBERT-NER** (`Jean-Baptiste/camembert-ner`, via `transformers`) — plus précis sur le français, recommandé en local.
  - **spaCy** (`fr_core_news_md`) — léger, utilisé sur Vercel où CamemBERT ne rentre pas dans une fonction serverless.
- **Manipulation de fichiers** : PyMuPDF (`fitz`) pour les PDF, `python-docx` pour Word, Pillow pour les images
- **OCR** : [Tesseract](https://github.com/tesseract-ocr/tesseract) (français, néerlandais, anglais), appelé localement — aucune donnée envoyée à un tiers. Redressement automatique des images pivotées.
- **Code** : `api/nlp_engine.py` (chaîne d'anonymisation), `api/recognizers.py` (règles belges / françaises), `api/ocr.py` (OCR), `api/anonymize_file.py` (extraction des fichiers)

### Choix du moteur (`ANON_NLP_BACKEND`)

Le backend est piloté par la variable d'environnement `ANON_NLP_BACKEND` :

| Valeur | Moteur | Usage |
|---|---|---|
| `transformers` | CamemBERT-NER | Local (défaut hors Vercel) |
| `spacy` | spaCy `fr_core_news_md` | Vercel (défaut si la variable `VERCEL` est présente) |

### Autres réglages (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `ANON_SCORE_THRESHOLD` | `0.5` | Seuil de confiance minimal — l'augmenter réduit la sur-anonymisation |
| `ANON_OCR_SCORE_THRESHOLD` | `0.4` | Seuil appliqué au texte lu par OCR (plus permissif : l'OCR fait baisser les scores) |
| `ANON_PSEUDONYMS` | `1` | `0` pour remplacer toutes les personnes par `[PERSONNE]` au lieu de `[PERSONNE_1]`, `[PERSONNE_2]`… |
| `ANON_OCR_ENABLED` | `1` | `0` pour désactiver l'OCR |
| `ANON_OCR_LANGS` | `fra+nld+eng` | Langues Tesseract (seules celles installées sont utilisées) |
| `ANON_OCR_MAX_PAGES` | `60` | Nombre maximal d'images / pages scannées lues par fichier |
| `ANON_OCR_WORKERS` | `4` | Nombre d'images lues en parallèle |
| `TESSERACT_CMD` | — | Chemin du binaire `tesseract` s'il n'est pas dans le `PATH` |

`GET /api/health` indique le moteur NER actif et si l'OCR est opérationnel.

## Prérequis

- Node.js (v18+)
- Python 3.11+
- Tesseract OCR (pour lire le texte des images) :
  - macOS : `brew install tesseract tesseract-lang`
  - Ubuntu / Debian : `sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-nld`

  Sans Tesseract, l'application fonctionne mais signale les images non analysées.

## Installation locale

1. **Installer les dépendances frontend :**
   ```bash
   npm install
   ```

2. **Créer et activer un environnement virtuel Python :**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Installer les dépendances backend :**

   Pour utiliser le moteur **CamemBERT-NER** (recommandé en local) :
   ```bash
   pip install -r requirements-local.txt
   ```
   > Au premier lancement, le modèle `Jean-Baptiste/camembert-ner` (~440 Mo)
   > est téléchargé une seule fois dans `~/.cache/huggingface`, puis utilisé
   > **hors-ligne**. Aucune donnée n'est envoyée à un tiers à l'usage.

   Pour utiliser uniquement le moteur **spaCy** (plus léger, sans `torch`) :
   ```bash
   pip install -r requirements.txt
   export ANON_NLP_BACKEND=spacy
   ```

## Développement

1. **Démarrer le backend interactif (Flask) :**
   Le serveur tournera sur le port `5328`.
   ```bash
   npm run dev:api
   ```

2. **Démarrer le frontend (Next.js) :**
   Sur un autre terminal, lancez le frontend :
   ```bash
   npm run dev
   ```

3. Ouvrez http://localhost:3000 dans votre navigateur.

## Tests

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. ./venv/bin/python -m pytest
```

`tests/test_recognizers.py` vérifie les règles (registre national, IBAN, adresses, noms…) sans charger de modèle ; `tests/test_anonymizer.py` teste la chaîne complète sur du texte, des fichiers TXT / DOCX / PDF et des images (tests OCR ignorés si Tesseract est absent).

## Déploiement production (Laravel Forge / VPS)

Sur un serveur dédié, il n'y a aucune limite de taille : le backend tourne avec
**CamemBERT-NER** (précision maximale). L'architecture est un reverse-proxy
Nginx devant deux processus :

```
Nginx (443)
  ├── /api/*  →  Gunicorn + CamemBERT   (127.0.0.1:5328)
  └── /       →  Next.js (next start)    (127.0.0.1:3000)
```

**1. Site Next.js (Forge → New Site, type Node.js)**
- Branch : `dev` — Mode : **Node.js server** — Server port : `3000`
- Package manager : `npm` — Build command : `npm run build`

**2. Backend Python (à ajouter après la création du site)**
- Installer Tesseract une seule fois (Forge → Server → Commands, ou SSH) :
  `sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-nld`
- Le déploiement (étape 4) crée un venv **stable** dans `…/venv` (hors releases)
  et y installe `requirements-prod.txt` + précharge CamemBERT.
- Daemon Forge (Site → **Processes** → New background process) :
  - Name : `anonymiser-api`
  - Command : `/home/forge/anonymiser.on-forge.com/venv/bin/gunicorn -c gunicorn.conf.py api.index:app`
  - Working directory : `/home/forge/anonymiser.on-forge.com/current` (valeur par défaut)
  - Processes : `1`
- Variable d'env : `ANON_NLP_BACKEND=transformers` (ou laisser l'auto-détection).
- ⚠️ Crée ce background process **après** un premier déploiement réussi (le venv
  doit déjà exister).

**3. Reverse-proxy** : copier le contenu de
[`deploy/nginx-anonymiser.conf`](deploy/nginx-anonymiser.conf) dans la config
Nginx du site (Forge → Site → Edit Files → Nginx Configuration).

**4. Déploiement** : utiliser [`scripts/deploy.sh`](scripts/deploy.sh) comme
script de déploiement Forge (build front + install Python + préchargement du
modèle).

> **RAM** : prévoir **≥ 2 Go** (torch + modèle CamemBERT ≈ 1 Go en mémoire).

## Confidentialité

Toute l'analyse est effectuée **localement**. Aucune donnée sensible n'est enregistrée ni envoyée à un tiers.


Pour lancer le projet en local :

Terminal 1
$ npm run dev:api

Terminal 2 
$ npm run dev