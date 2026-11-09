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
