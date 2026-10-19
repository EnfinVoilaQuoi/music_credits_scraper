"""Écarts de discographie Deezer : classification (pure) et création (base tmp).

Les cas sont ceux mesurés le 2026-09-21 : AD$ (Chopped & $crewed) est un disque
d'Ocho où Freeze contribue ; Au tour de ma bulle (Live 2006) est un disque de
versions ; Lucio Bukowski a de vrais trous et deux EP jumeaux « … » / « ... ».
"""

import asyncio
import dataclasses

from src.models import Artist, Track, TrackSpotifyId
from src.models.track import CreditRole
from src.services import ecarts_deezer as ed

NOUS = 1236609


def _piste(id_, title, short=None, version="", artist_id=NOUS, isrc=None, contributors=()):
    return ed.PisteDeezer(
        id=id_,
        title=title,
        title_short=short or title,
        title_version=version,
        isrc=isrc,
        duration=200,
        position=1,
        artist_id=artist_id,
        artist_name="Nous" if artist_id == NOUS else "Autre",
        contributors=list(contributors),
    )


def _album(id_, title, pistes, artist_id=NOUS, record_type="album", label="Label X"):
    return ed.AlbumDeezer(
        id=id_,
        title=title,
        record_type=record_type,
        artist_id=artist_id,
        artist_name="Nous" if artist_id == NOUS else "Ocho",
        release_date="2024-02-08",
        label=label,
        pistes=pistes,
    )


def _track(title, album=None, isrc=None):
    t = Track(title=title)
    t.album, t.isrc = album, isrc
    return t


class TestClasser:
    def test_trou_sur_disque_connu(self):
        base = [_track("Chardons", "Chardons Bleus")]
        albums = [_album(1, "Chardons Bleus", [_piste(10, "Chardons"), _piste(11, "Coal lla")])]
        ecarts, jumelles = ed.classer(albums, base, [], NOUS)
        # « Chardons » est CONNU : rattaché à la parution sans confirmation.
        assert [(e.nature, e.titre, e.coche) for e in ecarts] == [
            ("link", "Chardons", True),
            ("absent", "Coal lla", True),
        ]
        assert jumelles == []

    def test_titre_a_l_espace_pres_est_connu(self):
        """Booba « 3 G » créé par les écarts à côté de « 3G » (CORRECTIFS 1.3)."""
        base = [_track("3G", "Temps mort"), _track("AVLD", "Temps mort")]
        albums = [_album(1, "Temps mort", [_piste(10, "3 G"), _piste(11, "A V L D")])]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert {e.nature for e in ecarts} == {"link"}

    def test_titre_a_la_graphie_pres_est_connu(self):
        """2026-10-18 : Booba « Strass & paillettes » (*Temps mort* 2018) et
        Flynt « 1 pour la plume » proposés absents — la base les écrit « Strass
        et paillettes » et « Un pour la plume »."""
        base = [_track("Strass et paillettes", "Temps mort"), _track("Un pour la plume", "X")]
        albums = [
            _album(1, "Temps mort", [_piste(10, "Strass & paillettes")]),
            _album(2, "X", [_piste(20, "1 pour la plume")]),
        ]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert [(e.nature, e.existing_track.title) for e in ecarts] == [
            ("link", "Strass et paillettes"),
            ("link", "Un pour la plume"),
        ]

    def test_intro_nommee_par_son_disque(self):
        """Genius écrit « Intro (0.9) », Deezer « Intro » sur *0.9* (Booba,
        2026-10-18 : proposée absente et cochée). L'Intro d'un AUTRE disque
        n'en profite pas."""
        intro_09 = _track("Intro (0.9)", "0.9")
        base = [_track("Intro", "Autopsie Vol. 3"), intro_09]
        albums = [_album(1, "0.9", [_piste(10, "Intro")])]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        # Sans durée, un titre générique reste à confirmer — sur la BONNE fiche.
        assert [(e.nature, e.existing_track.title) for e in ecarts] == [
            ("link_candidate", "Intro (0.9)")
        ]
        intro_09.duration = 200
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert [(e.nature, e.existing_track.title) for e in ecarts] == [("link", "Intro (0.9)")]

    def test_disque_d_un_autre_connu_par_un_feat_n_est_pas_le_notre(self):
        """Freeze Corleone invité chez Jack Many (2026-10-18) : le disque était
        « connu » par sa ligne d'invité, ses 13 autres pistes proposées
        « absentes » et cochées."""
        feat = _track("Pas la peine (feat. Nous)", "MJ")
        feat.is_featuring, feat.primary_artist_name = True, "Ocho"
        albums = [
            _album(
                7,
                "MJ",
                [
                    _piste(70, "Ziak", artist_id=999, contributors=[(999, "Ocho")]),
                    _piste(71, "MJ", artist_id=999, contributors=[(999, "Ocho"), (NOUS, "Nous")]),
                ],
                artist_id=999,
            )
        ]
        ecarts, _ = ed.classer(albums, [feat], [], NOUS)
        assert [(e.nature, e.titre, e.coche) for e in ecarts] == [("apparition", "MJ", False)]

    def test_disque_d_un_autre_relie_par_son_id_sous_un_autre_titre(self):
        """La base l'appelle « Des flingues et des roses », Deezer « MJ Des
        flingues et des roses » : relié par son ID, il restait « le nôtre »
        (cassette Freeze, 2026-10-19 : les 13 absents cochés revenaient)."""
        feat = _track("Drapeaux hissés", "Des flingues et des roses")
        feat.is_featuring, feat.primary_artist_name = True, "Ocho"
        albums = [
            _album(
                7,
                "MJ Des flingues et des roses",
                [
                    _piste(70, "Ziak", artist_id=999, contributors=[(999, "Ocho")]),
                    _piste(
                        71,
                        "Drapeaux hissés",
                        artist_id=999,
                        contributors=[(999, "Ocho"), (NOUS, "Nous")],
                    ),
                ],
                artist_id=999,
            )
        ]
        albums_base = [{"deezer_album_id": 7, "title": "Des flingues et des roses"}]
        ecarts, _ = ed.classer(albums, [feat], albums_base, NOUS)
        assert [e.nature for e in ecarts if e.nature in ("absent", "album_absent")] == []

    def test_disque_absent_de_l_artiste_principal(self):
        albums = [_album(2, "Hourvari", [_piste(20, "Wunderlich"), _piste(21, "Ceux qui")])]
        ecarts, _ = ed.classer(albums, [_track("Autre chose")], [], NOUS)
        assert {e.nature for e in ecarts} == {"album_absent"} and all(e.coche for e in ecarts)

    def test_disque_de_versions_reste_version(self):
        base = [_track("Suzy", "Brut de femme"), _track("Marine", "Dans ma bulle")]
        albums = [
            _album(
                3,
                "Au Tour De Ma Bulle Live",
                [
                    _piste(30, "Suzy (Live 2006)", "Suzy", "(Live 2006)"),
                    _piste(31, "Marine (Live 2006)", "Marine", "(Live 2006)"),
                    _piste(32, "Intro Live"),
                ],
            )
        ]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert [e.nature for e in ecarts] == ["version", "version", "version"]
        assert ecarts[0].socle.title == "Suzy" and not ecarts[0].coche
        assert "version de « Suzy »" in ecarts[0].motifs

    def test_version_par_parse_variant_sans_title_version(self):
        base = [_track("Evasion", "Brut de femme")]
        albums = [
            _album(4, "Brut de femme", [_piste(40, "Evasion - Radio Edit", "Evasion - Radio Edit")])
        ]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        # Décision 2026-09-24 : un radio edit est le MÊME morceau — rattaché à la
        # fiche connue (parution), pas proposé comme version.
        assert ecarts[0].nature == "link" and ecarts[0].existing_track.title == "Evasion"

    def test_disque_d_un_autre_ne_garde_que_nos_contributions(self):
        base = [_track("MW2", "LMF")]
        ad = _album(
            5,
            "AD$ (Chopped & $crewed)",
            [
                _piste(
                    50,
                    "MW2 (Chopped & $crewed)",
                    "MW2",
                    "(Chopped & $crewed)",
                    999,
                    contributors=[(999, "Ocho"), (NOUS, "Nous")],
                ),
                _piste(51, "Interlude Ocho", artist_id=999, contributors=[(999, "Ocho")]),
            ],
            artist_id=999,
        )
        ecarts, _ = ed.classer([ad], base, [], NOUS)
        assert [(e.nature, e.titre) for e in ecarts] == [("apparition", "MW2 (Chopped & $crewed)")]
        assert not ecarts[0].coche and "disque de Ocho" in ecarts[0].motifs

    def test_disque_connu_credite_a_un_autre_reste_le_notre(self):
        """« Chardons Bleus » est en base ; Deezer le crédite à Mani Deiz : ses
        pistes manquantes sont des TROUS, pas des apparitions."""
        base = [_track("Chardons", "Chardons Bleus")]
        albums = [_album(1, "Chardons Bleus", [_piste(11, "Coal lla", artist_id=77)], artist_id=77)]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert [(e.nature, e.coche) for e in ecarts] == [("absent", True)]

    def test_editions_jumelles_ne_sont_pas_des_ecarts(self):
        base = [_track("Boulevard", "…"), _track("Cercueil", "…")]
        albums = [_album(6, "...", [_piste(60, "Boulevard"), _piste(61, "Cercueil")])]
        ecarts, jumelles = ed.classer(albums, base, [], NOUS)
        # « … » et « ... » sont le MÊME disque (clé NFKD) : ni écart, ni jumelle —
        # ses pistes connues sont des liens automatiques (titre, sans durée en base).
        assert [(e.nature, e.matched_by, e.coche) for e in ecarts] == [("link", "title", True)] * 2
        assert jumelles == []
        albums = [_album(6, "EP sans nom", [_piste(60, "Boulevard"), _piste(61, "Cercueil")])]
        ecarts, jumelles = ed.classer(albums, base, [], NOUS)
        # Un disque INCONNU dont on connaît toutes les pistes : jumelle ET liens.
        assert [e.nature for e in ecarts] == ["link", "link"] and jumelles == ["EP sans nom"]
        # Deja rattachees ET renseignees : plus rien a dire, le disque reste
        # une jumelle. (Un lien connu dont la fiche est VIDE est REPROPOSE.)
        for t, pid in zip(base, (60, 61), strict=True):
            t.deezer_id = pid
            t.durations_observees = {"deezer": 200}
        liens = {(6, base[0].id), (6, base[1].id)}
        assert ed.classer(albums, base, [], NOUS, liens_connus=liens) == ([], ["EP sans nom"])

    def test_titre_generique_sans_duree_reste_a_confirmer(self):
        base = [_track("Intro", "Album A"), _track("Outro", "Album A")]
        base[1].duration = 60
        albums = [_album(9, "Album A", [_piste(90, "Intro"), _piste(91, "Outro")])]
        albums[0].pistes[0].duration = None
        albums[0].pistes[1].duration = 61
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        # Deux « Intro » sans durée comparable : rien ne dit que c'est la même ;
        # « Outro » du même disque à 1 s près : lien.
        assert [(e.nature, e.matched_by, e.coche) for e in ecarts] == [
            ("link_candidate", "title_generique", False),
            ("link", "title_duration", True),
        ]

    def test_titre_generique_d_un_autre_disque_reste_a_confirmer_meme_a_duree_egale(self):
        """Freeze Corleone, 2026-10-10 : l'« Intro » de *Projet Blue Beam* reliée
        à l'« INTRO » de *RIYAD SADIO* (214 s des deux côtés). Chaque projet a
        son intro : la durée ne suffit pas, il faut le même disque."""
        base = [_track("INTRO", "RIYAD SADIO")]
        base[0].duration = 200
        albums = [_album(9, "Projet Blue Beam", [_piste(90, "Intro")])]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert [(e.nature, e.matched_by) for e in ecarts] == [("link_candidate", "title_generique")]

    def test_piste_du_disque_ne_va_pas_a_la_ligne_d_invite_d_un_autre(self):
        """Diam's, 2026-10-10 : « Par amour » de *Dans ma bulle* posé sur la
        ligne « Par Amour » de Dinos feat. Diam's (même titre, durée égale)."""
        invite = _track("Par Amour", "Hiver à Paris")
        invite.is_featuring, invite.primary_artist_name = True, "Dinos"
        invite.duration = 200
        sienne = _track("Par amour", "Dans ma bulle")
        albums = [_album(9, "Dans ma bulle", [_piste(90, "Par amour")])]
        ecarts, _ = ed.classer(albums, [invite, sienne], [], NOUS)
        assert [(e.nature, e.existing_track) for e in ecarts] == [("link", sienne)]
        # Seule la ligne d'invité porte ce titre : à confirmer, jamais d'office.
        ecarts, _ = ed.classer(albums, [invite], [], NOUS)
        assert [(e.nature, e.matched_by) for e in ecarts] == [
            ("link_candidate", "title_autre_principal")
        ]
        # Le principal crédité sur la piste : c'est bien ce morceau.
        albums[0].pistes[0].contributors = [(77, "Dinos")]
        ecarts, _ = ed.classer(albums, [invite], [], NOUS)
        assert [(e.nature, e.matched_by) for e in ecarts] == [("link", "title_duration")]

    def test_edition_generique_rejoint_le_socle_par_isrc(self):
        base = [_track("Pas de côté", "Chansons", isrc="FR123")]
        albums = [_album(7, "Chansons", [_piste(70, "Pas de coté (Version)", isrc="FR123")])]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert [(e.nature, e.matched_by) for e in ecarts] == [("link", "isrc")]

    def test_album_version_generique_n_est_pas_une_nouvelle_version(self):
        base = [_track("Argent, drogue et sexe", "Album")]
        base[0].duration = 200
        albums = [
            _album(
                71,
                "Album bis",
                [_piste(71, "Argent, Drogue et Sexe", "Argent, Drogue et Sexe", "Album Version")],
            )
        ]
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        assert [(e.nature, e.matched_by) for e in ecarts] == [("link", "title_duration")]

    def test_meme_titre_duree_differente_est_un_doute_explicite(self):
        base = [_track("Fucked Up", "Matrix")]
        base[0].duration = 201
        albums = [_album(72, "Autre album", [_piste(72, "Fucked Up")])]
        albums[0].pistes[0].duration = 198

        ecarts, _ = ed.classer(albums, base, [], NOUS)

        assert [(e.nature, e.matched_by) for e in ecarts] == [
            ("link_candidate", "title_duration_conflict")
        ]
        assert ecarts[0].motifs == [
            "même titre, durée différente (Deezer 198 s / base 201 s) — à confirmer"
        ]

    def test_album_connu_par_son_id_deezer(self):
        albums = [_album(8, "Titre Deezer différent", [_piste(80, "Inédit")])]
        ecarts, _ = ed.classer(
            albums, [_track("X", "Autre")], [{"title": "Autre", "deezer_album_id": 8}], NOUS
        )
        assert ecarts[0].nature == "absent"


class TestCocherParDefaut:
    def test_version_cochee_si_kworb_la_connait(self):
        socle = _track("DKR")
        socle.spotify_id_entries = [
            TrackSpotifyId(spotify_id="SPB", kind="rendition", label="DKR - Live", streams=108)
        ]
        e = ed.Ecart(
            "version",
            _album(1, "Ultra", []),
            _piste(1, "DKR (Live)", "DKR", "(Live)"),
            socle=socle,
        )
        ed.cocher_par_defaut([e], [socle])
        assert e.coche and e.kworb_spotify_id == "SPB"

    def test_version_cochee_si_genius_la_connait(self):
        e = ed.Ecart(
            "version", _album(1, "A", []), _piste(1, "Suzy (Live 2006)"), socle=_track("Suzy")
        )
        e.genius = {"id": 1, "title": "Suzy (Live)", "url": "u"}
        ed.cocher_par_defaut([e], [])
        assert e.coche

    def test_version_inconnue_decochee(self):
        e = ed.Ecart(
            "version", _album(1, "A", []), _piste(1, "Suzy (Live 2006)"), socle=_track("Suzy")
        )
        ed.cocher_par_defaut([e], [])
        assert not e.coche


class TestAccrocherGenius:
    class _Genius:
        def __init__(self, hits):
            self.hits, self.requetes = hits, []

        def search_songs(self, q, limit=10):
            self.requetes.append(q)
            return self.hits

    def test_hit_unique_artiste_et_titre_identiques(self):
        artist = Artist(name="Lucio Bukowski")
        e = ed.Ecart("absent", _album(1, "Chansons", []), _piste(1, "Pas de coté"))
        g = self._Genius(
            [
                {
                    "id": 5,
                    "title": "Pas de côté",
                    "url": "u",
                    "primary_artist": {"name": "Lucio Bukowski"},
                },
                {
                    "id": 6,
                    "title": "Pas de côté",
                    "url": "v",
                    "primary_artist": {"name": "Quelqu'un"},
                },
            ]
        )
        ed.accrocher_genius([e], artist, g)
        assert e.genius["id"] == 5

    def test_deux_hits_plausibles_n_accrochent_rien(self):
        artist = Artist(name="X")
        e = ed.Ecart("absent", _album(1, "A", []), _piste(1, "Intro"))
        g = self._Genius(
            [{"id": 1, "title": "Intro", "url": "u", "primary_artist": {"name": "X"}}] * 2
        )
        ed.accrocher_genius([e], artist, g)
        assert e.genius is None

    def test_apparition_jamais_cherchee(self):
        e = ed.Ecart("apparition", _album(1, "A", [], artist_id=9), _piste(1, "T"))
        g = self._Genius([])
        ed.accrocher_genius([e], Artist(name="X"), g)
        assert g.requetes == []


class TestDetecterAsync:
    class _Client:
        def __init__(self):
            self.pistes_lues = []

        async def get_artist_albums_async(self, http, aid):
            return [{"id": 1, "title": "LMF"}, {"id": 2, "title": "AD$ (Chopped & $crewed)"}]

        async def get_album_async(self, http, album_id):
            if album_id == 1:
                return {
                    "id": 1,
                    "title": "LMF",
                    "record_type": "album",
                    "artist": {"id": NOUS, "name": "Nous"},
                }
            return {
                "id": 2,
                "title": "AD$ (Chopped & $crewed)",
                "record_type": "album",
                "artist": {"id": 999, "name": "Ocho"},
            }

        async def get_album_tracks_async(self, http, album_id):
            if album_id == 1:
                return [
                    {"id": 10, "title": "MW2", "artist": {"id": NOUS}},
                    {"id": 11, "title": "Inédit", "artist": {"id": NOUS}},
                ]
            return [
                {
                    "id": 20,
                    "title": "MW2 (Chopped & $crewed)",
                    "title_short": "MW2",
                    "title_version": "(Chopped & $crewed)",
                    "artist": {"id": 999},
                }
            ]

        async def get_track_async(self, http, tid):
            self.pistes_lues.append(tid)
            return {
                "id": tid,
                "contributors": [{"id": 999, "name": "Ocho"}, {"id": NOUS, "name": "Nous"}],
            }

    class _DM:
        def discographie_reunie(self, artist):
            return [_track("MW2", "LMF")]

        def get_artist_edition_durations(self, aid):
            return {}

        def get_albums_for_artist(self, aid):
            return []

        def get_deezer_release_links(self, aid):
            return set()

    def test_lecture_classement_et_contributeurs(self):
        client = self._Client()
        artist = Artist(name="Nous")
        artist.id = 1
        bilan = asyncio.run(ed.detecter_async(client, None, self._DM(), artist, deezer_id=NOUS))
        assert bilan.complete and bilan.albums_lus == 2
        # Le catalogue lu est exposé (nature des disques, sans relire Deezer).
        assert len(bilan.albums) == 2
        assert [(e.nature, e.titre) for e in bilan.ecarts] == [
            ("link", "MW2"),
            ("absent", "Inédit"),
            ("apparition", "MW2 (Chopped & $crewed)"),
        ]
        assert client.pistes_lues == [20]  # seules les pistes inconnues d'un disque étranger

    def test_arret_demande_rend_un_bilan_incomplet(self):
        artist = Artist(name="Nous")
        artist.id = 1
        bilan = asyncio.run(
            ed.detecter_async(
                self._Client(), None, self._DM(), artist, deezer_id=NOUS, should_stop=lambda: True
            )
        )
        assert not bilan.complete and bilan.albums_lus == 0


class TestCreerLignes:
    def _artiste(self, dm):
        a = Artist(name="Lucio Bukowski")
        a.id = dm.save_artist(a)
        return a

    def test_une_ligne_complete_depuis_deezer(self, data_manager):
        artist = self._artiste(data_manager)
        piste = _piste(
            70,
            "Coal lla",
            isrc="FRX",
            # Deezer liste parfois la même personne deux fois (deux rôles).
            contributors=[(NOUS, "Lucio Bukowski"), (5, "DJ Fly"), (5, "DJ Fly")],
        )
        e = ed.Ecart("absent", _album(7, "Chardons Bleus", [piste]), piste)
        e.genius = {"id": 4242, "title": "Coal lla", "url": "https://genius.com/x"}

        comptes = ed.creer_lignes(data_manager, artist, [e])

        (t,) = data_manager.get_artist_tracks(artist.id)
        assert t.title == "Coal lla" and t.album == "Chardons Bleus" and t.track_number == 1
        assert (t.deezer_id, t.isrc, t.duration) == (70, "FRX", 200)
        assert str(t.release_date)[:10] == "2024-02-08"
        assert (t.genius_id, t.genius_url) == (4242, "https://genius.com/x")
        assert {(c.name, c.role) for c in t.credits} == {
            ("DJ Fly", CreditRole.FEATURED),
            ("Label X", CreditRole.LABEL),
        }
        assert len(t.credits) == 2
        obs = data_manager.get_observations(t.id)
        assert {(o.field, o.source) for o in obs} >= {
            ("duration", "deezer"),
            ("isrc", "deezer"),
            ("release_date", "deezer"),
        }
        (album,) = data_manager.get_albums_for_artist(artist.id)
        assert (album["title"], album["record_type"], album["deezer_album_id"]) == (
            "Chardons Bleus",
            "album",
            7,
        )
        assert "page Genius accrochée" in comptes[0]

    def test_idempotent(self, data_manager):
        artist = self._artiste(data_manager)
        piste = _piste(70, "Coal lla")
        e = ed.Ecart("absent", _album(7, "Chardons Bleus", [piste]), piste)
        ed.creer_lignes(data_manager, artist, [e])
        comptes = ed.creer_lignes(data_manager, artist, [e])
        assert len(data_manager.get_artist_tracks(artist.id)) == 1 and "déjà en base" in comptes[0]

    def test_ecart_perime_isrc_d_une_fiche_fusionnee_rien_cree(self):
        """A2H « Home Sweet Home (Episode 2) » (CORRECTIFS 1.2) : fusionné à la
        main dans « En attendant l'album (Episode 2) », qui portait l'ISRC, puis
        recréé depuis un signalement périmé. Titres différents : on ne crée pas,
        on n'affirme pas non plus le lien (un ISRC en base peut être faux)."""
        gardee = _track("En attendant l’album (Episode 2)", isrc="FR6V82386863")

        class _DM:
            def get_artist_tracks(self, _):
                return [gardee]

            def save_track(self, _):
                raise AssertionError("aucune fiche ne doit être créée")

        artist = Artist(name="A2H")
        artist.id = 1
        piste = _piste(76807198, "Home Sweet Home (Episode 2)", isrc="FR6V82386863")
        e = ed.Ecart("absent", _album(7, "En attendant l'album", [piste]), piste)
        (compte,) = ed.creer_lignes(_DM(), artist, [e])
        assert "rien créé" in compte and "En attendant" in compte

    def test_ecart_perime_titre_d_une_autre_graphie_rien_cree(self, data_manager):
        artist = self._artiste(data_manager)
        data_manager.save_track(Track(title="J'observe et je retiens", artist=artist))
        piste = _piste(81449104, "J’observe et je retiens")
        e = ed.Ecart("absent", _album(7, "Christmas Tape", [piste]), piste)
        (compte,) = ed.creer_lignes(data_manager, artist, [e])
        assert len(data_manager.get_artist_tracks(artist.id)) == 1 and "existe déjà" in compte

    def test_stop_ne_demarre_pas_la_prochaine_ecriture(self, data_manager):
        artist = self._artiste(data_manager)
        piste = _piste(71, "Premier")
        e = ed.Ecart("absent", _album(7, "Chardons Bleus", [piste]), piste)

        comptes = ed.creer_lignes(data_manager, artist, [e], should_stop=lambda: True)

        assert comptes == []
        assert data_manager.get_artist_tracks(artist.id) == []

    def test_apparition_en_featuring_sans_album(self, data_manager):
        artist = self._artiste(data_manager)
        piste = _piste(
            50,
            "MW2 (Chopped & $crewed)",
            "MW2",
            "(Chopped & $crewed)",
            999,
            contributors=[(999, "Ocho"), (NOUS, "Lucio Bukowski")],
        )
        e = ed.Ecart(
            "apparition", _album(5, "AD$ (Chopped & $crewed)", [piste], artist_id=999), piste
        )
        ed.creer_lignes(data_manager, artist, [e])
        (t,) = data_manager.get_artist_tracks(artist.id)
        assert (t.is_featuring, t.primary_artist_name, t.album) == (True, "Ocho", None)
        assert data_manager.get_albums_for_artist(artist.id) == []

    def test_version_deja_variante_kworb_reprend_ses_streams(self, data_manager):
        artist = self._artiste(data_manager)
        socle = Track(title="DKR", artist=artist)
        socle.spotify_id = "0QLjm2PNqKRJfWsxDPa9Ra"
        data_manager.save_track(socle)
        data_manager.record_variant_streams(socle.id, "SPBONUS", 108, 3, None, label="DKR - Live")
        (socle,) = data_manager.get_artist_tracks(artist.id)
        piste = _piste(80, "DKR (Live)", "DKR", "(Live)")
        e = ed.Ecart("version", _album(8, "Ultra (Bonus)", [piste]), piste, socle=socle)
        ed.cocher_par_defaut([e], [socle])
        assert e.coche and e.kworb_spotify_id == "SPBONUS"

        ed.creer_lignes(data_manager, artist, [e])

        lignes = {t.title: t for t in data_manager.get_artist_tracks(artist.id)}
        fiche = lignes["DKR (Live)"]
        assert fiche.streams.spotify_streams == 108 and fiche.spotify_id == "SPBONUS"
        (var,) = [x for x in lignes["DKR"].spotify_id_entries if x.est_rendition]
        assert var.variant_track_id == fiche.id
        assert (
            fiche.relationships[0]["type"] == "version_of"
            and fiche.relationships[0]["track_id"] == socle.id
        )

    def test_heritage_injecte(self, data_manager):
        artist = self._artiste(data_manager)
        socle = Track(title="Suzy", artist=artist)
        data_manager.save_track(socle)
        piste = _piste(90, "Suzy (Live 2006)", "Suzy", "(Live 2006)")
        e = ed.Ecart("version", _album(9, "Live", [piste]), piste, socle=socle)
        vus = []
        ed.creer_lignes(
            data_manager, artist, [e], heriter=lambda v, s: vus.append((v.title, s.title))
        )
        assert vus == [("Suzy (Live 2006)", "Suzy")]


def test_resume_lisible():
    piste = _piste(1, "Coal lla")
    b = ed.BilanEcarts(deezer_id=NOUS, albums_lus=3, pistes_lues=30)
    b.ecarts = [ed.Ecart("absent", _album(1, "Chardons Bleus", [piste]), piste, coche=True)]
    texte = ed.resume(b, "Lucio Bukowski")
    assert "1 écart(s) dont 1 coché(s)" in texte and "[x] ✚ Coal lla" in texte


def test_resume_nomme_l_artiste_sans_qu_on_le_passe():
    """Le hook headless ne passe que le bilan : l'en-tête sortait
    « Deezer —  (id 390) » (2026-10-18)."""
    b = ed.BilanEcarts(deezer_id=390, artiste="Booba")
    assert ed.resume(b).startswith("Deezer — Booba (id 390)")


def test_une_edition_de_diffusion_ne_touche_pas_la_fiche():
    """Un radio edit rattaché apporte SES données (durée coupée, ISRC propre) à
    son ÉDITION, jamais à la fiche de l'original (décision 2026-09-24)."""

    class _DM:
        def __init__(self):
            self.editions, self.identites = [], []

        def record_track_edition(self, track_id, label, **champs):
            self.editions.append((track_id, label, champs))
            return True

        def fill_track_identities(self, *a, **k):
            self.identites.append(k)
            return {}

    dm = _DM()
    fiche = _track("Evasion")
    fiche.id, fiche.duration = 5, 245
    piste = _piste(40, "Evasion - Radio Edit", "Evasion", isrc="FRX1")
    e = ed.Ecart(nature="link", album=_album(4, "Single", [piste]), piste=piste)
    assert ed._renseigner_fiche(dm, e, fiche) == ["édition « Radio Edit »"]
    assert dm.editions == [
        (
            5,
            "Radio Edit",
            {
                "title": "Evasion - Radio Edit",
                "source": "deezer",
                "deezer_id": 40,
                "isrc": "FRX1",
                "duration": 200,
            },
        )
    ]
    assert dm.identites == [] and fiche.duration == 245


def test_contributeur_au_nom_malforme_ecarte():
    """Deezer liste « Soprano) / Ils Disent / Personne n'est Innocent » comme
    contributeur de « La Colombe » (Médine) : un titre mal découpé, pas un
    artiste."""
    from src.services.ecarts_deezer import nom_de_contributeur_plausible

    assert not nom_de_contributeur_plausible("Soprano) / Ils Disent / Personne n'est Innocent")
    assert not nom_de_contributeur_plausible("")
    assert nom_de_contributeur_plausible("Soprano")
    assert nom_de_contributeur_plausible("Swing (BE)")


class TestSignalementsDuPanneau:
    """Les écarts d'une détection deviennent des signalements « À trancher »
    (2026-09-27) ; ce que l'utilisateur vient d'appliquer en sort."""

    def _bilan(self):
        alb = _album(10, "Hourvari", [_piste(1, "Inédit"), _piste(2, "Autre")])
        e1 = ed.Ecart("absent", alb, alb.pistes[0], motifs=["piste 1"])
        e2 = ed.Ecart("absent", alb, alb.pistes[1])
        return ed.BilanEcarts(ecarts=[e1, e2]), e1

    def _dm(self):
        ecrits = {}

        def remplacer(aid, code, cas):
            ecrits[code] = cas
            return len(cas)

        return ecrits, type("DM", (), {"remplacer_signalements": staticmethod(remplacer)})()

    def test_un_detecteur_par_nature_et_les_traites_retires(self):
        from src.models import Artist

        ecrits, dm = self._dm()
        bilan, e1 = self._bilan()
        artiste = Artist(name="Lucio Bukowski")
        artiste.id = 3
        assert ed.enregistrer_signalements(dm, artiste, bilan) == 2
        assert set(ecrits) == {f"deezer_{n}" for n in ed.NATURES_A_TRANCHER}
        assert [c.morceau for c in ecrits["deezer_absent"]] == ["Inédit", "Autre"]
        assert "Hourvari" in ecrits["deezer_absent"][0].motif
        ed.enregistrer_signalements(dm, artiste, bilan, traites=[e1])
        assert [c.morceau for c in ecrits["deezer_absent"]] == ["Autre"]

    def test_detection_incomplete_ou_ambigue_n_efface_rien(self):
        from src.models import Artist

        ecrits, dm = self._dm()
        bilan, _ = self._bilan()
        bilan.interrompu("réseau")
        assert ed.enregistrer_signalements(dm, Artist(name="X"), bilan) == 0
        assert ecrits == {}


def test_un_ecart_se_reconstruit_depuis_son_signalement():
    """Le panneau « À trancher » agit sur un écart ENREGISTRÉ (2026-09-27) :
    sérialisé à la détection, reconstruit à l'action, fiches par leur id."""
    import json

    socle = _track("Hourvari")
    socle.id = 12
    alb = _album(10, "Hourvari (Live)", [_piste(1, "Hourvari - Live", contributors=[(5, "X")])])
    e = ed.Ecart("version", alb, alb.pistes[0], socle=socle, motifs=["live"], matched_by="title")
    d = json.loads(json.dumps(ed.ecart_vers_dict(e)))  # comme en base
    r = ed.ecart_depuis(d, {12: socle})
    assert (r.nature, r.album.title, r.piste.title, r.socle) == (
        "version",
        "Hourvari (Live)",
        "Hourvari - Live",
        socle,
    )
    assert r.piste.contributors == [(5, "X")] and r.coche and r.album.pistes == []


class TestHomonymesDeLaBase:
    def test_disque_d_un_autre_va_a_sa_ligne(self):
        """Kid Cudi, 2026-10-10 : « Reborn » de KIDS SEE GHOSTS posé sur le
        « Reborn » de Dirty Nice (même titre, durée égale)."""
        ksg = _track("Reborn", "KIDS SEE GHOSTS")
        ksg.is_featuring, ksg.primary_artist_name, ksg.duration = True, "KIDS SEE GHOSTS", 200
        autre = _track("Reborn")
        autre.is_featuring, autre.primary_artist_name, autre.duration = True, "Dirty Nice", 200
        piste = _piste(90, "Reborn", artist_id=555, contributors=[(NOUS, "Nous")])
        piste.artist_name = "KIDS SEE GHOSTS"
        albums = [_album(9, "KIDS SEE GHOSTS", [piste], artist_id=555)]
        for base in ([autre, ksg], [ksg, autre]):
            ecarts, _ = ed.classer(albums, base, [], NOUS)
            assert [(e.matched_by, e.existing_track) for e in ecarts] == [("title_duration", ksg)]

    def test_deux_fiches_meme_titre_meme_duree_a_confirmer(self):
        a, b = _track("Boss", "X"), _track("BOSS", "Y")
        a.duration = b.duration = 200
        albums = [_album(9, "Z", [_piste(90, "Boss")])]
        ecarts, _ = ed.classer(albums, [a, b], [], NOUS)
        assert [(e.nature, e.matched_by) for e in ecarts] == [("link_candidate", "title_ambigu")]


class TestMemoireDesRefus:
    """2026-10-10 : un ID Deezer délié n'avait AUCUNE mémoire — « L.U.N.A.T.I.C. »
    et « Par Amour » le reprenaient au run suivant."""

    def test_piste_deliee_ne_revient_pas_sur_la_fiche(self, data_manager):
        from src.models import Artist
        from src.utils.corrections_fiches import memoriser_deezer_refuse

        fiche = _track("Par amour", "Dans ma bulle")
        fiche.artist = Artist(name="Diam's")
        fiche.duration = 200
        albums = [_album(9, "Dans ma bulle", [_piste(90, "Par amour")])]
        assert [e.nature for e in ed.classer(albums, [fiche], [], NOUS)[0]] == ["link"]
        memoriser_deezer_refuse(fiche, 90)
        ecarts, _ = ed.classer(albums, [fiche], [], NOUS)
        # Plus rattachée : la piste redevient un écart (à créer ou non, au choix).
        assert all(e.existing_track is None for e in ecarts)

    def test_provider_ignore_une_piste_refusee(self, data_manager):
        from types import SimpleNamespace

        from src.enrichment.providers.deezer import DeezerProvider
        from src.models import Artist
        from src.utils.corrections_fiches import memoriser_deezer_refuse

        fiche = _track("L.U.N.A.T.I.C.", "Black Album")
        fiche.artist = Artist(name="Booba")
        memoriser_deezer_refuse(fiche, 479308372)
        resultat = {
            "success": True,
            "data": {"deezer_track_id": 479308372, "deezer_duration": 214, "isrc": "FR8R61000008"},
            "verifications": {},
        }
        ctx = SimpleNamespace(force_update=False)
        assert DeezerProvider()._apply_result(fiche, ctx, resultat, None, None) is False
        assert fiche.deezer_id is None and fiche.isrc is None


def test_interprete_entre_crochets_credite_sur_la_piste():
    """Genius : « Skit - Dream Bank [Sofiane Pamart] » ; Deezer : « Skit - Dream
    Bank », Sofiane Pamart crédité (Médine, *Storyteller*, doublon créé le
    2026-09-22). Un « [Remix] » non crédité reste une autre piste."""
    skit = _track("Skit - Dream Bank [Sofiane Pamart]", "Storyteller")
    remix = _track("Lecture aléatoire [Remix]", "Storyteller")
    albums = [
        _album(
            9,
            "Storyteller",
            [
                _piste(90, "Skit - Dream Bank", contributors=[(4, "Sofiane Pamart")]),
                _piste(91, "Lecture aléatoire", contributors=[(4, "Sofiane Pamart")]),
            ],
        )
    ]
    ecarts, _ = ed.classer(albums, [skit, remix], [], NOUS)
    par_titre = {e.titre: e for e in ecarts}
    assert par_titre["Skit - Dream Bank"].existing_track is skit
    assert par_titre["Lecture aléatoire"].existing_track is not remix


def test_le_lien_vers_une_edition_connue_n_est_pas_a_revoir():
    """Booba « Avant De Partir (Radio Edit) » (250 s), notée en édition de la
    fiche album (325 s) : le lien ressortait « à revoir » à chaque run, faute
    des durées d'édition au classement (2026-10-19)."""
    fiche = _track("Avant de partir", "Panthéon")
    fiche.id, fiche.duration = 8984, 325
    fiche.durations_observees = {"deezer": 325, "discogs": 325, "spotify_web": 324}
    albums = [
        _album(
            5,
            "Avant De Partir (Radio Edit)",
            [dataclasses.replace(_piste(50, "Avant De Partir (Radio Edit)"), duration=250)],
        )
    ]
    sans, _ = ed.classer(albums, [fiche], [], NOUS, liens_connus={(5, 8984)})
    fiche.durees_editions = (250,)
    avec, _ = ed.classer(albums, [fiche], [], NOUS, liens_connus={(5, 8984)})
    assert [e.nature for e in sans] == ["link_review"]
    assert "link_review" not in [e.nature for e in avec]
