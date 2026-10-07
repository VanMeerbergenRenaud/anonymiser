"""Configuration Gunicorn pour le backend d'anonymisation (production).

Lancement :
    gunicorn -c gunicorn.conf.py api.index:app

Le module ``api.index`` expose l'objet Flask ``app`` (le bloc ``__main__``
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
# partage la mémoire du modèle entre les workers.
#
# Un seul worker par défaut : le NER et l'OCR utilisent déjà tous les cœurs,
# et la coordination des calculs (api/compute.py) ainsi que la file d'attente
# des fichiers (ANON_MAX_PARALLEL_JOBS) s'appliquent au sein d'un processus.
preload_app = True
workers = int(os.environ.get("WEB_CONCURRENCY", "1"))

# Threads « gthread » : un fichier en cours de traitement occupe un thread
# pendant toute la durée de son flux de progression (plusieurs minutes pour
# un gros PDF scanné) ; les autres threads servent les autres requêtes
# (fichiers en file d'attente, texte, /api/health). Le calcul lui-même a lieu
# dans des threads dédiés, limités par ANON_MAX_PARALLEL_JOBS.
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))

# Avec gthread, `timeout` surveille la santé du worker (pas la durée d'une
# requête) : un long traitement n'est donc pas interrompu.
timeout = 300
graceful_timeout = 60

# Logs vers stdout/stderr (récupérés par le daemon Forge / supervisor).
accesslog = "-"
errorlog = "-"
loglevel = "info"
