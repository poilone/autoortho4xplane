# AutoOrtho4XPlane (fork poilone) — instructions développeur

Fork de `ProgrammingDinosaur/autoortho4xplane`. Objectifs du fork, par ordre de priorité :
1. Réduire le temps de chargement d'un vol dans X-Plane 12.
2. Corriger les bugs connus (voir « Bugs connus » plus bas).
3. Préserver les FPS : ne jamais voler de temps CPU au **thread principal d'X-Plane**.

Langue : échanges et documentation du fork en français ; code, commentaires et messages de log en anglais (convention du code existant).
Guide détaillé des bonnes pratiques : [docs/dev/bonnes-pratiques.md](docs/dev/bonnes-pratiques.md).

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
  - `.venv\Scripts\python -m pytest --noconftest -q autoortho/tests/<fichier>.py` (`--noconftest` : le conftest importe PySide6).
  - `cd autoortho; ..\.venv\Scripts\python -m pytest --noconftest -q test_getortho.py test_pydds.py test_downloader.py`.
  - Toujours comparer à l'upstream intact (`git stash`, relancer, `git stash pop`) : sous Windows, une cinquantaine de tests échouent déjà (tests POSIX, tests réseau instables comme `test_maptype_chunk`, `store_from_file`, PySide6 absent). Lancé en un seul processus, `autoortho/tests` produit aussi des erreurs d'import croisées (`autoortho` résolu en `autoortho.py`) : lancer `test_dynamic_dds_cache.py` seul.
- Ne jamais modifier `~/.autoortho`, `scenery_packs.ini`, le cache ou les processus de l'utilisateur sans son accord explicite.

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
5. Mesurer avant/après sur le même scénario (vol LFBP, même météo, même config) : durée « Reading new scenery files », lignes `DSF load time` du `Log.txt` d'X-Plane, lignes `STATS:` d'AutoOrtho (nouveaux compteurs : `dynamic_dds_cache_known_miss`…), rapports de `diagnostics.py`, et si besoin HWiNFO. Log en `INFO` pour les mesures de durée.
6. Rendre compte honnêtement : ce qui est mesuré, ce qui est supposé.

## Bugs connus (réévaluation du 2026-09-27, code upstream 3.0)

Corrigés par l'upstream 3.0 :
- Position de l'avion et route SimBrief transmises aux workers (via le stats store).
- Cache d'images par tuile borné en octets ; sources libérées après construction.
- Cache DDS : persistance des lignes partielles de mm0 (`persist_partial_dds_cache = True`).

Corrigés sur la branche `fix/3.0-batch1` (non commités à ce jour) :
- Recopie de l'état de vol et de SimBrief dans les workers **même préchargement désactivé** (sinon zoom dynamique, exclusion nocturne et `suspend_maxwait` restaient aveugles).
- Scan disque : un seul par session (verrou + résultat partagé `.disk_usage.json`), en `os.scandir` ; l'éviction JPEG remesure l'occupation réelle (plus de sur-éviction entre workers).
- Attente active de `_try_native_mipmap_build` remplacée par une attente sur événements, qui s'arrête dès qu'un chunk échoue.
- Cache DDS : une tuile absente n'est plus re-sondée à chaque lecture (compteur d'écritures par entrée).
- Cœurs réservés à X-Plane pendant le vol (`in_flight_reserved_threads = auto`).
- Ancien suivi UDP (`flighttrack.ft`) retiré des workers ; la carte web lit le `dt` du parent.
- Verrou d'instance unique (`~/.autoortho-data/autoortho.lock`).
- `file_log_level = INFO` par défaut.

Toujours ouverts :
- Chunks 404 / JPEG invalides : jamais mis en cache négatif (161 JPEG invalides BI le 27/09, retéléchargés à chaque session) et comptés « indisponibles » par `_probe_chunk_cache_ratio` (tuile forcée sur le chemin progressif). Journalisés en WARNING + INFO à chaque chunk, avec deux appels IPC `get_stat` par échec.
- À mesurer : `_load_partial_dds_rows` relit le `.ddm` et tout le fichier `.rows` (sha256 par ligne) à chaque lecture d'une tuile dont le cache est partiel.
- `scan_existing()` + `migrate_uncompressed()` lisent tous les `.ddm` dans chaque worker actif.
- Pillow absent de la release : la réparation des DDS (`_do_patch`) échoue toujours ; `FallbackResolver._try_network_fallback` utilise l'API PIL sur `AoImage`.
- Serveur Flask (`flighttrack.py`) sur `0.0.0.0`.
- `TerrainTileLookup` : 2 à 16 s par région à chaque montage (glob des `.ter`).
- Option « Minimize to the system tray » (`general.hide`) sans effet dans la 3.0.
