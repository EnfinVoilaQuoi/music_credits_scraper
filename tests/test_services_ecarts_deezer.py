"""Écarts de discographie Deezer : classification (pure) et création (base tmp).

Les cas sont ceux mesurés le 2026-09-21 : AD$ (Chopped & $crewed) est un disque
d'Ocho où Freeze contribue ; Au tour de ma bulle (Live 2006) est un disque de
versions ; Lucio Bukowski a de vrais trous et deux EP jumeaux « … » / « ... ».
"""

import asyncio

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
        assert ecarts[0].nature == "version" and ecarts[0].socle.title == "Evasion"

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
        albums = [_album(9, "Album B", [_piste(90, "Intro"), _piste(91, "Outro")])]
        albums[0].pistes[0].duration = None
        albums[0].pistes[1].duration = 61
        ecarts, _ = ed.classer(albums, base, [], NOUS)
        # Deux « Intro » sans durée comparable : rien ne dit que c'est la même ;
        # « Outro » à 1 s près : lien.
        assert [(e.nature, e.matched_by, e.coche) for e in ecarts] == [
            ("link_candidate", "title_generique", False),
            ("link", "title_duration", True),
        ]

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
            TrackSpotifyId(
                spotify_id="SPB", kind="rendition", label="DKR - Bonus Track", streams=108
            )
        ]
        e = ed.Ecart(
            "version",
            _album(1, "Ultra", []),
            _piste(1, "DKR (Bonus Track)", "DKR", "(Bonus Track)"),
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
            70, "Coal lla", isrc="FRX", contributors=[(NOUS, "Lucio Bukowski"), (5, "DJ Fly")]
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
        assert len(data_manager.get_artist_tracks(artist.id)) == 1 and "existe déjà" in comptes[0]

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
        data_manager.record_variant_streams(
            socle.id, "SPBONUS", 108, 3, None, label="DKR - Bonus Track"
        )
        (socle,) = data_manager.get_artist_tracks(artist.id)
        piste = _piste(80, "DKR (Bonus Track)", "DKR", "(Bonus Track)")
        e = ed.Ecart("version", _album(8, "Ultra (Bonus)", [piste]), piste, socle=socle)
        ed.cocher_par_defaut([e], [socle])
        assert e.coche and e.kworb_spotify_id == "SPBONUS"

        ed.creer_lignes(data_manager, artist, [e])

        lignes = {t.title: t for t in data_manager.get_artist_tracks(artist.id)}
        fiche = lignes["DKR (Bonus Track)"]
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
