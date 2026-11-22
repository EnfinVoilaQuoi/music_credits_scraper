"""Règle 4 (décision utilisateur 2026-11-22) : `paroles_autre_fiche` compare
aussi les textes de repli aux paroles Genius des AUTRES artistes, lignes sœurs
exclues (HORS_AUDIT du 2026-11-10 : Booba « Lollipop (French Remix) »)."""

from sqlalchemy import text

from src.models import Artist, Track
from src.services import revue
from src.utils import paroles_entre_artistes as pe

LIGNES = [
    f"ligne numéro {i} d'un texte assez long pour être cherché mot{i} fin{i}" for i in range(30)
]
GENIUS = "\n".join(LIGNES)


def _repli(titre, tid, texte=GENIUS, genius_id=None):
    t = Track(title=titre)
    t.id, t.genius_id = tid, genius_id
    t.lyrics.text, t.lyrics.source = texte, "Source: Musixmatch"
    return t


def _ctx(track, candidates):
    ctx = revue.contexte([track], {}, [])
    ctx.paroles_autres_artistes = {track.id: candidates}
    return ctx


def _candidate(**kw):
    x = {
        "autre": 6160,
        "title": "No More Parties in LA",
        "genius_id": 2393451,
        "genius_url": "https://genius.com/Kanye-west-no-more-parties-in-la-lyrics",
        "relationships": "[]",
        "artiste": "Kanye West",
        "part": 0.9,
        "couvert": 0.7,
        "texte": "paroles YouTube Music",
    }
    return {**x, **kw}


class TestDetecteur:
    def test_paroles_d_une_fiche_d_un_autre_artiste(self):
        """The Game « No More L.A. » : paroles YTM de Kanye West (90 %)."""
        x = _repli("No More L.A.", 16189)
        motif, preuves = revue.paroles_d_une_autre_fiche(x, _ctx(x, [_candidate()]))
        assert "de Kanye West" in motif and preuves["autre"] == 6160
        assert preuves["artiste_autre"] == "Kanye West"

    def test_sous_les_seuils(self):
        x = _repli("No More L.A.", 16189)
        for kw in ({"part": 0.7}, {"couvert": 0.3}):
            assert revue.paroles_d_une_autre_fiche(x, _ctx(x, [_candidate(**kw)])) is None

    def test_un_meme_titre_de_base_excuse(self):
        """Simulé : sans cette excuse, 7 cas sur 8 étaient le même morceau
        sous deux pages ou un remix et son solo."""
        x = _repli("On va te douiller", 32542)
        c = _candidate(title="On va te douiller", artiste="Sefyu")
        assert revue.paroles_d_une_autre_fiche(x, _ctx(x, [c])) is None

    def test_une_relation_genius_excuse(self):
        x = _repli("Chose", 1)
        x.relationships = [{"type": "remix_of", "url": _candidate()["genius_url"]}]
        assert revue.paroles_d_une_autre_fiche(x, _ctx(x, [_candidate()])) is None

    def test_la_ligne_soeur_n_est_pas_une_autre_fiche(self):
        x = _repli("No More L.A.", 16189, genius_id=2393451)
        assert revue.paroles_d_une_autre_fiche(x, _ctx(x, [_candidate()])) is None


def test_lignes_sans_en_tetes_ni_horodatages():
    assert pe.lignes("[Couplet 1]\n[00:12.30] " + LIGNES[0] + "\ncourt") == {LIGNES[0].lower()}
    assert pe.lignes("J’arrive " + "x" * 30) == {"j'arrive " + "x" * 30}


def test_candidates_par_la_base(data_manager):
    a, b = Artist(name="Kanye West"), Artist(name="The Game")
    a.id, b.id = data_manager.save_artist(a), data_manager.save_artist(b)
    fiches = {}
    for artiste, titre, gid in (
        (a, "No More Parties in LA", 2393451),
        (b, "No More Parties in LA", 2393451),  # ligne sœur chez The Game
        (b, "No More L.A.", 999),
        (b, "Autre du même artiste", 998),
    ):
        t = Track(title=titre, artist=artiste)
        t.genius_id = gid
        data_manager.save_track(t)
        fiches[(artiste.name, titre)] = t
    with data_manager.engine.begin() as conn:
        for t in (
            fiches[("Kanye West", "No More Parties in LA")],
            fiches[("The Game", "No More Parties in LA")],
            fiches[("The Game", "Autre du même artiste")],
        ):
            conn.execute(
                text("UPDATE tracks SET lyrics = :p, lyrics_source = 'genius' WHERE id = :t"),
                {"p": GENIUS, "t": t.id},
            )
    cible = fiches[("The Game", "No More L.A.")]
    out = data_manager.paroles_d_autres_artistes(
        b.id, {cible.id: [("paroles YouTube Music", GENIUS, 999)]}
    )
    (x,) = out[cible.id]
    assert x["autre"] == fiches[("Kanye West", "No More Parties in LA")].id
    assert x["artiste"] == "Kanye West" and x["part"] == 1.0
    # La page elle-même (lignes sœurs) n'est jamais une candidate.
    out = data_manager.paroles_d_autres_artistes(
        b.id, {cible.id: [("paroles YouTube Music", GENIUS, 2393451)]}
    )
    assert out == {}
