# Guide des bonnes pratiques — AutoOrtho4XPlane

Ce guide complète [CLAUDE.md](../../CLAUDE.md). Il fixe la façon d'écrire et de modifier le code du fork. Il s'applique à toute contribution, humaine ou assistée.

## 1. Principes

1. **Mesurer d'abord.** Une optimisation sans mesure avant/après n'est pas acceptée. Une correction de bug s'appuie sur une preuve (ligne de log, test qui échoue, trace).
2. **Le simulateur passe avant AutoOrtho.** Chaque cycle CPU, chaque Mo de RAM et chaque E/S disque pris par AutoOrtho est pris à X-Plane. En cas de doute, on dégrade la qualité des textures plutôt que les FPS.
3. **Petits changements, un sujet à la fois.** Une branche = un problème. Pas de reformatage massif, pas de renommage gratuit : `getortho.py` fait 10 000 lignes et les conflits avec l'upstream coûtent cher.
4. **Compatibilité upstream.** Garder les signatures publiques, les clés de config et les formats de cache (`.dds`, `.ddm`, noms de fichiers JPEG) compatibles, ou fournir une migration.
5. **Échouer proprement.** Un échec de téléchargement ou de build doit produire une texture de repli, jamais un plantage du montage ni un `read()` bloqué indéfiniment.

## 2. Style Python

- Suivre le style du fichier modifié (PEP 8, 4 espaces, lignes ≤ 100 caractères de préférence).
- Annotations de type sur les nouvelles fonctions ; docstring courte expliquant le *pourquoi*, pas le *comment*.
- Constantes nommées en MAJUSCULES en tête de module plutôt que des nombres magiques (`0.05`, `6`, `1.0`).
- Pas de `except Exception: pass` silencieux dans du nouveau code : au minimum `log.debug("...: %s", exc)` hors chemin chaud, ou un compteur de stats.
- Lire la configuration une fois (à l'initialisation ou au rechargement) et la convertir au bon type ; ne pas reparser `CFG` à chaque appel (`isinstance(x, str)` + `float()` dans une fonction appelée par `read()`).
- Imports : respecter le double motif `autoortho.x` / `x` du fichier ; ne jamais importer un module sous deux noms dans un même processus (singletons dupliqués).
- Imports lourds (Flask, PySide6, requests, numpy) : pas au niveau module dans le code chargé par les workers si le worker n'en a pas besoin.

## 3. Journalisation

- Chemins chauds (`getattr`, `open`, `read`, boucles par chunk/mipmap) : pas de f-string dans `log.debug`. Utiliser `log.debug("msg %s", valeur)` ; si le calcul des arguments est coûteux, protéger par `if log.isEnabledFor(logging.DEBUG):`.
- Niveaux : `DEBUG` = diagnostic développeur ; `INFO` = événements de session (montage, connexion, résumé périodique) ; `WARNING` = anomalie récupérée qui mérite l'attention de l'utilisateur ; `ERROR` = fonctionnalité perdue.
- Un événement répétitif (chunk 404, repli) : un compteur dans les stats et un résumé périodique, pas une ligne par occurrence en `WARNING`.
- Chaque ligne émise par un worker doit être attribuable (nom de région ou PID dans le format).
- Défaut de la config : `INFO`. Les mesures de temps de chargement se font en `INFO`.

## 4. Concurrence

- **Pas d'attente active.** Remplacer `while cond: time.sleep(x)` par `threading.Event.wait(timeout)`, `Condition.wait_for` ou `concurrent.futures.wait`.
- **Verrous courts.** Aucune E/S, aucun appel réseau, aucune construction d'objet coûteuse sous un verrou global (`tc_lock`, `_lock` du cache). Construire hors verrou, publier sous verrou (double vérification).
- **Budget de threads global.** Threads Python × threads OpenMP par build ≤ cœurs logiques − réserve X-Plane (au moins 4 threads sur une machine 16 threads). Toujours passer `max_threads` explicite aux fonctions natives.
- **Priorités.** Live (X-Plane attend) > préchargement > maintenance (scan disque, migration, healing). Le travail de fond vérifie régulièrement `is_live_building()` et s'interrompt.
- **Arrêt.** Toute boucle de fond teste un `Event` d'arrêt et se termine en moins d'une seconde.
- **GIL.** Le travail CPU lourd va dans le code natif appelé via ctypes (qui relâche le GIL) ; éviter les boucles Python par pixel ou par bloc.

## 5. Multi-processus

- Considérer que le parent et chaque worker sont des programmes distincts. Pour partager un état :
  - données de vol : chaque worker écoute lui-même X-Plane (abonnement RREF léger) ou lit un état publié ;
  - données volumineuses (plan SimBrief) : fichier JSON dans `~/.autoortho-data` + numéro de génération dans le stats store ;
  - résultats de scans globaux : calculés par un seul processus, persistés sur disque, relus par les autres.
- Le proxy `aostats` fait un aller-retour TCP par appel : pas d'appel dans un chemin chaud, regrouper via `StatsBatcher`, espacer les sondages (≥ 5 s pour ce qui n'est pas critique).
- Écritures concurrentes sur le même fichier : nom temporaire unique (`.tmp.<pid>`) puis `os.replace`. Ne jamais supprimer un fichier « orphelin » qu'un autre processus est peut-être en train d'écrire (vérifier l'âge du fichier).
- Une seule instance d'AutoOrtho par utilisateur : verrou d'instance au démarrage.

## 6. Mémoire

- Ne pas allouer d'image RGBA pleine taille (8192² = 256 Mo) pour produire quelques lignes.
- Libérer explicitement les buffers natifs (`AoImage.close()`, `DDS.close()`, `with` quand disponible).
- Tout cache mémoire a une borne en octets et une politique d'éviction ; ne pas évincer ce qu'X-Plane vient d'ouvrir.
- Préférer `memoryview` / buffers pré-alloués aux concaténations de `bytes` dans les lectures.

## 7. Disque et réseau

- Scans de répertoire : `os.scandir` (les attributs sont fournis par Windows sans appel supplémentaire), jamais `os.walk` + `os.path.getsize` sur tout le cache.
- Pas d'écriture de métadonnées (`touch`, `utime`) à chaque accès en lecture ; mettre à jour l'accès de façon échantillonnée ou en mémoire.
- Résultats négatifs (fichier absent, 404, JPEG invalide) : les mettre en cache (mémoire pour la session, disque pour les 404 durables) avec une durée de validité.
- Requêtes réseau : timeout explicite, `Session` réutilisée, backoff exponentiel plafonné, respect des codes permanents.

## 8. Code natif (C / OpenMP)

- Tout nouveau `#pragma omp parallel` prend `num_threads(AODDS_OMP_THREADS(max_threads))`.
- Ne pas appeler `omp_set_num_threads` depuis une fonction exportée (effet de bord persistant sur le thread appelant) : utiliser la clause `num_threads`.
- Valider tailles et pointeurs côté Python avant l'appel ; aucune allocation non bornée côté C.
- Les binaires sont produits par la CI ; une modification C implique une PR qui déclenche `build_aopipeline.yml` et un test sur Windows.

## 9. Tests

- `pytest`, fichiers dans `autoortho/tests/test_<module>.py`. Pour limiter les conflits avec l'upstream, ajouter de nouveaux fichiers plutôt que modifier les siens.
- Importer comme les autres tests du dossier : `sys.path.insert(0, <dossier autoortho>)` puis `import getortho`, `from aopipeline.x import y`. La forme `from autoortho.x import y` casse dès qu'un autre test a placé le dossier `autoortho` en tête de `sys.path` (`autoortho` devient alors `autoortho.py`).
- Tester sans X-Plane ni réseau : simuler `datareftrack.dt`, les réponses HTTP et le système de fichiers (`tmp_path`). Extraire la décision à tester dans une petite fonction plutôt que de piloter tout `get_bytes`.
- Comportement inter-processus (verrous, fichiers partagés) : au moins un test avec un vrai sous-processus (`subprocess.Popen([sys.executable, "-c", ...])`), un test dans un seul processus ne prouve rien sur le comportement réel.
- Un `ProcessLock` n'est tenu que tant que l'objet vit : garder une référence (variable de module pour le verrou d'instance), sinon le ramasse-miettes ferme le fichier et relâche le verrou.
- Chaque bug corrigé reçoit un test de non-régression quand c'est faisable (ex. : un worker reçoit bien les données de vol ; un chunk 404 compte comme résolu).
- Avant de conclure qu'un test échoue à cause d'un changement, le relancer sur le code upstream intact (`git stash`) : plusieurs tests dépendent du réseau ou de POSIX.
- Benchmarks (`tests/bench_*.py`) : noter la machine et la config dans le compte rendu.

## 10. Revue avant de proposer un changement

- [ ] Le problème et la preuve sont décrits.
- [ ] Le changement tient dans un seul sujet.
- [ ] Aucun f-string de debug, attente active, E/S ou IPC ajouté dans un chemin chaud.
- [ ] Le comportement multi-processus a été pensé (parent, 6 workers).
- [ ] Budget de threads et priorités respectés.
- [ ] Tests passés en local et comparés à l'upstream intact (mêmes échecs, aucun nouveau).
- [ ] Mesure avant/après fournie, ou changement explicitement non mesurable.
- [ ] Compatibilité config/cache préservée ou migration fournie.
