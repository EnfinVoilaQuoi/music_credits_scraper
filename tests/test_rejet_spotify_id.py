"""Rejeter un ID Spotify fautif : les trois gestes indissociables.

Un identifiant faux ne se retire pas d'un seul endroit. Mesuré sur la base réelle
le 2026-09-08 : trois morceaux portaient l'ID d'un AUTRE morceau, et avec lui sa
durée, ses streams et — parce que ReccoBeats s'interroge PAR le Track ID — son
BPM et sa tonalité. Effacer la seule colonne `spotify_id` aurait retiré la
preuve du problème en laissant toutes ses conséquences.

L'oracle qui désigne le coupable est la page `/embed/track/{id}`, server-rendered :
elle donne titre, artistes et durée. La page `/track/{id}`, elle, est une
application JS dont le `<title>` vaut « Spotify » avant rendu — c'est pourquoi
`get_spotify_page_title` ne rendait JAMAIS rien.
"""

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track, TrackSpotifyId
from src.scrapers.spotify_id_scraper_v2 import SpotifyIDScraper

_ID = "3VXzVGAWFSrH47dBtTOPws"

_EMBED = """<html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"state":{"data":{"entity":{
  "name":"13 Organis\\u00e9","duration":1471000,
  "artists":[{"name":"SCH","uri":"spotify:artist:AAA"},
             {"name":"Jul","uri":"spotify:artist:BBB"}]}}}}}}
</script></body></html>"""


def _morceau(data_manager, titre="Rentre dans le Cercle", spotify_id=_ID, duree=1471) -> Track:
    artist = Artist(name="Swing")
    artist.id = data_manager.save_artist(artist)
    track = Track(title=titre, artist=artist)
    track.spotify_id = spotify_id
    track.duration = duree
    data_manager.save_track(track)
    return track


class TestOracleEmbed:
    """La logique de lecture est PURE : elle se teste sur du HTML, sans réseau."""

    def test_identite_lue_sur_lembed(self):
        identite = SpotifyIDScraper._identite_depuis_embed(_EMBED)
        assert identite == {"name": "13 Organisé", "artists": ["SCH", "Jul"], "duration": 1471}

    def test_la_duree_compte_autant_que_le_titre(self):
        """Un titre s'écrit de dix façons ; une durée est objective. C'est elle
        qui a confondu les trois attributions fautives."""
        assert SpotifyIDScraper._identite_depuis_embed(_EMBED)["duration"] == 1471

    def test_titre_de_page_lisible(self):
        titre = SpotifyIDScraper._titre_de_page(SpotifyIDScraper._identite_depuis_embed(_EMBED))
        assert titre == "13 Organisé • SCH, Jul"

    def test_une_page_sans_json_ne_conclut_pas(self):
        assert SpotifyIDScraper._identite_depuis_embed("<html>Spotify</html>") is None
        assert SpotifyIDScraper._titre_de_page(None) is None

    def test_les_artistes_derivent_du_meme_lecteur(self):
        """Un seul point de lecture du `__NEXT_DATA__` : titre, artistes et durée
        y vivent ensemble, et deux extracteurs finiraient par diverger."""
        assert [a["name"] for a in SpotifyIDScraper._parse_embed_artists(_EMBED)] == ["SCH", "Jul"]


class TestRejetDeLId:
    def test_les_trois_gestes(self, data_manager):
        track = _morceau(data_manager)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=_ID, source="scraper")]
        )
        data_manager.upsert_observations(
            track.id,
            [
                Observation(field="bpm", value="92", source="reccobeats"),
                Observation(field="spotify_streams", value="7701705", source="spotify_web"),
            ],
        )

        rapport = data_manager.clear_track_spotify_id(track.id)

        assert rapport["id_retire"] == _ID
        assert rapport["colonne_effacee"] is True
        assert data_manager.get_track_spotify_ids(track.id) == []
        with data_manager.engine.connect() as conn:
            ligne = (
                conn.execute(
                    text(
                        "SELECT spotify_id, spotify_page_title, spotify_id_checked_at, "
                        "spotify_streams FROM tracks WHERE id = :tid"
                    ),
                    {"tid": track.id},
                )
                .mappings()
                .first()
            )
        assert ligne["spotify_id"] is None
        assert ligne["spotify_page_title"] is None
        # « jamais cherché » plutôt que « cherché et absent » : le prochain run
        # doit REFAIRE la résolution, pas conclure que le morceau n'est pas sur
        # Spotify (c'est la sémantique de e17).
        assert ligne["spotify_id_checked_at"] is None
        assert ligne["spotify_streams"] is None

    def test_les_observations_venues_de_lid_partent(self, data_manager):
        """ReccoBeats s'interroge PAR le Track ID : son BPM et sa tonalité
        décrivent le morceau que l'ID désigne — un autre, quand l'ID est faux."""
        track = _morceau(data_manager)
        data_manager.upsert_observations(
            track.id,
            [
                Observation(field="bpm", value="92", source="reccobeats"),
                Observation(field="key", value="5", source="reccobeats"),
                Observation(field="spotify_streams", value="7701705", source="kworb"),
            ],
        )

        rapport = data_manager.clear_track_spotify_id(track.id)

        assert sorted(rapport["observations_retirees"]) == [
            ("bpm", "reccobeats"),
            ("key", "reccobeats"),
            ("spotify_streams", "kworb"),
        ]
        assert data_manager.get_observations(track.id) == []

    def test_le_quotidien_de_kworb_part_aussi(self, data_manager):
        """`spotify_daily_streams` ne vient QUE de Kworb, qui attribue par ID.

        Il n'est arbitré par personne — aucune autre source ne publie de
        quotidien — donc aucun verdict ne le remet d'aplomb : laissé en place, il
        serait le dernier reliquat visible du morceau de quelqu'un d'autre.
        Oublié au premier jet ; 3 lignes le portaient encore après le nettoyage
        des 99 identifiants fautifs.
        """
        track = _morceau(data_manager)
        data_manager.record_spotify_streams(track.id, 7701705, "kworb", daily_streams=4200)
        data_manager.upsert_observations(
            track.id, [Observation(field="spotify_streams", value="7701705", source="kworb")]
        )

        data_manager.clear_track_spotify_id(track.id)

        with data_manager.engine.connect() as conn:
            ligne = (
                conn.execute(
                    text(
                        "SELECT spotify_streams, spotify_daily_streams "
                        "FROM tracks WHERE id = :tid"
                    ),
                    {"tid": track.id},
                )
                .mappings()
                .first()
            )
        assert ligne["spotify_streams"] is None
        assert ligne["spotify_daily_streams"] is None

    def test_songbpm_survit_au_rejet(self, data_manager):
        """SongBPM cherche par ARTISTE et TITRE, pas par ID : ses mesures ne
        décrivent pas un autre morceau et n'ont aucune raison de partir."""
        track = _morceau(data_manager)
        data_manager.upsert_observations(
            track.id,
            [
                Observation(field="bpm", value="92", source="reccobeats"),
                Observation(field="bpm", value="140", source="songbpm"),
                Observation(field="lyrics_synced", value="[00:01]…", source="lrclib"),
            ],
        )

        data_manager.clear_track_spotify_id(track.id)

        restantes = {(o.field, o.source) for o in data_manager.get_observations(track.id)}
        assert restantes == {("bpm", "songbpm"), ("lyrics_synced", "lrclib")}

    def test_la_duree_est_signalee_et_non_effacee(self, data_manager):
        """La durée n'a pas de provenance en base : ReccoBeats ne l'écrit QUE si
        elle est vide, donc une durée venue de Deezer ne vient pas de l'ID. On
        ne peut pas prouver laquelle on a — on SIGNALE, l'humain tranche."""
        track = _morceau(data_manager, duree=1471)
        data_manager.upsert_observations(
            track.id, [Observation(field="bpm", value="92", source="reccobeats")]
        )

        rapport = data_manager.clear_track_spotify_id(track.id)

        assert rapport["duree_suspecte"] == 1471
        with data_manager.engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT duration FROM tracks WHERE id = :tid"), {"tid": track.id}
                ).scalar()
                == 1471
            )

        assert data_manager.clear_track_duration(track.id) is True
        with data_manager.engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT duration FROM tracks WHERE id = :tid"), {"tid": track.id}
                ).scalar()
                is None
            )

    def test_sans_observation_reccobeats_la_duree_nest_pas_suspecte(self, data_manager):
        track = _morceau(data_manager, duree=249)
        rapport = data_manager.clear_track_spotify_id(track.id)
        assert rapport["duree_suspecte"] is None

    def test_rejeter_une_edition_alternative_ne_touche_pas_au_principal(self, data_manager):
        """Le rejet vise UN identifiant. Retirer une édition ne doit pas effacer
        l'ID principal, qui est celui qu'on ouvre et qu'on interroge."""
        track = _morceau(data_manager, spotify_id=_ID)
        autre = "5go793BaOjzfop2JwctQ0r"
        data_manager.record_track_spotify_ids(
            track.id,
            [
                TrackSpotifyId(spotify_id=_ID, source="scraper"),
                TrackSpotifyId(spotify_id=autre, source="kworb"),
            ],
        )

        rapport = data_manager.clear_track_spotify_id(track.id, autre)

        assert rapport["colonne_effacee"] is False
        assert [e.spotify_id for e in data_manager.get_track_spotify_ids(track.id)] == [_ID]
        with data_manager.engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT spotify_id FROM tracks WHERE id = :tid"), {"tid": track.id}
                ).scalar()
                == _ID
            )

    def test_une_edition_survivante_cesse_d_etre_principale(self, data_manager):
        """`is_primary` recopie `tracks.spotify_id` : la colonne effacée, plus
        aucune édition n'est principale.

        Le laisser levé ferait du drapeau un SECOND verdict, en contradiction
        avec le premier — et l'édition survivante n'a pas été jugée digne d'être
        celle qu'on ouvre. Constaté sur la base réelle après le nettoyage du
        2026-09-08 : A2H « Yacht Music » gardait une édition Kworb marquée
        principale alors que sa colonne venait d'être vidée.
        """
        track = _morceau(data_manager, spotify_id=_ID)
        autre = "5go793BaOjzfop2JwctQ0r"
        data_manager.record_track_spotify_ids(
            track.id,
            [
                TrackSpotifyId(spotify_id=_ID, source="scraper"),
                TrackSpotifyId(spotify_id=autre, source="kworb"),
            ],
        )

        data_manager.clear_track_spotify_id(track.id, _ID)

        (restante,) = data_manager.get_track_spotify_ids(track.id)
        assert restante.spotify_id == autre
        assert restante.is_primary is False

    def test_un_morceau_sans_id_ne_fait_rien(self, data_manager):
        track = _morceau(data_manager, spotify_id=None)
        rapport = data_manager.clear_track_spotify_id(track.id)
        assert rapport["id_retire"] is None
        assert rapport["colonne_effacee"] is False
