# Maintenance des sources — procédure en cas de casse

Dans un projet de scraping, la dépendance la plus instable n'est pas notre code
mais le **HTML/JSON des autres** (Genius a forcé v1→v2→v3, SongBPM une v2, Kworb
une refonte…). Cette procédure sert à détecter la casse tôt et à la réparer vite.

Deux outils travaillent ensemble :

- **`scripts/check_sources_health.py`** (+ fenêtre GUI « État sources ») : sonde
  la santé de chaque source, distingue *panne réseau* et *changement de structure*.
- **`scripts/capture_fixtures.py`** + `tests/test_*_fixtures.py` : rejouent les
  parseurs sur des pages réelles enregistrées ; un test rouge localise la casse.

## 1. Symptôme

- Une source ressort `broken` (❌) dans la fenêtre « État sources » ou en CLI, **ou**
- un enrichissement échoue / renvoie des trous inexpliqués pour une source.

## 2. Diagnostiquer : réseau ou structure ?

```
python scripts/check_sources_health.py --full --only <source>
```

Lire le message :

- **« injoignable », « HTTP 5xx », « timeout »** → le site est *down* ou vous
  bloque temporairement. Rien à corriger : réessayer plus tard.
- **« anti-bot HTTP 403 » sur une source `degraded`** → normal pour les sources
  Cloudflare (Genius scrape, BRMA/Ultratop) et anti-bot (RIAA, SongBPM) : la
  sonde rapide ne fait qu'un GET, le vrai fetch passe par Playwright/CDP. Pas une
  casse.
- **« 0 entrée parsée », « format changé », « sans hits »** → la structure de la
  page a changé. C'est une vraie casse : passer à l'étape 3.

## 3. Re-capturer la fixture

```
python scripts/capture_fixtures.py --only <source>
```

Cela réécrit `tests/fixtures/<source>/…` avec la page actuelle (+ un sidecar
`.meta.json` daté). Committer la nouvelle fixture **même si le test passe ensuite
au rouge** : elle documente l'état réel du site au moment de la casse.

## 4. Lancer les tests ciblés

```
python -m pytest tests/test_<source>_fixtures.py -v
```

Le test rouge pointe **le parseur exact** qui ne retrouve plus ses données
(sélecteur CSS, clé JSON, regex…). C'est le cœur de la méthode : la casse est
localisée au lieu d'être devinée à partir de trous en base.

## 5. Réparer

Corriger le parseur (dans `src/scrapers/` ou `src/api/`) jusqu'à ce que le test
repasse au vert, puis re-sonder en complet :

```
python -m pytest tests/test_<source>_fixtures.py
python scripts/check_sources_health.py --full --only <source>
```

Si le piège est notable (changement de sélecteur, quirk d'anti-bot…), le
consigner dans `JOURNAL.md`.

## Récapitulatif par source

| Clé (`--only`)  | Capture     | Fichier de test                     | Sonde complète | Particularités |
|-----------------|-------------|-------------------------------------|----------------|----------------|
| `kworb`         | requests    | `test_kworb_fixtures.py`            | ✅ (parse)     | statique, pas de CF |
| `riaa`          | patchright  | `test_riaa_fixtures.py`            | rapide seule   | Cloudflare laxiste ; MORE DETAILS = AJAX |
| `spotify_embed` | requests    | `test_spotify_embed_fixtures.py`   | rapide seule   | `__NEXT_DATA__` de la page /embed |
| `brma`          | route CDP   | `test_brma_fixtures.py`            | rapide seule   | **CF strict : vrai Chrome via CDP** (JOURNAL 2026-06-29) |
| `genius`        | requests→PW | `test_genius_fixtures.py`          | rapide seule   | 403 sur requests → fallback Playwright |
| `lrclib`        | requests    | `test_lrclib_fixtures.py`          | ✅ (parse)     | API libre, match sur la durée |
| `getsongbpm`    | API (clé)   | `test_getsongbpm_fixtures.py`      | ✅ (parse)     | `GETSONGBPM_API_KEY` requise à la capture |
| `deezer`        | —           | —                                   | ✅ (parse)     | durée canonique |
| `genius_api`    | —           | —                                   | ✅ (parse)     | `GENIUS_API_KEY` requise |
| `songbpm`       | —           | —                                   | rapide seule   | dernier recours BPM ; CF possible |
| `bpmfinder`     | —           | —                                   | manuelle       | **login requis** (`BPMFINDER_*`) |

> La capture d'Ultratop (BRMA) exige la route CDP : au premier lancement, une
> fenêtre Chrome visible s'ouvre pour résoudre le challenge Cloudflare **une
> fois** ; le cookie est ensuite mémorisé (profil persistant).
