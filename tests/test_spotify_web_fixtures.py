"""Parseurs Spotify web rejoués OFFLINE sur des pages réelles.

Quand Spotify change son rendu, c'est ici que ça casse, et le test rouge pointe
le parseur exact (procédure `docs/maintenance-sources.md` : re-capturer avec
`scripts/capture_fixtures.py --only spotify_web`, puis relancer ce fichier).

Sentinelle **ISHA**, choisie exprès : c'est l'artiste sur lequel le projet s'est
déjà trompé d'identité (streams de Limsa d'Aulnay écrits sur Isha, JOURNAL
2026-07-02), et ses « Recommandés » sont pleins de Limsa d'Aulnay.
"""

from pathlib import Path

import pytest

from src.scrapers import spotify_web_parse as parse

_FIXTURES = Path(__file__).parent / "fixtures" / "spotify_web"

#: Les valeurs de la capture. Elles bougeront au prochain rescrape — les tests
#: qui les emploient vérifient donc des PROPRIÉTÉS (ordre de grandeur, cohérence)
#: et non l'égalité, sauf pour ce qui est structurellement stable (l'identité).
_TRACK_ID = "3EDDunSmj8RbiVGU0Qr0M8"  # ISHA — CR600 (Bonus Track)


def _fixture(nom: str) -> str:
    chemin = _FIXTURES / nom
    if not chemin.exists():  # pragma: no cover — capture jamais lancée
        pytest.skip(f"fixture absente : {chemin} (scripts/capture_fixtures.py --only spotify_web)")
    return chemin.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def page_titre() -> str:
    return _fixture("track.html")


@pytest.fixture(scope="module")
def page_artiste() -> str:
    return _fixture("artist.html")


# ── Nombres ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "texte,attendu",
    [
        ("8 186 033", 8186033),  # U+202F : le séparateur RÉELLEMENT servi
        ("387 860", 387860),
        ("12 345", 12345),  # U+00A0, par prudence
        ("2 488 419", 2488419),  # espace ordinaire
        ("1234", 1234),  # sans séparateur
    ],
)
def test_compteur_lu_quel_que_soit_l_espace(texte, attendu):
    assert parse.parse_playcount_number(texte) == attendu


@pytest.mark.parametrize("texte", ["3:54", "1,2 M", "1.2M", "", None, "abc", "—"])
def test_compteur_rejete_ce_qui_n_est_pas_un_entier(texte):
    """REJETER plutôt qu'arrondir : une valeur abrégée n'est pas un compteur, et
    l'accepter fabriquerait un faux précis."""
    assert parse.parse_playcount_number(texte) is None


# ── Page titre ────────────────────────────────────────────────────────────────
def test_page_titre_rend_son_compteur(page_titre):
    valeur = parse.parse_main_playcount(page_titre)
    assert valeur is not None and valeur > 1_000


def test_page_titre_recolte_toutes_ses_lignes(page_titre):
    """Une page titre expose son morceau PLUS 5 recommandés et 5 à 10 titres
    populaires : c'est ce qui rend une visite rentable malgré l'absence de
    compteur sur les pages album."""
    recolte = parse.parse_row_playcounts(page_titre)
    assert len(recolte) >= 10
    assert all(len(tid) == 22 for tid in recolte)
    assert all(isinstance(v, int) and v > 0 for v in recolte.values())


def test_recolte_rattache_le_morceau_de_la_page(page_titre):
    """Le compteur principal n'a pas de lien vers lui-même : c'est l'appelant qui
    fournit l'ID de la page."""
    recolte = parse.harvest_playcounts(page_titre, page_track_id=_TRACK_ID)
    assert recolte[_TRACK_ID] == parse.parse_main_playcount(page_titre)


def test_la_recolte_ne_filtre_pas_par_artiste(page_titre):
    """Le parseur RAPPORTE, il n'attribue pas. Les recommandations d'une page
    d'ISHA contiennent des morceaux qui ne sont pas à lui — c'est à
    l'orchestrateur de ne retenir que les IDs connus en base, jamais au parseur
    de deviner à qui appartient une ligne."""
    recolte = parse.parse_row_playcounts(page_titre)
    assert len(recolte) > 5  # bien plus que les seuls titres de l'artiste


# ── Page artiste ──────────────────────────────────────────────────────────────
def test_auditeurs_mensuels(page_artiste):
    """Seule valeur sans `data-testid` (classes CSS hachées) : elle s'accroche au
    texte, donc à la locale — d'où l'épinglage `fr-FR` côté navigateur."""
    valeur = parse.parse_monthly_listeners(page_artiste)
    assert valeur is not None and valeur > 1_000


def test_page_artiste_donne_son_identite(page_artiste):
    assert parse.parse_page_artist_name(page_artiste) == "ISHA"


def test_page_artiste_porte_la_discographie(page_artiste):
    """La page artiste liste les albums : la route `/discography/album` ne rend
    aucune carte (vérifié en session live), elle est inutile."""
    albums = parse.parse_albums(page_artiste)
    assert len(albums) >= 5
    assert all(len(aid) == 22 for aid in albums)


def test_les_albums_portent_leur_titre(page_artiste):
    """Le titre vient AVEC l'id : c'est lui qui permet d'écarter un album hors
    discographie sans payer l'ouverture de sa page."""
    albums = parse.parse_albums(page_artiste)
    assert "Bitume Caviar (vol.2)" in albums.values()
    assert all(titre for titre in albums.values())


def test_page_artiste_n_a_pas_de_compteur_principal(page_artiste):
    """`data-testid="playcount"` n'existe QUE sur une page titre : le top de la
    page artiste passe par les lignes."""
    assert parse.parse_main_playcount(page_artiste) is None
    assert len(parse.parse_row_playcounts(page_artiste)) >= 5


# ── Le piège du rang ──────────────────────────────────────────────────────────
_LIGNE_ALBUM = """
<div data-testid="tracklist-row">
  <div>1</div>
  <div><a href="/intl-fr/track/3EDDunSmj8RbiVGU0Qr0M8">Un titre</a></div>
  <div>3:54</div>
</div>
"""

_LIGNE_ARTISTE = """
<div data-testid="tracklist-row">
  <div>2</div>
  <div><a href="/intl-fr/track/4g3aNSz1rMRTrHMYXzvAz6">Un titre</a></div>
  <div>8 186 033</div>
  <div>2:31</div>
</div>
"""


def test_le_rang_n_est_jamais_pris_pour_un_compteur():
    """Sur une page ALBUM les cellules sont `[rang, titre, durée]` : sans la règle
    du premier enfant, le rang « 1 » serait écrit comme un nombre de streams —
    une valeur parfaitement plausible, donc indétectable en aval."""
    assert parse.parse_row_playcounts(_LIGNE_ALBUM) == {}


def test_le_compteur_est_lu_malgre_la_colonne_de_rang():
    """La contrepartie : quand un compteur EXISTE derrière un rang, il doit sortir
    — la position de la cellule varie d'une page à l'autre."""
    assert parse.parse_row_playcounts(_LIGNE_ARTISTE) == {"4g3aNSz1rMRTrHMYXzvAz6": 8186033}


def test_href_relatif_accepte():
    """Les hrefs sont RELATIFS (`/intl-fr/track/…`). Exiger « spotify » dans l'URL
    est ce qui a rendu la source muette le 2026-07-18 : zéro résultat, aucune
    erreur."""
    assert "4g3aNSz1rMRTrHMYXzvAz6" in parse.parse_track_ids(_LIGNE_ARTISTE)


# ── Identité d'une page titre (2026-09-21) ────────────────────────────────────


@pytest.fixture(scope="module")
def page_remix() -> str:
    return _fixture("track_remix.html")


def test_identite_d_un_remix_a_trois_artistes(page_remix):
    """« Dolce Camara - Snight B Remix » : TOUS les crédités, le single, la date
    complète, le label — ce qui fait qu'une ligne créée depuis Kworb n'est pas vide."""
    ident = parse.parse_track_identity(page_remix)
    assert ident["name"] == "Dolce Camara - Snight B Remix"
    assert ident["artists"] == ["Booba", "Snight B", "SDM"]
    assert ident["album"] == "Dolce Camara (Snight B Remix)"
    assert ident["album_id"] == "1SjL9H0lVR2gmsqADe3FMC"
    assert ident["release_date"] == "2024-04-25"
    assert ident["year"] == 2024
    assert ident["duration"] == 144
    assert ident["labels"] == {"©": "Tallac Records", "℗": "Tallac Records"}


def test_identite_d_un_morceau_d_album(page_titre):
    ident = parse.parse_track_identity(page_titre)
    assert ident["name"] == "CR600 - Bonus Track"
    assert ident["artists"] == ["ISHA"]
    assert ident["album"] == "Bitume Caviar (vol.1)"
    assert ident["release_date"] == "2024-12-01"
    assert ident["duration"] == 180


def test_une_page_non_rendue_ne_donne_pas_d_identite():
    assert parse.parse_track_identity("<html><title>Spotify – Web Player</title></html>") is None


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("25 avril 2024", "2024-04-25"),
        ("1 décembre 2024", "2024-12-01"),
        ("25 April 2024", None),  # locale non épinglée : on ne devine pas
        ("2024", None),
    ],
)
def test_date_en_toutes_lettres(texte, attendu):
    assert parse.parse_date_fr(texte) == attendu
