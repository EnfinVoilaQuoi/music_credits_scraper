"""« Vérifier les identifiants » remplace « Réinitialiser les Spotify IDs ».

La case effaçait **tous** les identifiants de l'artiste pour les re-scraper —
1 308 en base, dont la grande majorité est juste. Elle emportait les bons avec
les mauvais, et le re-scrape reproposait les fautifs depuis le cache : un scrape
complet pour revenir au même état. Le besoin derrière était réel, la réponse est
celle de `repair_spotify_ids.py` — **n'effacer que ce qu'on peut MONTRER**.

Deux choses sont vérifiées ici, et une seule est du GUI :

  · le BALAYAGE (`spotify_audit.verifier_lignes`), qui vit désormais à UN endroit
    partagé par le CLI et la fenêtre — deux copies d'un verdict divergent, c'est
    le défaut du 2026-09-06 ;
  · le RETRAIT de la case, par crible AST — le dialogue ne se monte pas sans Tk,
    mais l'invariant se lit dans le source (même méthode que
    `test_sources_enrichissement.py`).

⚠️ Aucun test ne parle à Spotify : l'oracle est INJECTÉ.
"""

import ast
from pathlib import Path

import pytest

from src.utils.spotify_audit import verifier_lignes

FICHIER = Path(__file__).resolve().parents[1] / "src" / "gui" / "workers" / "enrichment.py"
SOURCE = FICHIER.read_text(encoding="utf-8")


def _ligne(track_id=1, titre="Mouton noir", artiste="Swing", duree=180, sid="idSpotify22caracte"):
    return {
        "id": track_id,
        "title": titre,
        "artiste": artiste,
        "duration": duree,
        "is_featuring": 0,
        "primary_artist_name": None,
        "sid": sid,
        "principal": 1,
    }


def _embed(nom="Mouton noir", artistes=("Swing",), duree=180):
    return {"name": nom, "artists": list(artistes), "duration": duree}


class TestLeBalayagePartage:
    """Le CLI `audit_spotify_ids.py` et la fenêtre GUI appellent CETTE fonction.
    Si elles balayaient chacune de leur côté, le rapport et la réparation
    finiraient par ne plus parler des mêmes lignes."""

    def test_un_identifiant_juste_ne_sort_pas_du_balayage(self):
        rapport = verifier_lignes([_ligne()], lambda sid: _embed(), pause=0)
        assert rapport == {"verifies": 1, "illisibles": 0, "ecarts": []}

    def test_un_artiste_etranger_est_marque_comme_tel(self):
        rapport = verifier_lignes(
            [_ligne()],
            lambda sid: _embed("Dessine-moi un mouton", ("Mylène Farmer",), 240),
            pause=0,
        )
        (ecart,) = rapport["ecarts"]
        assert ecart["artiste_etranger"] is True
        assert ecart["spotify_id"] == "idSpotify22caracte"
        assert ecart["motif"]

    def test_une_variante_etrangere_est_marquee_comme_telle(self):
        """« Heartless (Remix) » qui porte l'ID de « Heartless » : bon artiste,
        même durée — seul le descripteur de version le trahit."""
        rapport = verifier_lignes(
            [_ligne(titre="Heartless (Remix)", artiste="Kanye West", duree=211)],
            lambda sid: _embed("Heartless", ("Kanye West",), 211),
            pause=0,
        )
        (ecart,) = rapport["ecarts"]
        assert ecart["variante_etrangere"] is True
        assert ecart["artiste_etranger"] is False
        assert ecart["motif"].startswith("variante")

    def test_une_page_illisible_n_accuse_personne(self):
        """Même règle qu'`absent` côté observabilité : ne pas savoir lire n'est
        pas un verdict de faute. Elle est comptée à part, jamais en écart."""
        rapport = verifier_lignes([_ligne()], lambda sid: None, pause=0)
        assert rapport == {"verifies": 0, "illisibles": 1, "ecarts": []}

    def test_l_avancement_est_rapporte_ligne_par_ligne(self):
        """La fenêtre a besoin d'une barre : sans ce rappel, un balayage de 330
        identifiants resterait muet pendant plusieurs minutes."""
        vus = []
        verifier_lignes(
            [_ligne(1), _ligne(2), _ligne(3)],
            lambda sid: _embed(),
            pause=0,
            progression=lambda faits, total: vus.append((faits, total)),
        )
        assert vus == [(1, 3), (2, 3), (3, 3)]

    def test_l_arret_est_teste_ENTRE_deux_requetes(self):
        """Règle de concurrence du projet : jamais au milieu d'une unité de
        travail. Ici l'unité est un aller-retour réseau."""
        appels = []

        def oracle(sid):
            appels.append(sid)
            return _embed()

        rapport = verifier_lignes(
            [_ligne(1), _ligne(2), _ligne(3)],
            oracle,
            pause=0,
            interrompu=lambda: len(appels) >= 2,
        )
        assert len(appels) == 2
        assert rapport["verifies"] == 2

    def test_aucun_test_ne_parle_a_Spotify(self):
        """L'oracle est injecté ; l'appeler sans double doit rester un choix
        explicite, pas un effet de bord d'un test distrait."""
        temoin = []
        verifier_lignes([_ligne()], lambda sid: temoin.append(sid) or _embed(), pause=0)
        assert temoin == ["idSpotify22caracte"]


class TestLaCaseDangereuseADisparu:
    def test_plus_aucune_reinitialisation_globale(self):
        """Le drapeau est retiré de BOUT EN BOUT — la case, le paramètre, la
        boucle qui vidait `track.spotify_id`, et la ligne du bilan. En laisser un
        bout ferait croire à un réglage qui ne fait plus rien."""
        assert "reset_spotify" not in SOURCE

    def test_le_bouton_de_verification_le_remplace(self):
        assert "show_verification_spotify" in SOURCE

    def test_le_bouton_ouvre_bien_la_fenetre_qui_existe(self):
        """L'invariant que le crible ne verrait pas : un import qui ne résout
        pas ne casse qu'au clic, c'est-à-dire chez l'utilisateur."""
        from src.gui.windows.verification_spotify import (  # noqa: F401
            show_verification_spotify,
        )

    def test_la_fenetre_n_ecrit_QUE_sur_validation(self):
        """Le point qui distingue cette fenêtre de la case qu'elle remplace :
        aucun appel de retrait hors du gestionnaire du bouton. Vérifié par AST —
        un commentaire ne prouverait rien."""
        fenetre = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "gui"
            / "windows"
            / "verification_spotify.py"
        )
        arbre = ast.parse(fenetre.read_text(encoding="utf-8"))
        fonctions = {
            n.name
            for n in ast.walk(arbre)
            if isinstance(n, ast.FunctionDef)
            and any(
                isinstance(a, ast.Call)
                and isinstance(a.func, ast.Name)
                and a.func.id == "rejeter_spotify_id"
                for a in ast.walk(n)
            )
        }
        assert fonctions == {"_retirer"}


@pytest.mark.parametrize("champ", ["verifies", "illisibles", "ecarts"])
def test_le_rapport_a_toujours_ses_trois_compteurs(champ):
    """La fenêtre et le CLI affichent les trois : un rapport amputé afficherait
    « 0 illisible » au lieu de planter, et un trou de mesure se dirait vérifié."""
    assert champ in verifier_lignes([], lambda sid: None, pause=0)
