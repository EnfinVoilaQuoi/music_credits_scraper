"""Chantier B — l'audit des données comme définition de « fini ».

Les invariants de persistance ont une cible ZÉRO : chacun est montré ici sur
une base saine (rien), puis sur la base qu'on abîme exprès (le défaut se voit).
"""

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
