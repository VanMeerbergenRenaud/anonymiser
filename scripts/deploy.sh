#!/usr/bin/env bash
#
# Script de déploiement pour Laravel Forge (déploiement « zero-downtime »).
# À coller dans : Forge → Site → Deployments → Deploy Script.
#
# Forge récupère déjà le code et exécute ce script depuis le dossier de la
# release courante (.../current). On NE fait donc PAS de `git pull` ici.
#
set -euo pipefail

# Racine du site (sibling de "current" et "releases") et venv stable :
# le venv vit HORS des releases pour ne pas réinstaller torch à chaque déploiement.
SITE="/home/forge/anonymiser.on-forge.com"
VENV="${SITE}/venv"

cd "${SITE}/current"

echo "→ Frontend Next.js"
npm ci
npm run build

echo "→ Backend Python (venv stable + dépendances)"
python3 -m venv "${VENV}" 2>/dev/null || true
"${VENV}/bin/pip" install --upgrade pip
"${VENV}/bin/pip" install -r requirements-prod.txt

echo "→ Préchargement de CamemBERT (téléchargé une seule fois, caché dans ~/.cache)"
PYTHONPATH="${SITE}/current" ANON_NLP_BACKEND=transformers \
    "${VENV}/bin/python" -c "import api.nlp_engine"

echo "→ Redémarrage du background process (adapter le nom si besoin)"
sudo supervisorctl restart anonymiser-api:* 2>/dev/null || true

echo "✓ Déploiement terminé"
