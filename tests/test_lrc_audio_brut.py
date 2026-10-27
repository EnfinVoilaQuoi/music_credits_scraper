"""La durée de l'audio des LRC d'avant `lrc_duree_audio`, retrouvée au brut
(2026-10-27) : sans elle, la règle de verdict « un LRC ne court pas au-delà de
l'audio de SA source » (`TestDebordeSonAudio`) ne voit aucun LRC plus ancien."""

import json

from sqlalchemy import text

from src.enrichment.observation import Observation
from src.models.artist import Artist
from src.models.track import Track
from src.persistence import brut
from src.services.lrc_audio_brut import audios_lrclib, planifier

_LRC = "[00:00.00] ♪\n[04:35.00] fin"
_AUTRE = "[00:00.00] ♪\n[02:00.00] autre"


def _lecture(corps, titre="I See Now", jour="2026-10-20"):
    requete = brut.url_sans_secret("https://lrclib.net/api/search", {"track_name": titre})
    return (requete, json.dumps(corps).encode(), f"{jour}T10:00:00", f"{jour}T10:05:00")


class TestPur:
    def test_get_et_search(self):
        index = audios_lrclib(
            [
                _lecture({"syncedLyrics": _LRC, "duration": 260.0}),
                _lecture([{"syncedLyrics": _AUTRE, "duration": 120}, {"plainLyrics": "x"}]),
                ("u", b"pas du json", "2026-10-20", "2026-10-20"),
            ]
        )
        assert [e.duree for e in index[_LRC]] == [260]
        assert [(e.duree, e.titre_demande) for e in index[_AUTRE]] == [(120, "I See Now")]

    def test_meme_texte_aux_fins_de_ligne_pres(self):
        crlf = _LRC.replace("\n", "\r\n")
        index = audios_lrclib([_lecture({"syncedLyrics": crlf, "duration": 260})])
        obs = (1, "lrclib", _LRC + "\n", "2026-10-20 23:59:00", "I See Now")
        plan = planifier([obs], {"lrclib": index})
        assert plan.faits == [(1, "lrclib", _LRC + "\n", "2026-10-20 23:59:00", 260)]
        assert plan.issues == {"posee": 1}

    def test_le_meme_texte_sur_un_autre_morceau_ne_compte_pas(self):
        """Mesuré le 2026-10-27 : LRCLIB recopie un LRC sur plusieurs
        enregistrements — « Whole Lotta Lovin' » (299 s) a le LRC du remix
        LeMarquis (226 s). La réponse doit avoir été demandée pour CE titre,
        le jour du LRC."""
        remix = "Whole Lotta Lovin' (LeMarquis Remix)"
        index = audios_lrclib(
            [
                _lecture({"syncedLyrics": _LRC, "duration": 226}, titre=remix),
                _lecture({"syncedLyrics": _AUTRE, "duration": 120}, jour="2026-10-15"),
            ]
        )
        plan = planifier(
            [
                (1, "lrclib", _LRC, "2026-10-20 10:00:00", "Whole Lotta Lovin'"),
                (2, "lrclib", _AUTRE, "2026-09-13 01:32:06", "I See Now"),
            ],
            {"lrclib": index},
        )
        assert plan.faits == []
        assert plan.issues == {"autre_morceau": 1, "autre_lecture": 1}

    def test_rien_de_devine(self):
        """Pas de réponse, durées qui divergent, source sans brut : rien posé."""
        index = audios_lrclib(
            [
                _lecture(
                    [
                        {"syncedLyrics": _LRC, "duration": 260},
                        {"syncedLyrics": _LRC, "duration": 280},
                    ]
                ),
                _lecture({"syncedLyrics": _AUTRE}),
            ]
        )
        plan = planifier(
            [
                (1, "lrclib", _LRC, "2026-10-20", "I See Now"),
                (2, "lrclib", "[00:01.00] inconnu", "2026-10-20", "I See Now"),
                (3, "lrclib", _AUTRE, "2026-10-20", "I See Now"),
                (4, "ytmusic", _LRC, "2026-10-20", "I See Now"),
            ],
            {"lrclib": index},
        )
        assert plan.faits == []
        assert plan.issues == {
            "durees_divergentes": 1,
            "absent_du_brut": 1,
            "sans_duree": 1,
            "source_sans_brut:ytmusic": 1,
        }
        assert plan.divergents == [(1, [260, 280])]


def test_parcourir_lit_le_brut_sans_l_ouvrir_en_ecriture(tmp_path):
    brut.consigner("lrclib", "https://lrclib.net/api/get", 200, b'{"a": 1}', {"track_name": "x"})
    brut.consigner("deezer", "https://api.deezer.com/x", 200, b"{}")
    lus = list(brut.parcourir("lrclib"))
    assert [corps for _, corps, *_ in lus] == [b'{"a": 1}']
    assert list(brut.parcourir("lrclib", tmp_path / "absent.db")) == []


def _fiche(dm):
    a = Artist(name="Kanye West")
    a.id = dm.save_artist(a)
    t = Track(title="I See Now", artist=a)
    dm.save_track(t)
    dm.upsert_observations(t.id, [Observation("lyrics_synced", _LRC, "lrclib")])
    with dm.engine.connect() as conn:
        vu = conn.execute(
            text("SELECT seen_at FROM observations WHERE track_id = :t"), {"t": t.id}
        ).scalar()
    return t, vu


def _colonne(dm, tid):
    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT lyrics_synced FROM tracks WHERE id = :t"), {"t": tid}
        ).scalar()


class TestEcrivain:
    def test_pose_au_seen_at_du_lrc_et_le_verdict_suit(self, data_manager):
        t, vu = _fiche(data_manager)
        assert _colonne(data_manager, t.id) == _LRC
        assert data_manager.poser_durees_audio_lrc([(t.id, "lrclib", _LRC, vu, 260)]) == 1
        obs = [o for o in data_manager.get_observations(t.id) if o.field == "lrc_duree_audio"]
        assert [(o.value, str(o.seen_at)) for o in obs] == [("260", str(vu))]
        assert _colonne(data_manager, t.id) is None
        # Rejoué : déjà posée, rien de plus.
        assert data_manager.poser_durees_audio_lrc([(t.id, "lrclib", _LRC, vu, 260)]) == 0

    def test_un_lrc_relu_depuis_n_est_pas_touche(self, data_manager):
        """La source a relu (autre texte ou autre date) : la durée dirait l'audio
        d'un autre LRC."""
        t, vu = _fiche(data_manager)
        faits = [
            (t.id, "lrclib", _AUTRE, vu, 100),
            (t.id, "lrclib", _LRC, "2020-01-01 00:00:00", 100),
            (t.id, "ytmusic", _LRC, vu, 100),
            (t.id, "lrclib", _LRC, vu, 0),
        ]
        assert data_manager.poser_durees_audio_lrc(faits) == 0
        assert _colonne(data_manager, t.id) == _LRC


def test_script_a_blanc_ne_touche_que_la_copie(data_manager, tmp_path, monkeypatch):
    """Le script copie la base, pose sur la COPIE et rend le rapport ; la base
    d'origine et le brut ne sont que lus."""
    import importlib.util
    from pathlib import Path

    import src.utils.data_manager as module_dm

    t, _vu = _fiche(data_manager)
    data_manager.upsert_observations(t.id, [Observation("duration", 262, "deezer")])
    brut.consigner(
        "lrclib",
        "https://lrclib.net/api/get",
        200,
        json.dumps({"syncedLyrics": _LRC, "duration": 260}).encode(),
        {"track_name": "I See Now", "artist_name": "Kanye West"},
    )
    brut.fermer()
    chemin = Path(__file__).resolve().parent.parent / "scripts" / "poser_durees_audio_lrc.py"
    spec = importlib.util.spec_from_file_location("poser_durees_audio_lrc", chemin)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    from src.utils import corrections_fiches

    monkeypatch.setattr(module_dm, "DATABASE_URL", module_dm.DATABASE_URL)
    monkeypatch.setattr(corrections_fiches, "moteur", corrections_fiches.moteur)
    sortie = tmp_path / "r.md"
    code = script.main(
        ["--base", str(data_manager.db_path), "--brut", str(brut.CHEMIN), "--sortie", str(sortie)]
    )
    assert code == 0
    rapport = sortie.read_text(encoding="utf-8")
    assert "| posee | 1 |" in rapport
    assert "LRC en colonne qui changent : 1" in rapport and "**confirme**" in rapport
    assert _colonne(data_manager, t.id) == _LRC  # la vraie base n'a pas bougé
