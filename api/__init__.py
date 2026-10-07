# api — Backend d'anonymisation de documents juridiques (Belgique / France)
#
# Réglages des threads de calcul de PyTorch (NER CamemBERT). Ils doivent être
# définis avant le chargement de PyTorch, donc à l'import du paquet ; une
# valeur déjà présente dans l'environnement est toujours respectée.

import os as _os


def _available_cpus() -> int:
    try:
        return len(_os.sched_getaffinity(0))  # cœurs réellement attribués (Linux)
    except AttributeError:  # macOS, Windows
        return _os.cpu_count() or 1


# Un cœur reste libre pour le reste de l'application. Mesure sur 4 cœurs :
# avec 4 threads, l'inférence prend 0,9 s au repos mais 7,7 s dès qu'un seul
# cœur est occupé (extraction d'un autre PDF, serveur web…) ; avec 3 threads,
# 1,2 s dans les deux cas.
_os.environ.setdefault("OMP_NUM_THREADS", str(max(1, _available_cpus() - 1)))

# Les threads OpenMP en attente rendent la main au lieu de boucler : sans
# coût mesurable quand le NER tourne seul, et sans effondrement des
# performances (jusqu'à ×50) quand d'autres calculs occupent les cœurs.
_os.environ.setdefault("OMP_WAIT_POLICY", "PASSIVE")
