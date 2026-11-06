"""`scripts/rattraper_colonnes_sans_provenance.py` et le retrait d'un ISRC chez les
sœurs : « Outro » (*Reste Calme*) et « Intro » (*AVS*) portaient, sans observation,
l'ISRC de l'« Outro » d'*OV3* et de l'« Intro » de *0,24* (2026-11-06).
"""

import pytest
from sqlalchemy import text

import scripts.rattraper_colonnes_sans_provenance as script
from scripts.rattraper_colonnes_sans_provenance import FAUX, INCONNU, SOUTENU, Piste, juger_isrc
from src.enrichment.observation import Observation
from src.models import Artist, Track
from src.utils.corrections_fiches import fiches_par_id, isrcs_refuses


def _fiche(**champs):
    return {
        "title": "Outro",
        "album": "Reste Calme",
        "artiste": "Josman",
        "primary_artist_name": "Di-Meh",
        "deezer_id": None,
        **champs,
    }


class TestJuge:
    def test_generique_d_un_autre_disque(self):
        piste = Piste(1, "Outro", "OV3", 195, ["Di-Meh", "Slimka"])
        assert juger_isrc(_fiche(), piste, {"youtube_video": 331})[0] == FAUX

    def test_generique_du_meme_disque(self):
        piste = Piste(1, "Outro", "Reste Calme", 331, ["Di-Meh"])
        assert juger_isrc(_fiche(), piste, {"youtube_video": 331})[0] == SOUTENU

    def test_la_piste_deezer_de_la_fiche(self):
        piste = Piste(7, "RAINBOW (COLORS)", "NEW BLUES", 238, ["B.B. Jacques"])
        fiche = _fiche(title="RAINBOW", album="NEW BLUES", artiste="B.B. Jacques", deezer_id=7)
        assert juger_isrc(fiche, piste, {})[0] == SOUTENU

    def test_autre_disque_meme_morceau_credite_a_la_duree(self):
        """« Jeune de banlieue » : la compilation de Disiz, à 1 s de la vidéo."""
        piste = Piste(1, "Jeune de banlieue", "Classics", 324, ["disiz", "Diam's", "Soprano"])
        fiche = _fiche(
            title="Jeune de banlieue ft Diam’s et Soprano",
            album="Psychanalyse avant l’album",
            artiste="Diam’s",
            primary_artist_name="disiz",
        )
        assert juger_isrc(fiche, piste, {"youtube": 325})[0] == SOUTENU

    def test_une_duree_lue_par_cet_isrc_ne_corrobore_pas(self):
        """ReccoBeats lit par l'ISRC jugé ; `legacy` ne vaut pas témoin."""
        piste = Piste(1, "Sombre", "Batterie faible", 174, ["Damso"])
        fiche = _fiche(title="Sombre", album="Batterie faible", primary_artist_name="Damso")
        assert juger_isrc(fiche, piste, {"reccobeats": 174, "legacy": 174})[0] == INCONNU

    def test_une_reprise_a_l_autre_duree_reste_a_l_humain(self):
        """« Erase me » : l'hommage (« made famous by Kid Cudi ») à 199 s, fiche à 192."""
        piste = Piste(1, "Erase me", "Erase me", 199, ["made famous by Kid Cudi feat Kanye West"])
        fiche = _fiche(
            title="Erase Me", album="MOTM II", artiste="Kid Cudi", primary_artist_name="Kid Cudi"
        )
        assert juger_isrc(fiche, piste, {"deezer": 192})[0] == INCONNU

    def test_deezer_sans_reponse(self):
        assert juger_isrc(_fiche(), None, {})[0] == INCONNU


def _artiste(dm, nom):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _ligne(dm, artiste, titre="Intro", genius_id=1866083, **champs):
    t = Track(title=titre, artist=artiste)
    t.genius_id = genius_id
    t.id = dm.save_track(t)
    if champs:
        with dm.engine.begin() as conn:
            sets = ", ".join(f"{c} = :{c}" for c in champs)
            conn.execute(text(f"UPDATE tracks SET {sets} WHERE id = :t"), {"t": t.id, **champs})
    return t


def _colonne(dm, tid, col):
    with dm.engine.connect() as conn:
        return conn.execute(text(f"SELECT {col} FROM tracks WHERE id = :t"), {"t": tid}).scalar()


def _champs(dm, tid):
    return {(o.field, o.source) for o in dm.get_observations(tid)}


class TestRetraitChezLesSoeurs:
    def test_les_mesures_lues_par_l_isrc_partent_d_une_soeur_sans_isrc(self, data_manager):
        """L'union les rendait : ReccoBeats et sa durée restaient en place."""
        nepal, sheldon = _artiste(data_manager, "Népal"), _artiste(data_manager, "Sheldon")
        a = _ligne(data_manager, nepal, isrc="FXR142200083")
        b = _ligne(data_manager, sheldon)
        data_manager.upsert_observations(
            a.id,
            [
                Observation("duration", 140, "reccobeats"),
                Observation("reccobeats_resolution", "isrc", "reccobeats"),
            ],
        )
        assert ("duration", "reccobeats") in _champs(data_manager, b.id)
        assert data_manager.clear_track_isrc(a.id)["isrc_efface"]
        for tid in (a.id, b.id):
            assert not {c for c in _champs(data_manager, tid) if c[1] == "reccobeats"}
            assert _colonne(data_manager, tid, "duration") is None
            assert _colonne(data_manager, tid, "isrc") is None

    def test_une_ligne_visee_sans_isrc_reste_un_geste_nul(self, data_manager):
        a = _ligne(data_manager, _artiste(data_manager, "Népal"))
        data_manager.upsert_observations(
            a.id,
            [
                Observation("duration", 140, "reccobeats"),
                Observation("reccobeats_resolution", "isrc", "reccobeats"),
            ],
        )
        assert not data_manager.clear_track_isrc(a.id)["isrc_efface"]
        assert ("duration", "reccobeats") in _champs(data_manager, a.id)

    def test_une_soeur_a_l_autre_isrc_garde_le_sien(self, data_manager):
        a = _ligne(data_manager, _artiste(data_manager, "Népal"), isrc="AAA")
        b = _ligne(data_manager, _artiste(data_manager, "Sheldon"), isrc="BBB")
        data_manager.clear_track_isrc(a.id)
        assert _colonne(data_manager, b.id, "isrc") == "BBB"


class TestScript:
    @pytest.fixture
    def base(self, data_manager, monkeypatch):
        import src.utils.data_manager as module_dm

        monkeypatch.setattr(module_dm, "DataManager", lambda: data_manager)
        monkeypatch.setattr(script, "colonnes_a_rattraper", script.colonnes_a_rattraper)
        return data_manager

    @staticmethod
    def _lire(isrc):
        return {"FAUX1": Piste(1, "Outro", "OV3", 195, ["Di-Meh"])}.get(isrc)

    def test_a_blanc_rien_n_est_ecrit(self, base, capsys):
        a = _ligne(base, _artiste(base, "Josman"), "Outro", 1, album="Reste Calme", isrc="FAUX1")
        assert script.main([], lire=self._lire) == 0
        sortie = capsys.readouterr().out
        assert "à retirer et refuser (1)" in sortie and "à blanc" in sortie
        assert _colonne(base, a.id, "isrc") == "FAUX1"
        assert not isrcs_refuses(fiches_par_id([a.id])[a.id])

    def test_apply_retire_refuse_et_pose_la_provenance(self, base, monkeypatch, capsys):
        from src.utils import database_backup

        monkeypatch.setattr(
            database_backup,
            "get_backup_manager",
            lambda: type("B", (), {"create_backup": lambda self, nom: nom})(),
        )
        artiste = _artiste(base, "Josman")
        faux = _ligne(base, artiste, "Outro", 1, album="Reste Calme", isrc="FAUX1")
        date = _ligne(base, artiste, "Morceau", 2, release_date="2014-05-10", spotify_id="sp")
        seul = _ligne(base, artiste, "Sans id", 3, duration=246)
        lrc = _ligne(
            base,
            artiste,
            "Chanson",
            4,
            deezer_id=9,
            lyrics_synced="[00:01.00] a",
            lyrics_synced_source="LRCLIB",
        )
        assert script.main(["--apply"], lire=self._lire) == 0

        assert _colonne(base, faux.id, "isrc") is None
        assert isrcs_refuses(fiches_par_id([faux.id])[faux.id]) == {"FAUX1"}
        assert ("release_date", "legacy") in _champs(base, date.id)
        assert ("lyrics_synced", "lrclib") in _champs(base, lrc.id)
        assert _colonne(base, lrc.id, "lyrics_synced") == "[00:01.00] a"
        # Rien ne la soutient : listée, ni observation ni retrait.
        assert not _champs(base, seul.id) and _colonne(base, seul.id, "duration") == 246
        assert "Sans id" in capsys.readouterr().out
