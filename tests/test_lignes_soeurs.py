"""Lot C — un enregistrement, plusieurs lignes, une seule vérité.

`UNIQUE(title, artist_id)` fait qu'un même enregistrement existe une fois par
artiste crédité. Ces lignes étaient enrichies INDÉPENDAMMENT : mesuré le
2026-09-08, « Grünt #33 » portait 36 crédits, les paroles et les streams chez
Swing, et 0 crédit, 0 parole, 0 observation chez Isha.

La règle tient en une phrase — *la propagation ne transporte que ce qui est
RENSEIGNÉ, elle ne vide jamais une sœur* — et c'est elle qui rend l'ordre
d'écriture inoffensif.
"""

import pytest
from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track, TrackSpotifyId, TrackVideo
from src.models.track import Credit, CreditRole

_GENIUS = 3365192


@pytest.fixture
def famille(data_manager):
    """Deux lignes du MÊME enregistrement, chez DEUX artistes — des jumelles."""
    swing = Artist(name="Swing")
    swing.id = data_manager.save_artist(swing)
    isha = Artist(name="Isha")
    isha.id = data_manager.save_artist(isha)

    chez_swing = Track(title="Grünt #33", artist=swing)
    chez_swing.genius_id = _GENIUS
    data_manager.save_track(chez_swing)

    chez_isha = Track(title="Grünt #33", artist=isha)
    chez_isha.genius_id = _GENIUS
    chez_isha.is_featuring = True
    chez_isha.primary_artist_name = "Swing"
    data_manager.save_track(chez_isha)
    return chez_swing, chez_isha


def _colonne(data_manager, track_id, colonne):
    with data_manager.engine.connect() as conn:
        return conn.execute(
            text(f"SELECT {colonne} FROM tracks WHERE id = :t"), {"t": track_id}
        ).scalar()


class TestPropagation:
    def test_les_paroles_arrivent_chez_la_soeur(self, data_manager, famille):
        riche, pauvre = famille
        riche.lyrics.text = "Premier couplet…"
        riche.lyrics.source = "genius"
        data_manager.save_track(riche)

        assert _colonne(data_manager, pauvre.id, "lyrics") == "Premier couplet…"
        assert _colonne(data_manager, pauvre.id, "lyrics_source") == "genius"

    def test_les_credits_sont_UNIS(self, data_manager, famille):
        """UNION, jamais remplacement : `save_track` fait `DELETE FROM credits`
        puis réinsertion, donc un save venu d'un flux qui ne porte pas les
        crédits les EFFACE. Une propagation qui remplacerait viderait la sœur au
        premier save pauvre."""
        riche, pauvre = famille
        riche.credits = [Credit(name="Doums", role=CreditRole.FEATURED)]
        data_manager.save_track(riche)

        credits_pauvre = data_manager.get_artist_tracks(pauvre.artist.id)[0].credits
        assert [c.name for c in credits_pauvre] == ["Doums"]

    def test_un_save_SANS_credits_ne_vide_pas_la_soeur(self, data_manager, famille):
        """LE piège du lot. Un flux qui ne porte pas les crédits ne doit pas les
        emporter chez les autres."""
        riche, pauvre = famille
        riche.credits = [Credit(name="Doums", role=CreditRole.FEATURED)]
        data_manager.save_track(riche)

        pauvre.credits = []  # un run qui n'a rien scrapé
        data_manager.save_track(pauvre)

        for track_id, artist_id in ((riche.id, riche.artist.id), (pauvre.id, pauvre.artist.id)):
            credits = data_manager.get_artist_tracks(artist_id)[0].credits
            assert [c.name for c in credits] == ["Doums"], f"perdus sur #{track_id}"

    def test_les_observations_sont_partagees(self, data_manager, famille):
        """La provenance décrit l'ENREGISTREMENT : ce que Deezer a mesuré ne
        dépend pas de l'artiste sous lequel on le regarde."""
        riche, pauvre = famille
        riche.observations = [
            Observation(field="bpm", value=92, source="deezer"),
            Observation(field="duration", value=249, source="deezer"),
        ]
        data_manager.save_track(riche)

        relu = data_manager.get_artist_tracks(pauvre.artist.id)[0]
        assert relu.audio.bpm == 92
        assert relu.duration == 249

    def test_les_videos_et_ids_spotify_sont_REUNIS(self, data_manager, famille):
        """Tables additives : aucun producteur n'en connaît la liste complète,
        donc chaque sœur en connaît un morceau."""
        chez_swing, chez_isha = famille
        data_manager.record_track_videos(
            chez_swing.id, [TrackVideo(video_id="aaaaaaaaaaa", source="genius_media")]
        )
        data_manager.record_track_spotify_ids(
            chez_isha.id, [TrackSpotifyId(spotify_id="3VXzVGAWFSrH47dBtTOPws", source="kworb")]
        )
        data_manager.save_track(chez_swing)  # une écriture quelconque déclenche la sync

        assert [v.video_id for v in data_manager.get_track_videos(chez_isha.id)] == ["aaaaaaaaaaa"]
        assert [e.spotify_id for e in data_manager.get_track_spotify_ids(chez_swing.id)] == [
            "3VXzVGAWFSrH47dBtTOPws"
        ]


class TestPartition:
    """Ce qui décrit la LIGNE d'un artiste ne bouge pas."""

    def test_le_contexte_artiste_reste_propre_a_chaque_ligne(self, data_manager, famille):
        chez_swing, chez_isha = famille
        chez_swing.album = "Saison 1"
        chez_swing.track_number = 11
        data_manager.save_track(chez_swing)

        assert _colonne(data_manager, chez_isha.id, "album") is None
        assert _colonne(data_manager, chez_isha.id, "track_number") is None
        # « À la base » est un feat chez l'un et un morceau principal chez l'autre.
        assert _colonne(data_manager, chez_isha.id, "is_featuring") == 1
        assert _colonne(data_manager, chez_swing.id, "is_featuring") == 0

    def test_les_certifications_ne_sont_PAS_partagees(self, data_manager, famille):
        """Elles ont un écrivain dédié qui les RECALCULE par artiste. Les
        partager entrerait en conflit avec ce recalcul — et l'incident du
        2026-09-06 (20 rattachements fautifs) venait d'une attribution trop
        généreuse."""
        chez_swing, chez_isha = famille
        data_manager.record_certifications(
            chez_swing.id, [{"body": "SNEP", "certification": "Or"}], []
        )
        data_manager.save_track(chez_swing)

        assert _colonne(data_manager, chez_isha.id, "certifications") in (None, "[]")


class TestGardeFous:
    def test_sans_genius_id_aucune_propagation(self, data_manager):
        """Le champ est nullable : rien ne permet alors de rattacher la ligne à
        un enregistrement, et deviner serait pire que ne rien faire."""
        a = Artist(name="A")
        a.id = data_manager.save_artist(a)
        b = Artist(name="B")
        b.id = data_manager.save_artist(b)
        t1 = Track(title="Sans clé", artist=a)
        t1.lyrics.text = "des paroles"
        data_manager.save_track(t1)
        t2 = Track(title="Sans clé", artist=b)
        data_manager.save_track(t2)

        assert _colonne(data_manager, t2.id, "lyrics") is None

    def test_un_doublon_intra_artiste_est_SIGNALE_et_non_synchronise(self, data_manager, caplog):
        """Après partage, deux lignes d'un doublon paraîtraient identiques — et
        c'est leur DIVERGENCE qui le trahit aujourd'hui. Le rendre invisible
        serait le pire service à rendre."""
        josman = Artist(name="Josman")
        josman.id = data_manager.save_artist(josman)
        boss = Track(title="BOSS", artist=josman)
        boss.genius_id = 638651
        boss.lyrics.text = "des paroles"
        data_manager.save_track(boss)

        bis = Track(title="Boss", artist=josman)  # même artiste, même genius_id
        bis.genius_id = 638651
        with caplog.at_level("WARNING"):
            data_manager.save_track(bis)

        assert any("Doublon INTRA-ARTISTE" in m for m in caplog.messages)
        assert _colonne(data_manager, bis.id, "lyrics") is None  # non synchronisé

    def test_la_synchronisation_est_idempotente(self, data_manager, famille):
        """Elle ne fait que des unions et des comblements : l'appeler deux fois,
        ou dans n'importe quel ordre, donne le même résultat."""
        riche, pauvre = famille
        riche.credits = [Credit(name="Doums", role=CreditRole.FEATURED)]
        riche.lyrics.text = "Premier couplet…"
        data_manager.save_track(riche)
        data_manager.save_track(riche)
        data_manager.save_track(pauvre)

        credits = data_manager.get_artist_tracks(pauvre.artist.id)[0].credits
        assert [c.name for c in credits] == ["Doums"]  # pas de doublon de crédit


class TestHeritageALaCreation:
    def test_une_ligne_NEUVE_herite_de_ses_soeurs(self, data_manager):
        """Le cas qui a motivé tout le chantier : ajouter L'Or du Commun ne doit
        pas re-scraper des morceaux déjà entièrement enrichis sous Swing."""
        swing = Artist(name="Swing")
        swing.id = data_manager.save_artist(swing)
        deja = Track(title="Grünt #33", artist=swing)
        deja.genius_id = _GENIUS
        deja.lyrics.text = "Premier couplet…"
        deja.credits = [Credit(name="Doums", role=CreditRole.FEATURED)]
        deja.observations = [Observation(field="bpm", value=92, source="deezer")]
        data_manager.save_track(deja)

        groupe = Artist(name="L'Or du Commun")
        groupe.id = data_manager.save_artist(groupe)
        neuve = Track(title="Grünt #33", artist=groupe)
        neuve.genius_id = _GENIUS
        data_manager.save_track(neuve)  # aucun scrape

        relu = data_manager.get_artist_tracks(groupe.id)[0]
        assert relu.lyrics.text == "Premier couplet…"
        assert [c.name for c in relu.credits] == ["Doums"]
        assert relu.audio.bpm == 92
