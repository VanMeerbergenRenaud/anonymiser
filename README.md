# Anonymiseur de Documents Juridiques

Une application web locale pour l'anonymisation automatique de documents juridiques et de textes libres, conçue pour les professionnels du droit.

## Fonctionnalités

- **Anonymisation de Fichiers** : Supporte les formats `.txt`, `.docx` et `.pdf`.
  - Remplace les entités dans le texte et les tableaux.
  - Supprime automatiquement les images intégrées.
  - Déposez jusqu'à 5 fichiers simultanément (max 4.5 Mo chacun).
- **Anonymisation de Texte Libre** : Collez un texte directement dans l'interface pour une anonymisation instantanée.
- **Entités Détectées** : 
  - Noms et prénoms (`[PERSONNE]`)
  - Lieux et adresses (`[LIEU]`)
  - Dates et dates de naissance (`[DATE]`, `[DATE_NAISSANCE]`)
  - Coordonnées : emails, numéros de téléphone (`[EMAIL]`, `[TÉLÉPHONE]`)
  - Données bancaires : IBAN (`[IBAN]`), Cartes bancaires (`[CARTE_BANCAIRE]`)
  - Identifiants : Numéro de sécurité sociale (`[NIR]`), Numéro de rôle/FA (`[ROLE]`)
  - Entreprises (`[SOCIÉTÉ]`)

## Architecture technique

- **Frontend** : Next.js (App Router), React, Tailwind CSS
- **Backend NLP** : Python, Flask, Presidio (Microsoft) avec deux moteurs de reconnaissance d'entités au choix :
  - **CamemBERT-NER** (`Jean-Baptiste/camembert-ner`, via `transformers`) — plus précis sur le français, recommandé en local.
  - **spaCy** (`fr_core_news_md`) — léger, utilisé sur Vercel où CamemBERT ne rentre pas dans une fonction serverless.
- **Manipulation de fichiers** : PyMuPDF (`fitz`) pour les PDF, `python-docx` pour Word

### Choix du moteur (`ANON_NLP_BACKEND`)

Le backend est piloté par la variable d'environnement `ANON_NLP_BACKEND` :

| Valeur | Moteur | Usage |
|---|---|---|
| `transformers` | CamemBERT-NER | Local (défaut hors Vercel) |
| `spacy` | spaCy `fr_core_news_md` | Vercel (défaut si la variable `VERCEL` est présente) |

Le seuil de confiance minimal d'anonymisation est réglable via `ANON_SCORE_THRESHOLD` (défaut `0.5`) — l'augmenter réduit la sur-anonymisation.

## Prérequis

- Node.js (v18+)
- Python 3.11+

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
- Installer les dépendances : `pip install -r requirements-prod.txt`
- Daemon Forge (Server → Daemons) :
  - Command : `venv/bin/gunicorn -c gunicorn.conf.py api.index:app`
  - Directory : le dossier du site — User : `forge`
- Variable d'env : `ANON_NLP_BACKEND=transformers` (ou laisser l'auto-détection).

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