"""Panneau « À trancher » : détecteurs PURS (`src/services/revue`, 2026-09-27)."""

from types import SimpleNamespace

from src.models import Artist, Track
from src.services import revue

_A = Artist(name="A2H")


def _t(titre, tid=1, **kw):
    t = Track(title=titre, artist=_A)
    t.id = tid
    for k, v in kw.items():
        setattr(t, k, v)
    return t


class TestDurees:
    def test_songbpm_dementi_par_youtube(self):
        t = _t("Blues (Live at AK Studios)", durations_observees={"youtube": 303, "songbpm": 176})
        motif, preuves = revue.duree_songbpm_dementie(t)
        assert "176" in motif and preuves == {"youtube": 303, "songbpm": 176}

    def test_ecart_d_encodage_toleré(self):
        # Louisville : 197 s / 193 s — dans la marge, la durée ne tranche pas.
        t = _t("Louisville", durations_observees={"youtube": 197, "songbpm": 193})
        assert revue.duree_songbpm_dementie(t) is None

    def test_id_spotify_dementi(self):
        t = _t(
            "X",
            spotify_id="SP",
            durations_observees={"youtube": 200, "reccobeats": 150},
        )
        assert "autre enregistrement" in revue.duree_spotify_dementie(t)[0]

    def test_sans_audio_youtube_rien(self):
        t = _t("X", spotify_id="SP", durations_observees={"reccobeats": 150})
        assert revue.duree_spotify_dementie(t) is None


class TestLrc:
    def _avec(self, lrc):
        t = _t("X")
        t.lyrics.text = " ".join(f"mot{i}" for i in range(20))
        t.lyrics.source = "genius"
        t.lyrics.synced = lrc
        t.lyrics.synced_source = "YouTube Music"
        return t

    def test_tranche_ambigue_signalee(self):
        # 10 mots communs sur 20 : 50 %.
        lrc = (
            " ".join(f"mot{i}" for i in range(10)) + " " + " ".join(f"autre{i}" for i in range(10))
        )
        assert "50%" in revue.lrc_douteux(self._avec(lrc))[0]

    def test_lrc_confirme_ou_dementi_non_signale(self):
        juste = " ".join(f"mot{i}" for i in range(20))
        faux = " ".join(f"autre{i}" for i in range(20))
        assert revue.lrc_douteux(self._avec(juste)) is None
        assert revue.lrc_douteux(self._avec(faux)) is None


class TestDoublons:
    def test_casse_et_ponctuation(self):
        a, b = _t("Pour de vrai", 1), _t("Pour de Vrai", 2)
        assert "Pour de Vrai" in revue.doublon_de_titre(a, [a, b])[0]

    def test_une_version_n_est_pas_un_doublon(self):
        a, b = _t("Heartless", 1), _t("Heartless (Live)", 2)
        assert revue.doublon_de_titre(a, [a, b]) is None

    def test_autre_interprete_ou_role_secondaire(self):
        # Depuis e36, deux « Heartless » sont légitimes : l'original, et la
        # reprise d'un tiers où l'artiste n'est qu'auteur.
        a = _t("Heartless", 1)
        feat = _t("Heartless", 2, is_featuring=True, primary_artist_name="The Fray")
        second = _t("Heartless", 3, secondary_role="Writer")
        assert revue.doublon_de_titre(a, [a, feat, second]) is None

    def test_meme_page_genius_n_est_pas_un_doublon(self):
        a, b = _t("Boss", 1, genius_id=7), _t("BOSS", 2, genius_id=7)
        assert revue.doublon_de_titre(a, [a, b]) is None


def test_generique_meme_duree():
    a, b = _t("Intro", 1, duration=94), _t("Outro", 2, duration=94)
    c = _t("Matrix (Intro)", 3, duration=94)  # le morceau Matrix, pas une intro
    assert "« Outro »" in revue.generique_meme_duree(a, [a, b, c])[0]
    assert revue.generique_meme_duree(c, [a, b, c]) is None


def test_detecter_trie_par_impact():
    a = _t("Pour de vrai", 1)
    b = _t("Pour de Vrai", 2)
    a.streams.spotify_streams = 10
    b.streams.spotify_streams = 1000
    cas = revue.detecter([a, b], detecteurs=[d for d in revue.DETECTEURS if d.code == "doublon"])
    assert [c.track_id for c in cas] == [2, 1]
    assert revue.par_detecteur(cas) == {"doublon": 2}


def test_fenetre_se_construit(racine_tk):
    """La fenêtre affiche les cas et filtre par détecteur (lecture seule)."""
    from src.gui.windows.a_trancher import ATrancherWindow

    a, b = _t("Pour de vrai", 1), _t("Pour de Vrai", 2)
    artiste = Artist(name="A2H")
    artiste.tracks = [a, b]
    app = SimpleNamespace(root=racine_tk, _show_track_details_for_track=lambda t: None)
    w = ATrancherWindow(app, artiste)
    try:
        assert len(w.cas) == 2
        filtre = next(k for k, v in w.choix.items() if v == "doublon")
        w.filtre.set(filtre)
        w._afficher()
        assert len(w.liste.winfo_children()) == 2
    finally:
        w.destroy()
