"""Morceaux qui en CONTIENNENT d'autres (`utils.contenants`, 2026-10-29).

Deux volets d'un même modèle — un contenant, des contenus, jamais de somme :
la relation `contient` d'un medley (« Compte à rebours + Vieux avant l'âge »
de Flynt sur *Rétrographie*, fiche Deezer sans page Genius) et la vidéo d'un
disque entier (« Di-Meh - Reste Calme (FULL EP) », posée par Genius sur la
fiche de Népal). Les titres sont ceux de la base (copie du 2026-10-29).
"""

import importlib.util

import pytest

from src.models import Artist, Track
from src.models.track import TrackVideo
from src.services import liens_contenus
from src.services.runtime import Manque, est_manquant
from src.utils import contenants
from src.utils import corrections_fiches as cf
from src.utils.track_soeurs import ORIGINE_MANUELLE, avec_relations_manuelles


def _fiche(tid, titre, album=None, genius_id=None, **kw):
    t = Track(title=titre, artist=Artist(name=kw.pop("artiste", "Flynt")), album=album)
    t.id, t.genius_id = tid, genius_id
    t.genius_url = f"https://genius.com/x-{genius_id}" if genius_id else None
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _medley(tid=9721, titre="Compte à rebours + Vieux avant l'âge"):
    return _fiche(tid, titre, "Rétrographie (Mixtape)")


def _flynt():
    return [
        _medley(),
        _fiche(1257, "Compte à rebours", "1 pour la plume", genius_id=11),
        _fiche(1223, "Vieux avant l’âge", "Scred Selexion 2", genius_id=12),
        _fiche(9716, "Vers le bas + Freestyle", "Rétrographie (Mixtape)"),
        _fiche(1300, "Vers le bas", "Itinéraire bis", genius_id=13),
    ]


# ── Découpage du titre ───────────────────────────────────────────────────────


class TestPartiesDuTitre:
    def test_trois_titres_joints(self):
        assert contenants.parties_du_titre("En froid + Relativise + Prouesses") == [
            "En froid",
            "Relativise",
            "Prouesses",
        ]

    def test_barre_oblique(self):
        assert contenants.parties_du_titre("Bad Night / Rap Tarantino") == [
            "Bad Night",
            "Rap Tarantino",
        ]

    def test_sans_espaces_ce_n_est_pas_une_jointure(self):
        assert contenants.parties_du_titre("24/7") == []
        assert contenants.parties_du_titre("C+C") == []

    def test_barre_oblique_sans_espaces_hors_chiffres(self):
        """Kanye « Chakras/We Free », Médine « Le Mal Qu'on a Fait/… » ; une
        date ou une fraction n'est pas une jointure."""
        assert contenants.parties_du_titre("Chakras/We Free") == ["Chakras", "We Free"]
        assert contenants.parties_du_titre("Houston We Got A Problem/ Me Too") == [
            "Houston We Got A Problem",
            "Me Too",
        ]
        assert contenants.parties_du_titre("La Récolte/Fils de Colonisé/Internationale") == [
            "La Récolte",
            "Fils de Colonisé",
            "Internationale",
        ]
        for titre in ("11/09", "68 3/4", "Freestyle 02/02/21", "7/7"):
            assert contenants.parties_du_titre(titre) == [], titre

    def test_dans_une_parenthese_c_est_un_seul_morceau(self):
        """Kanye « LAST BREATH (First Pressing / Single Version) »."""
        assert contenants.parties_du_titre("LAST BREATH (First Pressing / Single Version)") == []
        assert contenants.parties_du_titre("Wolves (SNL / Yeezy Season 1)") == []


# ── Règle des titres joints ──────────────────────────────────────────────────


class TestProposerContenus:
    def test_chaque_partie_a_sa_fiche(self):
        fiches = _flynt()
        proposes = contenants.proposer_contenus(fiches[0], fiches)
        assert [f.id for f in proposes] == [1257, 1223]

    def test_une_partie_sans_fiche_et_rien_n_est_propose(self):
        """« Vers le bas + Freestyle » : « Freestyle » n'a pas de fiche."""
        fiches = _flynt()
        assert contenants.proposer_contenus(fiches[3], fiches) is None

    def test_deux_pages_au_meme_titre_ne_departagent_pas(self):
        """« Numb / Encore » de *Collision Course* : deux pages « Numb »."""
        fiches = [
            _fiche(10935, "Numb / Encore", "Collision Course", artiste="Kanye West"),
            _fiche(1, "Numb", "Meteora", genius_id=1),
            _fiche(2, "Numb", "Collision Course", genius_id=2),
            _fiche(3, "Encore", "The Black Album", genius_id=3),
        ]
        assert contenants.proposer_contenus(fiches[0], fiches) is None

    def test_les_lignes_soeurs_d_une_page_ne_font_qu_une_fiche(self):
        fiches = _flynt() + [_fiche(5000, "Compte à rebours", "1 pour la plume", genius_id=11)]
        assert [f.id for f in contenants.proposer_contenus(fiches[0], fiches)] == [1257, 1223]

    def test_une_partie_generique_exige_le_meme_disque(self):
        """Diam's « Intro + Incassables » : l'« Intro » d'un autre disque n'y est pas."""
        fiches = [
            _fiche(9767, "Intro + Incassables", "ma vie / mon live", artiste="Diam’s"),
            _fiche(1, "Intro", "Brut de femme", genius_id=1),
            _fiche(2, "Incassables", "Dans ma bulle", genius_id=2),
        ]
        assert contenants.proposer_contenus(fiches[0], fiches) is None
        fiches[1].album = "ma vie / mon live"
        assert [f.id for f in contenants.proposer_contenus(fiches[0], fiches)] == [1, 2]

    def test_une_autre_version_n_est_pas_le_morceau(self):
        """« J’éclaire ma ville (Live au New Morning) » n'est pas la fiche studio."""
        fiches = [
            _fiche(9724, "Itinéraire bis + J’éclaire ma ville (Live au New Morning)", "R"),
            _fiche(1, "Itinéraire bis", "Itinéraire bis", genius_id=1),
            _fiche(2, "J’éclaire ma ville", "J’éclaire ma ville", genius_id=2),
        ]
        assert contenants.proposer_contenus(fiches[0], fiches) is None

    def test_propositions_ignore_les_liens_deja_poses(self):
        fiches = _flynt()
        medley = fiches[0]
        medley.relationships = [contenants.relation_vers(fiches[1], "Flynt", ORIGINE_MANUELLE)]
        ((t, manquants),) = liens_contenus.propositions(fiches)
        assert t is medley and [c.id for c in manquants] == [1223]


# ── La relation, ses effets ──────────────────────────────────────────────────


class TestRelation:
    def test_forme_stockee_et_lecture(self):
        fiches = _flynt()
        relations, ajoutes = liens_contenus.relations_avec(
            fiches[0], fiches[1:3], "Flynt", ORIGINE_MANUELLE
        )
        assert [c.id for c in ajoutes] == [1257, 1223]
        assert relations[0] == {
            "type": "contient",
            "title": "Compte à rebours",
            "artist": "Flynt",
            "url": "https://genius.com/x-11",
            "track_id": 1257,
            "origine": "manuel",
        }
        fiches[0].relationships = relations
        assert contenants.ids_contenus(fiches[0]) == [1257, 1223]
        assert contenants.contenant_sans_page(fiches[0])
        index = contenants.contenants_par_contenu(fiches)
        assert index[1223] == [fiches[0]] and 9716 not in index

    def test_un_lien_deja_pose_n_est_pas_double(self):
        fiches = _flynt()
        fiches[0].relationships, _ = liens_contenus.relations_avec(
            fiches[0], fiches[1:2], "Flynt", ORIGINE_MANUELLE
        )
        relations, ajoutes = liens_contenus.relations_avec(
            fiches[0], fiches[1:3], "Flynt", ORIGINE_MANUELLE
        )
        assert [c.id for c in ajoutes] == [1223] and len(relations) == 2

    def test_la_relation_de_regle_survit_a_la_page_relue_d_une_soeur(self):
        """Une page relue chez une sœur ne redonne pas la relation `contient`."""
        regle = contenants.relation_vers(_flynt()[1], "Flynt", contenants.ORIGINE_REGLE)
        page = '[{"type": "samples", "title": "X"}]'
        fusion = avec_relations_manuelles(page, [f"[{__import__('json').dumps(regle)}]"])
        assert '"contient"' in fusion

    def test_un_contenant_sans_page_n_est_pas_reclame_a_genius(self):
        fiches = _flynt()
        medley = fiches[0]
        assert est_manquant(medley, Manque.PAROLES)
        medley.relationships = [contenants.relation_vers(fiches[1], "Flynt")]
        assert not est_manquant(medley, Manque.CREDITS_GENIUS)
        assert not est_manquant(medley, Manque.PAROLES)
        assert est_manquant(medley, Manque.CREDITS_DISCOGS)

    def test_un_contenant_avec_page_reste_reclame(self):
        """Kanye « Bad Night / Rap Tarantino » a sa page Genius : elle se lit."""
        t = _fiche(7207, "Bad Night / Rap Tarantino", genius_id=99, artiste="Kanye West")
        t.relationships = [contenants.relation_vers(_fiche(1, "Bad Night", genius_id=1), "Ye")]
        assert est_manquant(t, Manque.CREDITS_GENIUS)

    def test_la_validation_n_exige_ni_credits_ni_paroles(self):
        from src.utils.track_validation import Manque as M
        from src.utils.track_validation import evaluer

        fiches = _flynt()
        medley = fiches[0]
        assert {M.CREDITS, M.PAROLES} <= set(evaluer(medley).manques)
        medley.relationships = [contenants.relation_vers(fiches[1], "Flynt")]
        constat = evaluer(medley)
        assert not {M.CREDITS, M.PAROLES} & set(constat.manques)
        assert any("medley" in d for d in constat.details)


class TestEtapeCredits:
    @pytest.fixture(autouse=True)
    def _aucune_absence(self, monkeypatch):
        monkeypatch.setattr("src.services.runtime.absences_recentes", lambda rt, s: {})

    def test_genius_ne_lit_pas_un_contenant_sans_page(self):
        from src.services import credits
        from src.services.runtime import Hooks
        from tests.test_services_credits import _clients, _rt

        fiches = _flynt()
        medley, solo = fiches[0], fiches[1]
        medley.relationships = [contenants.relation_vers(solo, "Flynt")]
        lus = []

        class _Genius:
            def scrape_multiple_tracks(self, tracks, progress_callback=None):
                lus.extend(t.title for t in tracks)
                return {"success": len(tracks), "failed": 0, "errors": []}

            def scrape_lyrics_batch(self, tracks, progress_callback=None):
                lus.extend(f"paroles:{t.title}" for t in tracks)
                return {"success": len(tracks), "failed": 0, "errors": []}

            def close(self):
                pass

        cl, _, _ = _clients(genius=_Genius())
        opts = credits.OptionsCredits(
            discogs=False, youtube=False, paroles_ytm=False, sync_lrclib=False, sync_ytm=False
        )
        credits.run(_rt(), Artist(name="Flynt"), [medley, solo], opts, Hooks(), cl)
        assert lus == ["Compte à rebours", "paroles:Compte à rebours"]


class TestSansPageGenius:
    """Une fiche SANS adresse Genius (venue de Deezer : « Rétroduction » de
    Flynt) ne part jamais au crawl Genius : ni échec, ni manque à retenter."""

    def test_pas_de_credits_genius_a_reclamer_sans_page(self):
        t = _fiche(9714, "Rétroduction", "Rétrographie (Mixtape)")
        assert not est_manquant(t, Manque.CREDITS_GENIUS)
        assert est_manquant(t, Manque.CREDITS_DISCOGS)

    def test_une_page_gagnee_plus_tard_la_rend_manquante(self):
        t = _fiche(9714, "Rétroduction", "Rétrographie (Mixtape)")
        t.genius_url = "https://genius.com/Flynt-retroduction-lyrics"
        assert est_manquant(t, Manque.CREDITS_GENIUS)

    def test_le_crawl_ne_recoit_que_les_fiches_avec_page(self):
        from src.services import credits
        from src.services.runtime import Hooks
        from tests.test_services_credits import _clients, _rt

        sans = _fiche(9714, "Rétroduction", "Rétrographie (Mixtape)")
        avec = _fiche(1257, "Compte à rebours", "1 pour la plume", genius_id=11)
        recus = []

        class _Genius:
            def scrape_multiple_tracks(self, tracks, progress_callback=None):
                recus.extend(t.title for t in tracks)
                return {"success": len(tracks), "failed": 0, "errors": []}

            def scrape_lyrics_batch(self, tracks, progress_callback=None):
                recus.extend(f"paroles:{t.title}" for t in tracks)
                return {"success": len(tracks), "failed": 0, "errors": []}

            def close(self):
                pass

        cl, _, _ = _clients(genius=_Genius())
        opts = credits.OptionsCredits(
            discogs=False, youtube=False, paroles_ytm=False, sync_lrclib=False, sync_ytm=False
        )
        bilan = credits.run(_rt(), Artist(name="Flynt"), [sans, avec], opts, Hooks(), cl)
        assert recus == ["Compte à rebours", "paroles:Compte à rebours"]
        assert bilan.genius["failed"] == 0

    def test_avec_page_genius(self):
        from src.services.credits import avec_page_genius

        sans = _fiche(9714, "Rétroduction")
        avec = _fiche(1257, "Compte à rebours", genius_id=11)
        assert avec_page_genius([sans, avec], "Genius") == [avec]


# ── Présentation : le lien dans les deux sens ────────────────────────────────


class TestPresentation:
    def test_contient_et_contenu_dans(self):
        from src.presentation.fiche import onglet_samples
        from src.presentation.table import construire_lignes
        from src.utils.track_validation import Contexte

        fiches = _flynt()
        fiches[0].relationships = [
            contenants.relation_vers(fiches[1], "Flynt"),
            contenants.relation_vers(fiches[2], "Flynt"),
        ]
        sections = {s.titre: s for s in onglet_samples(fiches[0]).sections}
        assert [e.libelle for e in sections["Contient"].entrees] == [
            "Flynt - Compte à rebours",
            "Flynt - Vieux avant l’âge",
        ]
        lignes = {ligne.id: ligne for ligne in construire_lignes(fiches, Contexte())}
        assert lignes[1257].contenu_dans == (fiches[0],)
        assert lignes[1300].contenu_dans == ()
        onglet = onglet_samples(fiches[1], lignes[1257].contenu_dans)
        (section,) = [s for s in onglet.sections if s.titre == "Contenu dans"]
        assert [e.libelle for e in section.entrees] == [
            "Flynt - Compte à rebours + Vieux avant l'âge"
        ]


# ── Pose en base : décision consignée, relation écrite ───────────────────────


def _script():
    spec = importlib.util.spec_from_file_location(
        "appliquer_corrections_fiches", "scripts/appliquer_corrections_fiches.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def flynt_en_base(data_manager):
    a = Artist(name="Flynt")
    a.id = data_manager.save_artist(a)
    ids = {}
    for titre, album, gid in (
        ("Compte à rebours + Vieux avant l'âge", "Rétrographie (Mixtape)", None),
        ("Compte à rebours", "1 pour la plume", 11),
        ("Vieux avant l’âge", "Scred Selexion 2", 12),
    ):
        t = Track(title=titre, artist=a, album=album)
        t.genius_id = gid
        t.genius_url = f"https://genius.com/x-{gid}" if gid else None
        ids[titre] = data_manager.save_track(t)
    return a, ids


def _par_id(dm, artist):
    return {t.id: t for t in dm.get_artist_tracks(artist.id)}


class TestPoseEnBase:
    def test_lier_ecrit_la_relation(self, data_manager, flynt_en_base):
        a, ids = flynt_en_base
        fiches = _par_id(data_manager, a)
        medley = fiches[ids["Compte à rebours + Vieux avant l'âge"]]
        contenus = [fiches[ids["Compte à rebours"]], fiches[ids["Vieux avant l’âge"]]]
        a_blanc = liens_contenus.lier(
            data_manager, medley, contenus, "Flynt", "manuel", apply=False
        )
        assert len(a_blanc) == 2
        assert not contenants.est_contenant(_par_id(data_manager, a)[medley.id])
        liens_contenus.lier(data_manager, medley, contenus, "Flynt", "manuel", apply=True)
        relu = _par_id(data_manager, a)[medley.id]
        assert contenants.ids_contenus(relu) == [c.id for c in contenus]
        assert contenants.contenant_sans_page(relu)

    def test_la_regle_de_l_etape_discographie_pose_une_fois(self, data_manager, flynt_en_base):
        """`lier_par_la_regle` (fin de l'étape discographie) pose la relation
        d'origine « regle:titres_joints » ; relancée, elle ne fait rien."""
        a, ids = flynt_en_base
        fiches = list(_par_id(data_manager, a).values())
        faits = liens_contenus.lier_par_la_regle(data_manager, "Flynt", fiches)
        assert faits == [
            "Compte à rebours + Vieux avant l'âge ⊃ Compte à rebours + Vieux avant l’âge"
        ]
        relu = _par_id(data_manager, a)
        medley = relu[ids["Compte à rebours + Vieux avant l'âge"]]
        assert contenants.ids_contenus(medley) == [
            ids["Compte à rebours"],
            ids["Vieux avant l’âge"],
        ]
        assert {r.get("origine") for r in contenants.relations_contient(medley)} == {
            contenants.ORIGINE_REGLE
        }
        assert liens_contenus.lier_par_la_regle(data_manager, "Flynt", list(relu.values())) == []

    def test_la_decision_consignee_est_une_union(self, data_manager):
        medley = {"titre": "Crazy + 6G", "album": "Rétrographie (Mixtape)"}
        assert cf.memoriser_contenus("Flynt", medley, [{"genius_id": 1}]) == [{"genius_id": 1}]
        assert cf.memoriser_contenus("Flynt", medley, [{"genius_id": 2}, {"genius_id": 1}]) == [
            {"genius_id": 1},
            {"genius_id": 2},
        ]
        (entree,) = cf.charger()["Flynt"]
        assert entree["contient"] == [{"genius_id": 1}, {"genius_id": 2}]

    def test_la_decision_s_exporte_avec_les_autres(self, data_manager):
        from src.services import decisions

        cf.memoriser_contenus(
            "Flynt", {"titre": "Crazy + 6G", "album": "R"}, [{"genius_id": 1}, {"genius_id": 2}]
        )
        exportees = decisions.lire_decisions(data_manager.engine)["corrections"]
        (ligne,) = [e for e in exportees if e["action"] == "contient"]
        assert ligne["valeur"] == [{"genius_id": 1}, {"genius_id": 2}]

    def test_corrections_appliquees_et_contenu_absent_en_attente(self, data_manager, flynt_en_base):
        """Le script des corrections lie ce qui existe ; la fiche contenue
        absente (« 6G », page jamais importée) reste désignée, jamais créée."""
        a, ids = flynt_en_base
        mod = _script()
        ctx = mod._Contexte(data_manager, apply=True)
        entree = {
            "fiche": {
                "titre": "Compte à rebours + Vieux avant l'âge",
                "album": "Rétrographie (Mixtape)",
            },
            "contient": [{"genius_id": 11}, {"genius_id": 12}, {"genius_id": 404}],
        }
        journal = []
        mod.appliquer(ctx, "Flynt", entree, None, journal)
        assert any("en attente" in ligne for ligne in journal)
        relu = _par_id(data_manager, a)
        medley = relu[ids["Compte à rebours + Vieux avant l'âge"]]
        assert contenants.ids_contenus(medley) == [
            ids["Compte à rebours"],
            ids["Vieux avant l’âge"],
        ]
        assert all(r["origine"] == "manuel" for r in medley.relationships)
        assert len(relu) == 3
        # Idempotent : relancé, rien de neuf.
        journal = []
        mod.appliquer(mod._Contexte(data_manager, apply=True), "Flynt", entree, None, journal)
        assert not any("contient →" in ligne for ligne in journal)


# ── Vidéo d'un disque entier ─────────────────────────────────────────────────


class TestVideoDeDisque:
    @pytest.mark.parametrize(
        "titre,album",
        [
            ("Di-Meh - Reste Calme (FULL EP)", "Reste Calme"),
            ("Paris - Genève (FULL EP)", "Paris - Genève"),
            ("Enter The Dojo Vol.1 (FULL BEAT-TAPE)", "Enter The Dojo Vol. 1"),
            (
                "BEN plg - Paraît que les miracles n’existent pas (Album entier)",
                "Paraît que les miracles n’existent pas",
            ),
            ("2Fingz - La Folie Des Glandeurs (FULL MIXTAPE)", "La folie des glandeurs"),
            ("Kanye West - Can't Tell Me Nothing (Full Mixtape)", "Can’t Tell Me Nothing"),
            ("The Pablo Collective - The Death of Pablo [FULL ALBUM]", "The Death Of Pablo"),
            (
                "G. Twilight - Another Detroit Gangster Story (Full Mixtape) [2013]",
                "Another Detroit Gangster Story (mixtape)",
            ),
            ("Flynt - Rétrographie (mixtape complète)", "Rétrographie (Mixtape)"),
        ],
    )
    def test_reconnue(self, titre, album):
        assert contenants.video_de_disque(titre, album)

    @pytest.mark.parametrize(
        "titre,album",
        [
            # Une piste d'un album mis en ligne en morceaux : le disque n'est pas nommé.
            (
                "Pharrell & The Yessirs - 15: Number One (ft Kanye West) .. FULL ALBUM",
                "Out Of My Mind",
            ),
            ("Travis Scott - Trill Mode [FULL VERSION] [LEAK]", None),
            (
                "TRAVIS SCOTT - ASTROWORLD FESTIVAL HOUSTON TEXAS - 2021 [FULL SET]",
                "Astroworld Festival 2021",
            ),
            ("Kanye West - DJ Khaled's Son [FULL SONG LEAKED!]", "DJ Khaled’s Son"),
            ("Thappad | Full Video | Raftaar | WTF Mixtape | Vol 1", "WTF MixTape"),
            # Le disque d'une AUTRE fiche.
            ("Di-Meh - Reste Calme (FULL EP)", "Paris - Genève"),
        ],
    )
    def test_pas_un_disque(self, titre, album):
        assert not contenants.video_de_disque(titre, album)

    def test_jamais_sa_duree(self):
        from src.utils.duree_youtube import duree_a_declarer

        videos = [{"duration": 1260, "title": "Di-Meh - Reste Calme (FULL EP)", "channel": "75e"}]
        assert duree_a_declarer(videos, hors_plateformes=True, titre_fiche="Reste Calme") is None

    def test_ses_vues_ne_comptent_pas_a_la_fiche(self):
        """La piste titre « Reste Calme » : la vidéo la NOMME, et c'est l'EP."""
        from src.services.vues_comptees import vue_comptee

        fiche = _fiche(1, "Reste Calme", "Reste Calme", artiste="Di-Meh")
        assert not vue_comptee(
            fiche, "L5Q9nBL7TDA", "Di-Meh - Reste Calme (FULL EP)", "genius_media", set()
        )
        assert not vue_comptee(
            fiche, "L5Q9nBL7TDA", "Di-Meh - Reste Calme (FULL EP)", "manual", set()
        )
        assert vue_comptee(
            fiche, "x", "Di-Meh - Reste Calme (Clip officiel)", "genius_media", set()
        )

    def test_le_panneau_ne_la_soumet_pas(self):
        from src.services.revue import video_etrangere

        fiche = _fiche(8480, "Le taff est fait", "Reste Calme", artiste="Népal")
        fiche.videos = [
            TrackVideo(
                video_id="L5Q9nBL7TDA",
                title="Di-Meh - Reste Calme (FULL EP)",
                source="genius_media",
                views=134962,
            )
        ]
        assert video_etrangere(fiche) is None


class TestTotalDAlbum:
    def _avec(self, fiche, video_id, titre, vues):
        fiche.videos = [
            TrackVideo(video_id=video_id, title=titre, source="genius_media", views=vues)
        ]
        return fiche

    def test_portee_par_une_seule_fiche_de_l_artiste(self):
        from src.utils.streams_calculator import clips_doubles
        from src.utils.title_matching import cle_album

        f = self._avec(
            _fiche(6621, "Stronger (Snippet)", "Can’t Tell Me Nothing", artiste="Kanye West"),
            "7UZXxSbv8JQ",
            "Kanye West - Can't Tell Me Nothing (Full Mixtape)",
            77193,
        )
        assert clips_doubles([f]) == {cle_album("Can’t Tell Me Nothing"): 77193}

    def test_pas_sur_la_seule_fiche_d_un_invite(self):
        """Népal, invité sur un titre de l'EP de Di-Meh : l'EP n'est pas à lui."""
        from src.utils.streams_calculator import clips_doubles

        f = self._avec(
            _fiche(8480, "Le taff est fait", "Reste Calme", artiste="Népal"),
            "L5Q9nBL7TDA",
            "Di-Meh - Reste Calme (FULL EP)",
            134962,
        )
        f.is_featuring, f.primary_artist_name = True, "Di-Meh"
        assert clips_doubles([f]) == {}

    def test_portee_par_tout_le_disque_comptee_une_fois(self):
        from src.utils.streams_calculator import clips_doubles
        from src.utils.title_matching import cle_album

        titre = "Enter The Dojo Vol.1 (FULL BEAT-TAPE)"
        fiches = [
            self._avec(
                _fiche(i, t, "Enter The Dojo Vol. 1", genius_id=i, artiste="Népal"),
                "uoD_BLVaB7I",
                titre,
                104703,
            )
            for i, t in ((8448, "64 Bullets (Instrumental)"), (8451, "Alchemist (Instrumental)"))
        ]
        assert clips_doubles(fiches) == {cle_album("Enter The Dojo Vol. 1"): 104703}
