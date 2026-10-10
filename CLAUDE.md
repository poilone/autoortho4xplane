# AutoOrtho4XPlane (fork poilone) — instructions développeur

Fork de `ProgrammingDinosaur/autoortho4xplane`. Objectifs du fork, par ordre de priorité :
1. Réduire le temps de chargement d'un vol dans X-Plane 12.
2. Corriger les bugs connus (voir « Bugs connus » plus bas).
3. Préserver les FPS : ne jamais voler de temps CPU au **thread principal d'X-Plane**.

Langue : échanges et documentation du fork en français ; code, commentaires et messages de log en anglais (convention du code existant).
Guide détaillé des bonnes pratiques : [docs/dev/bonnes-pratiques.md](docs/dev/bonnes-pratiques.md).
**Pour reprendre** : [docs/dev/plan.md](docs/dev/plan.md) (état des lots, mesures de référence, protocole de mesure en vol, mesures M1 à M15 du lot 2 et ce qui reste à faire).

## Dépôts et branches

- `origin` = `github.com/poilone/autoortho4xplane` (le fork, seul dépôt où l'on pousse, et uniquement à la demande de l'utilisateur).
- L'upstream (`ProgrammingDinosaur`) n'est pas configuré comme remote ; on le lit par URL : `git fetch https://github.com/ProgrammingDinosaur/autoortho4xplane.git develop:refs/remotes/upstream/develop`.
- Depuis le 2026-09-21, l'upstream `develop` contient la refonte « 3.0.0 improvements » (`514954b`). Le travail du fork part de ce code, pas de la 2.5 : branches de travail tirées de `upstream/develop`.

## Environnement

- Le poste de développement (chemins, matériel, release installée) est décrit dans `CLAUDE.local.md`, non versionné. Config utilisateur : `~/.autoortho` ; log : `~/.autoortho-data/logs/autoortho.log`.
- L'utilisateur peut être **en vol** pendant qu'on travaille : vérifier que X-Plane ne tourne pas et, s'il vole, rester léger (tests en priorité basse, pas de gros téléchargements).
- Python 3.14 et `uv`. Environnement de dev dans `.venv` (ignoré par git) :
  ```
  uv venv --python 3.14 .venv
  uv pip install --python .venv -r requirements.txt
  ```
  Sans PySide6 (lourd), les tests d'interface ne se chargent pas ; le reste fonctionne.
- Lancer depuis les sources : `.venv\Scripts\python -m autoortho` (WinFSP requis). Les DLL natives sont versionnées (`autoortho/aopipeline/lib/windows/`, `autoortho/aoimage/aoimage.dll`) ; on ne les recompile pas localement (CI MinGW : `.github/workflows/build_aopipeline.yml`).
- Tests (depuis la racine du dépôt) :
  - **Toujours isoler le répertoire personnel.** Importer `aoconfig` (donc presque tous les tests) réécrit `~/.autoortho` : `CFG = AOConfig()` sauvegarde la config au chargement, avec les clés et migrations du code testé, et certains tests écrivent dans `~/.autoortho-data`. C'est ainsi que la config de l'utilisateur a reçu des clés 3.0 le 27/09. Lancer pytest avec `USERPROFILE` (et `TEMP`/`TMP`) pointant vers un dossier temporaire, dans un processus dédié : `$env:USERPROFILE = "<dossier temporaire>"; .venv\Scripts\python -m pytest ...`.
  - `.venv\Scripts\python -m pytest --noconftest -q -p no:cacheprovider autoortho/tests/<fichier>.py` (`--noconftest` : le conftest importe PySide6).
  - `cd autoortho; ..\.venv\Scripts\python -m pytest --noconftest -q test_getortho.py test_pydds.py test_downloader.py`.
  - `test_downloader.py` réécrit `autoortho/testfiles/.release_info` (releases de scènes relues sur GitHub) : le restaurer après la suite (`git checkout -- autoortho/testfiles/.release_info`).
  - Lancé en un seul processus, `autoortho/tests` produit des erreurs d'import croisées (`autoortho` résolu en `autoortho.py`) : lancer **chaque fichier dans son propre processus**.
  - Toujours comparer à la base intacte, idéalement dans un worktree temporaire (`git worktree add --detach <dossier> <commit>`) lancé en parallèle : sous Windows, une cinquantaine de tests échouent déjà (tests POSIX, tests réseau instables comme `test_maptype_chunk`, `store_from_file`, PySide6 absent).
- Ne jamais modifier `~/.autoortho`, `scenery_packs.ini`, le cache ou les processus de l'utilisateur sans son accord explicite.

### Construire l'exécutable Windows (comme la CI)

La CI (`build_release.yml`) installe `requirements.txt` + `requirements-build.txt` sous Python 3.14, lance `python -m PyInstaller --noconfirm --clean --log-level INFO autoortho.spec` depuis la racine et zippe `dist\autoortho` sous le nom `autoortho_release`. En local, toujours depuis un commit précis, jamais depuis l'arbre de travail :
1. `git archive -o <tmp>\src.tar <commit>`, puis `tar -xf` dans `build\<nom>` (ignoré par git). Seul le lien symbolique `docs/README.md` échoue, sans effet.
2. Écrire `autoortho\.version` (ex. `3.0.0-fork-batch1-f30a596`). Le contrôle de mise à jour en extrait un numéro semver : une version inférieure à l'upstream proposerait une « mise à jour ».
3. `uv pip install --python .venv -r requirements.txt -r requirements-build.txt`.
4. Depuis `build\<nom>`, avec `USERPROFILE` isolé (l'analyse importe `aoconfig`) : `<dépôt>\.venv\Scripts\python -m PyInstaller --noconfirm --clean --log-level INFO autoortho.spec` (environ 2 min).
5. Les lignes `ERROR: Hidden import 'autoortho.…' not found` sont normales : dans l'exécutable, `autoortho` désigne `autoortho.py` et le code passe par la seconde forme d'import. PIL et winfspy manquent aussi dans la release officielle.
6. Test sans réseau : lancer l'exe avec `AO_RUN_MODE=mount_worker`, `USERPROFILE` isolé et sans argument. Le code 2 (arguments manquants) prouve que tous les modules du worker se chargent. Ne pas lancer le processus principal soi-même : il ouvre le serveur web sur `0.0.0.0` (alerte du pare-feu Windows pour un nouvel exécutable).
7. Formats de cache : la 3.0 écrit des DDM v5 et supprime les entrées d'une autre version (et réciproquement), donc alterner 2.5 et 3.0 refroidit le cache DDS. Le cache JPEG est partagé et compatible.

## Architecture (carte rapide, code 3.0)

| Rôle | Fichiers |
|---|---|
| Entrée, logs fichier, instance unique, arrêt global | `autoortho/__main__.py` |
| Processus parent : serveur de stats (BaseManager TCP), serveur de logs, broker HTTP/2, publication de `flight_state`, lancement des workers | `autoortho.py`, `process_supervisor.py`, `aostats.py`, `http2_broker.py` |
| Interface Qt (refonte 3.0) | `ui/` (`main_window.py`, `pages/`, `services/`…) |
| Worker de montage (1 processus par région `z_ao_*`) | `mount_worker.py`, `mount_setup.py`, `winsetup.py` |
| Système de fichiers FUSE/WinFSP, ordonnanceur de préchargement par DSF | `autoortho_fuse.py`, `mfusepy.py` |
| Cœur : chunks, tuiles, mipmaps, cache mémoire, préchargement, builder de fond, miroir de l'état de vol | `getortho.py` (~15 000 lignes), `prefetch.py` |
| Pipeline natif (lecture cache, décodage JPEG, compression BC1, OpenMP) | `aopipeline/*.c`, `AoDDS.py`, `AoDecode.py`, `AoCache.py` |
| Cache DDS persistant (.dds zstd + .ddm JSON + lignes partielles `.rows`), budget disque, fallbacks | `aopipeline/dynamic_dds_cache.py`, `dds_manifest.py`, `disk_budget_manager.py`, `fallback_resolver.py` |
| Profilage et rapports de vol | `diagnostics.py` |
| Datarefs X-Plane (UDP RREF), SimBrief, zoom dynamique, exclusion nocturne | `datareftrack.py`, `utils/simbrief_flight.py`, `utils/dynamic_zoom.py`, `time_exclusion.py` |
| Verrous inter-processus | `utils/process_lock.py` |

Invariants à respecter :
- **Multi-processus** : tout état global de module (`dt`, `simbrief_flight_manager`, `CFG`, caches) existe séparément dans le parent et dans chaque worker. Le parent publie `flight_state` et la route SimBrief dans le stats store ; les workers les recopient via `flight_runtime_gate` (démarré dès l'init du FUSE). `AO_SESSION_ID` identifie une exécution partagée par le parent et ses workers.
- Les services lourds d'un worker (préchargement, cache DDS, scans disque) démarrent à la **première ouverture de DDS**, donc pendant le chargement d'X-Plane : tout ce qu'ils font au démarrage pèse sur le temps de chargement.
- Imports : le code utilise le double motif `try: from autoortho.x import y / except ImportError: from x import y`. Dans la release PyInstaller, c'est la seconde forme qui s'applique (`autoortho` y désigne `autoortho.py`). Un même module importé sous deux noms crée **deux singletons** : injecter l'objet (ex. `flighttrack.set_dataref_tracker(dt)`) plutôt que le ré-importer.
- Tailles DDS : ZL16 = 4096² (256 chunks, mm0 8 Mio), ZL17 = 8192² (1024 chunks, mm0 32 Mio). `getattr` annonce la taille du zoom maximal possible : ne pas la changer sans vérifier `tests/test_dds_size_estimation.py`.
- X-Plane lit les DDS sur ses threads de chargement ; un `read()` FUSE bloqué bloque le chargement de la tuile, pas le thread principal — mais le CPU consommé par AutoOrtho est pris aux mêmes cœurs.

## Règles de performance (non négociables)

- Aucun `log.debug(f"...")` dans un chemin chaud (read/getattr/open, boucles par chunk) : utiliser `log.debug("... %s", x)` ou garder par `log.isEnabledFor(logging.DEBUG)`.
- Pas d'attente active (`while ...: time.sleep(0.01)`) : utiliser `Event.wait`, `Condition` ou des futures.
- Pas d'E/S disque ni d'aller-retour IPC (proxy `aostats`) par lecture FUSE ; mettre en cache les résultats négatifs.
- Toute nouvelle parallélisation passe par `_native_build_context()` / `_thread_budget_for()`, qui laissent `in_flight_reserved_threads` cœurs à X-Plane pendant le vol.
- Travail de fond (préchargement, builder prédictif, scans disque) : priorité basse, interruptible, cédant la place aux lectures « live ».
- Un seul processus fait les tâches globales (scan disque, éviction) sous `ProcessLock` ; les autres réutilisent le résultat.

## Méthode de travail

1. Branche dédiée tirée de `upstream/develop` (`fix/...`, `perf/...`) ; commit/push uniquement à la demande de l'utilisateur. Messages de commit en anglais, terminés par la ligne `Co-Authored-By` fournie par la session.
2. Lire le code concerné en entier avant de modifier ; noter l'hypothèse et la preuve attendue.
3. Changement minimal et ciblé ; pas de refactoring opportuniste dans `getortho.py`.
4. Ajouter un test dans `autoortho/tests/` quand le comportement est testable sans X-Plane (nouveau fichier plutôt que modifier ceux de l'upstream, pour limiter les conflits).
5. Mesurer avant/après sur le même scénario (vol LFBP, même météo, même config) selon le protocole de [docs/dev/plan.md](docs/dev/plan.md) : durée de chargement dans le `Log.txt` d'X-Plane, lignes `DSF load time`, lignes `STATS:` d'AutoOrtho, rapports de `diagnostics.py` (`~/.autoortho-data/reports/`), et si besoin HWiNFO. Log en `INFO` pour les mesures de durée. Avant de conclure à un défaut d'AutoOrtho, vérifier la configuration des scènes (`scenery_packs.ini`) et que le streaming était démarré.
6. Rendre compte honnêtement : ce qui est mesuré, ce qui est supposé.

## Bugs connus (réévaluation du 2026-09-28, code upstream 3.0)

Corrigés par l'upstream 3.0 :
- Position de l'avion et route SimBrief transmises aux workers (via le stats store).
- Cache d'images par tuile borné en octets ; sources libérées après construction.
- Cache DDS : persistance des lignes partielles de mm0 (`persist_partial_dds_cache = True`).

Mesures en vol du 28-29/09 (747 Felis à LFBP, BI, ZL16 + ZL18 près des aéroports, Global_Forests_v2 désactivé ; instrument : `Log.txt`, de « Loading airplane number 0 » à « Finished pre-warming paging cache ») : X-Plane seul, sans AutoOrtho, 2 min 37 s ; 2.5.0 à chaud (27/09) 6 min 29 s ; build du lot 1 à froid (835 Mo téléchargés, Bing TTFB médiane 0,5 s) 7 min 47 s ; lot 1 à chaud 2 min 43 s. L'essentiel du gain à chaud vient du cache DDS persistant de la 3.0 (3 053 lignes de mm0 restaurées, lectures DDS médiane 500 → 50 ms), pas du lot 1 : à chaud, le chargement est déjà au plancher d'X-Plane.

Corrigés sur la branche `fix/3.0-batch1` (poussée sur `origin` le 27/09, exécutable testé en vol le 29/09, voir ci-dessus) :
- Recopie de l'état de vol et de SimBrief dans les workers **même préchargement désactivé** (sinon zoom dynamique, exclusion nocturne et `suspend_maxwait` restaient aveugles).
- Scan disque : un seul par session (verrou + résultat partagé `.disk_usage.json`), en `os.scandir` ; l'éviction JPEG remesure l'occupation réelle (plus de sur-éviction entre workers).
- Attente active de `_try_native_mipmap_build` remplacée par une attente sur événements, qui s'arrête dès qu'un chunk échoue.
- Cache DDS : une tuile absente n'est plus re-sondée à chaque lecture (compteur d'écritures par entrée).
- Cœurs réservés à X-Plane pendant le vol (`in_flight_reserved_threads = auto`).
- Ancien suivi UDP (`flighttrack.ft`) retiré des workers ; la carte web lit le `dt` du parent.
- Verrou d'instance unique (`~/.autoortho-data/autoortho.lock`).
- `file_log_level = INFO` par défaut.

Corrigés sur la branche `fix/3.0-batch2` (tirée de `fix/3.0-batch1`, un commit par mesure le 2026-10-10, non poussée, pas encore testée en vol ; mesures M1 à M8, M14 et M15 de [docs/dev/plan.md](docs/dev/plan.md), retenues par Romain le 10/10) :
- Tampon mm0 creux (`pydds.SparseMipmapBuffer`) : les segments contigus étaient re-concaténés à chaque écriture, donc construire un mm0 ligne par ligne recopiait O(lignes²) octets. Mesuré : 12 → 0,2 ms (ZL16), 91 → 0,8 ms (ZL17), 713 → 5 ms (ZL18) par tuile. (M1)
- Cache DDS : une entrée partielle ou incomplète était relue à **chaque** lecture FUSE (`.ddm` 2 à 3 fois, `.rows` entier, 4 SHA-256 par ligne, réinstallation des lignes) : 31 à 75 ms par lecture en ZL16, 179 à 499 ms en ZL17. La sonde est désormais mémorisée comme une absence (jusqu'à la prochaine écriture), et les lignes déjà restaurées ne sont plus réinstallées. (M2)
- Chunks que le fournisseur n'a pas (404, 410, réponse 200 qui n'est pas un JPEG) : mémorisés par processus (`_PermanentMissMemo`) : redemandés au plus toutes les 30 s pendant les 2 premières minutes (un 404 peut être passager), puis mis de côté 15 min si le trou persiste. Plus de requête à chaque réouverture d'une tuile, relance du préchargement ou « healing » ; un JPEG invalide devient un échec permanent (avant, le préchargement le relançait indéfiniment toutes les 30 s) ; un 404 n'est plus journalisé qu'une fois par type de carte (INFO), les suivants en DEBUG et dans les compteurs STATS. (M3)
- « Stop Streaming » puis « Start Streaming » : le démarrage restait ~9 min sur « Starting… », car le client HTTP/2 du processus principal (utilisé par `diagnose()`) visait encore l'ancien broker (60 s d'attente par fournisseur). `stop_download_broker()` le réinitialise. (M4)
- Même scénario : la route SimBrief n'arrivait plus aux workers (le stats store est recréé vide). Elle est republiée à la création du nouveau store (`_ensure_parent_services` → `ConfigUI._publish_current_simbrief_route`). (M5)
- Lignes `STATS` sans la route SimBrief (`simbrief_route_snapshot`, ~80 Ko toutes les 10 s : le log tournait toutes les ~20 min). (M6)
- Ligne de log « Flight started N min SS s after X-Plane opened its first AutoOrtho scenery file » : chaque worker publie l'heure de sa première ouverture de DSF ou de DDS (`first_scenery_open_ts:<pid>`), le parent mesure jusqu'au premier paquet de datarefs (X-Plane ne répond qu'une fois le vol lancé, plugins compris), une fois par session de streaming. `Log.txt` reste la référence. (M7)
- Scan de démarrage du cache DDS (`scan_existing`, `migrate_uncompressed`) et du budget disque : lancés 60 s après le début du vol (ou au bout de 20 min sans vol), cèdent la place aux lectures live entre deux répertoires, interruptibles. Ils ne suppriment plus les fichiers de moins de 10 min (entrée en cours d'écriture) et gardent l'heure d'accès des tuiles déjà lues dans la session. Mesuré avant correctif : 19 s le 29/09 (2 973 entrées), 62 s le 10/10 (4 563 entrées), en plein chargement. (M8)
- Carte web (serveur Flask de `flighttrack.py`, Werkzeug en mode développement) : écoutait sur `0.0.0.0`, donc sur tout le réseau local. Nouvelle clé `[flightdata] webui_host = 127.0.0.1` ; `0.0.0.0` pour la rouvrir au réseau local. (M14)
- La tâche « Check for AutoOrtho updates » affichait « Update available » dès que GitHub répondait ; le libellé suit désormais la comparaison des versions (la bannière comparait déjà). (M15)

Toujours ouverts (numéros M du plan entre parenthèses) :
- Tuile contenant un chunk définitivement absent (404 / JPEG invalide) : jamais servie depuis le cache DDS persistant, car `_mm0_cache_complete` exige que toutes les lignes soient « exactes » ; elle est reconstruite à chaque ouverture (avec « healing » réseau). `_probe_chunk_cache_ratio` la compte aussi « froide » (chemin progressif). Correctif possible : une provenance « absent chez le fournisseur » acceptée par la barrière de qualité (change la sémantique du cache, à discuter). (M9)
- Première sonde d'une entrée partielle : encore 9 à 41 ms (le `.rows` est haché 4 fois par ligne). (M11)
- Avec les builds publiées (2.5.0, lot 1) : Stop/Start bloque ~9 min et perd la route SimBrief (M4, M5) ; le log tourne toutes les ~20 min avec une route SimBrief (M6) ; pas de ligne de temps de chargement (M7, mesurer dans `Log.txt`, de « Loading airplane number 0 » à « Finished pre-warming paging cache ») ; scan du cache DDS en plein chargement (M8) ; carte web ouverte au réseau local (M14). Corrigés dans le lot 2 (voir plus haut).
- Pillow absent de la release : la réparation des DDS (`_do_patch`) échoue toujours ; `FallbackResolver._try_network_fallback` utilise l'API PIL sur `AoImage`. (M13)
- Option « Minimize to the system tray » (`general.hide`) sans effet dans la 3.0.
- Avec les builds publiées, la tâche « Check for AutoOrtho updates » affiche « Update available » à tort (M15). Pour savoir si l'upstream a bougé, regarder ses branches (`git ls-remote`) et ses releases, pas ce message.
