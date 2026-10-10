"""Une certification appartient-elle à CE morceau ? (CORRECTIFS 1.9, 2026-10-09)

Cas réels de l'audit : les refus sont des certifs qui étaient rattachées à tort.
"""

import pytest

from src.models import Artist, Track
from src.utils.cert_titres import certif_du_morceau
from src.utils.certification_enricher import apply_certifications


@pytest.mark.parametrize(
    ("fiche", "certif"),
    [
        ("Sale mood", "SALE MOOD"),
        ("Cavaliero (Feat. Koba LaD)", "CAVALIERO"),
        ("Paranoid", "PARANOID (FEAT. MR HUDSON)"),
        ("Father Stretch My Hands, Pt. 1", "FATHER STRETCH MY HANDS PT. 1"),
        ("Je danse le mia", "JE DANSE LE MIA REMAST 2016"),
        ("Qui sème le vent récolte le tempo", "QUI SEME LE VENT"),  # coupé par la source
        ("L’empire du côté obscur", "L'empire du c"),  # coupé en plein mot
        ("Make Her Say (I Poke Her Face)", "MAKE HER SAY"),  # sous-titre côté fiche
        ("Day ‘N’ Nite", "DAY 'N' NITE (NIGHTMARE)"),  # sous-titre côté certif
        ("Suzy", "SUZY (LIVE 2006)"),  # version sans fiche : l'original la garde
    ],
)
def test_admis(fiche, certif):
    assert certif_du_morceau(fiche, certif)


@pytest.mark.parametrize(
    ("fiche", "certif"),
    [
        ("Life", "TUBA LIFE"),
        ("92i", "92I VEYRON"),
        ("La Mort", "LA MORT LEUR VA SI BIEN"),
        ("La zone", "J'FAIS PLAISIR À LA ZONE"),
        ("Oublie", "JE N'OUBLIE PAS"),
        ("Caroline", "COMME CAROLINE"),
        ("Scellé", "SCELLÉ PART.2"),
        ("Bande organisée", "BANDE ORGANISÉE 2"),
        ("Remote Control pt 2", "REMOTE CONTROL"),
        ("Drill FR 4", "DRILL FR"),
        ("Bad Boys de Marseille (Part. 2)", "BAD BOYS DE MARSEILLE"),
        ("Stronger (Snippet)", "STRONGER"),
        ("Jesus Walks (Orchestral)", "JESUS WALKS"),
        ("Desiigner - A COLORS SHOW", "DESIIGNER"),
        ("Up", "PICK UP THE PHONE"),
    ],
)
def test_refuse(fiche, certif):
    assert not certif_du_morceau(fiche, certif)


class _Matcher:
    def __init__(self, certifs):
        self.certifs = certifs

    def get_track_certifications(self, artist, title, extra_artists=None):
        return list(self.certifs)

    def get_album_certifications(self, artist, album):
        return []


def _certif(titre, categorie="single"):
    return {
        "certification": "Diamant",
        "title": titre,
        "artist_name": "BOOBA",
        "category": categorie,
        "certification_date": "2022-07-21",
        "release_date": "2017-01-01",
        "publisher": "X",
        "detail_url": "",
        "country": "FR",
        "body": "SNEP",
        "flag": "",
    }


def test_une_certif_d_album_ne_va_pas_sur_le_morceau_eponyme():
    """Booba « Trône » prenait le Diamant de l'ALBUM *Trône*."""
    booba = Artist(name="Booba")
    trone = Track(title="Trône", artist=booba)
    apply_certifications(booba, [trone], _Matcher([_certif("TRÔNE", "album")]))
    assert trone.certs.entries == []


def test_la_fiche_au_titre_exact_garde_seule_la_certif():
    """« Mr. Rager (Prime Day Show x Kid Cudi) » : descripteur inconnu, mais la
    fiche « Mr. Rager » existe — elle seule reçoit « MR RAGER »."""
    cudi = Artist(name="Kid Cudi")
    original = Track(title="Mr. Rager", artist=cudi)
    live = Track(title="Mr. Rager (Prime Day Show x Kid Cudi)", artist=cudi)
    apply_certifications(cudi, [original, live], _Matcher([_certif("MR RAGER")]))
    assert len(original.certs.entries) == 1 and live.certs.entries == []


def test_certif_d_une_version_portee_par_l_original():
    """CORRECTIFS 1.16 : la fiche morceau le dit (« ↩ certif de … »)."""
    from src.utils.cert_titres import certif_d_une_version

    assert certif_d_une_version("Dolce Camara", "DOLCE CAMARA (SNIGHT B REMIX)")
    assert not certif_d_une_version("Dolce Camara", "DOLCE CAMARA")
    assert not certif_d_une_version(
        "Dolce Camara (Snight B Remix)", "DOLCE CAMARA (SNIGHT B REMIX)"
    )


def test_une_place_dans_le_projet_n_est_pas_un_sous_titre():
    """« ALL OF THE LIGHTS (INTERLUDE) » est l'Interlude, un autre morceau ;
    « Matrix (Intro) » côté fiche reste le morceau Matrix (2026-10-10)."""
    from src.utils.cert_titres import certif_du_morceau

    assert not certif_du_morceau("All of the Lights", "ALL OF THE LIGHTS (INTERLUDE)")
    assert certif_du_morceau("Make Her Say (I Poke Her Face)", "MAKE HER SAY")
