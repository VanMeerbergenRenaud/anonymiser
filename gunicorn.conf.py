"""Configuration Gunicorn pour le backend d'anonymisation (production).

Lancement :
    gunicorn -c gunicorn.conf.py api.index:app

Le module ``api.index`` expose l'objet Flask ``app`` (le bloc ``app.run(...)``
n'est utilisé qu'en développement local via ``npm run dev:api``).
"""

import os

# Réseau : le backend écoute en local ; Nginx assure le reverse-proxy public
# (voir deploy/nginx-anonymiser.conf).
bind = "127.0.0.1:5328"

# api.index importe api.* → la racine du projet doit être sur le PYTHONPATH.
pythonpath = "."

# CamemBERT (~1 Go en RAM) est chargé une seule fois à l'import du moteur NLP.
# preload_app charge l'application dans le process maître AVANT le fork, ce qui
# partage la mémoire du modèle entre les workers. On garde peu de workers car
# le modèle est lourd ; ajustable via la variable d'env WEB_CONCURRENCY.
preload_app = True
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
threads = 2

# L'inférence et les gros fichiers peuvent être lents : timeout large.
timeout = 120
graceful_timeout = 30

# Logs vers stdout/stderr (récupérés par le daemon Forge / supervisor).
accesslog = "-"
errorlog = "-"
loglevel = "info"
