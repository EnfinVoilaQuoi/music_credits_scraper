"""Familles d'audit des CRÉDITS (2026-11-10) : `heritage_sans_source` et
`credit_graphie`. Mesure des candidats sur toute la base dans le rapport de la
session audit-vague4 (Discogs d'album et noms qui ne sont pas des personnes :
pas de famille, la précision n'est pas montrée)."""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import text

from src.models.artist import Artist
from src.models.track import Track
from src.services import audit as au
from src.utils.credit_normalize import credits_sous_deux_graphies
from src.utils.version_heritage import (
    ABSENT_DE_L_ORIGINAL,
    ROLE_NON_TRANSMIS,
    heritage_sans_source,
)


def _c(name, role="Producer", source="genius", detail=None):
    return SimpleNamespace(name=name, role=role, source=source, role_detail=detail)


class TestDeuxGraphies:
    def test_meme_personne_sous_deux_graphies(self):
        groupes = credits_sous_deux_graphies(
            [
                _c("Eric Chevet", "Mastering Engineer", "discogs"),
                _c("Éric Chevet", "Mastering Engineer", "genius"),
                _c("JeanJass", "Mixing Engineer", "genius"),
                _c("Jean Jass", "Mixing Engineer", "discogs"),
                _c("Blacko (FRA)", "Writer"),
                _c("Blacko", "Writer", "genius_api"),
            ]
        )
        assert len(groupes) == 3

    def test_deux_regions_sont_deux_personnes(self):
        assert credits_sous_deux_graphies([_c("M.A.T (FRA)"), _c("M.A.T. (USA)")]) == []

    def test_autre_role_ou_autre_detail_n_est_pas_un_doublon(self):
        assert (
            credits_sous_deux_graphies(
                [
                    _c("Studio de l'Étoile", "Other", detail="Recorded At"),
                    _c("Studio de l’Étoile", "Other", detail="Mixed At"),
                    _c("Mike Dean", "Producer"),
                    _c("MIKE DEAN", "Mixing Engineer"),
                ]
            )
            == []
        )

    def test_youtube_reste_a_l_invariant(self):
        assert (
            credits_sous_deux_graphies([_c("Lucci"), _c("Lucci’ (FRA)", source="youtube_topic")])
            == []
        )


class TestHeritageSansSource:
    def test_une_a_cappella_n_herite_plus_de_la_production(self):
        assert heritage_sans_source(
            "Stand United (Acapella)",
            [("Dem Jointz", "Producer"), ("Kanye West", "Writer")],
            [("Dem Jointz", "Producer", "genius"), ("Kanye West", "Writer", "genius")],
        ) == [("Dem Jointz", "Producer", ROLE_NON_TRANSMIS)]

    def test_un_credit_que_l_original_ne_porte_plus(self):
        """« Dreams (Remix) » : Genius a scindé « Gamble & Huff »."""
        assert heritage_sans_source(
            "Dreams (Remix)",
            [("Gamble & Huff", "Writer"), ("Kenneth Gamble", "Writer")],
            [("Kenneth Gamble", "Writer", "genius"), ("Leon Huff", "Writer", "genius")],
        ) == [("Gamble & Huff", "Writer", ABSENT_DE_L_ORIGINAL)]

    def test_un_heritage_de_l_original_ne_le_soutient_pas(self):
        assert heritage_sans_source(
            "Fugitif (Instrumental)",
            [("IAM", "Producer")],
            [("IAM", "Producer", "heritage")],
        ) == [("IAM", "Producer", ABSENT_DE_L_ORIGINAL)]

    def test_un_titre_sans_version_ni_role_inconnu(self):
        assert heritage_sans_source("Fugitif", [("IAM", "Producer")], []) == []
        assert heritage_sans_source("Fugitif (Instrumental)", [("IAM", "Inconnu")], []) == []


def _credit(dm, track_id, name, role, source):
    with dm.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO credits (track_id, name, role, source) VALUES (:t, :n, :r, :s)"),
            {"t": track_id, "n": name, "r": role, "s": source},
        )


def test_familles_de_credits_dans_l_audit(data_manager):
    a = Artist(name="Shurik’n")
    a.id = data_manager.save_artist(a)
    fiches = {}
    for titre in ("Fugitif", "Fugitif (Instrumental)", "Heartless (Studio Version)"):
        t = Track(title=titre, artist=a)
        data_manager.save_track(t)
        fiches[titre] = t.id
    _credit(data_manager, fiches["Fugitif"], "Shurik’n", "Producer", "genius")
    _credit(data_manager, fiches["Fugitif"], "Eric Chevet", "Mastering Engineer", "discogs")
    _credit(data_manager, fiches["Fugitif"], "Éric Chevet", "Mastering Engineer", "genius")
    _credit(data_manager, fiches["Fugitif (Instrumental)"], "Shurik’n", "Producer", "heritage")
    _credit(data_manager, fiches["Fugitif (Instrumental)"], "IAM", "Producer", "heritage")
    _credit(data_manager, fiches["Heartless (Studio Version)"], "No I.D.", "Producer", "heritage")
    audit = au.auditer(data_manager, ["Shurik’n"], revue=False, persistance=False)
    heritage = sorted(
        (c for c in audit.cas if c.famille.startswith("heritage_sans")), key=lambda c: c.nature
    )
    assert [(c.famille, c.nature, c.pistes) for c in heritage] == [
        (
            "heritage_sans_source",
            au.ERREUR,
            [fiches["Fugitif (Instrumental)"], fiches["Fugitif"]],
        ),
        ("heritage_sans_original", au.INFORMATION, [fiches["Heartless (Studio Version)"]]),
    ]
    # L'erreur est un INVARIANT (l'héritage se recalcule, 2026-11-10) ;
    # l'original introuvable reste une information, hors critère.
    assert "heritage_sans_source" in au.INVARIANTS
    assert "heritage_sans_original" in au.FAMILLES_HORS_CRITERE
    assert "IAM Producer" in heritage[0].libelle and "Shurik" not in heritage[0].libelle
    (graphie,) = [c for c in audit.cas if c.famille == "credit_graphie"]
    assert graphie.nature == au.INFORMATION and graphie.pistes == [fiches["Fugitif"]]
    assert "credit_graphie" in au.FAMILLES_HORS_CRITERE
    assert audit.critere() == 1


def test_production_heritee_d_une_reprise_sans_original_est_une_erreur(data_manager):
    """Chez Kid Cudi, huit « Heartless » : la reprise de Kris Allen n'a pas
    d'original unique, mais sa production héritée reste fausse (2026-11-10)."""
    a = Artist(name="Kid Cudi")
    a.id = data_manager.save_artist(a)
    fiches = {}
    for gid, titre in ((1, "Heartless"), (2, "Heartless"), (3, "Heartless (Studio Version)")):
        t = Track(title=titre, artist=a, genius_id=gid)
        data_manager.save_track(t)
        fiches[titre] = t.id
    with data_manager.engine.begin() as conn:
        conn.execute(
            text("UPDATE tracks SET relationships = :r WHERE id = :id"),
            {
                "r": '[{"type": "cover_of", "title": "Heartless", "artist": "Kanye West"}]',
                "id": fiches["Heartless (Studio Version)"],
            },
        )
    _credit(
        data_manager, fiches["Heartless (Studio Version)"], "No I.D.", "Co-Producer", "heritage"
    )
    _credit(data_manager, fiches["Heartless (Studio Version)"], "Kid Cudi", "Writer", "heritage")
    audit = au.auditer(data_manager, ["Kid Cudi"], revue=False, persistance=False)
    (cas,) = [c for c in audit.cas if c.famille == "heritage_sans_source"]
    assert cas.nature == au.ERREUR and cas.pistes == [fiches["Heartless (Studio Version)"]]
    assert "No I.D. Co-Producer" in cas.libelle and "Kid Cudi" not in cas.libelle
