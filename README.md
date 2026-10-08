# Anonymiseur de Documents Juridiques

Une application web locale pour l'anonymisation automatique de documents juridiques et de textes libres, conçue pour les professionnels du droit.

## Fonctionnalités

- **Anonymisation de Fichiers** : `.txt`, `.docx`, `.pdf` et images (`.png`, `.jpg`, `.tif`, `.bmp`, `.gif`, `.webp`).
  - Texte, tableaux, en-têtes et pieds de page, zones de texte, notes de bas de page (Word) ; champs de formulaire et commentaires (PDF).
  - **OCR** : le texte contenu dans les images (captures, cartes d'identité, scans, pages de PDF scannées…) est lu automatiquement puis anonymisé. Il est encadré dans le résultat par
    `--- Texte extrait d'une image (attention) ---` … `--- Fin du texte extrait de l'image ---`, car l'OCR peut contenir des erreurs : à relire.
    Les logos, sceaux, signatures et photos sans texte ne produisent rien (filtre de qualité calibré sur Tesseract 4.1 et 5.x), mais les textes courts nets sont lus (tampon « RG 21/123/A », numéro de carte).
  - **Texte fidèle** : les PDF sont reconstruits ligne à ligne (paragraphes, titres et puces séparés, double interligne recollé, traits d'union conservés — « avocat-intermédiaire » —, césures recollées, colonnes de signatures séparées par une tabulation) ; les en-têtes et pieds de page répétés ne sont gardés qu'une fois et les numéros de page retirés. La numérotation automatique de Word (« 1. », « a) », puces) est restituée.
  - Déposez jusqu'à 8 fichiers simultanément (**max 100 Mo chacun**). Le résultat est toujours un fichier `.txt`.
  - **Suivi en direct** : progression de l'envoi, puis de chaque étape (lecture des pages, OCR image par image, anonymisation). Retirer un fichier en cours annule son traitement sur le serveur.
- **Anonymisation de Texte Libre** : Collez un texte directement dans l'interface pour une anonymisation instantanée.
- **Entités Détectées** (Belgique et France) — politique détaillée ci-dessous :
  - Personnes physiques (parties, avocats, magistrats, greffiers, experts, témoins, enfants), numérotées de façon cohérente dans tout le document (`[PERSONNE_1]`, `[PERSONNE_2]`…). L'initiale accolée au nom fait partie du masque (« N. Dupont » → `[PERSONNE_2]`). Deux personnes ne partagent jamais une étiquette : « P. Dupont » et « N. Dupont », ou « Monsieur Jean DUPONT » et « Madame Marie DUPONT », restent distincts ; « Madame DUPONT » est rattachée à l'épouse. Initiales seules désignant une personne (« P.V. et G.G., assistés de leur avocat », « Madame F.M. ») : masquées. Noms en capitales non identifiés : `[Nom propre]`.
  - Adresses postales belges, françaises et néerlandophones (`[ADRESSE]`) : « Rue Petit Bioleux 18, 4120 Neupré », « 24 rue des Acacias, 69003 Lyon », « Kerkstraat 12 bus 3, 9000 Gent », « Grand-Place 22 » ; lieux liés à une personne (`[LIEU]`) : « domicilié à Namur », « née à Ciney », « réside chez sa mère à Liège ».
  - Numéro de registre national / BIS (`[REGISTRE_NATIONAL]`, clé modulo 97 vérifiée). Tout numéro annoncé par **RN, R.N., NN, N.N., NISS** ou « registre national » est masqué quel que soit son format, même avec une faute de frappe (« RN 85.04.03-123.45 », « NN 82011512345 », « 85073003328 (RN) ») — la mention reste lisible : `RN [REGISTRE_NATIONAL]`. Carte d'identité (`[CARTE_IDENTITÉ]`), passeport (`[PASSEPORT]`), NIR français (`[NIR]`)
  - **Numéros de rôle** (`[NUMÉRO_RÔLE]`) : tout numéro juste avant ou juste après la mention **RG** ou **FA** (« R.G. n° 24/1234/A », « RG n°s 19/1111/A, 19/2222/A et 19/3333/A », « 2023/789 RG », « FA 25/456 »), ou soudé à elle (« 2024/FA/123 », « 22/321/FA »), « N° de rôle : … », « Numéros du rôle : 7407, 7409, 7410 et 7412 », « inscrites sous les numéros … du rôle », « l'affaire n° 7407 ». Un identifiant reconnu une fois (numéro de rôle, registre national, IBAN, téléphone…) est masqué partout où il réapparaît (« l'affaire 7407 »). Références de dossier (`[RÉFÉRENCE_DOSSIER]`) : n° de répertoire (« Rép. n° »), notice du parquet, procès-verbal, Portalis.
  - Dates de naissance (`[DATE_NAISSANCE]`) — les autres dates sont conservées pour garder la chronologie —, nationalité (`[NATIONALITÉ]`)
  - Coordonnées : e-mails (`[EMAIL]`), téléphones belges / français / internationaux (`[TÉLÉPHONE]`) dans tous leurs formats : « 0475/12.34.56 », « 04 223 45 67 », « (081) 22 33 44 », « 02.512.34.56 », « 06 12 34 56 78 », « +32 (0)4 223 45 68 », « 0032 4 … », « +32471234567 », « +33612345678 »…
  - Données bancaires : IBAN de tous pays (`[IBAN]`), anciens n° de compte belges, cartes bancaires (`[CARTE_BANCAIRE]`)
  - N° BCE/TVA (`[NUMÉRO_ENTREPRISE]`, `[TVA]`), SIRET/SIREN, plaques d'immatriculation (`[PLAQUE]`), URL, adresses IP
  - Champs de formulaire : « Nom : … », « Adresse : … », « N° national : … », « Date de naissance : … », et cartes d'identité (« Nom / Name » puis la valeur à la ligne)

### Politique d'anonymisation

| Toujours masqué | Lisible par défaut | Réglage |
|---|---|---|
| Noms de personnes physiques, initiales comprises | Juridictions et leur siège (« Tribunal de première instance de Liège, division Namur », « barreau de Bruxelles ») | — |
| Initiales seules désignant une personne (« P.V. et G.G. ») | Institutions publiques : « l'Union », « États membres », « Commission », « Conseil des ministres », SPF, « Autorité de protection des données », ordres d'avocats (« Orde van Vlaamse balies ») | `ANON_MASK_INITIALS=0` pour garder les initiales seules |
| Numéros de rôle et leurs réapparitions | Pays, régions, lieux sans lien avec une personne (« signée à Rome », « les faits se sont produits à Wavre », « Fait à Bruxelles ») | `ANON_MASK_PLACES=1` pour masquer tous les lieux (hors pays, sièges, institutions) |
| Registre national, téléphones, e-mails, IBAN, adresses, dates de naissance, plaques, cartes d'identité | Personnes morales privées (« SRL Bati-Meuse », « Belgian Association of Tax Lawyers ») — un nom de personne qu'elles contiennent reste masqué (« Cabinet [PERSONNE_1] & Associés ») | `ANON_MASK_ORGANIZATIONS=1` pour `[SOCIÉTÉ]` |
| Lieux liés à une personne (domicile, naissance, résidence) | Jurisprudence publique citée (« Michaud c. France », « Consob, C-481/19 »), sauf si le nom est aussi celui d'une partie | `ANON_MASK_CASE_LAW=1` pour `[AFFAIRE]` |
| | Lois, articles, codes, dates, numéros d'arrêts publics (« arrêt n° 103/2022 »), ECLI, n° d'affaire de la CJUE (« C-694/20 »), points d'arrêt (« B.64 ») : jamais altérés | — |

« M. » devant un nom seul (« M. Pâques ») est masqué avec le nom (initiale ou « Monsieur » : au pire la civilité disparaît, jamais une initiale ne fuit) ; devant un prénom et un nom (« M. Jean DUPONT »), il reste lisible. Les décisions sont prises pour tout le document : un même texte reçoit la même décision partout, quel que soit le découpage du NER.

## Architecture technique

- **Frontend** : Next.js (App Router), React, Tailwind CSS
- **Backend NLP** : Python, Flask, Presidio (Microsoft) et le modèle de reconnaissance d'entités **CamemBERT-NER** (`Jean-Baptiste/camembert-ner`, via `transformers`). Le modèle spaCy `fr_core_news_md` ne sert qu'au découpage en mots et au lexique français (son NER, moins précis, n'est plus utilisé).
- **Manipulation de fichiers** : PyMuPDF (`fitz`) pour les PDF, `python-docx` pour Word, Pillow pour les images
- **OCR** : [Tesseract](https://github.com/tesseract-ocr/tesseract) (français, néerlandais, anglais), appelé localement — aucune donnée envoyée à un tiers. Redressement automatique des images pivotées.
- **Code** : `api/nlp_engine.py` (chaîne d'anonymisation), `api/recognizers.py` (règles belges / françaises), `api/persons.py` (initiales, regroupement des personnes, pseudonymes), `api/public.py` (références publiques, institutions, pays, jurisprudence, lieux liés à une personne), `api/first_names.py` (prénoms courants : second détecteur de personnes, indépendant du NER), `api/ocr.py` (OCR et filtre de qualité), `api/anonymize_file.py` (extraction des fichiers, reconstruction de la mise en page PDF, numérotation Word), `api/index.py` (API Flask, flux de progression), `api/settings.py` (limites), `api/compute.py` (coordination NER / OCR), `src/lib/anonymizeFile.ts` (client d'envoi)
- **Confidentialité des journaux** : aucun contenu de document n'est écrit dans les logs (les erreurs internes ne sont journalisées que par leur type et leur emplacement).

### Performances et gros fichiers

- **Flux de progression** : `POST /api/anonymize_file?stream=1` renvoie un flux NDJSON (progression, file d'attente, résultat) avec un signal toutes les 10 s au plus : aucun proxy ni navigateur ne coupe un traitement de plusieurs minutes. Sans `?stream=1`, l'endpoint renvoie directement le `.txt` (scripts).
- **Mémoire bornée** : les pages scannées sont rendues au moment de leur OCR (jamais toutes en mémoire), pendant que l'OCR des précédentes se poursuit.
- **Calculs coordonnés** : le NER (PyTorch) est très sensible à la concurrence pour les cœurs. Mesures sur 4 cœurs : pendant un OCR, une inférence de 1,1 s en prenait 62 ; avec 4 threads, un seul cœur occupé ailleurs la faisait passer de 0,9 s à 7,7 s. Désormais, NER et OCR sont alternés à grain fin (`api/compute.py`), PyTorch laisse un cœur libre (1,2 s dans les deux cas) et ses threads n'attendent plus en boucle (`OMP_WAIT_POLICY=PASSIVE`).
- **File d'attente** : `ANON_MAX_PARALLEL_JOBS` fichiers traités à la fois par processus ; les suivants affichent « En file d'attente ». Un fichier dont le texte dépasse `ANON_MAX_TEXT_CHARS` est refusé dès la limite franchie, avec un message clair (jamais d'anonymisation partielle).
- **Post-traitement** en O(n log n) (index triés) : négligeable même pour des milliers de pages.

### Réglages (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `ANON_SCORE_THRESHOLD` | `0.5` | Seuil de confiance minimal — l'augmenter réduit la sur-anonymisation |
| `ANON_OCR_SCORE_THRESHOLD` | `0.4` | Seuil appliqué au texte lu par OCR (plus permissif : l'OCR fait baisser les scores) |
| `ANON_PSEUDONYMS` | `1` | `0` pour remplacer toutes les personnes par `[PERSONNE]` au lieu de `[PERSONNE_1]`, `[PERSONNE_2]`… |
| `ANON_MASK_INITIALS` | `1` | `0` pour laisser lisibles les initiales seules désignant une personne (« P.V. et G.G. ») |
| `ANON_MASK_ORGANIZATIONS` | `0` | `1` pour masquer les personnes morales privées (`[SOCIÉTÉ]`) ; les institutions publiques restent lisibles |
| `ANON_MASK_PLACES` | `0` | `1` pour masquer aussi les lieux sans lien avec une personne (pays, sièges de juridiction et institutions restent lisibles) |
| `ANON_MASK_CASE_LAW` | `0` | `1` pour masquer les noms des affaires de jurisprudence citées (`[AFFAIRE]`) |
| `ANON_OCR_ENABLED` | `1` | `0` pour désactiver l'OCR |
| `ANON_OCR_LANGS` | `fra+nld+eng` | Langues Tesseract (seules celles installées sont utilisées) |
| `ANON_OCR_MAX_PAGES` | `300` | Nombre maximal d'images / pages scannées lues par fichier (≈ 1,3 s par page A4 avec 4 cœurs) ; au-delà, les images sont signalées « non analysées » |
| `ANON_OCR_WORKERS` | `4` | Nombre d'images lues en parallèle |
| `ANON_OCR_TIMEOUT` | `90` | Délai maximal d'OCR par image (secondes) |
| `TESSERACT_CMD` | — | Chemin du binaire `tesseract` s'il n'est pas dans le `PATH` (absent, il est recherché à nouveau toutes les 60 s) |
| `ANON_MAX_FILE_MB` | `100` | Taille maximale d'un fichier (à répercuter dans `deploy/nginx-anonymiser.conf`, `next.config.ts` et `src/lib/anonymizeFile.ts`) |
| `ANON_MAX_TEXT_CHARS` | `5000000` | Longueur maximale du texte d'un document (≈ 2 000 pages) |
| `ANON_MAX_PARALLEL_JOBS` | `2` | Fichiers traités simultanément par processus (les suivants attendent) |
| `ANON_CORS_ORIGINS` | — | Origines autorisées à appeler l'API depuis un autre site (séparées par des virgules). Inutile pour l'interface fournie |
| `OMP_NUM_THREADS` | cœurs − 1 | Threads de calcul de PyTorch (NER) |
| `GUNICORN_THREADS` | `8` | Threads HTTP de Gunicorn (production) |

`GET /api/health` indique le moteur NER actif, si l'OCR est opérationnel et les limites en vigueur.

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

   ```bash
   pip install -r requirements.txt
   ```
   > Au premier lancement, le modèle `Jean-Baptiste/camembert-ner` (~440 Mo)
   > est téléchargé une seule fois dans `~/.cache/huggingface`, puis utilisé
   > **hors-ligne**. Aucune donnée n'est envoyée à un tiers à l'usage.

## Développement

1. **Démarrer le backend interactif (Flask) :**
   Le serveur tournera sur le port `5328`. Il ne se recharge pas automatiquement
   (cela chargerait le modèle NER deux fois) : relancez-le après une modification
   du code Python.
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

Contenu :

- `tests/test_recognizers.py` : règles (registre national, numéros de rôle, téléphones, IBAN, adresses, noms…) et absence de faux positifs, sans modèle ;
- `tests/test_persons.py` : initiales, « M. », initiales seules, pseudonymes sans collision (signatures, homonymes, famille portant le même nom) ;
- `tests/test_public_entities.py` : références publiques, institutions, pays, lieux, organisations, jurisprudence, cohérence dans le document ;
- `tests/test_pdf_layout.py` : fidélité du texte des PDF (paragraphes, puces, césures, colonnes, en-têtes, couche texte illisible) ;
- `tests/test_ocr_quality.py` : aucun texte inventé sur un logo, un sceau, une signature ou une photo ; textes courts lus (ignorés sans Tesseract) ;
- `tests/test_anonymizer.py` : chaîne complète sur du texte, des fichiers TXT / DOCX / PDF et des images, progression, annulation, limites ;
- `tests/test_api.py`, `tests/test_logging.py` : API (validation, erreurs, flux, annulation, file d'attente) et absence de contenu dans les journaux ; `tests/test_compute.py` : verrou NER / OCR ;
- `tests/test_corpus.py` : corpus d'évaluation (voir ci-dessous), aucune fuite tolérée.

### Corpus d'évaluation

`tests/corpus` contient l'arrêt n° 1/2024 de la Cour constitutionnelle et des documents belges fictifs (jugement du tribunal de la famille, conclusions, citation, procès-verbal, acte notarié, bail, courrier, scan avec tampon, carte d'identité, images sans texte), chacun annoté : ce qui doit être masqué, ce qui doit rester lisible, les mentions d'une même personne, les personnes à distinguer, la fidélité du texte. Mesure détaillée :

```bash
PYTHONPATH=. ./venv/bin/python scripts/evaluate_corpus.py             # tableau + détails
```

Indicateurs : taux de fuite (objectif 0 %), sur-anonymisation, cohérence des pseudonymes, fidélité du texte extrait (mots perdus ou altérés), déterminisme. Voir `tests/corpus/README.md`.

### Compatibilité Tesseract 4.1.1 (serveur Ubuntu 22.04)

Les tests OCR sont à exécuter aussi avec la version du serveur. Sur une machine plus récente, les paquets d'Ubuntu 22.04 peuvent être extraits sans installation (`tesseract-ocr` et `libtesseract4` 4.1.1-2.1build1, `liblept5`, `libtiff5`, `tesseract-ocr-fra|nld|eng|osd` 4.00~git30) ; les fichiers `*.traineddata` doivent être placés dans le dossier `tessdata` du paquet `tesseract-ocr` (qui contient aussi `configs/`), puis :

```bash
TESSERACT_CMD=/chemin/vers/tesseract-4.1.1 PYTHONPATH=. ./venv/bin/python -m pytest tests/test_ocr_quality.py tests/test_corpus.py
```

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
- Branch : `main` (ou la branche déployée) — Mode : **Node.js server** — Server port : `3000`
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
- ⚠️ Crée ce background process **après** un premier déploiement réussi (le venv
  doit déjà exister).

**3. Reverse-proxy** : copier le contenu de
[`deploy/nginx-anonymiser.conf`](deploy/nginx-anonymiser.conf) dans la config
Nginx du site (Forge → Site → Edit Files → Nginx Configuration).
⚠️ À refaire après cette mise à jour : la limite d'envoi passe à 100 Mo
(`client_max_body_size 105m`) et le suivi en direct exige `proxy_buffering off`.

**4. Déploiement** : utiliser [`scripts/deploy.sh`](scripts/deploy.sh) comme
script de déploiement Forge (build front + install Python + préchargement du
modèle).

**5. Après chaque déploiement : redémarrer Gunicorn.** Le code Python et le
modèle NER sont chargés une seule fois, au démarrage du processus
(`preload_app`) : tant que le background process n'est pas redémarré, l'ancien
code continue de tourner. Le script de déploiement le fait (`sudo supervisorctl
restart anonymiser-api:*`) ; sinon : Forge → Site → Processes → `anonymiser-api`
→ **Restart**, ou en SSH `sudo supervisorctl restart all`. Vérifier ensuite :

```bash
curl -s http://127.0.0.1:5328/api/health
# "ocr": {"available": true, "engine": "tesseract", "languages": ["fra", "nld", "eng"]}
```

Si `"available": false` : Tesseract ou la langue française manque
(`sudo apt-get install -y tesseract-ocr tesseract-ocr-fra tesseract-ocr-nld`).
Un Tesseract installé après le démarrage est désormais détecté sans
redémarrage (nouvelle vérification toutes les 60 s), mais le redémarrage reste
nécessaire pour toute mise à jour du code.

> **RAM** : prévoir **≥ 2 Go** (torch + modèle CamemBERT ≈ 1 Go en mémoire),
> **3 Go** pour traiter confortablement deux fichiers de 100 Mo en parallèle.

## Confidentialité

Toute l'analyse est effectuée **localement**. Aucune donnée sensible n'est enregistrée ni envoyée à un tiers.


Pour lancer le projet en local :

Terminal 1
$ npm run dev:api

Terminal 2 
$ npm run dev