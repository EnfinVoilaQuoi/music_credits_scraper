"""`scripts/fusionner_copies_de_groupe.py` : le juge des copies d'un groupe chez un membre."""

from scripts.fusionner_copies_de_groupe import juger
from src.models import Artist, Track
from src.models.track import Credit, CreditRole


def _t(tid, titre, artiste, **kw):
    t = Track(title=titre, artist=Artist(name=artiste))
    t.id = tid
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _juger(du_membre, du_groupe):
    return {
        (c.copie_id, c.garde_id, c.motif)
        for c in juger("Akhenaton", "IAM", du_membre, du_groupe, {"Akhenaton", "AKH"})
    }


def test_copie_sans_page_au_meme_id_deezer():
    groupe = [_t(10, "Harley Davidson", "IAM", deezer_id=3129805)]
    membre = [_t(50, "Harley Davidson", "Akhenaton", deezer_id=3129805)]
    assert _juger(membre, groupe) == {(50, 10, "même ID Deezer")}


def test_copie_sans_page_au_meme_isrc():
    groupe = [_t(10, "La face B (Chill Remix)", "IAM", deezer_id=1, isrc="FRY689610001")]
    membre = [_t(50, "La face B (Chill Remix)", "Akhenaton", deezer_id=2, isrc="fry689610001")]
    assert _juger(membre, groupe) == {(50, 10, "même ISRC")}


def test_page_du_groupe_ou_le_membre_n_est_pas_credite():
    groupe = [_t(10, "Lettre", "IAM", genius_id=72994)]
    membre = [_t(50, "Lettre", "Akhenaton", genius_id=72994)]
    assert _juger(membre, groupe) == {(50, 10, "même page, non crédité")}


def test_membre_credite_garde_sa_ligne_soeur():
    page = _t(10, "Le couteau entre les dents", "IAM", genius_id=530660)
    page.credits.append(Credit(name="Akhenaton", role=CreditRole.WRITER, source="genius"))
    membre = [_t(50, "Le couteau entre les dents", "Akhenaton", genius_id=530660)]
    assert _juger(membre, [page]) == set()


def test_ligne_du_membre_creee_avant_celle_du_groupe_n_est_pas_une_copie():
    """JACKBOYS chez Travis Scott : sa discographie Genius a créé ses lignes avant
    que le groupe n'entre en base ; sans crédit de performance, elles sont justes."""
    groupe = [_t(28658, "110 SOUTH", "JACKBOYS", genius_id=5)]
    membre = [_t(12245, "110 SOUTH", "Travis Scott", genius_id=5)]
    assert _juger(membre, groupe) == set()
    groupe = [_t(28876, "This That", "Go Getters", deezer_id=7)]
    assert _juger([_t(100, "This That", "Kanye West", deezer_id=7)], groupe) == set()


def test_plusieurs_jumelles_ambigu_laisse():
    groupe = [_t(10, "Intro", "IAM"), _t(11, "Intro", "IAM")]
    assert _juger([_t(50, "Intro", "Akhenaton")], groupe) == set()
