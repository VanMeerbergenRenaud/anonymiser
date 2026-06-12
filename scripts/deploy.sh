#!/usr/bin/env bash
#
# Script de déploiement pour Laravel Forge (ou tout VPS).
# À coller dans le champ « Deploy Script » du site Forge, ou à exécuter par SSH
# depuis la racine du projet.
#
set -euo pipefail

cd "$(dirname "$0")/.."

# Branche de production (celle configurée dans Forge).
BRANCH="${DEPLOY_BRANCH:-dev}"

echo "→ Récupération du code (${BRANCH})"
git pull origin "${BRANCH}"

echo "→ Dépendances Python + CamemBERT (venv)"
python3 -m venv venv 2>/dev/null || true
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements-prod.txt

echo "→ Préchargement du modèle CamemBERT (téléchargé une seule fois)"
PYTHONPATH=. ANON_NLP_BACKEND=transformers ./venv/bin/python -c "import api.nlp_engine"

echo "→ Build du frontend Next.js"
npm ci
npm run build

echo "→ Redémarrage des daemons"
# Forge redémarre automatiquement ses daemons après le déploiement.
# Sinon, décommente et adapte les noms :
# sudo supervisorctl restart anonymiser-api anonymiser-web

echo "✓ Déploiement terminé"
