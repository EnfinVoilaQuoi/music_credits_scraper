"""Arbitrage des streams sur une VRAIE base — le chemin que les tests unitaires
ne couvrent pas.

Deux sources écrivent le même champ (`spotify_streams`) : Kworb et le scrape des
pages Spotify. Chacune ne déclare que ce qu'elle a vu ; la valeur de la colonne
est arbitrée à l'écriture par `track_repository._arbitrer_streams`.

Ce qui se joue ici et nulle part ailleurs : les valeurs d'observation sont
stockées en **TEXT** (`"142"`, cf. `test_observations_repository`) alors que la
colonne est un INTEGER, et le `seen_at` revient brut. Un test à doubles ne
verrait rien de tout ça.
"""

from datetime import datetime

import pytest
from sqlalchemy import text

from src.models import Artist, Track

_KWORB_DATE = datetime(2020, 1, 15)


def _artiste(dm, name="Artiste Test"):
    a = Artist(name=name)
    a.id = dm.save_artist(a)
    return a


def _track(dm, artist, title="Morceau"):
    return dm.save_track(Track(title=title, artist=artist))


def _colonnes(dm, track_id):
    with dm.engine.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT spotify_streams, spotify_daily_streams, spotify_streams_updated "
                    "FROM tracks WHERE id = :tid"
                ),
                {"tid": track_id},
            )
            .mappings()
            .first()
        )


@pytest.fixture
def morceau(data_manager):
    return _track(data_manager, _artiste(data_manager))


def test_une_seule_source_ecrit_sa_valeur(data_manager, morceau):
    data_manager.record_spotify_streams(morceau, 1000, "kworb", _KWORB_DATE, daily_streams=10)
    assert _colonnes(data_manager, morceau)["spotify_streams"] == 1000


def test_la_valeur_en_colonne_est_un_ENTIER(data_manager, morceau):
    """Les observations sont stockées en TEXT : sans coercition, la colonne
    recevrait la chaîne « 1000 » et toute comparaison numérique en aval
    deviendrait fausse sans rien casser visiblement."""
    data_manager.record_spotify_streams(morceau, 1000, "kworb", _KWORB_DATE)
    assert isinstance(_colonnes(data_manager, morceau)["spotify_streams"], int)


def test_le_maitre_gagne_meme_avec_la_valeur_la_plus_basse(data_manager, morceau):
    """Le cœur du dispositif : ce n'est pas le plus grand nombre qui gagne."""
    data_manager.record_spotify_streams(morceau, 1000, "kworb", _KWORB_DATE)
    data_manager.record_spotify_streams(morceau, 9999, "spotify_web", datetime.now())
    assert _colonnes(data_manager, morceau)["spotify_streams"] == 1000


def test_l_ordre_des_sources_est_sans_effet(data_manager):
    """La propriété que l'arbitrage a fait gagner : « Kworb puis Spotify » et
    « Spotify puis Kworb » laissent la base dans le MÊME état."""
    artist = _artiste(data_manager)
    a = _track(data_manager, artist, "A")
    b = _track(data_manager, artist, "B")

    data_manager.record_spotify_streams(a, 1000, "kworb", _KWORB_DATE)
    data_manager.record_spotify_streams(a, 9999, "spotify_web", datetime.now())

    data_manager.record_spotify_streams(b, 9999, "spotify_web", datetime.now())
    data_manager.record_spotify_streams(b, 1000, "kworb", _KWORB_DATE)

    assert _colonnes(data_manager, a)["spotify_streams"] == (
        _colonnes(data_manager, b)["spotify_streams"]
    )


def test_bascule_du_maitre(data_manager, morceau, monkeypatch):
    """La promesse de réversibilité : un seul réglage change le verdict."""
    from src.config import settings

    data_manager.record_spotify_streams(morceau, 1000, "kworb", _KWORB_DATE)
    data_manager.record_spotify_streams(morceau, 9999, "spotify_web", datetime.now())
    assert _colonnes(data_manager, morceau)["spotify_streams"] == 1000

    monkeypatch.setattr(settings, "streams_master", "spotify_web")
    # Une nouvelle écriture rejoue l'arbitrage sur les observations existantes.
    data_manager.record_spotify_streams(morceau, 9999, "spotify_web", datetime.now())
    assert _colonnes(data_manager, morceau)["spotify_streams"] == 9999


def test_spotify_seul_sert_quand_kworb_est_muet(data_manager, morceau):
    """La raison d'être du chantier : Kworb ne couvre pas tous les artistes."""
    data_manager.record_spotify_streams(morceau, 777, "spotify_web", datetime.now())
    assert _colonnes(data_manager, morceau)["spotify_streams"] == 777


def test_spotify_n_efface_pas_le_quotidien_de_kworb(data_manager, morceau):
    """Spotify ne publie aucun chiffre quotidien. Kworb en est la seule source :
    une écriture Spotify ne doit pas le remettre à NULL au passage."""
    data_manager.record_spotify_streams(morceau, 1000, "kworb", _KWORB_DATE, daily_streams=42)
    data_manager.record_spotify_streams(morceau, 9999, "spotify_web", datetime.now())
    assert _colonnes(data_manager, morceau)["spotify_daily_streams"] == 42


def test_la_date_est_celle_de_l_observation_gagnante(data_manager, morceau):
    """Sans ça, une écriture Spotify daterait d'aujourd'hui une valeur qui vient
    en réalité de la dernière mise à jour de Kworb — un chiffre vieux de
    plusieurs mois présenté comme frais."""
    data_manager.record_spotify_streams(morceau, 1000, "kworb", _KWORB_DATE)
    data_manager.record_spotify_streams(morceau, 9999, "spotify_web", datetime.now())
    vue_le = str(_colonnes(data_manager, morceau)["spotify_streams_updated"])
    assert vue_le.startswith("2020-01-15")


def test_une_saisie_manuelle_prime_sur_les_deux(data_manager, morceau):
    data_manager.record_spotify_streams(morceau, 1000, "kworb", _KWORB_DATE)
    data_manager.record_spotify_streams(morceau, 5, "manual", datetime.now())
    assert _colonnes(data_manager, morceau)["spotify_streams"] == 5


# ── Albums : deux sémantiques, une colonne de provenance ─────────────────────
def _album(dm, artist_id, titre="Mon Album"):
    with dm.engine.connect() as conn:
        return (
            conn.execute(
                text(
                    "SELECT spotify_streams, spotify_daily_streams, spotify_streams_source "
                    "FROM albums WHERE artist_id = :aid AND title = :t"
                ),
                {"aid": artist_id, "t": titre},
            )
            .mappings()
            .first()
        )


def test_la_provenance_du_total_est_enregistree(data_manager):
    """Sans elle, la colonne porterait deux sémantiques en silence : la part de
    l'artiste (Kworb) et le total du disque (Spotify)."""
    artist = _artiste(data_manager)
    data_manager.upsert_album(artist.id, "Mon Album", 100, 5, source="kworb")
    assert _album(data_manager, artist.id)["spotify_streams_source"] == "kworb"


def test_un_total_kworb_n_ecrase_jamais_un_total_spotify(data_manager):
    """Le garde décisif. Kworb ne somme que les morceaux de l'artiste : sur un
    album commun, son total est INCOMPLET. Un run Kworb postérieur ramènerait
    donc silencieusement la valeur à la somme partielle."""
    artist = _artiste(data_manager)
    data_manager.upsert_album(artist.id, "Mon Album", 1000, None, source="spotify_web")
    data_manager.upsert_album(artist.id, "Mon Album", 300, 7, source="kworb")

    ligne = _album(data_manager, artist.id)
    assert ligne["spotify_streams"] == 1000
    assert ligne["spotify_streams_source"] == "spotify_web"


def test_un_total_spotify_remplace_un_total_kworb(data_manager):
    """L'inverse est permis : le total réel vaut mieux que la part de l'artiste."""
    artist = _artiste(data_manager)
    data_manager.upsert_album(artist.id, "Mon Album", 300, 7, source="kworb")
    data_manager.upsert_album(artist.id, "Mon Album", 1000, None, source="spotify_web")

    ligne = _album(data_manager, artist.id)
    assert ligne["spotify_streams"] == 1000
    assert ligne["spotify_streams_source"] == "spotify_web"


def test_spotify_n_efface_pas_le_quotidien_d_album_de_kworb(data_manager):
    """Spotify n'en publie pas et passe None : sans COALESCE, il effacerait."""
    artist = _artiste(data_manager)
    data_manager.upsert_album(artist.id, "Mon Album", 300, 7, source="kworb")
    data_manager.upsert_album(artist.id, "Mon Album", 1000, None, source="spotify_web")
    assert _album(data_manager, artist.id)["spotify_daily_streams"] == 7


def test_kworb_reste_maitre_entre_deux_passages_kworb(data_manager):
    """Le garde ne doit pas figer la valeur : deux runs Kworb successifs se
    mettent bien à jour l'un l'autre."""
    artist = _artiste(data_manager)
    data_manager.upsert_album(artist.id, "Mon Album", 300, 7, source="kworb")
    data_manager.upsert_album(artist.id, "Mon Album", 350, 8, source="kworb")
    assert _album(data_manager, artist.id)["spotify_streams"] == 350


def test_les_ids_d_edition_FUSIONNENT_au_lieu_de_s_ecraser(data_manager):
    """Chaque source ne connaît que les éditions qu'elle a vues : la page artiste
    Spotify n'en liste qu'une là où Kworb en a deux. Remplacer perdrait
    silencieusement l'autre — or c'est précisément la donnée à garder."""
    artist = _artiste(data_manager)
    data_manager.upsert_album(artist.id, "Mon Album", 300, 7, "AL1,AL2", source="kworb")
    data_manager.upsert_album(artist.id, "Mon Album", 1000, None, "AL1", source="spotify_web")

    with data_manager.engine.connect() as conn:
        ids = conn.execute(
            text("SELECT spotify_album_ids FROM albums WHERE artist_id = :a"),
            {"a": artist.id},
        ).scalar()
    assert set(ids.split(",")) == {"AL1", "AL2"}


def test_kworb_peut_completer_les_ids_d_un_album_arbitre_par_spotify(data_manager):
    """Le garde ne porte que sur le TOTAL : Kworb reste utile pour enrichir les
    IDs d'édition, même quand Spotify possède le total du disque."""
    artist = _artiste(data_manager)
    data_manager.upsert_album(artist.id, "Mon Album", 1000, None, "AL1", source="spotify_web")
    data_manager.upsert_album(artist.id, "Mon Album", 300, 7, "AL2", source="kworb")

    ligne = _album(data_manager, artist.id)
    assert ligne["spotify_streams"] == 1000, "le total Spotify reste maître"
    with data_manager.engine.connect() as conn:
        ids = conn.execute(
            text("SELECT spotify_album_ids FROM albums WHERE artist_id = :a"),
            {"a": artist.id},
        ).scalar()
    assert set(ids.split(",")) == {"AL1", "AL2"}
