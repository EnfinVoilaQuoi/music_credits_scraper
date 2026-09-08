"""Effacement des données audio et unicité des Spotify ID (`DataEnricher`).

`clear_track_data` est un chemin DESTRUCTIF (46 statements) qui n'était couvert
par aucun test, alors qu'il porte deux promesses fortes :
  · il ne touche QU'AUX données musicales — jamais au titre, à l'artiste ni à
    l'album (une erreur ici viderait la fiche du morceau) ;
  · il pose `clear_audio_observations`, sans quoi les valeurs effacées
    RESSUSCITENT à la relecture par la réconciliation (piège E7-D1).

Harnais `DataEnricher.__new__` : le `__init__` réel monterait tout le pipeline
pour rien (même approche que test_enrich_track_orchestration.py). Depuis le
2026-09-08, `validate_spotify_id_unique` a deux collaborateurs — l'accès base
(portée GLOBALE : un `spotify_id` est mondial, pas propre à un artiste) et le
journal de ses refus — que la fixture pose à la main.
"""

import pytest

from src.models.track import Track
from src.utils.data_enricher import DataEnricher


@pytest.fixture
def enricher():
    e = DataEnricher.__new__(DataEnricher)
    e.data_manager = None  # pas de base : seul le périmètre en mémoire est vu
    e.spotify_id_conflits = []
    return e


class _BaseFictive:
    """Le minimum de `DataManager` dont le validateur a besoin : qui revendique
    un ID sur TOUTE la base (et non chez le seul artiste courant)."""

    def __init__(self, lignes):
        self._lignes = lignes

    def lignes_du_spotify_id(self, spotify_id):
        return [ligne for ligne in self._lignes if ligne["spotify_id"] == spotify_id]


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


class TestUniciteSpotifyIdGlobale:
    """Portée GLOBALE : le garde-fou consulte la base, pas le seul artiste.

    C'est le défaut qui a laissé « Rentre dans le Cercle - Belgique #1 » (Swing)
    et « 13 Organisé » (SCH) porter le MÊME `spotify_id`, durée comprise : deux
    artistes différents, donc deux `artist_tracks` disjoints, donc aucun des
    deux runs ne pouvait voir l'autre.
    """

    def test_id_pris_chez_un_autre_artiste(self, enricher):
        enricher.data_manager = _BaseFictive(
            [{"id": 1157, "artist_id": 9, "title": "13 Organisé", "spotify_id": "SP1"}]
        )
        courant = Track(title="Rentre dans le Cercle - Belgique #1")
        assert enricher.validate_spotify_id_unique("SP1", courant, []) is False
        assert enricher.spotify_id_conflits == [
            ("SP1", "Rentre dans le Cercle - Belgique #1", "13 Organisé")
        ]

    def test_ligne_soeur_acceptee(self, enricher):
        """Même titre chez un autre artiste = la ligne SŒUR du même
        enregistrement (le morceau chez son auteur, le « feat » chez l'invité).
        Elle partage légitimement l'ID."""
        enricher.data_manager = _BaseFictive(
            [{"id": 42, "artist_id": 9, "title": "À la base", "spotify_id": "SP1"}]
        )
        assert enricher.validate_spotify_id_unique("SP1", Track(title="À la base"), []) is True

    def test_sa_propre_ligne_ne_se_contredit_pas(self, enricher):
        enricher.data_manager = _BaseFictive(
            [{"id": 7, "artist_id": 1, "title": "Titre A", "spotify_id": "SP1"}]
        )
        courant = Track(title="Titre A")
        courant.id = 7
        assert enricher.validate_spotify_id_unique("SP1", courant, []) is True

    def test_version_entre_parentheses_est_un_autre_morceau(self, enricher):
        """NON-RÉGRESSION NOMMÉE — c'est la suppression de `_normalize_title`
        qui se vérifie ici.

        La copie privée effaçait les parenthèses : « Un pour la plume » et
        « Un pour la plume (Version équipe) » devenaient le même titre, donc le
        second ID passait pour « une version alternative » et s'écrivait en
        silence (mesuré : les deux morceaux de Flynt partagent un ID et une
        durée de 249 s alors qu'ils durent 249 s et 230 s). Le normaliseur
        partagé du projet les distingue.
        """
        enricher.data_manager = _BaseFictive(
            [
                {
                    "id": 1233,
                    "artist_id": 3,
                    "title": "Un pour la plume",
                    "spotify_id": "5go793BaOjzfop2JwctQ0r",
                }
            ]
        )
        courant = Track(title="Un pour la plume (Version équipe)")
        assert enricher.validate_spotify_id_unique("5go793BaOjzfop2JwctQ0r", courant, []) is False

    def test_le_feat_reste_le_meme_morceau(self, enricher):
        """Le relâchement utile est CONSERVÉ : « Titre (feat. X) » et « Titre »
        sont bien le même morceau pour le normaliseur partagé."""
        enricher.data_manager = _BaseFictive(
            [{"id": 5, "artist_id": 2, "title": "Titre A (feat. SCH)", "spotify_id": "SP1"}]
        )
        assert enricher.validate_spotify_id_unique("SP1", Track(title="Titre A"), []) is True
