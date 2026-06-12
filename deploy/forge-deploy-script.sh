# Script de déploiement Laravel Forge (site Node.js + backend Python).
#
# À coller dans : Forge → Site → Deployments → Deploy Script.
# Il étend le script PM2 généré par Forge (frontend Next.js) avec
# l'installation du backend Python (Gunicorn + CamemBERT).
#
# Le venv vit dans .../venv (stable, hors releases) pour ne pas réinstaller
# torch à chaque déploiement. Le modèle CamemBERT est mis en cache dans
# ~/.cache/huggingface (téléchargé une seule fois).

$CREATE_RELEASE()

cd $FORGE_RELEASE_DIRECTORY

npm ci || npm install
npm run build

# --- Backend Python (anonymisation CamemBERT) ----------------------------
SITE="/home/forge/anonymiser.on-forge.com"
VENV="$SITE/venv"

python3 -m venv "$VENV" 2>/dev/null || true
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$FORGE_RELEASE_DIRECTORY/requirements-prod.txt"

# Préchargement du modèle (téléchargé une seule fois)
PYTHONPATH="$FORGE_RELEASE_DIRECTORY" ANON_NLP_BACKEND=transformers \
    "$VENV/bin/python" -c "import api.nlp_engine"
# -------------------------------------------------------------------------

$ACTIVATE_RELEASE()

# Ensure PM2 config exists...
if [ ! -f /home/forge/.pm2-conf/site-3241441.json ]; then
    mkdir -p /home/forge/.pm2-conf
    cat <<'EOF' > /home/forge/.pm2-conf/site-3241441.json
{
    name: "site-3241441",
    cwd: "/home/forge/anonymiser.on-forge.com/current",
    script: "./node_modules/next/dist/bin/next",
    args: "start --hostname 0.0.0.0 --port 3000",
    instances: "max",
    exec_mode: "cluster",
}
EOF
fi

# Start or reload the PM2 process...
pm2 start /home/forge/.pm2-conf/site-3241441.json || pm2 reload site-3241441 --update-env
pm2 save

# Redémarrer le backend Python pour charger le nouveau code (.../current)
sudo supervisorctl restart all || true
