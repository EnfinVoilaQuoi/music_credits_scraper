"""Effacement des données audio et unicité des Spotify ID (`DataEnricher`).

`clear_track_data` est un chemin DESTRUCTIF (46 statements) qui n'était couvert
par aucun test, alors qu'il porte deux promesses fortes :
  · il ne touche QU'AUX données musicales — jamais au titre, à l'artiste ni à
    l'album (une erreur ici viderait la fiche du morceau) ;
  · il pose `clear_audio_observations`, sans quoi les valeurs effacées
    RESSUSCITENT à la relecture par la réconciliation (piège E7-D1).

Harnais `DataEnricher.__new__` : ces deux méthodes n'utilisent aucun état
d'instance, le `__init__` réel monterait tout le pipeline pour rien (même
approche que test_enrich_track_orchestration.py).
"""

import pytest

from src.models.track import Track
from src.utils.data_enricher import DataEnricher


@pytest.fixture
def enricher():
    return DataEnricher.__new__(DataEnricher)


def _track_rempli(**kw):
    t = Track(title=kw.get("title", "Bande organisée"))
    t.artist = kw.get("artist", "Jul")
    t.album = "Mon Album"
    t.audio.bpm = 140
    t.audio.key = 5
    t.audio.mode = 1
    t.audio.musical_key = "Fa majeur"
    t.duration = 214
    t.spotify_id = "SP1"
    return t


class TestEffacement:
    def test_efface_les_donnees_audio(self, enricher):
        t = _track_rempli()
        assert enricher.clear_track_data(t) is True
        assert t.audio.bpm is None
        assert t.audio.key is None
        assert t.audio.mode is None
        assert t.audio.musical_key is None
        assert t.duration is None

    def test_ne_touche_pas_a_l_identite_du_morceau(self, enricher):
        """LA promesse du module : nettoyer les données musicales ne doit jamais
        vider la fiche. Un morceau sans titre ni artiste est irrécupérable."""
        t = _track_rempli()
        enricher.clear_track_data(t)
        assert t.title == "Bande organisée"
        assert t.artist == "Jul"
        assert t.album == "Mon Album"

    def test_spotify_id_conserve_par_defaut(self, enricher):
        """Le Spotify ID coûte un scrape à retrouver : on ne l'efface que sur
        demande explicite."""
        t = _track_rempli()
        enricher.clear_track_data(t)
        assert t.spotify_id == "SP1"

    def test_spotify_id_efface_sur_demande(self, enricher):
        t = _track_rempli()
        assert enricher.clear_track_data(t, clear_spotify_id=True) is True
        assert t.spotify_id is None

    def test_observations_marquees_pour_suppression(self, enricher):
        """Piège E7-D1 : sans ce drapeau, la réconciliation ressuscite les
        valeurs effacées à la relecture — l'effacement paraît sans effet."""
        t = _track_rempli()
        enricher.clear_track_data(t)
        assert t.clear_audio_observations is True

    def test_rien_a_effacer(self, enricher):
        """Aucune donnée audio : False, et surtout PAS de drapeau posé (rien à
        supprimer côté observations)."""
        t = Track(title="Vide")
        t.artist = "Jul"
        assert enricher.clear_track_data(t) is False
        assert getattr(t, "clear_audio_observations", False) is False

    def test_effacement_partiel(self, enricher):
        """Un seul champ renseigné suffit à déclencher un nettoyage effectif."""
        t = Track(title="Partiel")
        t.artist = "Jul"
        t.audio.bpm = 90
        assert enricher.clear_track_data(t) is True
        assert t.audio.bpm is None

    def test_spotify_id_seul_deja_vide(self, enricher):
        t = Track(title="Partiel")
        t.artist = "Jul"
        assert enricher.clear_track_data(t, clear_spotify_id=True) is False


class TestGardeFous:
    def test_morceau_sans_titre_refuse(self, enricher):
        """Un track sans titre est le signe d'un objet corrompu : on n'y touche
        pas, plutôt que d'effacer des champs sur une fiche déjà cassée."""
        t = _track_rempli()
        t.title = ""
        assert enricher.clear_track_data(t) is False
        assert t.audio.bpm == 140  # rien n'a été effacé

    def test_artiste_disparu_apres_nettoyage(self, enricher):
        """Vérification POST-nettoyage : si l'artiste a disparu en cours de
        route, on le signale par un False même si l'effacement a eu lieu."""

        class _TrackPiege(Track):
            @property
            def artist(self):
                return None

            @artist.setter
            def artist(self, v):
                pass

        t = _TrackPiege(title="Piégé")
        t.audio.bpm = 140
        assert enricher.clear_track_data(t) is False


class TestUniciteSpotifyId:
    def _t(self, titre, ids=()):
        t = Track(title=titre)
        t.spotify_ids = list(ids)
        return t

    def test_id_libre(self, enricher):
        courant = self._t("Titre A")
        assert enricher.validate_spotify_id_unique("SP1", courant, [self._t("Titre B")]) is True

    def test_id_pris_par_un_autre_titre(self, enricher):
        """Le rejet est le cœur du garde-fou : deux morceaux distincts ne
        peuvent pas partager un ID Spotify."""
        autre = self._t("Titre B", ["SP1"])
        assert enricher.validate_spotify_id_unique("SP1", self._t("Titre A"), [autre]) is False

    def test_id_pris_par_le_meme_titre(self, enricher):
        """Version alternative du MÊME morceau (radio edit, remaster) : plusieurs
        IDs pour un titre sont légitimes."""
        jumeau = self._t("Titre A", ["SP1"])
        assert enricher.validate_spotify_id_unique("SP1", self._t("Titre A"), [jumeau]) is True

    def test_titre_normalise_avant_comparaison(self, enricher):
        """« Titre (feat. X) » et « Titre » sont le même morceau."""
        jumeau = self._t("Titre A (feat. SCH)", ["SP1"])
        assert enricher.validate_spotify_id_unique("SP1", self._t("Titre A"), [jumeau]) is True

    def test_id_vide(self, enricher):
        assert enricher.validate_spotify_id_unique("", self._t("A"), [self._t("B")]) is True

    def test_aucun_morceau_en_base(self, enricher):
        assert enricher.validate_spotify_id_unique("SP1", self._t("A"), []) is True


class TestNormalisationDeTitre:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("Bande Organisée", "bande organisee"),
            ("Titre (feat. SCH)", "titre"),
            ("Titre [Remix]", "titre"),
            ("Titre feat. SCH", "titre"),
            ("Titre ft. SCH", "titre"),
            ("Titre !!!", "titre"),
            ("Titre   espacé", "titre espace"),
        ],
    )
    def test_normalisation(self, enricher, entree, attendu):
        assert enricher._normalize_title(entree) == attendu
