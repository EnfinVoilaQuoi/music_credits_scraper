"""La PLACE d'un morceau sur un disque, et qui l'a dite (e52, 2026-10-14).

Deux fiches au même emplacement d'un même disque sont un seul morceau ; à deux
emplacements, deux morceaux — à source ÉGALE seulement : l'ordre de la page
album Genius saute les titres sans page (A2H *Art de vivre*, « Trivette » 13 →
12), il ne se compare jamais à un numéro Deezer.
"""

import sqlalchemy as sa

from src.api.deezer_api import DeezerAPI
from src.models import Artist, ReleaseObservation, Track
from src.services import revue
from src.utils.positions_disque import appoint_emplacement, position_retenue


class TestPositionRetenue:
    def test_genius_n_ecrase_jamais_deezer(self):
        assert position_retenue((1, 13, "deezer"), (1, 12, "genius")) == (1, 13, "deezer")

    def test_deezer_remplace_genius_et_legacy(self):
        assert position_retenue((None, 12, "genius"), (1, 13, "deezer")) == (1, 13, "deezer")
        assert position_retenue((None, 12, "legacy"), (1, 13, "deezer")) == (1, 13, "deezer")

    def test_une_observation_sans_numero_ne_touche_a_rien(self):
        assert position_retenue((1, 13, "deezer"), (None, None, "genius")) == (1, 13, "deezer")
        assert position_retenue(None, (None, None, "genius")) == (None, None, None)

    def test_le_disque_suit_le_numero(self):
        """Jamais le disque d'une source et le numéro d'une autre."""
        assert position_retenue((2, 5, "deezer"), (None, 5, "manual")) == (None, 5, "manual")

    def test_premiere_position(self):
        assert position_retenue(None, (1, 4, "genius")) == (1, 4, "genius")


def _p(release_id, numero, source="deezer", disque=1, titre="Art de vivre"):
    return {
        "release_id": release_id,
        "title": titre,
        "disc_number": disque,
        "track_number": numero,
        "position_source": source,
    }


class TestAppoint:
    def test_meme_place_meme_source_un_seul_morceau(self):
        """Swing « RECO30221 » / « REC030221 » : le vrai doublon de 2026-10-12."""
        dit = appoint_emplacement([_p(1, 7)], [_p(1, 7)])
        assert dit and "un seul morceau" in dit and "piste 7" in dit

    def test_deux_places_deux_morceaux(self):
        dit = appoint_emplacement([_p(1, 12)], [_p(1, 13)])
        assert dit and "deux morceaux" in dit and "12 et 13" in dit

    def test_jamais_a_sources_differentes(self):
        """« Trivette » (Genius, 12) / « Skit (Qui est trivette) » (Deezer, 12) :
        deux morceaux que Genius et Deezer numérotent pareil."""
        assert appoint_emplacement([_p(1, 12, "genius")], [_p(1, 12)]) is None

    def test_genius_et_legacy_ne_se_comparent_meme_pas_entre_eux(self):
        assert appoint_emplacement([_p(1, 12, "genius")], [_p(1, 12, "genius")]) is None
        assert appoint_emplacement([_p(1, 3, "legacy")], [_p(1, 4, "legacy")]) is None

    def test_deux_disques_differents_ne_disent_rien(self):
        assert appoint_emplacement([_p(1, 7)], [_p(2, 7)]) is None

    def test_disque_2_distingue(self):
        dit = appoint_emplacement([_p(1, 3, disque=1)], [_p(1, 3, disque=2)])
        assert dit and "deux morceaux" in dit and "2-3" in dit

    def test_la_meme_place_l_emporte(self):
        """Même place sur une parution, places distinctes sur une autre (deux
        éditions mal liées) : « un seul morceau » est rendu."""
        a = [_p(1, 4), _p(2, 9, titre="Deluxe")]
        b = [_p(1, 5), _p(2, 9, titre="Deluxe")]
        assert "un seul morceau" in appoint_emplacement(a, b)


# ── Repository : la règle tient à l'écriture ─────────────────────────────────


def _fiche(dm, titre="Trivette"):
    artist = Artist(name="A2H", deezer_id=1)
    artist.id = dm.save_artist(artist)
    track = Track(title=titre, artist=artist)
    track.id = dm.save_track(track)
    return artist, track


def _obs(source, numero, disque=None, release_id=55):
    return ReleaseObservation(
        title="Art de vivre",
        source=source,
        external_release_id=release_id,
        disc_number=disque,
        track_number=numero,
    )


def _lien(dm, track_id):
    with dm.engine.connect() as conn:
        return conn.execute(
            sa.text(
                "SELECT disc_number, track_number, position_source FROM release_tracks "
                "WHERE track_id = :t"
            ),
            {"t": track_id},
        ).one()


def test_un_numero_genius_n_ecrase_pas_celui_de_deezer(data_manager):
    _, track = _fiche(data_manager)
    data_manager.record_release_observations(track.id, [_obs("deezer", 13, 1)])
    release_id = data_manager.get_track_releases(track.id)[0]["id"]
    data_manager.link_track_to_release(release_id, track.id, track_number=12, source="genius")
    assert tuple(_lien(data_manager, track.id)) == (1, 13, "deezer")


def test_une_source_sure_remplace_un_numero_legacy(data_manager):
    _, track = _fiche(data_manager)
    release_id = data_manager.ensure_release(track.artist.id, "Art de vivre", scope="own")
    data_manager.link_track_to_release(
        release_id, track.id, track_number=12, source="legacy", matched_by="legacy"
    )
    data_manager.link_track_to_release(release_id, track.id, disc_number=1, track_number=13)
    assert tuple(_lien(data_manager, track.id)) == (1, 13, "manual")


def test_les_places_de_l_artiste_se_lisent_numerotees_seules(data_manager):
    artist, track = _fiche(data_manager)
    autre = Track(title="Skit (Qui est trivette)", artist=artist)
    autre.id = data_manager.save_track(autre)
    data_manager.record_release_observations(track.id, [_obs("deezer", 13, 1)])
    data_manager.record_release_observations(autre.id, [_obs("deezer", None)])
    positions = data_manager.get_artist_positions(artist.id)
    assert list(positions) == [track.id]
    (place,) = positions[track.id]
    assert (place["track_number"], place["position_source"]) == (13, "deezer")


def test_le_panneau_ajoute_l_appoint_au_doublon(data_manager):
    """Deux fiches au même titre (👯) : leur place sur le disque Deezer est
    ajoutée au MOTIF, la clé du cas ne bouge pas (un verdict passé tient)."""
    artist, a = _fiche(data_manager, "RECO30221")
    b = Track(title="RECO 30221", artist=artist)
    b.id = data_manager.save_track(b)
    sans = revue.detecter(revue.contexte([a, b]))
    ctx = revue.contexte([a, b])
    ctx.positions = {a.id: [_p(1, 7)], b.id: [_p(1, 7)]}
    avec = revue.detecter(ctx)
    (cas,) = [c for c in avec if c.detecteur == "doublon"]
    (avant,) = [c for c in sans if c.detecteur == "doublon"]
    assert "📀 même place" in cas.motif
    assert cas.cle == avant.cle


# ── Migration e52 ────────────────────────────────────────────────────────────


def test_la_reprise_e52_est_conservatrice(data_manager):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "e52_position_source", "alembic/versions/e52_position_source.py"
    )
    e52 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(e52)
    _, track = _fiche(data_manager)
    hors = Track(title="Autre", artist=track.artist)
    hors.id = data_manager.save_track(hors)
    sans = Track(title="Sans numéro", artist=track.artist)
    sans.id = data_manager.save_track(sans)
    release_id = data_manager.ensure_release(track.artist.id, "Art de vivre", scope="own")
    with data_manager.engine.begin() as conn:
        for tid, numero, source in (
            (track.id, 12, "legacy"),
            (hors.id, 3, "genius"),
            (sans.id, None, "genius"),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO release_tracks (release_id, track_id, track_number, source, "
                    "matched_by) VALUES (:r, :t, :n, :s, :s)"
                ),
                {"r": release_id, "t": tid, "n": numero, "s": source},
            )
        e52.reprendre(conn)
    assert _lien(data_manager, track.id)[2] == "legacy"
    # Seul Deezer écrit un numéro hors e31 : un lien Genius numéroté le tient de lui.
    assert _lien(data_manager, hors.id)[2] == "deezer"
    assert _lien(data_manager, sans.id)[2] is None


# ── Deezer : la fiche piste porte sa place ───────────────────────────────────


def test_la_fiche_piste_deezer_porte_sa_place():
    data = DeezerAPI.extract_enrichment_data(
        None, {"id": 1, "track_position": 13, "disk_number": 1, "album": {"id": 55}}
    )
    assert (data["deezer_track_position"], data["deezer_disk_number"]) == (13, 1)


def test_un_hit_de_recherche_n_invente_pas_de_place():
    data = DeezerAPI.extract_enrichment_data(None, {"id": 1, "track_position": 0})
    assert data["deezer_track_position"] is None and data["deezer_disk_number"] is None


def test_le_script_de_mesure_tourne_sur_une_base(data_manager, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("mp", "scripts/mesurer_positions.py")
    mp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mp)
    artist, a = _fiche(data_manager, "RECO30221")
    b = Track(title="RECO 30221", artist=artist)
    b.id = data_manager.save_track(b)
    for t in (a, b):
        data_manager.record_release_observations(t.id, [_obs("deezer", 7, 1)])
    liens = mp._liens(data_manager, "A2H")
    mp.densite(liens)
    mp.places_partagees(liens)
    mp.panneau(data_manager, "A2H")
    sortie = capsys.readouterr().out
    assert "dont à source égale et comparable (preuve d'appoint) : 1" in sortie
    assert "avec une place en appoint : 1" in sortie


def test_les_ids_du_disque_excluent_les_titres_generiques(data_manager):
    """Références de pochette : les pistes du disque, jamais une autre
    « Intro » (elle-même à prouver)."""
    artist = Artist(name="Diam's")
    artist.id = data_manager.save_artist(artist)
    for titre, album, sid in (
        ("Intro", "Premier mandat", "I" * 22),
        ("Marine", "Premier mandat", "M" * 22),
        ("Enfants du désert", "Brut de femme", "E" * 22),
        ("Sans ID", "Premier mandat", None),
    ):
        t = Track(title=titre, artist=artist, album=album)
        t.spotify_id = sid
        data_manager.save_track(t)
    assert data_manager.ids_spotify_du_disque(artist.id, "PREMIER MANDAT") == ["M" * 22]


def test_fiches_deja_rattachees_par_une_source(data_manager):
    artist, track = _fiche(data_manager)
    autre = Track(title="Autre", artist=artist)
    autre.id = data_manager.save_track(autre)
    data_manager.record_release_observations(track.id, [_obs("apple_music", 4, 1, release_id=9)])
    data_manager.record_release_observations(autre.id, [_obs("deezer", 5, 1)])
    assert data_manager.track_ids_avec_parution(artist.id, "apple_music") == {track.id}


def test_spotify_web_ne_dit_pas_le_disque():
    """2026-10-14 : Spotify ne donne que l'ORDRE (la 11ᵉ piste d'un double
    album est la 1 du disque 2) — rangé sous Genius, jamais comparé, et ne
    remplace jamais une place Deezer ou Discogs."""
    from src.utils.positions_disque import SOURCES_COMPARABLES

    assert "spotify_web" not in SOURCES_COMPARABLES
    assert position_retenue((2, 1, "deezer"), (1, 11, "spotify_web")) == (2, 1, "deezer")
    assert position_retenue((None, 3, "discogs"), (1, 11, "spotify_web")) == (None, 3, "discogs")
    assert appoint_emplacement([_p(1, 11, "spotify_web")], [_p(1, 11, "spotify_web")]) is None


def test_deezer_et_apple_se_comparent_entre_eux():
    """2026-10-14 : même code-barres, même place ⇒ même morceau 2 519 fois
    sur 2 520 (Deezer / Apple)."""
    dit = appoint_emplacement([_p(1, 7, "deezer")], [_p(1, 7, "apple_music")])
    assert dit and "un seul morceau" in dit
    dit = appoint_emplacement([_p(1, 7, "deezer")], [_p(1, 8, "apple_music")])
    assert dit and "deux morceaux" in dit
    assert appoint_emplacement([_p(1, 7, "deezer")], [_p(1, 7, "musicbrainz")]) is None


class TestMemePlace:
    """📀 `meme_place` (2026-10-30) : deux fiches au même emplacement d'un même
    disque — un seul morceau, ou un lien de parution faux. Mesuré sur toute la
    base : 7 paires, 3 doublons (fiche Genius / fiche sans page), 3 liens
    faux, 1 à juger par Genius ; aucun des 13 👯 n'a de place en commun."""

    @staticmethod
    def _fiche(titre, tid, duree, genius_id=None):
        t = Track(title=titre, artist=Artist(name="Kid Cudi"))
        t.id, t.duration, t.genius_id = tid, duree, genius_id
        return t

    def _ctx(self, *fiches, places):
        ctx = revue.contexte(fiches)
        ctx.positions = places
        return ctx

    def test_cle_d_emplacement_deezer_et_apple_confondus(self):
        from src.utils.positions_disque import cle_d_emplacement

        assert cle_d_emplacement(_p(1, 7, "deezer")) == cle_d_emplacement(_p(1, 7, "apple_music"))
        assert cle_d_emplacement(_p(1, 7, "genius")) is None
        assert cle_d_emplacement(_p(1, None)) is None

    def test_fiche_genius_et_fiche_sans_page_a_la_meme_place(self):
        """Kid Cudi « Heaven at Nite » / « Heaven At Night » (fiche Deezer d'une
        autre édition de *A Kid Named Cudi*) : même place, même durée."""
        a = self._fiche("Heaven at Nite", 1, 196, genius_id=4052)
        b = self._fiche("Heaven At Night", 2, 196)
        places = {1: [_p(9, 16, "apple_music")], 2: [_p(9, 16, "deezer")]}
        ctx = self._ctx(a, b, places=places)
        motif, preuves = revue.meme_place(a, ctx)
        assert "un seul morceau" in motif and preuves["autres"] == [2]
        assert revue.meme_place(b, ctx) is None  # un cas par paire

    def test_durees_ecartees_un_lien_faux(self):
        """« Par Amour » de Dinos (216 s) rangé à la place de « Par amour » de
        Diam's (403 s) sur *Dans ma bulle*."""
        a = self._fiche("Par Amour", 1, 216, genius_id=1)
        b = self._fiche("Par amour", 2, 403, genius_id=2)
        a.is_featuring, a.primary_artist_name = True, "Dinos"
        ctx = self._ctx(a, b, places={1: [_p(9, 9)], 2: [_p(9, 9, "apple_music")]})
        assert "lien de parution est faux" in revue.meme_place(a, ctx)[0]

    def test_deux_versions_un_lien_faux(self):
        a = self._fiche("Loup noir", 1, 160, genius_id=1)
        b = self._fiche("Loup noir (A COLORS SHOW)", 2, 162, genius_id=2)
        ctx = self._ctx(a, b, places={1: [_p(9, 1)], 2: [_p(9, 1, "apple_music")]})
        assert "deux versions" in revue.meme_place(a, ctx)[0]

    def test_ni_place_differente_ni_autre_parution_ni_meme_titre(self):
        a = self._fiche("A", 1, 100, genius_id=1)
        b = self._fiche("B", 2, 100, genius_id=2)
        c = self._fiche("a", 3, 100, genius_id=3)  # même titre : aux 👯
        places = {1: [_p(9, 1)], 2: [_p(9, 2), _p(8, 1)], 3: [_p(9, 1, "apple_music")]}
        ctx = self._ctx(a, b, c, places=places)
        assert revue.meme_place(a, ctx) is None
        assert revue.meme_place(b, ctx) is None

    def test_numeros_non_comparables_ignores(self):
        a = self._fiche("A", 1, 100, genius_id=1)
        b = self._fiche("B", 2, 100, genius_id=2)
        ctx = self._ctx(a, b, places={1: [_p(9, 1, "genius")], 2: [_p(9, 1, "genius")]})
        assert revue.meme_place(a, ctx) is None

    def test_le_cas_ouvre_la_fusion(self):
        from src.services.revue_actions import actions_pour

        cas = revue.Cas("meme_place", 1, "A", "m", 0, {"autres": [2]}, "k")
        assert [a.code for a in actions_pour(cas)] == ["fusionner"]
