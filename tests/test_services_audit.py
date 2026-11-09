"""Chantier B — l'audit des données comme définition de « fini ».

Les invariants de persistance ont une cible ZÉRO : chacun est montré ici sur
une base saine (rien), puis sur la base qu'on abîme exprès (le défaut se voit).
"""

import json

import pytest
from sqlalchemy import text

from src.models import Artist, Track
from src.services import audit as au


@pytest.fixture
def isha(data_manager):
    a = Artist(name="Isha")
    a.id = data_manager.save_artist(a)
    t = Track(title="Durag", artist=a)
    t.genius_id = 4242
    data_manager.save_track(t)
    return a, t


def _sql(dm, requete, **params):
    with dm.engine.begin() as conn:
        conn.execute(text(requete), params)


def _familles(audit):
    return audit.compte()


class TestInvariants:
    def test_base_saine_aucun_invariant(self, data_manager, isha):
        audit = au.auditer(data_manager)
        assert not any(_familles(audit).get(f) for f in au.INVARIANTS)

    def test_colonne_qui_ne_suit_pas_ses_observations(self, data_manager, isha):
        """La colonne `duration` dit 999 s, ses observations disent 200 s."""
        _, t = isha
        from src.enrichment.observation import Observation

        data_manager.record_discography_observations(t.id, [Observation("duration", 200, "deezer")])
        _sql(data_manager, "UPDATE tracks SET duration = 999 WHERE id = :t", t=t.id)

        audit = au.auditer(data_manager, ["Isha"])
        (cas,) = [c for c in audit.cas if c.famille == "colonne_verdict"]
        assert cas.pistes == [t.id] and "duration 999 → 200" in cas.libelle

    def test_soeurs_divergentes(self, data_manager, isha):
        _, t = isha
        swing = Artist(name="Swing")
        swing.id = data_manager.save_artist(swing)
        soeur = Track(title="Durag", artist=swing)
        soeur.genius_id = 4242
        data_manager.save_track(soeur)
        _sql(data_manager, "UPDATE tracks SET lyrics_synced = '[00:01.00] a' WHERE id = :t", t=t.id)
        _sql(
            data_manager,
            "UPDATE tracks SET lyrics_synced = '[00:01.00] b' WHERE id = :t",
            t=soeur.id,
        )

        audit = au.auditer(data_manager, ["Isha"])
        cas = [c for c in audit.cas if c.famille == "soeurs_divergentes"]
        assert len(cas) == 1 and "lyrics_synced : 2 valeurs sur 2 lignes" in cas[0].libelle

    def test_soeurs_aux_editions_divergentes(self, data_manager, isha):
        """« Make Her Say » notée en édition chez Kid Cudi, pas chez Kanye
        (2026-10-29) : l'édition décrit l'enregistrement, l'invariant la voit."""
        _, t = isha
        swing = Artist(name="Swing")
        swing.id = data_manager.save_artist(swing)
        soeur = Track(title="Durag", artist=swing)
        soeur.genius_id = 4242
        data_manager.save_track(soeur)
        # En contournant l'écrivain (qui unit les éditions des sœurs).
        _sql(
            data_manager,
            "INSERT INTO track_editions (track_id, label, duration) VALUES (:t, 'Radio Edit', 200)",
            t=t.id,
        )
        audit = au.auditer(data_manager, ["Isha"])
        cas = [c for c in audit.cas if c.famille == "soeurs_divergentes"]
        assert len(cas) == 1 and "éditions : 2 jeux sur 2 lignes" in cas[0].libelle

        data_manager.realigner_soeurs(appliquer=True)
        audit = au.auditer(data_manager, ["Isha"])
        assert not [c for c in audit.cas if c.famille == "soeurs_divergentes"]

    def test_un_horodatage_different_n_est_pas_une_divergence(self, data_manager, isha):
        assert au._volatile("lyrics_scraped_at") and au._volatile("youtube_video_views")
        assert not au._volatile("lyrics_synced")

    def test_refus_vivant(self, data_manager, isha):
        """Une vidéo refusée à la main, revenue en contournant le filtre des
        écrivains : l'invariant la voit — chez la fiche ET chez une sœur."""
        from src.utils import corrections_fiches as cf

        a, t = isha
        swing = Artist(name="Swing")
        swing.id = data_manager.save_artist(swing)
        soeur = Track(title="Durag", artist=swing)
        soeur.genius_id = 4242
        data_manager.save_track(soeur)
        cf.memoriser_video_refusee(t, "V" * 11)
        assert not _familles(au.auditer(data_manager, persistance=True, revue=False)).get(
            "refus_vivant"
        )
        _sql(
            data_manager,
            "INSERT INTO track_videos (track_id, video_id) VALUES (:t, :v)",
            t=soeur.id,
            v="V" * 11,
        )
        audit = au.auditer(data_manager, ["Swing"], revue=False)
        (cas,) = [c for c in audit.cas if c.famille == "refus_vivant"]
        assert cas.pistes == [soeur.id] and "video" in cas.libelle

    def test_isrc_d_une_edition_porte_par_une_autre_fiche(self, data_manager, isha):
        """« La Gamberge - Live 2006 » (#8199) portait en édition les pages
        Deezer de l'EP (#12092, FRZ110600957) ; la même édition sur une SŒUR
        de la même page Genius n'est pas un défaut."""
        a, t = isha
        ep = Track(title="Durag (Version EP)", artist=a)
        ep.isrc = "FRZ110600957"
        ep.id = data_manager.save_track(ep)
        data_manager.record_track_edition(
            ep.id, "Collector", source="deezer", deezer_id=3113153, isrc="FRZ110600957"
        )
        assert not _familles(au.auditer(data_manager, revue=False)).get("isrc_edition_autre_fiche")
        data_manager.record_track_edition(
            t.id,
            "Au Tour De Ma Bulle Live",
            source="deezer",
            deezer_id=3437202,
            isrc="frz110600957",
        )
        audit = au.auditer(data_manager, ["Isha"], revue=False)
        (cas,) = [c for c in audit.cas if c.famille == "isrc_edition_autre_fiche"]
        assert cas.pistes == [t.id, ep.id] and "3437202" in cas.libelle

        swing = Artist(name="Swing")
        swing.id = data_manager.save_artist(swing)
        soeur = Track(title="Durag", artist=swing)
        soeur.genius_id = 4242
        soeur.isrc = "FRZ120000001"
        data_manager.save_track(soeur)
        data_manager.record_track_edition(t.id, "Radio Edit", source="deezer", isrc="FRZ120000001")
        audit = au.auditer(data_manager, ["Isha"], revue=False)
        assert len([c for c in audit.cas if c.famille == "isrc_edition_autre_fiche"]) == 1

    def test_orphelin_et_schema_sur_la_base_entiere(self, data_manager, isha):
        _sql(
            data_manager,
            "INSERT INTO credits (track_id, name, role, source) VALUES (999, 'X', 'Writer', 'g')",
        )
        audit = au.auditer(data_manager)
        (cas,) = [c for c in audit.cas if c.famille == "orphelin"]
        assert cas.libelle == "credits : 1 ligne(s) sans fiche"
        # Sur un artiste, ni orphelins ni schéma : ils ne portent sur personne.
        assert "orphelin" not in _familles(au.auditer(data_manager, ["Isha"]))

    def test_orphelins_derives_du_schema(self, data_manager, isha):
        """2026-11-09 : `revue_signalements`, `kworb_decisions`, `artist_id` et
        `release_id` n'étaient pas vérifiés ; les exclusions restent muettes."""
        a, _ = isha
        for sql in (
            "INSERT INTO revue_signalements (artist_id, detecteur, cle, track_id) "
            f"VALUES ({a.id}, 'kworb', 'k', 999)",
            "INSERT INTO release_tracks (release_id, track_id) VALUES (777, 1)",
            "INSERT INTO revue_verdicts (artist_id, detecteur, cle, verdict) "
            "VALUES (888, 'd', 'c', 'normal')",
            # Exclusions : l'historique d'usage survit à l'artiste, le journal
            # d'une page supprimée à sa fiche.
            "INSERT INTO source_usage_daily (day, source_key, artist_id, flow, issue) "
            "VALUES ('2026-11-09', 's', 888, 'f', 'ok')",
            "INSERT INTO revue_corrections (artist_id, detecteur, cle, track_id, action, "
            f"annulation) VALUES ({a.id}, 'annotee', 'c', 999, 'x', "
            """'{"type": "page_supprimee"}')""",
        ):
            _sql(data_manager, sql)
        libelles = sorted(
            c.libelle for c in au.auditer(data_manager).cas if c.famille == "orphelin"
        )
        assert libelles == [
            "release_tracks.release_id : 1 ligne(s) sans parution",
            "revue_signalements : 1 ligne(s) sans fiche",
            "revue_verdicts.artist_id : 1 ligne(s) sans artiste",
        ]

    def test_toute_colonne_de_lien_est_verifiee_ou_exclue(self, data_manager):
        """Garde : une table neuve qui désigne une fiche, un artiste ou une
        parution (par son nom de colonne ou par une clé étrangère) est vérifiée
        par `orphelins`, ou exclue avec sa raison dans `EXCLUSIONS_D_ORPHELINS`."""
        from contextlib import closing

        from src.persistence.schema import metadata

        visees = set(au.CIBLES_DES_LIENS.values())
        attendues = {
            (table.name, col.name)
            for table in metadata.tables.values()
            for col in table.columns
            if col.name in au.CIBLES_DES_LIENS
            or any(fk.column.table.name in visees for fk in col.foreign_keys)
        }
        with closing(au._connexion(data_manager.db_path)) as c:
            verifiees = {(t, k) for t, k, _ in au.colonnes_liees(c)}
        assert attendues - verifiees - set(au.EXCLUSIONS_D_ORPHELINS) == set()
        assert {"revue_signalements", "kworb_decisions", "releases"} <= {t for t, _ in verifiees}

    def test_artiste_inconnu(self, data_manager, isha):
        with pytest.raises(ValueError, match="Personne"):
            au.auditer(data_manager, ["Personne"])


class TestHausse:
    def _audit(self, **compte):
        a = au.Audit(date="d", perimetre="p")
        for famille, n in compte.items():
            a.cas += [au.Cas(famille, "A", [], "") for _ in range(n)]
        return a

    def test_une_famille_qui_monte(self):
        ref = self._audit(id_partage=2, duree_aberrante=5).vers_dict()
        assert au.en_hausse(self._audit(id_partage=3, duree_aberrante=1), ref) == {
            "id_partage": (2, 3)
        }

    def test_seuls_les_cas_du_critere_montent(self):
        """2026-10-19 : un run qui ajoute des cas « à faire par un run » ou
        « pour information » n'a rien dégradé — le critère du gel ne compte que
        les erreurs et les décisions."""
        ref = self._audit(**{"revue:credits_api": 1}).vers_dict()
        audit = self._audit()
        audit.cas += [au.Cas("revue:credits_api", "A", [], "", au.RUN) for _ in range(3)]
        audit.cas += [au.Cas("revue:video_etrangere", "A", [], "", au.INFORMATION)]
        audit.cas += [au.Cas("revue:doublon", "A", [], "", au.DECISION)]
        assert au.en_hausse(audit, ref) == {"revue:doublon": (0, 1)}
        assert audit.critere() == 1
        assert audit.par_nature() == {
            au.ERREUR: 0,
            au.DECISION: 1,
            au.RUN: 3,
            au.INFORMATION: 1,
            au.INDECIDABLE: 0,
        }
        texte = au.tableau(audit, ref)
        assert "panneau : à faire par un run (3)" in texte and "pour information (1)" in texte
        assert "CRITÈRE de fin du gel (erreurs + décisions) : 1 (avant : 1)" in texte

    def test_reference_d_avant_les_natures(self):
        """Une référence sans `compte_critere` : tous ses cas comptaient — la
        comparaison est plus indulgente, jamais une fausse hausse."""
        ref = {"compte": {"revue:credits_api": 5}, "cas": []}
        audit = self._audit()
        audit.cas += [au.Cas("revue:credits_api", "A", [], "", au.RUN) for _ in range(9)]
        assert au.en_hausse(audit, ref) == {}

    def test_sans_reference_seuls_les_invariants_montent(self):
        audit = self._audit(duree_aberrante=4, colonne_verdict=1)
        assert au.en_hausse(audit, None) == {"colonne_verdict": (0, 1)}

    def test_la_reference_suit_la_base(self, data_manager, isha, tmp_path):
        """Fin de run : comparé au PRÉCÉDENT audit du même artiste, puis le
        remplace — la référence suit la base, un même défaut ne crie qu'une fois."""
        a, t = isha
        _, hausse, avait = au.apres_run(data_manager, a, tmp_path)
        assert hausse == {} and not avait

        from src.enrichment.observation import Observation

        # Une durée que fait une plateforme (sans elle : indécidable, hors critère).
        data_manager.record_discography_observations(t.id, [Observation("duration", 5, "deezer")])
        _sql(data_manager, "UPDATE tracks SET duration = 5 WHERE id = :t", t=t.id)
        audit, hausse, avait = au.apres_run(data_manager, a, tmp_path)
        assert avait and hausse["duree_aberrante"] == (0, 1)
        assert "duree_aberrante : 0 → 1" in au.resume_fin_de_run(audit, hausse, avait)

        # La hausse laisse une trace : la référence, elle, vient d'être remplacée.
        (ligne,) = (tmp_path / "hausses.jsonl").read_text(encoding="utf-8").splitlines()
        trace = json.loads(ligne)
        assert trace["hausse"] == {"duree_aberrante": [0, 1]}
        assert [c["pistes"] for c in trace["nouveaux"]] == [[t.id]]

        _, hausse, _ = au.apres_run(data_manager, a, tmp_path)
        assert "duree_aberrante" not in hausse
        assert len((tmp_path / "hausses.jsonl").read_text(encoding="utf-8").splitlines()) == 1

    def test_un_cas_nouveau_monte_a_compte_egal(self):
        """2026-11-09 : un cas réparé et un cas créé dans la même famille ne se
        compensent pas — la comparaison se fait par CAS, pas au seul compte."""
        ref = au.Audit(date="d", perimetre="p")
        ref.cas = [au.Cas("id_partage", "A", [1, 2], "x"), au.Cas("orphelin", "—", [], "credits")]
        audit = au.Audit(date="d", perimetre="p")
        audit.cas = [
            au.Cas("id_partage", "A", [3, 4], "y"),
            au.Cas("orphelin", "—", [], "credits"),
            au.Cas("revue:credits_api", "A", [9], "hors critère", au.RUN),
        ]
        assert au.en_hausse(audit, ref.vers_dict()) == {"id_partage": (1, 1)}
        audit.nouveaux = au.cas_nouveaux(audit, ref.vers_dict())
        assert [c.pistes for c in audit.nouveaux] == [[3, 4]]
        resume = au.resume_fin_de_run(audit, {"id_partage": (1, 1)}, True)
        assert "id_partage : 1 → 1 (1 nouveau(x))" in resume and "• y" in resume

    def test_le_libelle_d_un_cas_de_fiche_ne_fait_pas_un_cas_nouveau(self):
        """Durées et compteurs bougent dans le libellé : la fiche désigne le cas."""
        ref = au.Audit(date="d", perimetre="p", cas=[au.Cas("ytm_incoherent", "A", [1], "10")])
        audit = au.Audit(date="d", perimetre="p", cas=[au.Cas("ytm_incoherent", "A", [1], "12")])
        assert au.en_hausse(audit, ref.vers_dict()) == {}

    def test_le_tableau_montre_les_familles_hors_critere(self):
        """2026-11-09 : elles s'affichaient à 0 parmi les erreurs, et nulle part
        ailleurs (1 319 cas cachés)."""
        audit = au.Audit(date="d", perimetre="p")
        audit.cas = [
            au.Cas("id_spotify_sans_compteur", "A", [1], "", au.RUN),
            au.Cas("fiche_sans_participation", "A", [2], "", au.INFORMATION),
            au.Cas("certif_autre", "A", [3], "", au.INFORMATION),
        ]
        erreurs, reste = au.tableau(audit).split("── indécidables")
        assert "id_spotify_sans_compteur" not in erreurs
        assert "fiche_sans_participation" not in erreurs
        run = reste.split("à faire par un run (1)")[1].split("── panneau")[0]
        assert "     1" in run and "id_spotify_sans_compteur" in run
        info = reste.split("pour information (2)")[1]
        assert "fiche_sans_participation" in info and "certif_autre" in info


def test_couverture_des_durees_hors_critere(data_manager, isha):
    """2026-11-09 : une durée qu'aucune source qui fait foi ne porte n'est pas
    une erreur, mais la part de ces durées se suit — une ligne du tableau, un
    champ de `vers_dict`, aucun cas."""
    from src.enrichment.observation import Observation

    a, t = isha
    for titre, sources in (("Vidéo", ["youtube_video"]), ("Discogs", ["discogs", "legacy"])):
        f = Track(title=titre, artist=a)
        data_manager.save_track(f)
        data_manager.record_discography_observations(
            f.id, [Observation("duration", 200, s) for s in sources]
        )
    data_manager.record_discography_observations(
        t.id,
        [Observation("duration", 200, "deezer"), Observation("duration", 201, "youtube_video")],
    )
    audit = au.auditer(data_manager, revue=False)
    assert audit.couverture_durees == {
        "durees": 3,
        "sans_source_qui_fait_foi": 2,
        "seule_youtube_video": 1,
    }
    assert audit.vers_dict()["couverture_durees"] == audit.couverture_durees
    assert "Couverture des durées : 2 / 3 (67%)" in au.tableau(audit)
    assert "dont 1 sur la seule vidéo" in au.tableau(audit)
    # Sans durée en base, pas de ligne.
    assert "Couverture" not in au.tableau(au.Audit(date="d", perimetre="p"))


class TestATrancher:
    """Chantier B, lot 3 : l'audit compte ce que le panneau « À trancher »
    montre — un « ✓ Normal » le retire du compte, comme du panneau."""

    def test_un_doublon_de_titre_est_compte_puis_masque(self, data_manager, isha):
        a, _ = isha
        for titre in ("Boss", "BOSS"):
            data_manager.save_track(Track(title=titre, artist=a))

        audit = au.auditer(data_manager, ["Isha"], persistance=False)
        doublons = [c for c in audit.cas if c.famille == "revue:doublon"]
        assert doublons and "revue:doublon" in au.toutes_les_familles()

        from src.services import revue

        artiste = data_manager.get_artist_by_name("Isha")
        artiste.tracks = data_manager.discographie_reunie(artiste)
        for cas in revue.analyser(data_manager, artiste).actifs:
            if cas.detecteur == "doublon":
                data_manager.trancher_cas(a.id, cas.detecteur, cas.cle)

        audit = au.auditer(data_manager, ["Isha"], persistance=False)
        assert not [c for c in audit.cas if c.famille == "revue:doublon"]

    def test_sans_revue(self, data_manager, isha):
        a, _ = isha
        for titre in ("Boss", "BOSS"):
            data_manager.save_track(Track(title=titre, artist=a))
        audit = au.auditer(data_manager, ["Isha"], persistance=False, revue=False)
        assert not any(c.famille.startswith("revue:") for c in audit.cas)


@pytest.mark.parametrize(
    "titre, court",
    [
        ("Interlude : Lucien", True),
        ("YZY SND (Skit)", True),
        ("Hasta La Vista (Intro)", True),
        ("Intro", True),
        ("ISABELLA", False),
        ("RXN", False),
        ("Introspection", False),
    ],
)
def test_un_titre_qui_annonce_un_morceau_court(titre, court):
    """2026-10-13 : les « Interlude : … » des Cool Sessions de MC Solaar (15-24 s)
    criaient « durée aberrante » ; un titre qui annonce un morceau court n'est
    pas suspect sous 25 s — « ISABELLA » à 9 s, si."""
    from src.services.audit import court_par_nature

    assert court_par_nature(titre) is court


@pytest.mark.parametrize(
    "titre, duree, observees, paroles, aberrante",
    [
        # Deux catalogues la donnent : le morceau EST court (ou long).
        ("ISABELLA", 9, {"deezer": 9, "spotify_web": 8, "discogs": 8}, "", False),
        ("13 Organisé", 1471, {"deezer": 1471, "spotify_web": 1471}, "", False),
        # Une émission longue, ses paroles à la mesure de la vidéo.
        ("Grünt #55", 1933, {"youtube_video": 1933}, "mot " * 5386, False),
        ("Runaway (Live at Sydney 2014)", 907, {"youtube_video": 907}, "mot " * 1634, False),
        # … ou sans paroles : rien ne la dément.
        ("A TRAUMA Freestyle", 1697, {"youtube_video": 1697}, None, False),
        # L'entretien entier sous la page d'un freestyle de 73 mots.
        ("Kanye West Sway Freestyle", 2055, {"youtube_video": 2055}, "mot " * 73, True),
        # Un extrait de 12 s, seule source : inexpliqué.
        ("SICKO MODE (ref. CyHi)", 12, {"youtube_video": 12}, None, True),
        # Long et pas une émission.
        ("Morts pour rien", 3840, {"youtube_video": 3840}, None, True),
    ],
)
def test_une_duree_hors_norme_expliquee_n_est_pas_aberrante(
    titre, duree, observees, paroles, aberrante
):
    from src.services.audit import duree_aberrante

    assert duree_aberrante(titre, duree, observees, paroles) is aberrante


def test_paroles_identiques_sur_deux_titres_non_equivalents():
    """Chantier D, 2026-10-13 : le pendant de `lrc_emprunte` pour les paroles."""
    texte = "\n".join(
        f"[Couplet {i}]\nligne numéro {i} du texte qui parle de la ville et du temps qui passe"
        for i in range(12)
    )

    def fiche(id_, titre, genius_id, paroles=texte):
        return {
            "id": id_,
            "title": titre,
            "genius_id": genius_id,
            "lyrics": paroles,
            "lyrics_source": "genius",
            "artiste": "Josman",
        }

    par = {
        1: [
            fiche(1, "Fais le vide", 10),
            fiche(2, "Tous Les Jours", 11),
            fiche(3, "Tous Les Jours (Remix)", 12),  # version de 2 : pas d'emprunt entre elles
            fiche(4, "Fais le vide", 10),  # ligne sœur : même page
        ]
    }
    cas = au.paroles_identiques(par)
    assert {tuple(c.pistes) for c in cas} == {(1, 2), (1, 3), (2, 4), (3, 4)}
    assert all(c.famille == "paroles_identiques" for c in cas)
    assert all(c.nature == au.ERREUR for c in cas)


def test_deux_versions_aux_memes_paroles_pour_information():
    """« Any Man (Acapella) » / « Fucking Crazy (Any Man Demo Version) » d'Eminem
    (2026-11-02) : deux versions d'un morceau ont ses paroles."""
    texte = "\n".join(
        f"[Couplet {i}]\nligne numéro {i} du texte qui parle de la ville et du temps qui passe"
        for i in range(12)
    )
    par = {
        1: [
            {
                "id": i,
                "title": titre,
                "genius_id": i,
                "lyrics": texte,
                "lyrics_source": "genius",
                "artiste": "Eminem",
            }
            for i, titre in ((1, "Any Man (Acapella)"), (2, "Fucking Crazy (Any Man Demo Version)"))
        ]
    }
    (cas,) = au.paroles_identiques(par)
    assert cas.nature == au.INFORMATION


@pytest.mark.parametrize(
    "durees, nature",
    [
        # « Best Day Ever » / « BDE Bonus » de Mac Miller : 159 / 243 s.
        ({1: {"deezer": 159}, 2: {"spotify_web": 243}}, au.INFORMATION),
        # Mêmes durées : un doublon peut-être, mais aucun cas ne l'a validé.
        ({1: {"deezer": 159}, 2: {"spotify_web": 160}}, au.ERREUR),
        # Sans durée lue par identifiant d'un côté : rien ne tranche.
        ({1: {"deezer": 159}}, au.ERREUR),
    ],
)
def test_paroles_identiques_aux_durees_differentes_version_non_nommee(durees, nature):
    """2026-11-09, simulé : 3 cas sur 11, les faux positifs de CORRECTIFS."""
    texte = "\n".join(
        f"[Couplet {i}]\nligne numéro {i} du texte qui parle de la ville et du temps qui passe"
        for i in range(12)
    )
    par = {
        1: [
            {
                "id": i,
                "title": titre,
                "genius_id": i,
                "lyrics": texte,
                "lyrics_source": "genius",
                "artiste": "Mac Miller",
            }
            for i, titre in ((1, "Best Day Ever"), (2, "BDE Bonus"))
        ]
    }
    (cas,) = au.paroles_identiques(par, durees)
    assert cas.nature == nature
    assert ("version non nommée" in cas.libelle) is (nature == au.INFORMATION)


def test_lrc_minute_au_dela_de_la_duree(data_manager, isha):
    """Un LRC qui court après la fin du morceau : l'un des deux est faux, et
    l'audit dit si la durée était confirmée (2026-10-13)."""
    from src.enrichment.observation import Observation

    _, t = isha
    data_manager.record_discography_observations(
        t.id, [Observation("duration", 38, "deezer"), Observation("duration", 38, "spotify_web")]
    )
    _sql(
        data_manager,
        "UPDATE tracks SET lyrics_synced = '[00:00.00] a\n[03:19.00] b' WHERE id = :t",
        t=t.id,
    )
    (cas,) = [c for c in au.auditer(data_manager, ["Isha"]).cas if c.famille == "lrc_trop_long"]
    assert cas.pistes == [t.id] and "199s" in cas.libelle and "(confirmée)" in cas.libelle
    assert cas.nature == au.ERREUR

    _sql(data_manager, "UPDATE tracks SET lyrics_synced = '[00:30.00] a' WHERE id = :t", t=t.id)
    assert not _familles(au.auditer(data_manager, ["Isha"])).get("lrc_trop_long")


class TestYtmIncoherent:
    """`ytm_incoherent` juge la colonne avec LE calcul de la colonne
    (`track_repository.total_ytm`) : vidéos propres + compteurs du run YTM sans
    mesure exacte (2026-10-20). Il comparait aux seules vidéos : une fiche à
    compteur sans videoId sortait à tort."""

    def test_un_compteur_sans_video_fait_partie_du_total(self, data_manager, isha):
        from src.models import TrackVideo

        _, t = isha
        data_manager.record_ytm_compteurs(t.id, {"_novid_Alb_durag": 1000})
        data_manager.record_track_videos(t.id, [TrackVideo(video_id="a" * 11, views=500)])

        audit = au.auditer(data_manager, ["Isha"], revue=False)

        assert "ytm_incoherent" not in _familles(audit)

    def test_une_colonne_fausse_sort_toujours(self, data_manager, isha):
        from src.models import TrackVideo

        _, t = isha
        data_manager.record_ytm_compteurs(t.id, {"_novid_Alb_durag": 1000})
        data_manager.record_track_videos(t.id, [TrackVideo(video_id="a" * 11, views=500)])
        _sql(data_manager, "UPDATE tracks SET ytm_streams = 9000 WHERE id = :t", t=t.id)

        audit = au.auditer(data_manager, ["Isha"], revue=False)

        (cas,) = [c for c in audit.cas if c.famille == "ytm_incoherent"]
        assert "colonne 9,000 / vidéos et compteurs 1,500" in cas.libelle


class TestRecherchePerimee:
    """Une vidéo de la recherche que le score courant refuse : ses vues ne
    comptent pas, et le PANNEAU la montre à trancher (`revue:recherche_perimee`,
    2026-10-29 — c'était une famille de l'audit, comptée deux fois sinon)."""

    def test_la_video_refusee_est_a_trancher(self, data_manager):
        from src.models import TrackVideo

        a = Artist(name="Django")
        a.id = data_manager.save_artist(a)
        t = Track(title="Chaos", artist=a, album="Anthracite")
        t.genius_id = 711
        data_manager.save_track(t)
        vu = {"titre": "Chaos", "artistes": ["Jango Jack"], "album": None}
        data_manager.record_track_videos(
            t.id,
            [TrackVideo(video_id="FC05EcH6dUw", source="search_auto", views=400, recherche=vu)],
        )
        sans_revue = au.auditer(data_manager, ["Django"], revue=False)
        assert "recherche_perimee" not in _familles(sans_revue)
        assert "ytm_incoherent" not in _familles(sans_revue)
        audit = au.auditer(data_manager, ["Django"])
        (cas,) = [c for c in audit.cas if c.famille == "revue:recherche_perimee"]
        assert cas.nature == au.DECISION and "Jango Jack" in cas.libelle
        # Affichée dans le bloc des décisions.
        decisions = au.tableau(audit).split("panneau : décisions")[1].split("── panneau")[0]
        assert "revue:recherche_perimee" in decisions


class TestVideoPartagee:
    """Une vidéo portée par plusieurs fiches n'a qu'UN juge, le panneau
    (`revue.video_partagee`, `revue.partage_a_juger`) : l'audit en comptait 33
    en erreurs que le panneau tenait pour information ou ne voyait pas
    (2026-10-29). L'audit compte le cas du panneau, avec SA nature."""

    @staticmethod
    def _fiches(dm, albums, titres=("KING", "KING"), video_titre="KING"):
        from src.models import TrackVideo

        a = Artist(name="Ye")
        a.id = dm.save_artist(a)
        for i, (titre, album) in enumerate(zip(titres, albums, strict=True)):
            t = Track(title=titre, artist=a, album=album)
            t.genius_id = 900 + i
            dm.save_track(t)
            dm.record_track_videos(
                t.id, [TrackVideo(video_id="g6vAQpxo35g", title=video_titre, views=800)]
            )

    def test_l_audit_compte_le_cas_du_panneau_avec_sa_nature(self, data_manager):
        from src.services import revue

        self._fiches(data_manager, ("BULLY", "VULTURES 1"))
        assert "video_partagee" not in au.FAMILLES
        assert not any(
            "video_partagee" in f for f in _familles(au.auditer(data_manager, ["Ye"], revue=False))
        )

        audit = au.auditer(data_manager, ["Ye"], persistance=False)
        (cas,) = [c for c in audit.cas if "video_partagee" in c.famille]
        artiste = data_manager.get_artist_by_name("Ye")
        artiste.tracks = data_manager.discographie_reunie(artiste)
        (panneau,) = [
            c
            for c in revue.analyser(data_manager, artiste).actifs
            if c.detecteur == "video_partagee"
        ]
        assert (cas.famille, cas.nature) == ("revue:video_partagee", panneau.nature)
        # Deux pages homonymes d'un même artiste : ses vues ne comptent nulle
        # part, rien dans la base ne départage — une information.
        assert panneau.nature == au.INFORMATION and panneau.preuves["homonymes"]

    def test_meme_disque_ni_audit_ni_panneau(self, data_manager):
        """Clip double d'un même album : compté au niveau de l'album
        (décision utilisateur 2026-10-10), un cas pour personne."""
        self._fiches(data_manager, ("BULLY", "BULLY"), ("KING", "Album entier"), "Ye - BULLY")
        audit = au.auditer(data_manager, ["Ye"], persistance=False)
        assert not [c for c in audit.cas if "video_partagee" in c.famille]

    def test_clip_qui_nomme_deux_titres_distincts_n_est_pas_un_cas(self, data_manager):
        """« Prélude avant l'amour / A4 » d'A2H : la vidéo nomme chacune."""
        self._fiches(data_manager, ("L'amour", "A4"), ("Prélude", "A4"), "Ye - Prélude / A4")
        audit = au.auditer(data_manager, ["Ye"], persistance=False)
        assert not [c for c in audit.cas if "video_partagee" in c.famille]


def test_id_de_catalogue_entre_pages_laisse_au_panneau():
    """Spotify / Deezer entre deux PAGES : `revue:id_catalogue_partage` le
    compte (une fois) ; un ISRC, ou une fiche sans page, reste à l'audit."""

    def _r(tid, genius_id, **ids):
        return {
            "id": tid,
            "title": "Famous",
            "artiste": "Kanye West",
            "genius_id": genius_id,
            "spotify_id": None,
            "deezer_id": None,
            "isrc": None,
            "apple_music_id": None,
            **ids,
        }

    par = {1: [_r(1, 10, deezer_id=7, isrc="US1"), _r(2, 11, deezer_id=7, isrc="US1")]}
    assert [c.libelle.split(" ")[0] for c in au.ids_partages(par)] == ["isrc"]
    sans_page = {1: [_r(1, 10, spotify_id="SP"), _r(2, None, spotify_id="SP")]}
    assert [c.libelle.split(" ")[0] for c in au.ids_partages(sans_page)] == ["spotify_id"]


class TestIsrcDUnTitreJoint:
    """2026-11-09, simulé : 5 cas sur 6. Même ISRC, l'un JOINT au bout de son
    titre celui de l'autre, durées par identifiant à ± 2 s : le même
    enregistrement sous deux pages — une décision de fusion, pas une erreur."""

    @staticmethod
    def _par(titre_a, titre_b):
        def r(tid, titre):
            return {
                "id": tid,
                "title": titre,
                "artiste": "Sofiane",
                "genius_id": 100 + tid,
                "spotify_id": None,
                "deezer_id": None,
                "isrc": "FRPJW1600870",
                "apple_music_id": None,
            }

        return {1: [r(1, titre_a), r(2, titre_b)]}

    @pytest.mark.parametrize(
        "titre_a, titre_b",
        [
            ("93 Empire", "#Jesuispasséchezso : Épisode 6 / 93 Empire"),
            # Le titre de l'autre n'est que son SOUS-TITRE entre parenthèses.
            (
                "C’est nous les condés (Police Nationale)",
                "#Jesuispasséchezso : Episode 5 / Police Nationale",
            ),
        ],
    )
    def test_titre_joint_et_memes_durees_decision(self, titre_a, titre_b):
        (cas,) = au.ids_partages(
            self._par(titre_a, titre_b), {1: {"deezer": 201}, 2: {"spotify_web": 199}}
        )
        assert cas.nature == au.DECISION and "fusionner ?" in cas.libelle

    @pytest.mark.parametrize(
        "titre_b, durees",
        [
            # Durées à plus de 2 s : deux enregistrements possibles.
            (
                "#Jesuispasséchezso : Épisode 6 / 93 Empire",
                {1: {"deezer": 201}, 2: {"deezer": 230}},
            ),
            # Sans durée lue par identifiant d'un côté : rien ne le montre.
            ("#Jesuispasséchezso : Épisode 6 / 93 Empire", {1: {"deezer": 201}}),
            # « 24/7 » n'est pas une jointure.
            ("24/7", {1: {"deezer": 201}, 2: {"deezer": 201}}),
            # Même titre (« Tigini (Remix) », deux remix) : rien n'est joint.
            ("93 Empire", {1: {"deezer": 201}, 2: {"deezer": 201}}),
        ],
    )
    def test_sinon_erreur(self, titre_b, durees):
        (cas,) = au.ids_partages(self._par("93 Empire", titre_b), durees)
        assert cas.nature == au.ERREUR

    def test_les_durees_lues_par_identifiant_seulement(self, data_manager, isha):
        """`durees_par_identifiant` ne lit que les catalogues lus par ID."""
        from src.enrichment.observation import Observation

        _, t = isha
        data_manager.record_discography_observations(
            t.id, [Observation("duration", 201, "deezer"), Observation("duration", 150, "legacy")]
        )
        from contextlib import closing

        with closing(au._connexion(data_manager.db_path)) as c:
            assert au.durees_par_identifiant(c)[t.id] == {"deezer": 201}


@pytest.mark.parametrize(
    "titre, titre_certif, nature",
    [
        # Admis par le juge de la pose (décisions utilisateur du 2026-10-09) :
        # sous-titre, titre coupé par la source, édition, invités entre
        # parenthèses, certif d'une version sur l'original sans fiche de version.
        ("Make Her Say (I Poke Her Face)", "MAKE HER SAY", au.INFORMATION),
        ("L'empire du côté obscur", "L'empire du c", au.INFORMATION),
        ("Je danse le mia", "JE DANSE LE MIA REMAST 2016", au.INFORMATION),
        ("Euphoria", "EUPHORIA (KAASH PAIGE & TRAVIS SCOTT)", au.INFORMATION),
        ("Dolce Camara", "DOLCE CAMARA (SNIGHT B REMIX)", au.INFORMATION),
        # Refusé par le juge : posé avant une règle, pas rejoué.
        ("Life", "TUBA LIFE", au.ERREUR),
        ("Suzy (Live 2006)", "SUZY", au.ERREUR),
    ],
)
def test_certif_admise_par_le_juge_n_est_qu_une_information(titre, titre_certif, nature):
    """Un verdict, un seul endroit (2026-10-29) : l'audit ne compte en erreur
    que ce que `certif_du_morceau` refuse."""
    piste = {
        "id": 1,
        "title": titre,
        "artiste": "X",
        "certifications": json.dumps([{"title": titre_certif, "category": "single"}]),
    }
    (cas,) = au.certifs([piste])
    assert cas.nature == nature


@pytest.mark.parametrize(
    "duree, paroles, fausse",
    [
        # L'« Outro » de Di-Meh (12 rappeurs) sous l'audio de 167 s de Népal.
        (167, "mot " * 1350, True),
        # Le même texte à sa vraie durée.
        (330, "mot " * 1350, False),
        # Un rap rapide reste sous le plafond (Flynt, 7,6 mots/s).
        (102, "mot " * 773, False),
        (167, None, False),
        (None, "mot " * 1350, False),
    ],
)
def test_une_duree_trop_courte_pour_ses_paroles(duree, paroles, fausse):
    from src.utils.duree_youtube import trop_courte_pour_les_paroles

    assert trop_courte_pour_les_paroles(duree, paroles) is fausse


@pytest.mark.parametrize(
    "titre, album, segment",
    [
        # Décision utilisateur 2026-10-29 : le segment d'un DJ mix (46 s).
        ("Know No Better [BROHUG Remix] [Mixed]", "Nostalgix at EDC (DJ Mix)", True),
        ("Parking Lot (Mixed)", None, True),
        ("Goosebumps", "Today’s Hits, November 2022 (DJ Mix)", True),
        # « Mixed » hors d'un groupe, ou un autre montage : pas un DJ mix.
        ("Mixed Feelings", None, False),
        ("Know No Better (BROHUG Remix)", "Know No Better (Remixes)", False),
    ],
)
def test_un_segment_de_dj_mix_n_est_pas_juge_au_debit(titre, album, segment):
    from src.utils.duree_youtube import segment_de_dj_mix

    assert segment_de_dj_mix(titre, album) is segment


def test_duree_discogs_dementie_par_deezer_et_apple_hors_critere(data_manager, isha):
    """CORRECTIFS D (2026-10-29) : 33 durées Discogs démenties par Deezer ET
    Apple (B.B. Jacques « Donjon » 170 s pour 134 s). L'erreur est dans la
    tracklist Discogs elle-même (release 24758360 : « Donjon » 2:50, « 2h22 »
    2:14), pas dans le rapprochement par titre ; le run la repose. Discogs est
    FRAGILE (contredit, ne soutient jamais) et 7e des priorités : la colonne
    garde les catalogues, et l'audit ne compte rien au critère."""
    from src.enrichment.observation import Observation

    _, t = isha
    data_manager.record_discography_observations(
        t.id,
        [
            Observation("duration", 134, "deezer"),
            Observation("duration", 134, "apple_music"),
            Observation("duration", 134, "spotify_web"),
            Observation("duration", 170, "discogs"),
        ],
    )
    with data_manager.engine.connect() as conn:
        colonne = conn.execute(
            text("SELECT duration FROM tracks WHERE id = :t"), {"t": t.id}
        ).scalar()
    assert int(colonne) == 134
    audit = au.auditer(data_manager, ["Isha"])
    assert [c for c in audit.cas if t.id in c.pistes and c.nature in au.NATURES_DU_CRITERE] == []


@pytest.mark.parametrize(
    ("observees", "nature"),
    [
        # BULLY V1 : Discogs contre une vidéo, aucune source qui fait foi.
        ({"discogs": 2711, "youtube_video": 1278}, au.INDECIDABLE),
        ({"legacy": 159}, au.INDECIDABLE),
        ({"apple_music": 167, "lrclib": 327}, au.ERREUR),
        ({"manual": 147}, au.ERREUR),
    ],
)
def test_une_duree_sans_plateforme_est_indecidable(observees, nature):
    """Décision utilisateur 2026-10-31 : un choix entre sources faibles n'est
    pas une erreur démontrable — hors du critère, gardé à l'affichage."""
    assert au._nature_d_une_duree(observees) == nature
    assert au.INDECIDABLE not in au.NATURES_DU_CRITERE


def test_un_cas_tranche_indecidable_reste_compte_hors_critere(isha, data_manager, monkeypatch):
    """Décision utilisateur 2026-10-31 : un cas du panneau qu'aucune donnée ne
    tranche quitte le panneau et le critère, mais l'audit le garde parmi les
    indécidables ; un verdict « normal » le fait disparaître tout à fait."""
    from src.services import revue
    from src.utils.revue_repository import VERDICT_INDECIDABLE

    a, t = isha
    lrc = revue.Cas("lrc_douteux", t.id, "Durag", "LRC douteux", cle="g4242|lrc")
    bpm = revue.Cas("bpm", t.id, "Durag", "BPM en désaccord", cle="g4242|bpm")
    monkeypatch.setattr(
        revue, "analyser", lambda dm, artiste: revue.Revue(actifs=[], masques=[lrc, bpm])
    )
    data_manager.trancher_cas(a.id, "lrc_douteux", lrc.cle, verdict=VERDICT_INDECIDABLE)
    data_manager.trancher_cas(a.id, "bpm", bpm.cle)

    cas = au.a_trancher(data_manager, ["Isha"])

    assert [(c.famille, c.nature) for c in cas] == [("revue:lrc_douteux", au.INDECIDABLE)]
    assert au.INDECIDABLE not in au.NATURES_DU_CRITERE


def test_un_cas_de_fiche_n_est_compte_qu_une_fois(data_manager, isha, monkeypatch):
    """La discographie réunie montre le même cas chez l'artiste, son groupe et
    ses lignes sœurs (16 cas du critère comptés deux ou trois fois, 2026-11-09) :
    une décision, un cas. Un cas d'artiste et une certif restent par artiste."""
    from src.services import revue

    autre = Artist(name="Caballero")
    autre.id = data_manager.save_artist(autre)
    _, t = isha
    partage = [
        revue.Cas("lrc_douteux", t.id, "Durag", "LRC douteux", cle="g4242|lrc"),
        revue.Cas("liens_proposes", None, "Sniper", "formation", cle="artiste|sniper"),
        revue.Cas("certif_date", t.id, "Durag", "certif", cle="g4242|certif"),
    ]
    monkeypatch.setattr(
        revue, "analyser", lambda dm, artiste: revue.Revue(actifs=list(partage), masques=[])
    )

    cas = au.a_trancher(data_manager, ["Isha", "Caballero"])

    assert sorted((c.famille, c.artiste) for c in cas) == [
        ("revue:certif_date", "Caballero"),
        ("revue:certif_date", "Isha"),
        ("revue:liens_proposes", "Caballero"),
        ("revue:liens_proposes", "Isha"),
        ("revue:lrc_douteux", "Isha"),
    ]


def test_une_version_au_meme_lrc_et_a_la_meme_duree_est_indecidable():
    """« Just Don't Give A Fuck (EP Version) » 244 s / l'original 242 s : peut-être
    le même audio — le juge de la pose (`lrc_de_l_original`) ne juge pas, l'audit
    ne compte pas d'erreur (2026-11-02). Une durée différente reste une erreur."""
    lrc = "\n".join(f"[00:{i:02d}.00]ligne {i} du couplet" for i in range(40))

    def fiche(id_, titre, duree):
        return {
            "id": id_,
            "title": titre,
            "duration": duree,
            "lyrics": None,
            "lyrics_source": None,
            "lyrics_synced": lrc,
            "lyrics_synced_source": "YouTube Music",
            "genius_id": id_,
            "artiste": "Eminem",
        }

    for duree, nature in ((244, au.INDECIDABLE), (300, au.ERREUR)):
        par = {
            1: [
                fiche(1, "Just Don't Give a Fuck", 242),
                fiche(2, "Just Don't Give A Fuck (EP Version)", duree),
            ]
        }
        (cas,) = [c for c in au.lrc(par) if c.famille == "lrc_version"]
        assert cas.nature == nature


def _fiche_id(tid, artist_id, genius_id, **ids):
    return {
        "id": tid,
        "title": "Gangsta Shit",
        "artist_id": artist_id,
        "artiste": f"A{artist_id}",
        "genius_id": genius_id,
        "spotify_id": None,
        "deezer_id": None,
        "isrc": None,
        "apple_music_id": None,
        **ids,
    }


class TestIdsPartagesEntreArtistes:
    """2026-11-09 : `ids_partages` ne lit qu'un artiste à la fois."""

    def test_deux_pages_de_deux_artistes_au_meme_id_apple(self):
        """« Lollipop (French Remix) » de Booba sur l'ID Apple du « Lollipop
        (Remix) » de Kanye West : une erreur."""
        pistes = [
            _fiche_id(6550, 1, 7, apple_music_id="1440737575"),
            _fiche_id(8873, 2, 5463611, apple_music_id="1440737575"),
        ]
        (cas,) = au.ids_partages_entre_artistes(pistes)
        assert (cas.famille, cas.nature, cas.pistes) == (
            "id_partage_artistes",
            au.ERREUR,
            [6550, 8873],
        )

    def test_une_fiche_sans_page_a_l_isrc_d_une_page_d_un_autre(self):
        """« Gangsta Shit » d'Isha, sans page, à l'ISRC de la page d'Aketo : une
        ligne sœur non reliée — une décision, pas une donnée fausse."""
        pistes = [
            _fiche_id(9746, 1, None, isrc="FXR142300075"),
            _fiche_id(21707, 2, 9183723, isrc="fxr142300075 "),
        ]
        (cas,) = au.ids_partages_entre_artistes(pistes)
        assert (cas.famille, cas.nature) == ("soeur_non_reliee", au.DECISION)
        assert "soeur_non_reliee" in au.FAMILLES_DECISION

    @pytest.mark.parametrize(
        "pistes",
        [
            # Lignes sœurs : même page.
            [_fiche_id(1, 1, 10, isrc="X"), _fiche_id(2, 2, 10, isrc="X")],
            # Deux fiches sans page : la piste vue chez chaque artiste crédité.
            [_fiche_id(1, 1, None, deezer_id=5), _fiche_id(2, 2, None, deezer_id=5)],
            # Un même artiste : `ids_partages` le compte.
            [_fiche_id(1, 1, 10, isrc="X"), _fiche_id(2, 1, 11, isrc="X")],
            # Deux pages chez A, leurs sœurs chez B (« 93 Empire », Sofiane et
            # Kalash Criminel) : vu par `ids_partages` chez chacun.
            [
                _fiche_id(1, 1, 10, isrc="X"),
                _fiche_id(2, 1, 11, isrc="X"),
                _fiche_id(3, 2, 10, isrc="X"),
                _fiche_id(4, 2, 11, isrc="X"),
            ],
        ],
    )
    def test_ce_qui_n_est_pas_un_cas(self, pistes):
        assert au.ids_partages_entre_artistes(pistes) == []

    def test_spotify_entre_pages_d_une_formation_laisse_au_panneau(self):
        """Le panneau lit membre et groupe ensemble (`id_catalogue_partage`) ;
        un ISRC n'y est pas jugé, il reste à l'audit."""
        pistes = [
            _fiche_id(1, 1, 10, spotify_id="SP", isrc="X"),
            _fiche_id(2, 2, 11, spotify_id="SP", isrc="X"),
        ]
        liees = {frozenset((1, 2))}
        cas = au.ids_partages_entre_artistes(pistes, liees)
        assert [c.libelle.split(" ")[0] for c in cas] == ["isrc"]
        assert len(au.ids_partages_entre_artistes(pistes)) == 2

    def test_base_entiere_seulement(self, data_manager, isha):
        _, t = isha
        swing = Artist(name="Swing")
        swing.id = data_manager.save_artist(swing)
        autre = Track(title="Autre", artist=swing)
        autre.genius_id = 999
        data_manager.save_track(autre)
        for tid in (t.id, autre.id):
            _sql(data_manager, "UPDATE tracks SET apple_music_id = '42' WHERE id = :t", t=tid)
        entiere = au.auditer(data_manager, revue=False, persistance=False)
        assert _familles(entiere).get("id_partage_artistes") == 1
        par_artiste = au.auditer(data_manager, ["Isha"], revue=False, persistance=False)
        assert "id_partage_artistes" not in _familles(par_artiste)


def _certif(titre, titre_certif, categorie="single"):
    return {
        "id": 1,
        "title": titre,
        "artiste": "X",
        "certifications": json.dumps(
            [{"title": titre_certif, "category": categorie, "body": "SNEP"}]
        ),
    }


class TestFamillesNues:
    """2026-11-09 : six familles de l'audit qu'aucun test ne nommait."""

    def test_duree_paroles(self, data_manager, isha):
        """L'« Outro » de Di-Meh sous les 167 s de Népal : 1 350 mots en 167 s."""
        a, t = isha
        rapide = Track(title="Rapide", artist=a)
        data_manager.save_track(rapide)
        for tid, duree in ((t.id, 167), (rapide.id, 330)):
            _sql(
                data_manager,
                "UPDATE tracks SET duration = :d, lyrics = :p, lyrics_source = 'genius' "
                "WHERE id = :t",
                d=duree,
                p="mot " * 1350,
                t=tid,
            )
        # Les mêmes paroles venues de YTM ne sont pas un oracle.
        _sql(
            data_manager,
            "UPDATE tracks SET lyrics_source = 'ytmusic', duration = 100 WHERE id = :t",
            t=rapide.id,
        )
        audit = au.auditer(data_manager, ["Isha"], revue=False, persistance=False)
        (cas,) = [c for c in audit.cas if c.famille == "duree_paroles"]
        assert cas.pistes == [t.id] and cas.nature == au.ERREUR
        assert cas.libelle.endswith("167s, 1350 mots")

    def test_streams_double(self, data_manager, isha):
        """Le même compteur Spotify sur deux pages aux titres sans parenté."""
        a, t = isha
        autre = Track(title="Bavardage", artist=a)
        autre.genius_id = 4243
        data_manager.save_track(autre)
        for tid in (t.id, autre.id):
            _sql(data_manager, "UPDATE tracks SET spotify_streams = 123456 WHERE id = :t", t=tid)
        audit = au.auditer(data_manager, ["Isha"], revue=False, persistance=False)
        (cas,) = [c for c in audit.cas if c.famille == "streams_double"]
        assert sorted(cas.pistes) == sorted([t.id, autre.id]) and "123,456" in cas.libelle
        # Sous 1 000 streams, un compteur égal ne dit rien.
        for tid in (t.id, autre.id):
            _sql(data_manager, "UPDATE tracks SET spotify_streams = 900 WHERE id = :t", t=tid)
        audit = au.auditer(data_manager, ["Isha"], revue=False, persistance=False)
        assert "streams_double" not in _familles(audit)

    @pytest.mark.parametrize(
        "albums, pages_lues, nature, fin",
        [
            # Second signal : « Calypso I / II », même disque.
            (("Caravelle", "Caravelle"), {"A" * 22, "B" * 22}, au.ERREUR, "même disque"),
            # Aucun signal, les deux pages lues : le compteur est celui de Spotify.
            (("X", "Y"), {"A" * 22, "B" * 22}, au.INFORMATION, "compteur de Spotify"),
            # Aucun signal, une page jamais lue (« Vrai » d'Ol' Kainry).
            (("X", "Y"), {"A" * 22}, au.ERREUR, "compteur sans page lue : #"),
        ],
    )
    def test_streams_double_second_signal(
        self, data_manager, isha, albums, pages_lues, nature, fin
    ):
        """2026-11-09, simulé sur 4 cas."""
        from contextlib import closing

        a, t = isha
        autre = Track(title="Bavardage", artist=a)
        autre.genius_id = 4243
        data_manager.save_track(autre)
        for tid, album, sid in ((t.id, albums[0], "A" * 22), (autre.id, albums[1], "B" * 22)):
            _sql(
                data_manager,
                "UPDATE tracks SET spotify_streams = 123456, album = :a, spotify_id = :s "
                "WHERE id = :t",
                a=album,
                s=sid,
                t=tid,
            )
        with closing(au._connexion(data_manager.db_path)) as c:
            par = au._par_artiste(au._pistes(c, [a.id]))
            (cas,) = [x for x in au.streams(c, par, pages_lues) if x.famille == "streams_double"]
        assert cas.nature == nature and fin in cas.libelle

    def test_pages_spotify_lues_dans_le_brut(self, tmp_path):
        from src.persistence import brut

        brut.consigner(
            "spotify_web", "https://open.spotify.com/intl-fr/track/" + "A" * 22, 200, b"x"
        )
        brut.consigner(
            "spotify_web", "https://open.spotify.com/intl-fr/album/" + "B" * 22, 200, b"x"
        )
        brut.fermer()
        assert au.pages_spotify_lues() == {"A" * 22}

    @pytest.mark.parametrize(
        "piste, famille",
        [
            (_certif("Durag", "Durag", "album"), "certif_album"),
            # Mots EN PLUS dans le titre certifié.
            (_certif("Life", "TUBA LIFE"), "certif_plus_long"),
            # La certif de l'original sur une version à descripteur.
            (_certif("Suzy (Live 2006)", "SUZY"), "certif_version"),
            (_certif("Durag", "Bandana"), "certif_autre"),
        ],
    )
    def test_familles_de_certifs(self, piste, famille):
        (cas,) = au.certifs([piste])
        assert cas.famille == famille and "SNEP" in cas.libelle

    def test_certif_au_bon_titre_ou_echo_n_est_pas_un_cas(self):
        echo = _certif("Durag", "Bandana")
        echo["certifications"] = json.dumps([{"title": "Bandana", "echo": True}])
        assert au.certifs([_certif("Durag (feat. X)", "DURAG"), echo]) == []

    def test_echec_mal_classe(self, data_manager, isha):
        """Une absence (« pas de paroles ») ou une annulation rangée en `crash` ;
        un vrai plantage n'en est pas un, et un (morceau, source) compte une fois."""
        _, t = isha
        for source, issue, message in (
            ("lrclib", "crash", "Pas de paroles trouvées"),
            ("genius", "crash", "CancelledError()"),
            ("deezer", "crash", "KeyError: 'data'"),
            ("ytmusic", "absent", "aucun résultat"),
        ):
            _sql(
                data_manager,
                "INSERT INTO source_track_failures (track_id, source_key, flow, issue, message, "
                "n_echecs) VALUES (:t, :s, 'credits', :i, :m, 1)",
                t=t.id,
                s=source,
                i=issue,
                m=message,
            )
        audit = au.auditer(data_manager, ["Isha"], revue=False, persistance=False)
        cas = sorted(c.libelle for c in audit.cas if c.famille == "echec_mal_classe")
        assert len(cas) == 2
        assert "genius crash : CancelledError()" in cas[0]
        assert "lrclib crash : Pas de paroles" in cas[1]


def test_date_ancienne_pour_information(data_manager, isha):
    """2026-11-09 : « Lacrimosa » 1792 (juste), « Suicide » 1861 chez Kanye West
    (douteux) — montrés avec leurs sources, hors critère."""
    from src.enrichment.observation import Observation

    a, t = isha
    recente = Track(title="Récente", artist=a)
    data_manager.save_track(recente)
    data_manager.record_discography_observations(
        t.id, [Observation("release_date", "1861-01-01", "genius")]
    )
    data_manager.record_discography_observations(
        recente.id, [Observation("release_date", "1950-03-01", "deezer")]
    )
    audit = au.auditer(data_manager, ["Isha"], revue=False)
    (cas,) = [c for c in audit.cas if c.famille == "date_ancienne"]
    assert cas.pistes == [t.id] and cas.nature == au.INFORMATION
    assert "1861-01-01 (genius 1861-01-01)" in cas.libelle
    assert "date_ancienne" in au.FAMILLES_HORS_CRITERE
    assert audit.critere() == 0
