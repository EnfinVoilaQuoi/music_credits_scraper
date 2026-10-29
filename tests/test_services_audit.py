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
        assert audit.par_nature() == {au.ERREUR: 0, au.DECISION: 1, au.RUN: 3, au.INFORMATION: 1}
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

        _sql(data_manager, "UPDATE tracks SET duration = 5 WHERE id = :t", t=t.id)
        audit, hausse, avait = au.apres_run(data_manager, a, tmp_path)
        assert avait and hausse["duree_aberrante"] == (0, 1)
        assert "duree_aberrante : 0 → 1" in au.resume_fin_de_run(audit, hausse, avait)

        _, hausse, _ = au.apres_run(data_manager, a, tmp_path)
        assert "duree_aberrante" not in hausse


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
