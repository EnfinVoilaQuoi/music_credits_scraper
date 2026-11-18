"""Étape crédits : une fiche apportée par une formation se sauve chez SON artiste.

2026-11-17 : la fin de l'étape crédits d'Akhenaton réécrivait au nom du membre
toutes les fiches lues (`discographie_reunie` y met celles d'IAM) ; `save_track`
ne les trouvait pas chez lui et en créait des copies — 54 fiches sans page et
18 lignes de pages, puis 9 chez Rim’K (113). Les doublons du panneau et
`isrc_edition_autre_fiche` en sont nés.
"""

from src.models import Artist, ArtistRelation, Track
from src.services import credits
from src.services.runtime import Runtime


def _artiste(dm, nom: str) -> Artist:
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _fiche(dm, artiste: Artist, titre: str, **kw) -> Track:
    t = Track(title=titre, artist=artiste)
    for k, v in kw.items():
        setattr(t, k, v)
    t.id = dm.save_track(t)
    return t


def _lignes(dm, artiste: Artist) -> list[str]:
    return sorted(t.title for t in dm.get_artist_tracks(artiste.id))


def test_la_fiche_du_groupe_reste_au_groupe(data_manager):
    dm = data_manager
    iam, akh = _artiste(dm, "IAM"), _artiste(dm, "Akhenaton")
    dm.record_artist_relations(
        akh.id, [ArtistRelation(related_name="IAM", kind="member_of", formation="groupe")]
    )
    _fiche(dm, iam, "Harley Davidson", deezer_id=3129805)
    _fiche(dm, iam, "Nés sous la même étoile", genius_id=59311)
    _fiche(dm, akh, "Mon texte, le savon", genius_id=101)
    akh.tracks = dm.discographie_reunie(akh)
    assert len(akh.tracks) == 3

    credits._sauver(
        Runtime(data_manager=dm, genius_api=None, data_enricher=None, deleted=None, disabled=None),
        akh,
        akh.tracks,
        credits.BilanCredits(morceaux=3),
    )

    assert _lignes(dm, akh) == ["Mon texte, le savon"]
    assert _lignes(dm, iam) == ["Harley Davidson", "Nés sous la même étoile"]


def test_une_fiche_sans_artiste_enregistre_prend_celui_du_run():
    """Le cas des objets neufs (tests, fiches sans id d'artiste) : inchangé."""
    t = Track(title="A", artist=Artist(name="Swing"))
    run_artist = Artist(name="Swing")
    vus = []

    class _DM:
        def save_track(self, tr):
            vus.append(tr.artist)

        def record_pending(self, tr):
            pass

    credits._sauver(
        Runtime(
            data_manager=_DM(), genius_api=None, data_enricher=None, deleted=None, disabled=None
        ),
        run_artist,
        [t],
        credits.BilanCredits(morceaux=1),
    )
    assert vus == [run_artist]
