"""Fixtures partagées.

`data_manager` : DataManager branché sur une base SQLite TEMPORAIRE (base
vierge à chaque test), avec le chargement des certifications neutralisé
(il importerait les CSV SNEP réels de data/ — lent et non hermétique).

Ces tests de comportement serviront de filet de sécurité pour la migration
SQLAlchemy 2.0 + Alembic : ils ne testent que l'API publique (save/get),
jamais le SQL.

`load_fixture` / `load_fixture_json` : chargement des pages réelles
enregistrées dans tests/fixtures/ (capture : scripts/capture_fixtures.py).
Skip propre si la fixture n'a pas encore été capturée — la suite reste verte
sur un clone frais.
"""

import json
import os
from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Tests PyQt6 (pytest-qt) : jamais de fenêtre à l'écran, en local comme en CI.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def src_d_ailleurs(fichier_src: str | None, racine: Path) -> str | None:
    """PUR. Pourquoi le paquet `src` importé (`src.__file__`) n'est pas celui
    de `racine` (le dossier où tourne pytest), ou None s'il l'est."""
    if not fichier_src:
        return "le paquet `src` importé n'a pas de fichier (paquet d'espace de noms ?)"
    dossier = Path(fichier_src).resolve().parent
    attendu = (racine / "src").resolve()
    if os.path.normcase(str(dossier)) == os.path.normcase(str(attendu)):
        return None
    return f"`src` importé depuis {dossier}, et non {attendu}"


def pytest_configure(config):
    """Les tests jugent le code du dossier où tourne pytest, ou ne tournent pas.

    Cas d'origine (2026-10-30) : un agent lancé dans un worktree importait le
    `src` du checkout principal (installation `pip install -e .`), et ses mesures
    portaient sur le mauvais code sans que rien ne le dise. → arrêt immédiat.
    """
    import src

    raison = src_d_ailleurs(getattr(src, "__file__", None), Path(config.rootpath))
    if raison:
        pytest.exit(
            f"Mauvais code sous test : {raison}. Mettre le dossier testé en tête de "
            f'PYTHONPATH (PowerShell : $env:PYTHONPATH = "{config.rootpath}") puis '
            'relancer ; vérifier par `python -c "import src; print(src.__file__)"`.',
            returncode=pytest.ExitCode.USAGE_ERROR,
        )


def load_fixture(relpath: str) -> str:
    """Contenu texte d'une fixture enregistrée (skip si absente)."""
    path = FIXTURES_DIR / relpath
    if not path.exists():
        pytest.skip(f"fixture manquante: {relpath} — lancer scripts/capture_fixtures.py")
    return path.read_text(encoding="utf-8")


def load_fixture_json(relpath: str) -> dict:
    """Fixture JSON parsée (skip si absente)."""
    return json.loads(load_fixture(relpath))


@pytest.fixture(autouse=True)
def _oracle_spotify_hors_ligne(monkeypatch):
    """AUCUN test ne parle à Spotify.

    Le garde-fou de justesse d'un `spotify_id` (2026-09-08) interroge la page
    `/embed/` quand aucun lecteur ne lui est injecté. Branché tel quel, il rendait
    la suite NON HERMÉTIQUE — exactement le défaut relevé sur le repli LLM, où un
    test partait soit en timeout de 34 s, soit en vraie inférence.

    Le lecteur rend `None`, ce qui signifie « on ne conclut pas » : un ID non
    vérifiable n'est pas un ID fautif, donc le comportement par défaut des tests
    est inchangé. Un test qui veut EXERCER le garde-fou injecte son propre
    lecteur (ou appelle `identite_concorde`, qui est pur).
    """
    monkeypatch.setattr("src.utils.spotify_identity.lire_identite_http", lambda spotify_id: None)
    # La page titre (disque d'un titre générique) ouvrirait un navigateur.
    monkeypatch.setattr("src.utils.spotify_identity.lire_album_page", lambda spotify_id: None)
    # Même règle pour l'oracle Deezer de l'audit des ids (2026-09-22) et de
    # l'audit des ISRC hérités (2026-09-23).
    monkeypatch.setattr("src.utils.deezer_identity.lire_piste_http", lambda deezer_id: None)
    monkeypatch.setattr("src.utils.deezer_identity.lire_piste_isrc_http", lambda isrc: None)


@pytest.fixture(autouse=True)
def _aucune_boite_de_dialogue(monkeypatch):
    """AUCUN test n'ouvre de boîte de dialogue Tk.

    Même famille que les oracles hors ligne : un `messagebox` réel ATTEND un
    clic — un test de la vue morceaux a laissé une popup « Erreur de tri » à
    l'écran bien après la fin du run, et mis 82 s à rendre la main
    (2026-09-22). Les réponses par défaut sont neutres : « non » à une
    question, rien à afficher. Un test qui veut EXERCER un dialogue
    monkeypatche sa propre réponse par-dessus.
    """
    from tkinter import messagebox

    for nom, reponse in (
        ("showinfo", None),
        ("showwarning", None),
        ("showerror", None),
        ("askyesno", False),
        ("askokcancel", False),
        ("askretrycancel", False),
        ("askquestion", "no"),
    ):
        monkeypatch.setattr(messagebox, nom, lambda *a, _r=reponse, **k: _r, raising=False)


@pytest.fixture(autouse=True)
def _gate_spotify_sans_fournisseur(monkeypatch):
    """Le fournisseur de titres du gate Spotify est posé par `DataManager` : sans
    ce cran, il survivrait d'un test à l'autre en pointant une base détruite."""
    from src.utils import spotify_identity

    monkeypatch.setattr(spotify_identity, "fournisseur_titres", None)
    monkeypatch.setattr(spotify_identity, "fournisseur_propres", None)
    monkeypatch.setattr(spotify_identity, "fournisseur_ids_artiste", None)
    monkeypatch.setattr(spotify_identity, "fournisseur_ids_du_disque", None)
    from src.utils import titre_annee

    monkeypatch.setattr(titre_annee, "fournisseur_durees", None)


@pytest.fixture(autouse=True)
def _brut_isole(tmp_path, monkeypatch):
    """AUCUN test n'écrit le brut réel des sources (`data/brut/brut.db`)."""
    from src.persistence import brut

    brut.fermer()
    monkeypatch.setattr(brut, "CHEMIN", tmp_path / "brut" / "brut.db")
    yield
    brut.fermer()


@pytest.fixture(autouse=True)
def _corrections_de_fiches_isolees(tmp_path, monkeypatch):
    """AUCUN test ne lit `data/corrections/fiches.json` réel : il est relu par le
    gate Spotify, `save_track` et l'import discographie (2026-09-24)."""
    from src.utils import corrections_fiches

    monkeypatch.setattr(corrections_fiches, "FICHIER", tmp_path / "corrections_fiches.json")
    # Le moteur est posé par chaque `DataManager` construit : sans remise à
    # zéro, un test SANS base lirait et écrirait la base d'un test précédent.
    monkeypatch.setattr(corrections_fiches, "moteur", None)
    from src.utils import kworb_links_manager

    monkeypatch.setattr(kworb_links_manager, "moteur", None)


@pytest.fixture(autouse=True)
def _cache_des_tracklists_isole(tmp_path, monkeypatch):
    """AUCUN test n'écrit `data/tracklists_genius.json` réel (run discographie)."""
    from src.services import tracklists_genius

    monkeypatch.setattr(tracklists_genius, "FICHIER", tmp_path / "tracklists_genius.json")


@pytest.fixture(autouse=True)
def _cache_des_nouveautes_isole(tmp_path, monkeypatch):
    """AUCUN test n'écrit le cache du badge « nouveautés » réel.

    Un run discographie l'OUBLIE pour l'artiste traité (2026-09-24) : sans ce
    cran, un test de `discographie.run` retirait l'entrée de l'artiste d'id 1
    dans `data/nouveautes_cache.json`.
    """
    from src.utils import nouveautes_cache

    monkeypatch.setattr(nouveautes_cache, "FICHIER", tmp_path / "nouveautes_cache.json")


@pytest.fixture(autouse=True)
def _aucun_chrome_de_debug(monkeypatch):
    """AUCUN test ne lance Chrome.

    `ultratop_fetch.preparer_route_cdp` (2026-09-17) lance un Chrome de debug
    quand `GENIUS_CDP_URL` manque — c'est son rôle, et tout ce qui scrape
    Ultratop y passe (`update_brma.main`, la sonde complète, la capture de
    fixtures). Sans ce cran, un test qui traverse `main()` ouvrirait un vrai
    navigateur sur la machine du développeur et échouerait sur le CI, qui n'en a
    pas. Le neutraliser rend « Chrome introuvable », le cas le plus proche du
    CI ; un test qui veut EXERCER la route pose la variable ou remplace
    `ensure_cdp_chrome` lui-même. `GENIUS_CDP_URL` est retirée pour que le `.env`
    du développeur ne fasse pas passer un test qui échoue ailleurs.
    """
    monkeypatch.delenv("GENIUS_CDP_URL", raising=False)
    monkeypatch.setattr("src.scrapers.cdp_chrome.ensure_cdp_chrome", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def _aucun_export_de_decisions(monkeypatch):
    """AUCUN test n'écrit dans le dossier versionné des décisions.

    `cli.main` exporte les décisions en fin de run vers `DECISIONS_DIR`
    (2026-10-19) — sur la machine du développeur, un dépôt git réel. Un test qui
    veut l'export pose lui-même `config.DECISIONS_DIR`.
    """
    monkeypatch.setattr("src.config.DECISIONS_DIR", None)


@pytest.fixture(autouse=True)
def _aucune_copie_du_brut(monkeypatch):
    """AUCUN test ne copie de brut vers `BRUT_BACKUP_DIR` (le dossier réel du
    développeur, souvent un autre disque). Un test qui veut la copie pose
    lui-même `config.BRUT_BACKUP_DIR`."""
    monkeypatch.setattr("src.config.BRUT_BACKUP_DIR", None)
    monkeypatch.setattr("src.config.BASE_BACKUP_EXTERNE_DIR", None)


@pytest.fixture(scope="session")
def _gabarit_base_migree(tmp_path_factory):
    """UNE base migrée jusqu'à `head` pour toute la session, copiée par test.

    Chaque `DataManager()` sur un fichier neuf rejoue toutes les révisions
    Alembic : 1,1 s par base, ~600 bases par suite, soit ~11 des 14 minutes
    (mesuré 2026-10-11). Sur une copie déjà à `head`, `_ensure_schema` ne fait
    plus rien : 0,03 s. La migration elle-même reste testée là où c'est son
    sujet (`test_alembic_*`, qui construisent leurs propres bases).
    """
    import src.utils.data_manager as dm_mod

    gabarit = tmp_path_factory.mktemp("gabarit") / "gabarit.db"
    origine = dm_mod.DATABASE_URL
    dm_mod.DATABASE_URL = f"sqlite:///{gabarit.as_posix()}"
    try:
        dm_mod.DataManager()
    finally:
        dm_mod.DATABASE_URL = origine
    return gabarit


@pytest.fixture
def data_manager(tmp_path, monkeypatch, _gabarit_base_migree):
    # DataManager n'importe plus les certifications au démarrage (elles vivent
    # dans certif_snep.csv) : seule la base à isoler reste à monkeypatcher.
    import sqlite3
    from contextlib import closing

    import src.utils.data_manager as dm_mod

    db_file = tmp_path / "test_music_credits.db"
    # Par l'API de sauvegarde, pas fichier par fichier : en WAL (2026-10-20),
    # des écritures peuvent n'être que dans le `-wal` du gabarit.
    with (
        closing(sqlite3.connect(_gabarit_base_migree)) as src,
        closing(sqlite3.connect(db_file)) as dest,
    ):
        src.backup(dest)
    monkeypatch.setattr(dm_mod, "DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    return dm_mod.DataManager()


@pytest.fixture(scope="session")
def racine_tk():
    """UNE racine Tk pour TOUTE la session. Recréer Tk après `destroy()` dans le
    même process rate sous Windows (« Can't find a usable init.tcl ») : chaque
    fichier qui créait la sienne faisait tomber les suivants en « aucun
    affichage disponible » — des tests GUI ignorés en silence dès qu'ils ne
    tournaient pas seuls (mesuré 2026-09-26 : `test_snep_slugs` 19/19 seul,
    ignoré en lot)."""
    ctk = pytest.importorskip("customtkinter")
    try:
        root = ctk.CTk()
    except Exception as exc:  # noqa: BLE001 — pas d'affichage (CI headless)
        pytest.skip(f"aucun affichage disponible : {exc!r}")
    root.withdraw()
    yield root
    root.destroy()


def pytest_runtest_teardown(item):
    """Après un test qui a touché Tk, le ramasse-miettes passe SUR LE FIL
    PRINCIPAL. Sinon les variables Tk de ses widgets restent en cycles et sont
    collectées plus tard par n'importe quel fil : un fil de fond qui détruit un
    objet Tk attend ~1 s la boucle principale puis lève « main thread is not
    in main loop » — 5 objets, 5 s, et `test_en_fond_signale_l_echec` dépassait
    son délai en CI (2026-10-11)."""
    if "racine_tk" in getattr(item, "fixturenames", ()):
        import gc

        gc.collect()
