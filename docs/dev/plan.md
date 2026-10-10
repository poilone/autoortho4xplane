# Plan de travail du fork (état au 2026-10-10)

Document de reprise : où en est le fork, ce qui a été mesuré, comment mesurer, et les décisions en attente. Les règles de travail sont dans [CLAUDE.md](../../CLAUDE.md), les bonnes pratiques dans [bonnes-pratiques.md](bonnes-pratiques.md).

## Où on en est

- **Lot 1** : `origin/fix/3.0-batch1` (`f30a596`, 9 commits au-dessus de l'upstream 3.0 `514954b`). Exécutable construit comme la CI (version `3.0.0-fork-batch1-f30a596`, emplacement dans `CLAUDE.local.md`), testé en vol du 28 au 30/09. Aucune erreur, comportement conforme (un seul scan disque, sondes DDS évitées, état du vol transmis aux workers).
- **Lot 2** : branche locale `fix/3.0-batch2`, tirée du lot 1, commitée le 10/10 (11 commits), **non poussée**. Romain a retenu M1 à M8 le 10/10, Précision de Romain pour M3 : redemander toutes les 30 s pendant 2 min, puis 15 min si ça échoue encore. Il a ajouté M15, et M14 (la carte n'a pas besoin d'être vue d'un autre appareil pour l'instant). M1 à M8, M14 et M15 sont codées, avec leurs tests (fichiers `autoortho/tests/test_*` nouveaux : `chunk_permanent_miss`, `sparse_mipmap_buffer`, `stop_start`, `stats_log_filter`, `load_time_log`, `deferred_cache_scan`, `dds_cache_scan_safety`, `webui_host`, `update_check_stage`, plus des ajouts dans `dds_cache_probe`).
- Branche poussée sur `origin` le 10/10. Exécutable construit comme la CI depuis `f773ecc` (version `3.0.0-fork-batch2-f773ecc`, emplacement dans `CLAUDE.local.md`) ; test sans réseau réussi (worker de montage, code 2).
- En attente : les vols de mesure.
- L'upstream n'a pas bougé depuis `514954b` (vérifié le 10/10) : `develop` = `release/3.0.0` = `514954b` ; la PR `release/3.0.0 → main` (ProgrammingDinosaur/autoortho4xplane#449) est ouverte depuis le 22/09, sans release 3.0.0 publiée (dernière release : 2.5.0 du 19/06). Aucune autre branche plus récente.

## Mesures de référence

Même départ (747 Felis, LFBP poste 7, sauf vol 3 au poste 8A), BI, ZL16 + ZL18 près des aéroports, `cache_mem_limit = 30`, Global_Forests_v2 désactivé. Instrument : `Log.txt` d'X-Plane, de « Loading airplane number 0 » à « Finished pre-warming paging cache ».

| Vol | Chargement | Conditions |
|---|---|---|
| 28/09, sans AutoOrtho (streaming pas démarré) | 2 min 37 s | textures d'origine d'X-Plane : le plancher |
| 27/09, release 2.5.0 | 6 min 29 s | cache JPEG chaud, cache DDS vide |
| 29/09, vol 2, lot 1 | 7 min 47 s | cache froid : 835 Mo téléchargés, Bing TTFB médiane 0,5 s, p95 2,5 s, 320 requêtes simultanées |
| 29/09, vol 3, lot 1 | 2 min 43 s | caches JPEG et DDS chauds : 0 octet téléchargé, 3 053 lignes de mm0 restaurées |

Lecture : à chaud, le chargement est au plancher d'X-Plane. Le gain vient surtout du cache DDS persistant de la 3.0, pas du lot 1. À froid, il reste environ 5 min au-dessus du plancher, dues à la latence de Bing.

Données brutes (logs, rapports de performance) : copiées dans le dossier temporaire de la session du 28/09 ; non conservées dans le dépôt.

Vol du 10/10 (lot 1, A300 iniSimulations, observé pendant le développement, pas un vol de mesure) : X-Plane lancé à 15:02:10, « Loading airplane number 0 » à 15:07:25, première DSF AutoOrtho ouverte à 15:13:16, « Finished pre-warming paging cache » à 15:15:25, puis initialisation des plugins (FlyWithLua, avion, BetterPushback) jusqu'à 15:17:3x ; premier paquet de datarefs (« Flight is starting ») à 15:17:23. X-Plane ne répond pas aux datarefs à son menu. Scan du cache DDS : 62,5 s (4 563 entrées, 5,7 Go), de 15:13:18 à 15:14:21, en plein chargement.

## Protocole de mesure

Leçons des vols du 28 au 30/09 :

1. **Scènes** : noter l'état de `scenery_packs.ini`. Les bâtiments réalistes viennent de **simHeaven X-World Europe** ; le 30/09, « aucune maison » venait de ces packs restés désactivés après les tests du 13/09, pas d'AutoOrtho. Sans eux, seul l'autogen par défaut de `yAutoOrtho_Overlays` est dessiné.
2. Lancer AutoOrtho, cliquer **« Start Streaming »**, attendre **« ● Running »**, puis seulement lancer X-Plane : il ne voit les dossiers `z_ao_*` que s'ils sont montés à son démarrage. Le 28/09, le streaming n'avait pas été démarré : le vol s'est fait sans AutoOrtho.
3. Pour recommencer, **quitter et relancer AutoOrtho**, pas Stop/Start Streaming (bugs M4 et M5, corrigés à partir du lot 2 ; garder la règle pour des mesures comparables).
4. Mesure à chaud comparable au 27/09 : AutoOrtho redémarré (mémoire vide) et X-Plane redémarré, même départ.
5. Vérifier dans `Log.txt` que les « DSF load time » contiennent bien `z_ao_eur`.
6. **Copier dans l'heure** `~/.autoortho-data/logs/autoortho.log*`, `Log.txt` et `~/.autoortho-data/reports/performance-<session>/` : avec une route SimBrief, le log tourne toutes les ~20 min (M6).
7. Compteurs utiles dans `STATS` : `bytes_dl`, `chunk_hit`, `chunk_miss`, `chunk_invalid_jpeg`, `partial_dds_cache_rows_loaded`, `dynamic_dds_cache_skip_partial_missing_mm0`, `dynamic_dds_cache_known_miss`. Dans les rapports de performance : `fuse.dds_read` (p50), `network.http_request`, `broker.time_to_first_byte`, `cache.dds_metadata`.
8. Prochain vol à froid : relever l'activité disque avec HWiNFO (M12).
9. À partir du lot 2, relever aussi la ligne « Flight started … after X-Plane opened its first AutoOrtho scenery file » (M7) : elle couvre la partie du chargement qui passe par AutoOrtho et l'initialisation des plugins qui suit (~2 min le 10/10), pas le chargement de l'avion.

## Lot 2 : mesures proposées, à trancher

Preuve : « vol » = mesuré en vol, « banc » = micro-benchmark, « code » = lu dans le code.

| # | Mesure | Preuve | Effort | Risque | Avis |
|---|---|---|---|---|---|
| M1 | Tampon mm0 sans recopie quadratique | banc | fait | faible | oui |
| M2 | Ne plus relire une entrée DDS incomplète à chaque lecture | vol 3 + banc | fait | faible | oui |
| M3 | Mémoriser les chunks absents chez le fournisseur (30 s pendant 2 min, puis 15 min) | vols 2 et 3 | fait | faible | oui |
| M4 | Stop/Start : oublier l'ancien broker | vol 3 | fait | très faible | oui |
| M5 | Stop/Start : republier la route SimBrief | vol 3 | fait | faible | oui |
| M6 | Retirer la route SimBrief des lignes `STATS` | vol 1 | fait | nul | oui |
| M7 | Remettre une ligne « temps de chargement » dans le log | — | fait | nul | oui |
| M8 | Différer le scan du cache DDS après le chargement | vols 3 et 10/10 | fait | faible | oui |
| M9 | Accepter dans le cache DDS les tuiles à trous permanents | vols 2 et 3 | gros | moyen | lot suivant |
| M10 | Précharger le départ SimBrief avant X-Plane | vol 2 | moyen | faible | lot suivant, selon les habitudes de départ |
| M11 | Hacher les lignes une fois au lieu de quatre | banc | petit | faible | plus tard |
| M12 | Écritures du cache DDS pendant un chargement à froid | compteurs | ? | ? | mesurer d'abord |
| M13 | Embarquer Pillow (réparation des DDS) | code | petit | moyen | non pour l'instant |
| M14 | Carte web limitée à ce PC | code | fait | faible | oui (pas d'autre appareil pour l'instant) |
| M15 | Faux « Update available » dans les tâches | code | fait | nul | oui |

### Déjà codées

- **M1** — `pydds.SparseMipmapBuffer._merge_segments` recollait tous les segments contigus à chaque écriture : construire un mm0 ligne par ligne recopiait O(lignes²) octets en bloquant les autres threads Python. Banc : 12 / 91 / 713 ms par tuile en ZL16 / 17 / 18, 0,2 / 0,8 / 5 ms après. Chemin très emprunté (3 787 constructions partielles au vol 2, 3 053 lignes restaurées au vol 3, tuiles ZL18 de LFBP).
- **M2** — Une tuile avec une entrée DDS partielle relisait à chaque lecture FUSE le `.ddm`, tout le `.rows`, re-hachait et réinstallait les lignes : 31 à 75 ms par lecture en ZL16, 179 à 499 ms en ZL17 (banc). Vol 3 : 5 947 lectures concernées pendant le chargement, `cache.dds_metadata` jusqu'à 336 ms, lecture de texture médiane 50 ms. Correctif : la sonde est mémorisée jusqu'à la prochaine écriture pour la tuile (`get_bytes`), et `_load_partial_dds_rows` saute les lignes déjà installées.
- **M3** — Les chunks absents chez Bing (404, 410, réponse 200 non JPEG) étaient redemandés à chaque réouverture, par le préchargement (JPEG invalide relancé toutes les 30 s, indéfiniment) et par le healing : 172 requêtes inutiles au vol 2, 102 au vol 3, 11 tuiles dégradées à l'identique. Correctif : mémo par processus (clé : chemin de cache), JPEG invalide = échec permanent, un seul INFO par type de carte et code. À la demande de Romain, le chunk est redemandé au plus toutes les 30 s pendant les 2 premières minutes (un 404 passager ne laisse une texture de repli que 30 s) ; s'il manque encore après 2 min, il est mis de côté 15 min. Un trou dont la dernière entrée a expiré depuis plus de 15 min repart de zéro.

### Issues des vols (codées le 10/10)

- **M4** — `stop_download_broker()` (`autoortho.py`) arrête le broker HTTP/2 sans réinitialiser `getortho._http2_client` du processus principal. Après Stop/Start, `diagnose()` envoie ses 9 tests de fournisseurs à l'ancien broker : 60 s chacun, ~9 min sur « Starting… » (contre 6 s au premier démarrage). Correctif : appeler `getortho._reset_http2_client()` (même forme d'import que `diagnose()`), plus un test. Fait : `stop_download_broker()` réinitialise le client des deux formes d'import déjà chargées (`getortho`, `autoortho.getortho`).
- **M5** — La route n'est publiée (`MainWindow._publish_simbrief_to_workers`, `ui/main_window.py`) qu'au chargement de SimBrief. Stop/Start recrée le serveur de stats, vide : aucun « Loaded SimBrief flight » dans la session 2, vol 3 fait sans route. Correctif : republier la route chargée au démarrage du streaming. Fait : à la création d'un nouveau stats store (`_ensure_parent_services`), le parent republie `simbrief_flight_data` avec l'état de `use_flight_data`.
- **M6** — Le rapporteur `STATS` de `autoortho.py` exclut déjà `flight_state` mais pas `simbrief_route_snapshot` : lignes de 82 Ko toutes les 10 s, ~30 Mo/h, rotation toutes les ~20 min, ~1 h 45 d'historique. Correctif : ajouter la clé aux exclusions (et dans `aostats.filter_stats_snapshot`). Fait.
- **M7** — Le lot 1 a supprimé « FT: Time to start » en retirant l'ancien traceur. Correctif : chaque worker publie une fois l'heure de sa première lecture de texture ; au « Flight is starting », le parent journalise la durée. `Log.txt` reste la référence. Fait, avec la première ouverture de DSF ou de DDS (la DSF arrive avant les textures) ; la durée n'est journalisée qu'au premier passage déconnecté → connecté qui suit une ouverture de scène (pas au menu d'X-Plane, pas si le vol tournait déjà), une fois par session de streaming.
- **M8** — `scan_existing()` (lancé par `_initialize_disk_caches` dans le thread `dds-cache-init`, à la première ouverture de DDS) parcourt tout `dds_cache`, toutes régions, dans chaque worker actif : ~19 s pour 2 973 entrées au vol 3, en plein chargement, refait par un second worker. Extrapolation non mesurée : ~15 min par worker si le cache atteint son budget de 80 Go. Il supprime aussi les orphelins sans vérifier leur âge (course possible avec un autre worker qui écrit). Rien n'attend ce scan (`wait_for_scan` n'est appelé nulle part). Correctif : le lancer une fois la simulation démarrée, en priorité basse, et ignorer les fichiers récents. Mesuré le 10/10 : 62,5 s pour 4 563 entrées, en plein chargement. Fait : `getortho._initialize_disk_caches` attend la barrière de vol (20 min au plus), puis 60 s ; entre deux répertoires, le scan attend qu'aucune lecture live ne soit en cours (`live_pressure_controller.wait_until_clear`) ; il s'arrête avec `stop_predictive_dds`. Pas de baisse de priorité du thread : sous le GIL, un thread ralenti par l'OS bloquerait les autres. Le scan ne supprime plus de fichier de moins de 10 min, et garde l'heure d'accès des entrées lues avant lui (sinon l'éviction LRU aurait pris les tuiles du départ pour les plus anciennes). Le scan du budget disque suit le même délai ; rien n'est évincé avant lui (compteurs à zéro), comme avant.

### Plus lourdes, ou à mesurer d'abord

- **M9** — Une tuile avec un trou permanent chez Bing n'entre jamais dans le cache DDS (`_mm0_cache_complete` exige des lignes 100 % exactes) : les 11 mêmes tuiles, dont 3 en ZL18 de 4 096 chunks, sont reconstruites à chaque chargement. Correctif : provenance « absent chez le fournisseur » acceptée avec une durée de validité (~30 jours). Change le format et la sémantique du cache ; la 3.0 d'origine rejetterait ces entrées. Gain surtout CPU.
- **M10** — Vol 2 : streaming prêt à 00:16:36 avec la route chargée, rien téléchargé avant la première DSF à 00:19:16. À 2,4 Mo/s, ~380 Mo des 835 auraient pu être préchargés. Correctif : lancer le préchargement DSF du départ au démarrage du streaming ou au chargement de SimBrief. Gain : jusqu'à ~5 min sur un départ jamais visité, rien sur LFBP.
- **M11** — La première sonde d'une entrée partielle hache chaque ligne 4 fois (9 à 41 ms par tuile, ~3 s de CPU estimés par chargement à chaud). Comparer les empreintes au lieu de recalculer.
- **M12** — Au vol 2, 1,55 Go de lignes écrites avec un `fsync` par ligne pendant le chargement, sur le SSD d'X-Plane. Effet non mesuré : HWiNFO d'abord.
- **M13** — Sans Pillow, la réparation des DDS échoue toujours ; l'activer mettrait en service en vol un code jamais exercé et coûteux, qui ne répare pas les trous de Bing.
- **M14** — La carte web écoute sur `0.0.0.0` (Werkzeug en mode « unsafe ») : accessible depuis le réseau local. Correctif : `127.0.0.1` par défaut, option pour le réseau local. Fait : clé `[flightdata] webui_host` (défaut `127.0.0.1`, `0.0.0.0` pour le réseau local).
- **M15** — `MainWindow.on_update_check_result` (`ui/main_window.py`) termine la tâche « Check for AutoOrtho updates » sur « Update available » dès que GitHub répond, avant de comparer les versions. Constaté le 10/10 : dernière release 2.5.0, build du fork en 3.0.0, donc pas de mise à jour (la bannière, elle, compare bien et ne s'affiche pas). Correctif : fixer le libellé après la comparaison. Fait (« Update available », « Up to date » ou « No update information »).

Vérifié et écarté : `TerrainTileLookup` (2 à 14 s par région au premier montage, 20 à 45 ms ensuite grâce à son index sur disque, terminé avant le chargement des scènes).

## Suite

1. ~~Romain choisit les mesures~~ (M1 à M8, le 10/10).
2. ~~Implémenter~~ et commiter (10/10 : un commit par mesure sur `fix/3.0-batch2`, M1 à M8, M14, M15, puis la documentation ; chaque commit passe ses propres tests ; non poussé).
3. ~~Construire l'exécutable~~ (10/10), puis deux vols : à froid avec HWiNFO, puis à chaud, selon le protocole ci-dessus. À vérifier dans le log du lot 2 : la ligne « Flight started … » (M7), « DDS cache scan » environ une minute après le début du vol et non pendant le chargement (M8), plus aucune route SimBrief dans les lignes `STATS` (M6), un seul INFO par type de carte pour les 404 (M3).
4. Lot suivant selon les résultats : M9, M10, M11.
