"""La piste Discogs d'une fiche : même ENREGISTREMENT, même DISQUE (2026-10-30).

La recherche rapprochait une piste par `titre_de_piste`, qui retire tout ce
qui est entre parenthèses. Mesuré le 30/10 sur la base réelle :

- une VERSION prenait la durée de l'original : les cinq remix « Cudi the Kid
  (… remix) » de Kid Cudi avaient 193 s (l'original) contre 225 à 319 s chez
  leurs catalogues ; Booba « Double Poney (Remix) » 322 s contre 179 s ;
- un HOMONYME prenait celle d'une autre page : Kanye « Reborn » de Dirty Nice
  (186 s partout) recevait les 324 s de *KIDS SEE GHOSTS*, « Runaway » de
  Gezebelle Gaburgably (387 s) les 548 s de *My Beautiful Dark Twisted
  Fantasy*, « Mercy » de HVME (144 s) les 333 s de « Mercy ».

Le crible reste `titre_de_piste` ; le verdict est celui du juge
(`discogs_identity.piste_concorde`). Le brut Discogs (activé le 2026-10-28)
ne contient encore aucune de ces lectures : pas de cassette, les disques sont
reconstitués d'après la base. Aucun réseau.
"""

import pytest

from src.models import Artist, Track
from src.utils.discogs_durees import Fiche, juger
from src.utils.discogs_identity import homonymes_de, piste_concorde
from tests import test_discogs_api as _tda
from tests import test_discogs_search as _tds

_Piste, _Release, _Client, _Artiste = _tda._Piste, _tda._Release, _tds._Client, _tds._Artiste
client = _tda.client  # fixture du module voisin, réexposée sous son nom


def _fiche(titre, artiste="Kid Cudi", album=None, durees=None, **extra):
    t = Track(title=titre, artist=Artist(name=artiste), album=album, **extra)
    t.durations_observees = dict(durees or {})
    return t


class TestVersion:
    @pytest.mark.parametrize(
        "titre",
        [
            "Cudi the Kid (TAI remix)",
            "Cudi the Kid (Mysto & Pizzi remix)",
            "Cudi the Kid (Lucky Date remix)",
            "Cudi the Kid (Designer Drugs remix)",
        ],
    )
    def test_un_remix_n_est_pas_l_original(self, titre):
        ok, motif = piste_concorde(_fiche(titre), "Cudi The Kid", duree_piste="3:13")
        assert not ok and motif.startswith("version")

    def test_le_meme_remix_est_accepte(self):
        assert piste_concorde(_fiche("Cudi the Kid (TAI remix)"), "Cudi The Kid (TAI Remix)")[0]

    def test_remix_et_instrumental_de_booba(self):
        for titre in ("Double Poney (Remix)", "Double Poney (Instrumental)"):
            assert not piste_concorde(_fiche(titre, "Booba"), "Double Poney")[0]
        assert piste_concorde(_fiche("Double poney", "Booba"), "Double Poney")[0]

    def test_un_sous_titre_exige_une_duree_independante(self):
        """« (Kissy Sell Out's Style From the Dark Side) » se lit comme un
        sous-titre : sans durée qui l'appuie, la piste nue n'est pas la fiche."""
        kissy = "Cudi the Kid (Kissy Sell Out’s Style From the Dark Side)"
        durees = {"spotify_web": 290, "youtube": 291, "discogs": 193}
        ok, motif = piste_concorde(_fiche(kissy, durees=durees), "Cudi The Kid", duree_piste="3:13")
        assert not ok and "durée indépendante" in motif
        # Sans aucune durée, pas davantage.
        assert not piste_concorde(_fiche(kissy), "Cudi The Kid", duree_piste="3:13")[0]
        # La durée de la piste corroborée : c'est bien elle.
        porte = _fiche("Make Her Say (I Poke Her Face)", durees={"deezer": 222})
        assert piste_concorde(porte, "Make Her Say", duree_piste="3:44")[0]

    def test_la_duree_discogs_deja_posee_n_est_pas_un_temoin(self):
        porte = _fiche("Make Her Say (I Poke Her Face)", durees={"discogs": 224})
        assert not piste_concorde(porte, "Make Her Say", duree_piste="3:44")[0]

    def test_une_graphie_ou_un_feat_ne_changent_rien(self):
        assert piste_concorde(_fiche("Bande organisée", "Jul"), "Bande Organisée (feat. SCH)")[0]


class TestDisque:
    def test_homonyme_sans_disque_refuse(self):
        reborn = _fiche("Reborn", "Kanye West", durees={"deezer": 186})
        ok, motif = piste_concorde(reborn, "Reborn", disque="Kids See Ghosts", homonymes=True)
        assert not ok and "plusieurs pages" in motif

    def test_homonyme_sur_son_disque_accepte(self):
        ksg = _fiche("Reborn", "Kanye West", album="KIDS SEE GHOSTS")
        assert piste_concorde(ksg, "Reborn", disque="Kids See Ghosts", homonymes=True)[0]
        mbdtf = _fiche("Runaway", "Kanye West", album="My Beautiful Dark Twisted Fantasy")
        assert not piste_concorde(mbdtf, "Runaway", disque="Cruel Summer", homonymes=True)[0]

    def test_titre_generique_sur_un_autre_disque(self):
        intro = _fiche("Intro", "Booba", album="0.9")
        assert not piste_concorde(intro, "Intro", disque="Autopsie Vol. 3")[0]
        assert piste_concorde(intro, "Intro", disque="0.9")[0]
        # Sans disque de la fiche, la règle des gates Spotify et Deezer ne juge pas.
        assert piste_concorde(_fiche("Intro", "Booba"), "Intro", disque="Autopsie Vol. 3")[0]


class TestHomonymes:
    def test_les_autres_pages_au_meme_titre(self):
        reborn = _fiche("Reborn", "Kanye West", id=12115, genius_id=4286164)
        ksg = _fiche("Reborn", "Kanye West", album="KIDS SEE GHOSTS", id=9918, genius_id=3720970)
        autre = _fiche("Stronger", "Kanye West", id=1, genius_id=1)
        assert homonymes_de(reborn, [reborn, ksg, autre]) == [ksg]

    def test_une_ligne_soeur_n_est_pas_un_homonyme(self):
        a = _fiche("Reborn", "Kanye West", id=12115, genius_id=4286164)
        b = _fiche("Reborn", "Kanye West", id=10012, genius_id=4286164)
        assert homonymes_de(a, [a, b]) == []
        assert homonymes_de(a, None) == []


class TestRecherche:
    """Le parcours de `search_track` / `enrich_track_data` sur des disques
    reconstitués d'après la base."""

    def test_le_remix_trouve_sa_piste_et_non_l_original(self, client):
        disque = _Release(
            id=13319609,
            title="Cudi The Kid",
            tracklist=[
                _Piste("Cudi The Kid", "1", "3:13"),
                _Piste("Cudi The Kid (TAI Remix)", "2", "4:11"),
            ],
            artists=[_Artiste(1, "Steve Aoki")],
        )
        client.client = _Client([disque])
        fiche = _fiche("Cudi the Kid (TAI remix)", is_featuring=True)
        fiche.primary_artist_name = "Steve Aoki"
        data = client.search_track(
            fiche.title, "Kid Cudi", noms_attendus={"Steve Aoki"}, fiche=fiche
        )
        assert (data["position"], data["duration"]) == ("2", "4:11")

    def test_le_remix_sans_sa_piste_n_a_rien(self, client):
        disque = _Release(
            tracklist=[_Piste("Cudi The Kid", "1", "3:13")], artists=[_Artiste(1, "Kid Cudi")]
        )
        client.client = _Client([disque])
        fiche = _fiche("Cudi the Kid (Designer Drugs remix)")
        assert client.search_track(fiche.title, "Kid Cudi", fiche=fiche) is None

    def test_double_poney_remix_ne_prend_pas_les_322_s(self, client, monkeypatch):
        autopsie = _Release(
            id=4566414,
            title="Autopsie Vol. 3",
            tracklist=[_Piste("Double Poney", "3", "5:22")],
            artists=[_Artiste(2, "Booba")],
        )
        client.client = _Client([autopsie])
        fiche = _fiche("Double Poney (Remix)", "Booba", durees={"deezer": 179})
        assert client.enrich_track_data(fiche) is False
        assert not [o for o in fiche.observations if o.field == "duration"]

    def test_reborn_de_dirty_nice_ne_prend_pas_kids_see_ghosts(self, client):
        ksg_disque = _Release(
            id=12108759,
            title="Kids See Ghosts",
            tracklist=[_Piste("Reborn", "5", "5:24")],
            artists=[_Artiste(3, "Kanye West"), _Artiste(4, "Kid Cudi")],
        )
        client.client = _Client([ksg_disque])
        reborn = _fiche("Reborn", "Kanye West", id=12115, genius_id=4286164, is_featuring=True)
        reborn.primary_artist_name = "Dirty Nice"
        ksg = _fiche("Reborn", "Kanye West", album="KIDS SEE GHOSTS", id=9918, genius_id=3720970)
        assert client.enrich_track_data(reborn, fiches=[reborn, ksg]) is False
        # La page de KIDS SEE GHOSTS, elle, a sa piste.
        client.client = _Client([ksg_disque])
        assert client.enrich_track_data(ksg, fiches=[reborn, ksg]) is True
        assert ("duration", 324) in [(o.field, o.value) for o in ksg.observations]

    def test_sans_les_fiches_de_l_artiste_rien_ne_change(self, client):
        """Un appelant qui ne donne pas les fiches (oracle, script) garde le
        juge de version ; seul le critère des homonymes manque."""
        disque = _Release(tracklist=[_Piste("Runaway", "E1", "9:08")])
        disque.artists = [_Artiste(3, "Kanye West")]
        client.client = _Client([disque])
        assert client.enrich_track_data(_fiche("Runaway", "Kanye West")) is True


# ── Rattrapage des durées déjà posées (`discogs_durees`, script
# `retirer_durees_discogs.py`) — fiches reconstituées d'après la base du 30/10.


def _f(id_, titre, durees, artiste="Kid Cudi", **kw):
    kw.setdefault("artist_id", 1)
    kw.setdefault("genius_id", id_)
    return Fiche(id=id_, artiste=artiste, title=titre, durations_observees=durees, **kw)


def _etats(fiches):
    return {v.fiche.id: v.etat for v in juger(fiches)}


class TestRattrapage:
    def test_les_remix_de_cudi_the_kid_perdent_les_193_s(self):
        piste = {"discogs_id": 13319609, "position": "1"}
        fiches = [
            _f(3946, "Cudi the Kid (TAI remix)", {"discogs": "193", "deezer": "251"}, **piste),
            _f(
                3951,
                "Cudi the Kid (Designer Drugs remix)",
                {"discogs": "193", "deezer": "225"},
                **piste,
            ),
            _f(3711, "Cudi the Kid", {"deezer": "286"}, album="Wonderland"),
        ]
        assert _etats(fiches) == {3946: "retirer", 3951: "retirer"}

    def test_double_poney_remix_part_l_original_et_l_instrumental_restent(self):
        piste = {"discogs_id": 4566414, "position": "3", "artiste": "Booba"}
        album = {"album": "Autopsie Vol. 3"}
        fiches = [
            _f(8868, "Double poney", {"discogs": "322", "deezer": "318"}, **album, **piste),
            _f(8528, "Double Poney (Remix)", {"discogs": "322", "deezer": "179"}, **piste),
            # La vidéo (327 s) ne dément pas : l'instrumental dure autant.
            _f(
                8865,
                "Double Poney (Instrumental)",
                {"discogs": "322", "youtube_video": "327"},
                **album,
                **piste,
            ),
        ]
        assert _etats(fiches) == {8868: "confirme", 8528: "retirer", 8865: "indecis"}

    def test_une_version_sans_temoin_perd_la_piste_de_l_original(self):
        piste = {"discogs_id": 4566414, "position": "13", "artiste": "Booba"}
        fiches = [
            _f(8859, "Rats des villes", {"discogs": "224", "deezer": "227"}, **piste),
            _f(8866, "Rats des villes (Instrumental)", {"discogs": "224"}, **piste),
        ]
        assert _etats(fiches)[8866] == "retirer"

    def test_deux_versions_d_une_seance_ne_se_departagent_pas(self):
        piste = {"discogs_id": 9, "position": "2", "artiste": "Médine"}
        fiches = [
            _f(9835, "Houri (version acoustique)", {"discogs": "149", "deezer": "149"}, **piste),
            _f(3077, "Houri (live session)", {"discogs": "149"}, **piste),
        ]
        assert _etats(fiches)[3077] == "indecis"

    def test_homonyme_d_un_autre_interprete(self):
        """« Mercy » d'ESH (vidéo 222 s) reçoit les 333 s de la « Mercy » de Kanye."""
        k = {"artiste": "Kanye West", "noms_de_l_artiste": frozenset({"Kanye West"})}
        fiches = [
            _f(6328, "Mercy", {"deezer": "332"}, album="Cruel Summer", principal="Kanye West", **k),
            _f(10223, "Mercy", {"discogs": "333", "deezer": "144"}, principal="HVME", **k),
            _f(10383, "Mercy", {"discogs": "333", "youtube_video": "222"}, principal="ESH", **k),
        ]
        assert _etats(fiches) == {10223: "retirer", 10383: "retirer"}

    def test_homonyme_d_une_formation_de_l_artiste_reste(self):
        noms = frozenset({"OrelSan", "Casseurs Flowters"})
        o = {"artiste": "OrelSan", "noms_de_l_artiste": noms}
        fiches = [
            _f(4504, "Entre bien et mal", {"deezer": "281"}, album="X", principal="OrelSan", **o),
            _f(9989, "Entre bien et mal", {"discogs": "281"}, principal="Casseurs Flowters", **o),
        ]
        assert _etats(fiches)[9989] == "indecis"

    def test_les_lignes_soeurs_ne_comptent_qu_une_fois(self):
        commun = {"genius_id": 4286164, "discogs_id": 12108759}
        fiches = [
            _f(12115, "Reborn", {"discogs": "324", "deezer": "186"}, "Kanye West", **commun),
            _f(10012, "Reborn", {"discogs": "324", "deezer": "186"}, artist_id=2, **commun),
            _f(9918, "Reborn", {"deezer": "325"}, "Kanye West", album="KIDS SEE GHOSTS"),
        ]
        # Un verdict pour la page ; le retrait passe chez la sœur par l'écrivain.
        assert [(v.fiche.id, v.etat) for v in juger(fiches)] == [(12115, "retirer")]

    def test_une_duree_ne_designe_pas_un_titre_generique(self):
        fiches = [
            _f(6881, "Intro (Get Well Soon)", {"deezer": "34"}, album="Get Well Soon"),
            _f(6731, "Intro (Freshmen Adjustment)", {"discogs": "34"}, album="Freshmen"),
        ]
        assert _etats(fiches) == {}
