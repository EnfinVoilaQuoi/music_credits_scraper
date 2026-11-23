"""Règle 3 (décision utilisateur 2026-11-22) : deux pages Genius, un même
enregistrement, proposées au panneau ♊ (`meme_enregistrement`).

Positifs donnés par l'utilisateur : Kanye « Out The Game » (188 s) / Consequence
« Getting Out the Game » (190 s) ; Booba « DCR (Dolce Camara Remix) » (page
sans durée, crédite Snight B) / « Dolce Camara (Snight B Remix) » (fiche
Deezer sans page, 144 s). Simulé sur une copie de la base : 10 paires.
"""

import json

import pytest
from sqlalchemy import text

from src.models import Artist, Track
from src.utils import meme_enregistrement as me
from src.utils.version_descriptors import titre_contenu_ou_sigle

PAROLES = (
    "je marche seul dans la nuit noire et la ville dort encore sous la pluie fine "
    "les lumières brillent au loin comme des promesses que personne ne tient jamais "
    "mon frère me dit tiens bon la route est longue mais la fin est belle quand on y "
    "croit vraiment alors je continue d'avancer sans regarder derrière moi"
)


def _resume(titre, genius_id, *, artistes=("kanye west",), credits=None, durees=None, **kw):
    r = {
        "id": kw.pop("id", genius_id or 99),
        "title": titre,
        "genius_id": genius_id,
        "artistes": set(artistes),
        "credits": {k: set(v) for k, v in (credits or {}).items()},
        "durees": durees or {},
        "remix_of": set(kw.pop("remix_of", ())),
        "paroles": kw.pop("paroles", None),
        "identifiants": kw.pop("identifiants", {}),
    }
    assert not kw
    return r


class TestTitres:
    @pytest.mark.parametrize(
        "a, b",
        [
            ("Out The Game", "Getting Out the Game"),
            ("JW", "James Worthy"),
            ("DTF", "Down To Fuck"),
        ],
    )
    def test_lies(self, a, b):
        assert titre_contenu_ou_sigle(a, b)

    @pytest.mark.parametrize(
        "a, b",
        [
            ("Intro", "Intro Skit"),  # générique
            ("Game", "The Game"),  # un seul mot n'est pas « contenu »
            ("Out The Game", "Out The Game"),
        ],
    )
    def test_pas_lies(self, a, b):
        assert not titre_contenu_ou_sigle(a, b)


class TestJuge:
    def _out_the_game(self, **kw):
        a = _resume(
            "Out The Game",
            1194473,
            credits={"producer": {"kanye west"}, "other": {"plain pat"}},
            durees={"youtube_video": 189},
            paroles=PAROLES,
        )
        b = _resume(
            "Getting Out the Game",
            14539,
            artistes=("consequence", "kanye west", "john legend"),
            credits={
                "producer": {"kanye west"},
                "featured artist": {"kanye west", "john legend"},
                "writer": {"consequence", "kanye west"},
            },
            durees={"deezer": 190, "apple_music": 191},
            paroles=PAROLES,
            identifiants={"isrc": {"QZES92310382"}},
        )
        for cle, v in kw.items():
            b[cle] = v
        return a, b

    def test_out_the_game(self):
        motif = me.juger_deux_pages(*self._out_the_game())
        assert motif and "durées à 1 s" in motif and "paroles à 100%" in motif

    def test_dcr_page_contre_fiche_sans_page(self):
        page = _resume(
            "DCR (Dolce Camara Remix)",
            10462052,
            artistes=("booba", "sdm", "snight b"),
            credits={"featured artist": {"sdm", "snight b"}, "writer": {"booba", "sdm"}},
            remix_of=("https://genius.com/booba-dolce-camara-lyrics",),
        )
        fiche = _resume(
            "Dolce Camara (Snight B Remix)",
            None,
            id=9710,
            artistes=("booba", "sdm", "snight b"),
            credits={"featured artist": {"sdm", "snight b"}, "remixer": {"snight b"}},
            durees={"deezer": 144},
            remix_of=("https://genius.com/booba-dolce-camara-lyrics",),
        )
        motif = me.juger_deux_pages(page, fiche)
        assert motif and "remixeur crédité" in motif

    def test_deux_mixes_du_meme_remixeur_entre_deux_pages(self):
        """Kanye « I Want You (E-Smoove Remix) » / « (E-Smoove Hard Mix Edit) »."""
        a = _resume(
            "I Want You (E-Smoove Remix)",
            1,
            credits={"remixer": {"e smoove"}},
            remix_of=("u",),
        )
        b = _resume(
            "I Want You (E-Smoove Hard Mix Edit)",
            2,
            credits={"remixer": {"e smoove"}},
            remix_of=("u",),
            durees={"deezer": 300},
        )
        assert me.juger_deux_pages(a, b) is None

    @pytest.mark.parametrize(
        "champ, valeur",
        [
            # Népal « JW » / « James Worthy » : un autre producteur.
            ("credits", {"producer": {"geraldo"}}),
            ("durees", {"deezer": 230}),  # à plus de 2 s
            ("durees", {}),  # rien à comparer, pas de remix
            ("artistes", {"consequence", "jay z"}),  # Kanye absent
            ("paroles", PAROLES[:120] + " toute autre chanson " * 30),
            # *92.2012* : deux ISRC propres, deux enregistrements.
            ("identifiants", {"isrc": {"FR9W11201876"}}),
            ("genius_id", 1194473),  # lignes sœurs
            ("title", "Getting Out the Game (Instrumental)"),
        ],
    )
    def test_pas_propose(self, champ, valeur):
        a, b = self._out_the_game()
        if champ == "identifiants":
            a["identifiants"] = {"isrc": {"FRN370102110"}}
            b["identifiants"] = {"isrc": valeur["isrc"]}
        else:
            b[champ] = valeur
        assert me.juger_deux_pages(a, b) is None

    def test_un_identifiant_commun_est_l_affaire_d_une_autre_branche(self):
        a, b = self._out_the_game()
        a["identifiants"] = {"video": {"zhX5bStY0-M"}}
        b["identifiants"] = {"video": {"zhX5bStY0-M"}}
        assert me.juger_deux_pages(a, b) is None

    def test_une_autre_partie(self):
        a = _resume("Keep My Spirit Alive", 1, durees={"deezer": 200})
        b = _resume("Keep My Spirit Alive pt 2", 2, durees={"deezer": 200})
        assert me.juger_deux_pages(a, b) is None


def test_au_panneau_par_fusions_a_decider(data_manager):
    kanye = Artist(name="Kanye West")
    kanye.id = data_manager.save_artist(kanye)
    fiches = []
    for titre, gid in (("Out The Game", 1194473), ("Getting Out the Game", 14539)):
        t = Track(title=titre, artist=kanye)
        t.genius_id = gid
        data_manager.save_track(t)
        fiches.append(t)
    with data_manager.engine.begin() as conn:
        for t, source, duree in ((fiches[0], "youtube_video", 189), (fiches[1], "deezer", 190)):
            conn.execute(
                text(
                    "INSERT INTO observations (track_id, field, value, source) "
                    "VALUES (:t, 'duration', :v, :s)"
                ),
                {"t": t.id, "v": str(duree), "s": source},
            )
        conn.execute(
            text("UPDATE tracks SET relationships = :r WHERE id = :t"),
            {"r": json.dumps([]), "t": fiches[1].id},
        )
    paires = data_manager.fusions_a_decider([t.id for t in fiches])
    assert paires[fiches[0].id]["autre"] == fiches[1].id
    assert "titres liés" in paires[fiches[0].id]["deux_pages"]
