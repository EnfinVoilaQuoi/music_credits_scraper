"""L'oracle d'identité Discogs : les disques déjà rattachés désignent le bon homonyme.

Mesuré le 2026-09-23 : 14 artistes sur 25 ont des homonymes Discogs, et le vote
par l'artiste des disques de `tracks.discogs_id` tranche 23/23 sans égalité.
Isha : six homonymes exacts, le nôtre est « Isha (7) » (6244752). Aucun réseau.
"""

from collections import Counter

from src.models import Artist, Track
from src.services import discogs_identite as di


def _t(discogs_id, featuring=False, title="x"):
    t = Track(title=title)
    t.discogs_id, t.is_featuring = discogs_id, featuring
    return t


class _Client:
    def __init__(self, disques=None, annuaire=()):
        self.disques = disques or {}
        self.annuaire = list(annuaire)
        self.lus = []

    def artistes_du_disque(self, rid):
        self.lus.append(rid)
        return self.disques.get(rid)

    def candidats_artiste(self, nom):
        return self.annuaire


class _DM:
    def __init__(self, tracks=()):
        self._tracks = list(tracks)
        self.ecrit = None

    def get_artist_tracks(self, artist_id):
        return self._tracks

    def update_artist_discogs_id(self, artist_id, discogs_id, source=None):
        self.ecrit, self.source = discogs_id, source
        return True


def _isha(discogs_id=None):
    a = Artist(name="Isha")
    a.id, a.discogs_id = 1, discogs_id
    return a


ISHA_7 = (6244752, "Isha (7)")


class TestFonctionsPures:
    def test_disques_non_feat_les_plus_frequents_dabord(self):
        tracks = [_t(10), _t(20), _t(20), _t(30, featuring=True), _t(None)]
        assert di.disques_de(tracks) == [20, 10]
        assert di.disques_de(tracks, max_disques=1) == [20]

    def test_les_artistes_tiers_ne_votent_pas(self):
        """Limsa, Oster Lapwass sont crédités sur les mêmes disques."""
        artistes = [ISHA_7, (77, "Limsa D'Aulnay"), (78, "Oster Lapwass")]
        assert di.voix_du_disque(artistes, "Isha") == {6244752}

    def test_pluralite_nette_et_deux_voix(self):
        assert di.departager_votes(Counter({1: 3, 2: 1})) == 1
        assert di.departager_votes(Counter({1: 2, 2: 2})) is None  # égalité
        assert di.departager_votes(Counter({1: 1})) is None  # une seule voix
        assert di.departager_votes(Counter()) is None


class TestResoudre:
    def test_isha_tranche_par_les_disques_et_memorise(self):
        """Six homonymes à l'annuaire ; les disques désignent Isha (7)."""
        client = _Client(
            disques={
                101: [ISHA_7, (77, "Limsa D'Aulnay")],
                102: [ISHA_7],
                103: [(5, "Isha (2)")],
            },
            annuaire=[(n, f"Isha ({n})") for n in range(1, 7)],
        )
        dm = _DM([_t(101), _t(101), _t(102), _t(103)])
        artist = _isha()
        identite = di.resoudre(client, dm, artist)
        assert identite.id == 6244752 and identite.origine == "disques" and identite.verifiee
        assert dm.ecrit == 6244752 and artist.discogs_id == 6244752

    def test_egalite_ne_tranche_pas_et_nectrit_rien(self):
        client = _Client(
            disques={1: [ISHA_7], 2: [ISHA_7], 3: [(5, "Isha (2)")], 4: [(5, "Isha (2)")]},
            annuaire=[ISHA_7, (5, "Isha (2)")],
        )
        dm = _DM([_t(1), _t(2), _t(3), _t(4)])
        identite = di.resoudre(client, dm, _isha())
        assert identite.id is None and identite.origine == "ambigu"
        assert dm.ecrit is None

    def test_les_feats_ne_votent_pas(self):
        """Le disque d'un feat est celui de l'ARTISTE PRINCIPAL."""
        client = _Client(disques={1: [ISHA_7], 2: [ISHA_7]}, annuaire=[ISHA_7, (5, "Isha (2)")])
        dm = _DM([_t(1, featuring=True), _t(2, featuring=True)])
        assert di.resoudre(client, dm, _isha()).origine == "ambigu"
        assert client.lus == []

    def test_disque_illisible_ne_vote_pas_les_autres_si(self):
        client = _Client(disques={2: [ISHA_7], 3: [ISHA_7]})
        dm = _DM([_t(1), _t(2), _t(3)])  # 1 : illisible (None)
        assert di.resoudre(client, dm, _isha()).id == 6244752

    def test_repli_annuaire_provisoire_jamais_memorise(self):
        """Shurik'n, Diam's : aucun disque rattaché, un seul homonyme."""
        client = _Client(annuaire=[(123, "Shurik'n")])
        dm = _DM([])
        artist = Artist(name="Shurik'N")
        artist.id = 1
        identite = di.resoudre(client, dm, artist)
        assert identite.id == 123 and identite.origine == "annuaire"
        assert not identite.verifiee
        assert dm.ecrit is None and artist.discogs_id is None

    def test_memorise_puis_force(self):
        client = _Client()
        dm = _DM()
        assert di.resoudre(client, dm, _isha(6244752)).origine == "memorisee"
        assert client.lus == [] and dm.ecrit is None
        artist = _isha()
        identite = di.resoudre(client, dm, artist, force_id=42)
        assert identite.id == 42 and identite.origine == "forcee" and dm.ecrit == 42

    def test_inconnu(self):
        identite = di.resoudre(_Client(), _DM(), _isha())
        assert identite.id is None and identite.origine == "inconnu"


class TestLienMusicBrainz:
    """Décision utilisateur 2026-09-29 : un lien MusicBrainz unique vaut
    identité, plusieurs contraignent le vote ; garde-fou : les disques peuvent
    CONTREDIRE, rien n'est alors écrasé."""

    def test_lien_unique_vaut_identite_sans_disque(self):
        dm = _DM([])
        artist = _isha()
        identite = di.resoudre(_Client(), dm, artist, mb_discogs=[6244752])
        assert identite.id == 6244752 and identite.origine == "musicbrainz"
        assert identite.verifiee
        assert (dm.ecrit, dm.source) == (6244752, "musicbrainz")
        assert artist.discogs_id_source == "musicbrainz"

    def test_lien_unique_confirme_par_les_disques(self):
        client = _Client(disques={1: [ISHA_7], 2: [ISHA_7]})
        dm = _DM([_t(1), _t(2)])
        identite = di.resoudre(client, dm, _isha(), mb_discogs=[6244752])
        assert identite.origine == "disques" and dm.source == "disques"

    def test_contredit_par_les_disques_rien_n_est_ecrit(self):
        client = _Client(disques={1: [(5, "Isha (2)")], 2: [(5, "Isha (2)")]})
        dm = _DM([_t(1), _t(2)])
        identite = di.resoudre(client, dm, _isha(), mb_discogs=[6244752])
        assert identite.origine == "contredite" and identite.id is None
        assert (identite.selon_musicbrainz, identite.selon_disques) == (6244752, 5)
        assert dm.ecrit is None

    def test_une_identite_musicbrainz_reste_revisable(self):
        artist = _isha(6244752)
        artist.discogs_id_source = "musicbrainz"
        # Pas encore de disques : elle tient, sans rien réécrire.
        dm = _DM([])
        assert di.resoudre(_Client(), dm, artist).origine == "musicbrainz"
        assert dm.ecrit is None
        # Les disques la contredisent : à trancher, la base garde l'ID.
        client = _Client(disques={1: [(5, "Isha (2)")], 2: [(5, "Isha (2)")]})
        dm = _DM([_t(1), _t(2)])
        assert di.resoudre(client, dm, artist).origine == "contredite"
        assert dm.ecrit is None and artist.discogs_id == 6244752
        # Les disques la confirment : elle devient `disques`, plus jamais revotée.
        client = _Client(disques={1: [ISHA_7], 2: [ISHA_7]})
        dm = _DM([_t(1), _t(2)])
        assert di.resoudre(client, dm, artist).origine == "disques"
        assert artist.discogs_id_source == "disques"
        assert di.resoudre(client, _DM(), artist).origine == "memorisee"

    def test_plusieurs_liens_contraignent_le_vote_et_l_annuaire(self):
        """Les disques votent pour une page que MusicBrainz ne lie pas : elle
        ne peut pas être élue."""
        client = _Client(
            disques={1: [(5, "Isha (2)")], 2: [(5, "Isha (2)")], 3: [ISHA_7]},
            annuaire=[ISHA_7, (5, "Isha (2)"), (9, "Isha (9)")],
        )
        dm = _DM([_t(1), _t(2), _t(3)])
        identite = di.resoudre(client, dm, _isha(), mb_discogs=[6244752, 9])
        # Une voix pour Isha (7) : pas assez pour le vote ; l'annuaire restreint
        # aux liens garde deux pages — ambigu.
        assert identite.origine == "ambigu" and dm.ecrit is None
        client.annuaire = [ISHA_7, (5, "Isha (2)")]
        identite = di.resoudre(client, _DM([_t(1), _t(2), _t(3)]), _isha(), mb_discogs=[6244752, 9])
        assert identite.id == 6244752 and identite.origine == "musicbrainz"


class TestRepository:
    def test_save_artist_ne_vide_pas_discogs_id(self, data_manager):
        artist = Artist(name="Isha")
        artist.id = data_manager.save_artist(artist)
        assert data_manager.update_artist_discogs_id(artist.id, 6244752)
        # Un `Artist` reconstruit sans l'id (la plupart des appelants)…
        data_manager.save_artist(Artist(id=artist.id, name="Isha"))
        assert data_manager.get_artist_by_name("Isha").discogs_id == 6244752
        # …et l'écrivain dédié sait effacer.
        data_manager.update_artist_discogs_id(artist.id, None)
        assert data_manager.get_artist_by_name("Isha").discogs_id is None
