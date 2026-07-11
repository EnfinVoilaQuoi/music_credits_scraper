# Fixtures de tests — pages réelles enregistrées

Pages HTML / réponses JSON **réelles** capturées une fois, commitées, et rejouées
hors ligne par les tests `tests/test_*_fixtures.py`. Quand un site change sa
structure, on re-capture la page : le test qui passe au rouge localise et
documente la casse (procédure complète : `docs/maintenance-sources.md`).

## Conventions

- Un sous-dossier par source : `kworb/`, `riaa/`, `genius/`, `spotify_embed/`,
  `brma/`, `lrclib/`, `getsongbpm/`…
- Chaque fixture est accompagnée d'un sidecar `<fichier>.meta.json` (URL, date
  de capture, méthode) écrit automatiquement par le script de capture.
- Les fixtures sont commitées **brutes** (pas d'élagage : Genius stocke des
  données dans des `<script>`, git compresse bien le HTML).
- Jamais de secret dans une fixture : la capture GetSongBPM ne stocke pas la
  clé API.

## (Re)capturer

```
python scripts/capture_fixtures.py --list
python scripts/capture_fixtures.py --all
python scripts/capture_fixtures.py --only kworb,riaa
```

Les tests **skippent** proprement si une fixture n'a pas encore été capturée.

## Sentinelles utilisées

Pages stables d'artistes du corpus (Josman) quand la source le permet ;
Daft Punk pour RIAA/GetSongBPM (catalogue stable, couvert par ces sources).
Le choix est centralisé en tête de `scripts/capture_fixtures.py`.
