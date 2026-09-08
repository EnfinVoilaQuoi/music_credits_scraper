"""Les colonnes d'IDENTITÉ se remplissent, elles ne se remplacent pas.

`save_track` annonçait un « UPDATE NON-DESTRUCTIF ». C'est vrai face à un `NULL`,
faux face à une valeur concurrente : `COALESCE(:new, old)` rend `:new` **dès
qu'il est non nul**, donc un identifiant entrant écrasait silencieusement celui
qui était en base. « La dernière écriture gagne » n'est pas une règle, c'est un
effet de bord de l'ordre d'exécution.

Découvert le 2026-09-08 par l'incohérence de `is_primary` : 19 lignes où
`track_spotify_ids` disait « principal » d'un ID que la colonne n'avait plus.

`genius_id` est le cas grave — c'est la clé de l'ENREGISTREMENT, celle par
laquelle le partage entre lignes sœurs les retrouve. Le réécrire en silence
ferait fusionner les données de deux enregistrements différents.
"""

import pytest
from sqlalchemy import text

from src.models import Artist, Track, TrackSpotifyId

_ID_A = "3VXzVGAWFSrH47dBtTOPws"
_ID_B = "5go793BaOjzfop2JwctQ0r"


@pytest.fixture
def artiste(data_manager):
    a = Artist(name="Flynt")
    a.id = data_manager.save_artist(a)
    return a


def _colonne(data_manager, track_id, colonne):
    with data_manager.engine.connect() as conn:
        return conn.execute(
            text(f"SELECT {colonne} FROM tracks WHERE id = :tid"), {"tid": track_id}
        ).scalar()


CAS = [
    ("genius_id", 61280, 343518),
    ("spotify_id", _ID_A, _ID_B),
    ("isrc", "FRPJQ1501290", "FR26V2011526"),
    ("discogs_id", 111, 222),
    ("deezer_id", 333, 444),
]


class TestOnRemplitSansRemplacer:
    @pytest.mark.parametrize(("colonne", "premiere", "seconde"), CAS)
    def test_une_valeur_concurrente_ne_remplace_pas(
        self, data_manager, artiste, colonne, premiere, seconde
    ):
        track = Track(title="Un pour la plume", artist=artiste)
        setattr(track, colonne, premiere)
        data_manager.save_track(track)

        setattr(track, colonne, seconde)
        data_manager.save_track(track)

        assert _colonne(data_manager, track.id, colonne) == premiere

    @pytest.mark.parametrize(("colonne", "premiere", "seconde"), CAS)
    def test_une_colonne_vide_se_remplit_toujours(
        self, data_manager, artiste, colonne, premiere, seconde
    ):
        """La règle est « le PREMIER renseigne » — pas « on n'écrit jamais »."""
        track = Track(title="Un pour la plume", artist=artiste)
        data_manager.save_track(track)
        assert _colonne(data_manager, track.id, colonne) is None

        setattr(track, colonne, premiere)
        data_manager.save_track(track)

        assert _colonne(data_manager, track.id, colonne) == premiere

    @pytest.mark.parametrize("colonne", ["spotify_id", "isrc"])
    def test_la_chaine_vide_compte_comme_vide(self, data_manager, artiste, colonne):
        """Sur les colonnes TEXTE, `''` dit la même chose que `NULL` — « rien » —
        et doit se laisser remplir, sinon une ligne resterait bloquée à vide."""
        track = Track(title="Un pour la plume", artist=artiste)
        setattr(track, colonne, "")
        data_manager.save_track(track)

        setattr(track, colonne, _ID_A)
        data_manager.save_track(track)

        assert _colonne(data_manager, track.id, colonne) == _ID_A

    def test_reecrire_la_meme_valeur_ne_change_rien(self, data_manager, artiste):
        track = Track(title="Un pour la plume", artist=artiste)
        track.genius_id = 61280
        data_manager.save_track(track)
        data_manager.save_track(track)
        assert _colonne(data_manager, track.id, "genius_id") == 61280


class TestSignalement:
    def test_un_genius_id_concurrent_est_signale_en_ERROR(self, data_manager, artiste, caplog):
        """La clé de l'ENREGISTREMENT : un changement veut dire soit une erreur,
        soit une ré-identification volontaire. Les deux méritent un humain,
        aucune ne mérite le silence — et `logger.debug` n'atteint aucun fichier
        de log (handler à INFO)."""
        track = Track(title="Un pour la plume", artist=artiste)
        track.genius_id = 61280
        data_manager.save_track(track)

        track.genius_id = 343518
        with caplog.at_level("ERROR"):
            data_manager.save_track(track)

        assert any("genius_id concurrent" in m for m in caplog.messages)
        assert any("343518 REFUSÉ" in m for m in caplog.messages)

    def test_un_spotify_id_concurrent_ne_crie_pas(self, data_manager, artiste, caplog):
        """Une seconde édition est le cas NORMAL (un titre sort en single puis
        sur l'album) : la signaler en ERROR ferait crier le garde-fou en
        permanence, et un garde-fou qui crie toujours ne garde rien."""
        track = Track(title="Un pour la plume", artist=artiste)
        track.spotify_id = _ID_A
        data_manager.save_track(track)

        track.spotify_id = _ID_B
        with caplog.at_level("ERROR"):
            data_manager.save_track(track)

        assert not any("spotify_id" in m for m in caplog.messages)

    def test_remplir_une_colonne_vide_ne_signale_rien(self, data_manager, artiste, caplog):
        track = Track(title="Un pour la plume", artist=artiste)
        data_manager.save_track(track)

        track.genius_id = 61280
        with caplog.at_level("ERROR"):
            data_manager.save_track(track)

        assert not any("concurrent" in m for m in caplog.messages)


class TestRienNestPerdu:
    def test_l_edition_concurrente_atterrit_dans_la_table(self, data_manager, artiste):
        """Refuser l'écrasement ne perd RIEN : un second ID Spotify est presque
        toujours une autre ÉDITION du même enregistrement, et `track_spotify_ids`
        (e23) la garde. C'est ce qui rend la règle sans coût."""
        track = Track(title="Un pour la plume", artist=artiste)
        track.spotify_id = _ID_A
        data_manager.save_track(track)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_A, source="genius_media")]
        )

        # Un producteur propose une autre édition : la colonne ne bouge pas…
        track.spotify_id = _ID_B
        data_manager.save_track(track)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID_B, source="kworb")]
        )

        assert _colonne(data_manager, track.id, "spotify_id") == _ID_A
        # …mais l'édition est CONNUE, et reconnue à la récolte croisée.
        entrees = {e.spotify_id: e.is_primary for e in data_manager.get_track_spotify_ids(track.id)}
        assert entrees == {_ID_A: True, _ID_B: False}
        assert set(data_manager.get_track_ids_by_spotify_id()) >= {_ID_A, _ID_B}
