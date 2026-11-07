"""Un verdict dérivé ne dépend ni de la ligne sœur qu'on regarde, ni des horloges.

« G.O.O.D. Friday » (296 s chez Kid Cudi, rien chez Kanye West) et « The Storm »
(LRC chez Kid Cudi, aucun chez Kanye West) avaient les mêmes observations : seul
le `seen_at` de `lrc_duree_audio` différait, et l'audit écartait le vide contre
le plein (2026-11-06).
"""

from contextlib import closing

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.services import audit as au
from src.utils.lrc_garde import audios_des_lrc


def _artiste(dm, nom):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _fiche(dm, artiste, titre="The Storm", genius_id=4128285, duree=None):
    t = Track(title=titre, artist=artiste)
    t.genius_id = genius_id
    t.duration = duree
    dm.save_track(t)
    return t


def _colonne(dm, track_id, colonne):
    with dm.engine.connect() as conn:
        return conn.execute(
            text(f"SELECT {colonne} FROM tracks WHERE id = :t"), {"t": track_id}
        ).scalar()


def _audios(dm, track_id):
    with dm.engine.connect() as conn:
        return {
            r[0]: r[1]
            for r in conn.execute(
                text(
                    "SELECT source, value FROM observations "
                    "WHERE track_id = :t AND field = 'lrc_duree_audio'"
                ),
                {"t": track_id},
            )
        }


class TestAuditPleinContreVide:
    def _divergences(self, dm):
        with closing(au._connexion(dm.db_path)) as c:
            return [x.libelle for x in au.soeurs_divergentes(c, au._pistes(c, None))]

    def test_un_verdict_vide_chez_une_seule_soeur_se_voit(self, data_manager):
        kanye, cudi = _artiste(data_manager, "Kanye West"), _artiste(data_manager, "Kid Cudi")
        a, b = _fiche(data_manager, kanye, duree=200), _fiche(data_manager, cudi, duree=200)
        with data_manager.engine.begin() as conn:
            conn.execute(text("UPDATE tracks SET lyrics_synced = NULL WHERE id = :t"), {"t": a.id})
            conn.execute(text("UPDATE tracks SET lyrics_synced = 'x' WHERE id = :t"), {"t": b.id})
        divergences = self._divergences(data_manager)
        assert len(divergences) == 1 and "lyrics_synced : 2 valeurs" in divergences[0]

    def test_une_colonne_qui_se_comble_ne_compte_pas_le_vide(self, data_manager):
        kanye, cudi = _artiste(data_manager, "Kanye West"), _artiste(data_manager, "Kid Cudi")
        a, b = _fiche(data_manager, kanye), _fiche(data_manager, cudi)
        with data_manager.engine.begin() as conn:
            conn.execute(text("UPDATE tracks SET genre = 'rap' WHERE id = :t"), {"t": a.id})
            conn.execute(text("UPDATE tracks SET genre = NULL WHERE id = :t"), {"t": b.id})
        assert not self._divergences(data_manager)


class TestAudioSansHorloge:
    def test_pur_l_audio_vaut_quel_que_soit_son_horodatage(self):
        obs = [
            Observation("lyrics_synced", "[00:01.00] a", "lrclib", seen_at="2026-11-02 11:13:06"),
            Observation("lrc_duree_audio", 199, "lrclib", seen_at="2026-10-28 16:34:40"),
            # Un audio sans LRC de sa source ne dit rien.
            Observation("lrc_duree_audio", 240, "ytmusic", seen_at="2026-10-28 16:34:40"),
        ]
        assert audios_des_lrc(obs) == {"lrclib": 199}

    def test_memes_faits_meme_verdict_chez_les_soeurs(self, data_manager):
        """La ligne au vieil horodatage échappait à `deborde_son_audio`."""
        kanye, cudi = _artiste(data_manager, "Kanye West"), _artiste(data_manager, "Kid Cudi")
        a, b = _fiche(data_manager, kanye, duree=200), _fiche(data_manager, cudi, duree=200)
        lu, avant = "2026-11-02 11:13:06", "2026-10-28 16:34:40"
        long = "[00:00.00] a\n[03:55.00] fin"
        for tid, vu in ((a.id, lu), (b.id, avant)):
            data_manager.upsert_observations(
                tid,
                [
                    Observation("lyrics_synced", long, "lrclib", seen_at=lu),
                    Observation("lrc_duree_audio", 199, "lrclib", seen_at=vu),
                ],
            )
        assert _colonne(data_manager, a.id, "lyrics_synced") is None
        assert _colonne(data_manager, b.id, "lyrics_synced") is None


class TestLaGardeEstChezLEcrivain:
    """Ce que le `seen_at` protégeait : un LRC RELU différent, sans durée, ne
    garde pas l'audio de l'ancien."""

    def _lue(self, dm, tid, lrc, audio=None, source="ytmusic"):
        obs = [Observation("lyrics_synced", lrc, source)]
        if audio:
            obs.append(Observation("lrc_duree_audio", audio, source))
        dm.upsert_observations(tid, obs)

    def test_un_autre_lrc_sans_duree_retire_l_audio_soeurs_comprises(self, data_manager):
        kanye, cudi = _artiste(data_manager, "Kanye West"), _artiste(data_manager, "Kid Cudi")
        a, b = _fiche(data_manager, kanye), _fiche(data_manager, cudi)
        self._lue(data_manager, a.id, "[00:01.00] ancien", 240)
        assert _audios(data_manager, b.id) == {"ytmusic": "240"}  # union
        self._lue(data_manager, a.id, "[00:01.00] nouveau")
        assert _audios(data_manager, a.id) == {} and _audios(data_manager, b.id) == {}

    def test_le_meme_lrc_relu_garde_son_audio(self, data_manager):
        a = _fiche(data_manager, _artiste(data_manager, "Kanye West"))
        self._lue(data_manager, a.id, "[00:01.00] même", 240)
        self._lue(data_manager, a.id, "[00:01.00] même")
        assert _audios(data_manager, a.id) == {"ytmusic": "240"}

    def test_un_autre_lrc_avec_sa_duree_la_remplace(self, data_manager):
        a = _fiche(data_manager, _artiste(data_manager, "Kanye West"))
        self._lue(data_manager, a.id, "[00:01.00] ancien", 240)
        self._lue(data_manager, a.id, "[00:01.00] nouveau", 180)
        assert _audios(data_manager, a.id) == {"ytmusic": "180"}

    def test_une_autre_source_n_y_touche_pas(self, data_manager):
        a = _fiche(data_manager, _artiste(data_manager, "Kanye West"))
        self._lue(data_manager, a.id, "[00:01.00] ytm", 240)
        self._lue(data_manager, a.id, "[00:01.00] lrclib", source="lrclib")
        self._lue(data_manager, a.id, "[00:01.00] autre", source="lrclib")
        assert _audios(data_manager, a.id) == {"ytmusic": "240"}


def test_les_voisines_se_lisent_chez_tous_les_artistes_de_la_famille(data_manager):
    """La garde de DT1 : « Sky City » a une voisine chez Kanye, aucune chez Kid
    Cudi ; le verdict ne dépend pas de la ligne (2026-11-06)."""
    from src.utils.track_repository import TrackRepository

    kanye, cudi = _artiste(data_manager, "Kanye West"), _artiste(data_manager, "Kid Cudi")
    a = _fiche(data_manager, kanye, "Sky City", 11)
    b = _fiche(data_manager, cudi, "Sky City", 11)
    _fiche(data_manager, kanye, "Sky City Pt. 2", 12, duree=250)
    _fiche(data_manager, cudi, "Autre", 13, duree=99)
    lire = TrackRepository._durees_des_titres_parents
    with data_manager.engine.connect() as conn:
        assert lire(conn, a.id, kanye.id, "Sky City") == [250]
        assert lire(conn, b.id, cudi.id, "Sky City") == [250]
