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

    def test_le_constat_d_instrumental_arrive_chez_la_soeur(self, data_manager, famille):
        riche, pauvre = famille
        riche.lyrics.instrumental = True
        data_manager.save_track(riche)
        assert _colonne(data_manager, pauvre.id, "instrumental") == 1

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

    def test_une_rendition_arrive_chez_la_soeur_avec_ses_colonnes(self, data_manager, famille):
        """e28 : la copie additive suit `PRAGMA table_info`, donc `kind`, `label`
        et le compteur voyagent sans code dédié — c'est ce que ce test garde."""
        chez_swing, chez_isha = famille
        data_manager.record_variant_streams(
            chez_swing.id, "5go793BaOjzfop2JwctQ0r", 108, 7, None, label="Grünt #33 - Live"
        )
        data_manager.save_track(chez_isha)

        (rendition,) = data_manager.get_track_spotify_ids(chez_isha.id)
        assert (rendition.kind, rendition.label, rendition.streams, rendition.daily_streams) == (
            "rendition",
            "Grünt #33 - Live",
            108,
            7,
        )


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

    def test_un_doublon_intra_artiste_ne_peut_plus_naitre(self, data_manager):
        """Avant e36, « BOSS » et « Boss » (même page Genius) devenaient deux
        fiches, signalées « Doublon INTRA-ARTISTE ». Depuis, l'identité d'une
        fiche est son genius_id : la seconde écriture RETOMBE sur la première.
        Le signalement reste pour les doublons nés avant e36."""
        josman = Artist(name="Josman")
        josman.id = data_manager.save_artist(josman)
        boss = Track(title="BOSS", artist=josman)
        boss.genius_id = 638651
        boss.lyrics.text = "des paroles"
        data_manager.save_track(boss)

        bis = Track(title="Boss", artist=josman)  # même artiste, même genius_id
        bis.genius_id = 638651
        data_manager.save_track(bis)

        assert bis.id == boss.id
        assert len(data_manager.get_artist_tracks(josman.id)) == 1

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


class TestRoleSecondaireEtGroupes:
    """Le 🎙️ d'un rôle secondaire survit à l'association de groupe.

    La question se pose vraiment : un membre crédité « Additional Voices » sur un
    morceau de son groupe a DEUX lignes pour le même enregistrement — la sienne,
    qui porte le rôle, et celle du groupe, qui ne le porte pas. Deux mécanismes
    pouvaient le faire disparaître, et aucun ne le fait :

    · la SYNCHRONISATION des sœurs ne touche pas `secondary_role`, qui décrit la
      ligne d'UN artiste et non l'enregistrement (`COLONNES_DE_CONTEXTE`) ;
    · la DISCOGRAPHIE RÉUNIE lit l'artiste lui-même en TÊTE et dédoublonne sur
      l'identité métier — c'est donc sa ligne à lui, celle qui porte le rôle,
      qui est retenue.
    """

    def _famille_de_groupe(self, data_manager):
        from src.models.artist import ArtistRelation

        iam = Artist(name="IAM")
        iam.id = data_manager.save_artist(iam)
        membre = Artist(name="Shurik'n")
        membre.id = data_manager.save_artist(membre)
        data_manager.record_artist_relations(
            membre.id,
            [
                ArtistRelation(
                    related_artist_id=iam.id,
                    related_name="IAM",
                    kind="member_of",
                    formation="groupe",
                    source="manuel",
                )
            ],
        )
        chez_iam = Track(title="Petit frère", artist=iam)
        chez_iam.genius_id = 4242
        data_manager.save_track(chez_iam)
        chez_membre = Track(title="Petit frère", artist=membre)
        chez_membre.genius_id = 4242
        # Tel que l'import Genius le produit RÉELLEMENT : un rôle secondaire
        # s'accompagne toujours de `is_featuring` et du nom de l'artiste
        # principal (`genius_api`, verdict « secondary »). Un fixture qui les
        # omettait faisait passer le marqueur pour muet à tort.
        chez_membre.is_featuring = True
        chez_membre.primary_artist_name = "IAM"
        chez_membre.secondary_role = "Additional Voices"
        data_manager.save_track(chez_membre)
        return iam, membre, chez_iam, chez_membre

    def test_le_role_ne_fuit_pas_vers_la_ligne_du_groupe(self, data_manager):
        iam, _, chez_iam, chez_membre = self._famille_de_groupe(data_manager)

        assert _colonne(data_manager, chez_membre.id, "secondary_role") == "Additional Voices"
        assert _colonne(data_manager, chez_iam.id, "secondary_role") is None

    def test_la_discographie_reunie_garde_la_ligne_du_MEMBRE(self, data_manager):
        """Le morceau n'apparaît qu'une fois, et c'est la version qui porte le
        rôle. `via_group` est None : c'est bien SON morceau, pas un morceau qu'il
        reçoit de sa formation."""
        _, membre, _, _ = self._famille_de_groupe(data_manager)

        (track,) = data_manager.discographie_reunie(membre)
        assert track.secondary_role == "Additional Voices"
        assert track.via_group is None

    def test_le_marqueur_de_role_secondaire_se_TAIT_chez_le_membre(self, data_manager):
        """Un rôle secondaire dit « ni auteur, ni invité, juste une petite
        contribution » — ce qui devient FAUX quand l'artiste est membre du groupe
        qui signe : sa présence est déjà expliquée par son appartenance, et le
        marqueur sous-entendrait le contraire.

        La DONNÉE reste (base et fiche technique) ; seul l'affichage du tableau
        se tait. C'est un jugement sur le SENS, pas une correction : effacer
        `secondary_role` perdrait un fait vrai que Genius a publié.
        """
        _, membre, _, _ = self._famille_de_groupe(data_manager)

        (track,) = data_manager.discographie_reunie(membre)
        assert track.membre_de_la_formation is True
        assert track.secondary_role == "Additional Voices"  # la donnée est intacte

    def test_le_marqueur_reste_hors_formation(self, data_manager):
        """Un rôle secondaire sur le morceau d'un artiste QUELCONQUE garde son
        sens plein — c'est justement ce que le marqueur sert à dire."""
        from src.models.artist import ArtistRelation

        iam = Artist(name="IAM")
        iam.id = data_manager.save_artist(iam)
        membre = Artist(name="Shurik'n")
        membre.id = data_manager.save_artist(membre)
        data_manager.record_artist_relations(
            membre.id,
            [
                ArtistRelation(
                    related_artist_id=iam.id,
                    related_name="IAM",
                    kind="member_of",
                    formation="groupe",
                    source="manuel",
                )
            ],
        )
        ailleurs = Track(title="Un morceau de SCH", artist=membre)
        ailleurs.genius_id = 777
        ailleurs.primary_artist_name = "SCH"
        ailleurs.secondary_role = "Additional Voices"
        data_manager.save_track(ailleurs)

        (track,) = data_manager.discographie_reunie(membre)
        assert track.membre_de_la_formation is False
        assert track.secondary_role == "Additional Voices"


def test_un_credit_discogs_hors_piste_part_de_toutes_les_jumelles(data_manager, famille):
    """Lot B5 : laissé chez une sœur, l'UNION de `synchroniser_soeurs` le
    ramènerait au prochain `save_track`."""
    riche, pauvre = famille
    for credit in (
        Credit(name="Mr Hudson", role=CreditRole.VOCALS, tracks="C2", source="discogs"),
        Credit(name="No I.D.", role=CreditRole.PRODUCER, tracks="A1", source="discogs"),
    ):
        riche.add_credit(credit)
    data_manager.save_track(riche)
    cle = ("Mr Hudson", CreditRole.VOCALS.value, "C2")
    assert data_manager.forget_discogs_credits(riche.id, [cle]) == 2
    for t in (riche, pauvre):
        noms = {c.name for c in data_manager.get_artist_tracks(t.artist.id)[0].credits}
        assert noms == {"No I.D."}


def test_la_duree_de_la_soeur_suit_ses_observations(data_manager, famille):
    """2026-10-10 : 43 fiches à durée VIDE malgré leurs observations — la ligne
    enregistrée ré-arbitrait seule, la sœur recevait les observations sans que
    sa colonne suive (Kanye « Heartless », sœur de la ligne Kid Cudi à 210 s)."""
    riche, pauvre = famille
    riche.observations = [Observation("duration", 210, "deezer")]
    data_manager.save_track(riche)
    assert _colonne(data_manager, riche.id, "duration") == 210
    assert _colonne(data_manager, pauvre.id, "duration") == 210


def test_un_disque_discogs_relu_vaut_pour_les_soeurs(data_manager, famille):
    """2026-10-10, *808s & Heartbreak* : chaque ligne relue récupérait par
    l'union les crédits hors piste que l'autre gardait — aucune ne se nettoyait."""
    riche, pauvre = famille
    pauvre.add_credit(
        Credit(name="Mr Hudson", role=CreditRole.PRODUCER, source="discogs", tracks="C2")
    )
    data_manager.save_track(pauvre)
    riche.credits = [Credit(name="No I.D.", role=CreditRole.PRODUCER, source="discogs")]
    riche._credits_discogs_frais = True
    data_manager.save_track(riche)
    with data_manager.engine.connect() as conn:
        noms = {
            r[0]
            for r in conn.execute(
                text("SELECT name FROM credits WHERE track_id = :t AND source = 'discogs'"),
                {"t": pauvre.id},
            )
        }
    assert noms == {"No I.D."}


def test_le_total_youtube_de_la_soeur_suit_ses_videos(data_manager, famille):
    """2026-10-10 : Kanye « Heartless » gardait 258 M en colonne pour 466 M de
    vidéos reçues de la ligne Kid Cudi."""
    riche, pauvre = famille
    data_manager.record_track_videos(
        riche.id, [TrackVideo(video_id="CLIPGRUNT33", source="genius_media", views=5000)]
    )
    data_manager.save_track(riche)
    assert _colonne(data_manager, pauvre.id, "ytm_streams") == 5000


def test_la_ligne_ENREGISTREE_suit_les_observations_recues_de_sa_soeur(data_manager, famille):
    """2026-10-11 : SCH « Kimono » sans streams en colonne malgré 22,7 M
    d'observation Kworb — l'union verse les observations de la sœur dans la ligne
    enregistrée APRÈS son propre arbitrage, et seule la sœur était ré-arbitrée."""
    riche, pauvre = famille
    data_manager.upsert_observations(
        pauvre.id,
        [
            Observation("spotify_streams", 22_700_000, "kworb"),
            Observation("duration", 201, "deezer"),
        ],
    )
    data_manager.save_track(riche)
    assert _colonne(data_manager, riche.id, "spotify_streams") == 22_700_000
    assert _colonne(data_manager, riche.id, "duration") == 201


def test_la_soeur_qui_recoit_les_paroles_est_marquee_avec_paroles(data_manager, famille):
    """2026-10-11 : 72 fiches portaient des paroles avec `has_lyrics = 0` — le
    comblement tient `0` pour une valeur et ne le remplaçait pas."""
    riche, pauvre = famille
    riche.lyrics.text = "Premier couplet…"
    data_manager.save_track(riche)
    assert _colonne(data_manager, pauvre.id, "has_lyrics") == 1


def test_l_id_spotify_recu_d_une_soeur_est_marque_principal(data_manager, famille):
    """2026-10-11 : 82 lignes sœurs avaient `tracks.spotify_id` comblé par la
    sœur, sans que la ligne de `track_spotify_ids` reçue soit marquée principale."""
    chez_swing, chez_isha = famille
    chez_isha.spotify_id = "3VXzVGAWFSrH47dBtTOPws"
    data_manager.save_track(chez_isha)
    data_manager.record_track_spotify_ids(
        chez_isha.id, [TrackSpotifyId(spotify_id="3VXzVGAWFSrH47dBtTOPws", source="kworb")]
    )
    data_manager.save_track(chez_swing)

    assert _colonne(data_manager, chez_swing.id, "spotify_id") == "3VXzVGAWFSrH47dBtTOPws"
    assert [e.is_primary for e in data_manager.get_track_spotify_ids(chez_swing.id)] == [True]


class TestRetraitDUnIdSpotifyChezLesSoeurs:
    """2026-10-11 : un ID retiré d'une ligne restait chez sa sœur, et l'union
    des sœurs le RENDAIT à la ligne d'où on venait de le retirer (Kid Cudi
    « Sky City » portait l'ID refusé chez Kanye West)."""

    _ID = "3VXzVGAWFSrH47dBtTOPws"

    def _poser(self, data_manager, track, sid):
        track.spotify_id = sid
        data_manager.save_track(track)
        data_manager.record_track_spotify_ids(
            track.id, [TrackSpotifyId(spotify_id=sid, source="kworb")]
        )

    def test_le_retrait_vaut_pour_la_soeur_et_ne_revient_pas(self, data_manager, famille):
        chez_swing, chez_isha = famille
        self._poser(data_manager, chez_isha, self._ID)
        data_manager.upsert_observations(
            chez_isha.id, [Observation("spotify_streams", 1000, "kworb")]
        )
        data_manager.save_track(chez_swing)  # union : Swing reçoit l'ID
        assert _colonne(data_manager, chez_swing.id, "spotify_id") == self._ID

        data_manager.clear_track_spotify_id(chez_swing.id)
        chez_isha.spotify_id = None
        data_manager.save_track(chez_isha)  # une écriture quelconque relance l'union

        for t in (chez_swing, chez_isha):
            assert _colonne(data_manager, t.id, "spotify_id") is None
            assert data_manager.get_track_spotify_ids(t.id) == []
            assert _colonne(data_manager, t.id, "spotify_streams") is None

    def test_une_soeur_a_son_PROPRE_id_garde_ses_mesures(self, data_manager, famille):
        chez_swing, chez_isha = famille
        self._poser(data_manager, chez_isha, "7a0tTD7xdvKsNcJfMIDWp4")
        data_manager.record_track_spotify_ids(
            chez_isha.id, [TrackSpotifyId(spotify_id=self._ID, source="kworb")]
        )
        data_manager.upsert_observations(
            chez_isha.id, [Observation("spotify_streams", 1000, "kworb")]
        )
        data_manager.save_track(chez_swing)

        data_manager.clear_track_spotify_id(chez_swing.id, self._ID)

        assert _colonne(data_manager, chez_isha.id, "spotify_id") == "7a0tTD7xdvKsNcJfMIDWp4"
        assert [e.spotify_id for e in data_manager.get_track_spotify_ids(chez_isha.id)] == [
            "7a0tTD7xdvKsNcJfMIDWp4"
        ]
        assert _colonne(data_manager, chez_isha.id, "spotify_streams") == 1000


class TestRetraitDeezerEtIsrcChezLesSoeurs:
    """2026-10-11 (audit §9.3) : même défaut que l'ID Spotify — un ID Deezer ou
    un ISRC retiré d'une ligne restait chez sa sœur, et l'union le rendait."""

    def _poser_deezer(self, data_manager, track, deezer_id, isrc):
        track.deezer_id = deezer_id
        data_manager.save_track(track)
        data_manager.record_discography_observations(
            track.id,
            [Observation("duration", 201, "deezer"), Observation("isrc", isrc, "deezer")],
        )

    def test_l_id_deezer_part_de_toute_la_famille(self, data_manager, famille):
        chez_swing, chez_isha = famille
        self._poser_deezer(data_manager, chez_isha, 70, "FRX111")
        data_manager.save_track(chez_swing)  # union : Swing reçoit tout
        assert _colonne(data_manager, chez_swing.id, "duration") == 201

        data_manager.clear_track_deezer_id(chez_swing.id)
        chez_isha.deezer_id = None
        data_manager.save_track(chez_isha)  # une écriture quelconque relance l'union

        for t in (chez_swing, chez_isha):
            assert _colonne(data_manager, t.id, "deezer_id") is None
            assert _colonne(data_manager, t.id, "duration") is None
            assert _colonne(data_manager, t.id, "isrc") is None
            assert not [o for o in data_manager.get_observations(t.id) if o.source == "deezer"]

    def test_une_soeur_a_son_propre_id_deezer_garde_tout(self, data_manager, famille):
        chez_swing, chez_isha = famille
        self._poser_deezer(data_manager, chez_isha, 70, "FRX111")
        # Deux ID différents (état hérité : l'union ne remplace jamais une
        # colonne d'identité déjà remplie, elle ne produit pas ce cas).
        with data_manager.engine.begin() as conn:
            conn.execute(
                text("UPDATE tracks SET deezer_id = 99 WHERE id = :t"), {"t": chez_swing.id}
            )

        data_manager.clear_track_deezer_id(chez_swing.id)

        assert _colonne(data_manager, chez_isha.id, "deezer_id") == 70
        assert _colonne(data_manager, chez_isha.id, "duration") == 201

    def test_l_isrc_part_de_toute_la_famille(self, data_manager, famille):
        chez_swing, chez_isha = famille
        data_manager.save_track(chez_isha)
        data_manager.record_discography_observations(
            chez_isha.id, [Observation("isrc", "FRX222", "deezer")]
        )
        data_manager.save_track(chez_swing)  # l'ISRC voyage par son observation
        assert _colonne(data_manager, chez_swing.id, "isrc") == "FRX222"

        data_manager.clear_track_isrc(chez_swing.id)
        data_manager.save_track(chez_isha)

        for t in (chez_swing, chez_isha):
            assert _colonne(data_manager, t.id, "isrc") is None


class TestRemplacerIsrc:
    """« Tradition » (2026-10-29) : deux ISRC observés pour un enregistrement,
    la colonne d'identité ne se remplace jamais seule — le bon se pose à la
    main sur toute la famille, sans retirer d'observation."""

    def _deux_isrc(self, dm, famille):
        chez_swing, chez_isha = famille
        dm.record_discography_observations(chez_isha.id, [Observation("isrc", "FRX111", "legacy")])
        dm.save_track(chez_swing)
        dm.record_discography_observations(chez_isha.id, [Observation("isrc", "FRX222", "deezer")])
        dm.save_track(chez_swing)
        for t in famille:
            assert _colonne(dm, t.id, "isrc") == "FRX111"  # remplit, ne remplace pas
        return chez_swing, chez_isha

    def test_l_isrc_choisi_part_sur_toute_la_famille(self, data_manager, famille):
        chez_swing, chez_isha = self._deux_isrc(data_manager, famille)

        changees = data_manager.remplacer_isrc(chez_swing.id, "frx222")

        assert sorted(changees) == sorted([chez_swing.id, chez_isha.id])
        data_manager.save_track(chez_isha)
        for t in famille:
            assert _colonne(data_manager, t.id, "isrc") == "FRX222"
        with data_manager.engine.connect() as conn:
            sources = set(
                conn.execute(
                    text(
                        "SELECT source FROM observations " "WHERE track_id = :t AND field = 'isrc'"
                    ),
                    {"t": chez_swing.id},
                ).scalars()
            )
        assert sources == {"legacy", "deezer"}  # aucune observation retirée

    def test_un_isrc_jamais_observe_n_est_pas_pose(self, data_manager, famille):
        chez_swing, _ = self._deux_isrc(data_manager, famille)

        assert data_manager.remplacer_isrc(chez_swing.id, "FRX999") == []
        for t in famille:
            assert _colonne(data_manager, t.id, "isrc") == "FRX111"


class TestDonneesDePageLaPlusRecenteGagne:
    """Décision utilisateur 2026-10-12 : une donnée lue sur la PAGE Genius
    (anecdotes, relations, paroles) n'est plus figée à la première valeur chez
    une sœur — la lecture la plus récente l'emporte, sans jamais vider."""

    def _poser(self, dm, tid, **colonnes):
        with dm.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tracks SET "
                    + ", ".join(f"{c} = :{c}" for c in colonnes)
                    + " WHERE id = :tid"
                ),
                {**colonnes, "tid": tid},
            )

    def test_l_anecdote_relue_le_plus_recemment_gagne(self, data_manager, famille):
        chez_swing, chez_isha = famille
        self._poser(
            data_manager,
            chez_swing.id,
            anecdotes="ancienne",
            genius_detail_checked_at="2026-09-01T10:00:00",
        )
        self._poser(
            data_manager,
            chez_isha.id,
            anecdotes="relue",
            genius_detail_checked_at="2026-10-01T10:00:00",
        )
        with data_manager.engine.begin() as conn:
            from src.utils.track_soeurs import synchroniser_soeurs

            synchroniser_soeurs(conn, chez_swing.id, _GENIUS)
            synchroniser_soeurs(conn, chez_swing.id, _GENIUS)  # idempotent
        assert _colonne(data_manager, chez_swing.id, "anecdotes") == "relue"
        assert _colonne(data_manager, chez_isha.id, "anecdotes") == "relue"

    def test_une_valeur_vide_ne_se_transmet_jamais(self, data_manager, famille):
        chez_swing, chez_isha = famille
        self._poser(
            data_manager,
            chez_swing.id,
            lyrics="Premier couplet",
            lyrics_scraped_at="2026-09-01 10:00:00",
        )
        self._poser(data_manager, chez_isha.id, lyrics="", lyrics_scraped_at="2026-10-01 10:00:00")
        data_manager.save_track(chez_isha)
        assert _colonne(data_manager, chez_isha.id, "lyrics") == "Premier couplet"
        assert _colonne(data_manager, chez_swing.id, "lyrics") == "Premier couplet"

    def test_les_paroles_relues_remplacent_avec_leur_source(self, data_manager, famille):
        chez_swing, chez_isha = famille
        self._poser(
            data_manager,
            chez_swing.id,
            lyrics="v1",
            lyrics_source="genius",
            lyrics_scraped_at="2026-09-01 10:00:00",
        )
        self._poser(
            data_manager,
            chez_isha.id,
            lyrics="v2",
            lyrics_source="genius",
            lyrics_scraped_at="2026-10-01 10:00:00",
        )
        relu = data_manager.get_artist_tracks(chez_swing.artist.id)[0]
        data_manager.save_track(relu)
        assert _colonne(data_manager, chez_swing.id, "lyrics") == "v2"

    def test_une_relation_manuelle_survit_a_la_page_relue(self, data_manager, famille):
        """2026-10-12 : Kanye « Sky City » avait perdu « version de SKY CITY »,
        posée à la main, quand la ligne de Kid Cudi a relu la page — la page ne
        la porte pas. La relation manuelle reste, et gagne toute la famille."""
        import json

        chez_swing, chez_isha = famille
        manuelle = {"type": "version_of", "title": "SKY CITY", "track_id": 9, "origine": "manuel"}
        page = [{"type": "samples", "title": "Dope Man"}]
        self._poser(
            data_manager,
            chez_swing.id,
            relationships=json.dumps([manuelle]),
            genius_detail_checked_at="2026-09-01T10:00:00",
        )
        self._poser(
            data_manager,
            chez_isha.id,
            relationships=json.dumps(page),
            genius_detail_checked_at="2026-10-03T10:00:00",
        )
        with data_manager.engine.begin() as conn:
            from src.utils.track_soeurs import synchroniser_soeurs

            synchroniser_soeurs(conn, chez_swing.id, _GENIUS)
            synchroniser_soeurs(conn, chez_swing.id, _GENIUS)  # idempotent
        for tid in (chez_swing.id, chez_isha.id):
            rels = json.loads(_colonne(data_manager, tid, "relationships"))
            assert rels == [*page, manuelle]


def test_une_identite_divergente_entre_soeurs_va_au_panneau(data_manager, famille):
    """Deux ID Spotify différents sur le même enregistrement : aucune n'est
    « la plus récente », l'utilisateur tranche (décision 2026-10-12)."""
    from src.services import revue

    chez_swing, chez_isha = famille
    with data_manager.engine.begin() as conn:
        for tid, sid in ((chez_swing.id, "AAAAAAAAAAAAAAAAAAAAAA"), (chez_isha.id, "B" * 22)):
            conn.execute(
                text("UPDATE tracks SET spotify_id = :s WHERE id = :t"), {"s": sid, "t": tid}
            )

    (d,) = data_manager.divergences_identite_soeurs(chez_isha.artist.id)
    assert d["colonne"] == "spotify_id" and d["track_id"] == chez_isha.id
    assert set(d["valeurs"]) == {"Swing", "Isha"}

    artiste = data_manager.get_artist_by_name("Isha")
    artiste.tracks = data_manager.discographie_reunie(artiste)
    cas = [
        c for c in revue.analyser(data_manager, artiste).actifs if c.detecteur == "soeurs_identite"
    ]
    assert len(cas) == 1 and "spotify_id différent" in cas[0].motif


class TestLienYoutube:
    """Le lien principal s'aligne là où un arbitre existe (2026-10-26 : 4
    familles Kanye / Kid Cudi, un ✔️ posé sur une seule ligne)."""

    @staticmethod
    def _poser(data_manager, track_id, url, source, nature):
        with data_manager.engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE tracks SET youtube_url = :u, youtube_url_source = :s, "
                    "youtube_video_kind = :k WHERE id = :t"
                ),
                {"u": url, "s": source, "k": nature, "t": track_id},
            )

    @staticmethod
    def _lien(data_manager, track_id):
        return tuple(
            _colonne(data_manager, track_id, c)
            for c in ("youtube_url", "youtube_url_source", "youtube_video_kind")
        )

    def test_meme_video_la_provenance_la_plus_forte_et_la_nature_connue(
        self, data_manager, famille
    ):
        """« Champions » : `manual` / « unknown » chez Kanye, `genius_media` /
        « audio » chez Travis, même vidéo."""
        swing, isha = famille
        self._poser(
            data_manager,
            swing.id,
            "https://www.youtube.com/watch?v=YIoAZ0Sh8-U",
            "manual",
            "unknown",
        )
        self._poser(data_manager, isha.id, "https://youtu.be/YIoAZ0Sh8-U", "genius_media", "audio")
        data_manager.save_track(isha)
        attendu = ("manual", "audio")
        assert self._lien(data_manager, swing.id)[1:] == attendu
        assert self._lien(data_manager, isha.id)[1:] == attendu

    def test_le_choix_manuel_vaut_chez_la_soeur(self, data_manager, famille):
        """« Sky City » : le ✔️ chez Kanye, le lien Genius gardé chez Kid Cudi."""
        swing, isha = famille
        self._poser(
            data_manager,
            isha.id,
            "https://www.youtube.com/watch?v=BzcuDb8WYUU",
            "genius_media",
            "audio",
        )
        url = "https://www.youtube.com/watch?v=g44Y0L-TNpk"
        assert data_manager.update_track_youtube_url(swing.id, url, "manual")
        assert self._lien(data_manager, isha.id)[:2] == (url, "manual")

    def test_deux_liens_sans_choix_manuel_restent_au_panneau(self, data_manager, famille):
        swing, isha = famille
        self._poser(
            data_manager,
            swing.id,
            "https://www.youtube.com/watch?v=AAAAAAAAAAA",
            "genius_media",
            "audio",
        )
        self._poser(
            data_manager,
            isha.id,
            "https://www.youtube.com/watch?v=BBBBBBBBBBB",
            "search_auto",
            "audio",
        )
        data_manager.save_track(isha)
        assert self._lien(data_manager, swing.id)[1] == "genius_media"
        assert self._lien(data_manager, isha.id)[1] == "search_auto"

    def test_deux_choix_manuels_differents_restent_au_panneau(self, data_manager, famille):
        swing, isha = famille
        self._poser(
            data_manager, swing.id, "https://www.youtube.com/watch?v=AAAAAAAAAAA", "manual", "audio"
        )
        self._poser(
            data_manager, isha.id, "https://www.youtube.com/watch?v=BBBBBBBBBBB", "manual", "clip"
        )
        data_manager.save_track(isha)
        assert self._lien(data_manager, swing.id)[0].endswith("AAAAAAAAAAA")
        assert self._lien(data_manager, isha.id)[0].endswith("BBBBBBBBBBB")


class TestEcrivainsHorsSaveTrack:
    """Un écrivain qui ne passe pas par `save_track` touche UNE ligne : la fin
    de transaction (`_recalculer_touchees`) unit les observations de la
    famille, et chaque retrait passe chez les sœurs (« Outro » : 167 s chez
    Josman, 421 s chez Népal, 2026-10-29)."""

    @staticmethod
    def _observations(data_manager, track_id, champ):
        with data_manager.engine.connect() as conn:
            return {
                (r[0], str(r[1]))
                for r in conn.execute(
                    text(
                        "SELECT source, value FROM observations WHERE track_id = :t AND field = :f"
                    ),
                    {"t": track_id, "f": champ},
                )
            }

    def test_une_duree_posee_sur_une_ligne_arrive_chez_la_soeur(self, data_manager, famille):
        swing, isha = famille
        for ligne in famille:
            data_manager.record_duration_observation(ligne.id, 421, "legacy")
        data_manager.record_duration_observation(swing.id, 167, "youtube")
        assert _colonne(data_manager, isha.id, "duration") == 167
        assert ("youtube", "167") in self._observations(data_manager, isha.id, "duration")

    def test_une_duree_video_retiree_part_de_toute_la_famille(self, data_manager, famille):
        swing, isha = famille
        data_manager.record_duration_observation(swing.id, 167, "youtube")
        data_manager.retirer_durees_video(isha.id)
        for ligne in famille:
            assert self._observations(data_manager, ligne.id, "duration") == set()
            assert _colonne(data_manager, ligne.id, "duration") is None

    def test_une_observation_oubliee_ne_revient_pas_par_la_soeur(self, data_manager, famille):
        swing, isha = famille
        data_manager.record_duration_observation(swing.id, 200, "deezer")
        data_manager.forget_discography_observation(isha.id, "duration", "deezer", 200)
        for ligne in famille:
            assert ("deezer", "200") not in self._observations(data_manager, ligne.id, "duration")

    def test_l_isrc_rempli_arrive_chez_la_soeur_vide(self, data_manager, famille):
        swing, isha = famille
        data_manager.fill_track_identities(swing.id, isrc="FR9W12217454")
        assert _colonne(data_manager, isha.id, "isrc") == "FR9W12217454"

    def test_le_lien_retire_part_de_la_soeur_qui_porte_la_meme_video(self, data_manager, famille):
        swing, isha = famille
        for ligne in famille:
            data_manager.update_track_youtube_url(
                ligne.id, "https://www.youtube.com/watch?v=AAAAAAAAAAA", "genius_media"
            )
        data_manager.clear_track_youtube_link(swing.id)
        assert _colonne(data_manager, isha.id, "youtube_url") is None
        data_manager.save_track(swing)
        assert _colonne(data_manager, swing.id, "youtube_url") is None

    def test_la_soeur_a_l_autre_video_garde_son_lien(self, data_manager, famille):
        swing, isha = famille
        data_manager.update_track_youtube_url(
            swing.id, "https://www.youtube.com/watch?v=AAAAAAAAAAA", "manual"
        )
        data_manager.update_track_youtube_url(
            isha.id, "https://www.youtube.com/watch?v=BBBBBBBBBBB", "manual"
        )
        data_manager.clear_track_youtube_link(swing.id)
        assert _colonne(data_manager, isha.id, "youtube_url").endswith("BBBBBBBBBBB")


class TestEditionsDesSoeurs:
    """Une ÉDITION (`track_editions`) décrit l'enregistrement : « Make Her Say »
    notée chez Kid Cudi #3819 et pas chez Kanye #6479, « Mon pote » chez Flynt
    et pas chez OrelSan #4440 (2026-10-29, 25 familles sur 302)."""

    @staticmethod
    def _editions(dm, tid):
        return {(e["label"], e["duration"]) for e in dm.get_track_editions(tid)}

    def test_une_edition_notee_chez_une_soeur_arrive_chez_l_autre(self, data_manager, famille):
        swing, isha = famille
        data_manager.record_track_edition(swing.id, "Radio Edit", source="deezer", duration=201)
        assert self._editions(data_manager, isha.id) == {("Radio Edit", 201)}

    def test_une_colonne_vide_se_comble_sans_rien_remplacer(self, data_manager, famille):
        swing, isha = famille
        data_manager.record_track_edition(swing.id, "Radio Edit", source="kworb", spotify_id="S1")
        data_manager.record_track_edition(isha.id, "Radio Edit", source="deezer", duration=201)
        for ligne in famille:
            (e,) = data_manager.get_track_editions(ligne.id)
            assert (e["spotify_id"], e["duration"], e["source"]) == ("S1", 201, "kworb")

    def test_le_retrait_part_de_toute_la_famille(self, data_manager, famille):
        swing, isha = famille
        data_manager.record_track_edition(swing.id, "Radio Edit", duration=201)
        assert data_manager.retirer_edition(isha.id, "Radio Edit")
        data_manager.save_track(swing)
        for ligne in famille:
            assert self._editions(data_manager, ligne.id) == set()

    def test_le_deplacement_quitte_toute_la_famille(self, data_manager, famille):
        swing, isha = famille
        autre = Track(title="Grünt #34", artist=swing.artist)
        autre.genius_id = _GENIUS + 1
        data_manager.save_track(autre)
        data_manager.record_track_edition(swing.id, "Radio Edit", duration=201)
        assert data_manager.deplacer_edition(swing.id, "Radio Edit", autre.id)
        for ligne in famille:
            assert self._editions(data_manager, ligne.id) == set()
        assert self._editions(data_manager, autre.id) == {("Radio Edit", 201)}

    def test_realigner_a_blanc_n_ecrit_rien_puis_aligne(self, data_manager, famille):
        swing, isha = famille
        with data_manager.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO track_editions (track_id, label, duration) "
                    "VALUES (:t, 'Version 3:36', 216)"
                ),
                {"t": isha.id},
            )
        assert data_manager.realigner_soeurs() == {_GENIUS: 1}
        assert self._editions(data_manager, swing.id) == set()
        data_manager.realigner_soeurs(appliquer=True)
        assert self._editions(data_manager, swing.id) == {("Version 3:36", 216)}
        assert data_manager.realigner_soeurs() == {}
