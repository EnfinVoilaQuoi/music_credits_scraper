"""Compléter les albums de l'artiste par leur tracklist Genius (2026-09-24).

Cas réel : MC Solaar, *Qui sème le vent récolte le tempo* — l'« Intro » et
« Funky Dreamer » sont signés Jimmy Jay chez Genius, l'« Interlude » Boom Bass.
Absents de `/artists/{id}/songs`, ce sont pourtant des titres de SON album.
"""

from datetime import datetime

from src.models import Artist, Track
from src.services import tracklists_genius as tg
from src.utils.version_descriptors import titre_generique

SOLAAR = 1310
ALBUM = {"id": 12001, "name": "Qui sème le vent récolte le tempo", "primary_artist_ids": {SOLAAR}}


def _artiste():
    return Artist(name="MC Solaar", id=7, genius_id=SOLAAR)


def _song(gid, titre, primaire, pid, invites=(), date=(1991, 10, 15)):
    return {
        "id": gid,
        "title": titre,
        "url": f"https://genius.com/{gid}",
        "primary_artist": {"id": pid, "name": primaire},
        "primary_artists": [{"id": pid, "name": primaire}],
        "featured_artists": [{"id": i, "name": f"x{i}"} for i in invites],
        "release_date_components": dict(zip(("year", "month", "day"), date, strict=True)),
    }


class _Genius:
    def __init__(self, albums, tracklists):
        self.albums, self.tracklists = albums, tracklists
        self.appels = []

    def album_du_morceau(self, song_id):
        self.appels.append(("album", song_id))
        return self.albums.get(song_id)

    def tracklist_album(self, album_id):
        self.appels.append(("tracks", album_id))
        return self.tracklists.get(album_id)


def _connu(gid, titre, album=ALBUM["name"], feat=False):
    t = Track(title=titre, artist=_artiste(), genius_id=gid, album=album, is_featuring=feat)
    return t


TRACKLIST = [
    {"number": 1, "song": _song(378624, "Intro", "Jimmy Jay", 99)},
    {"number": 10, "song": _song(31147, "Caroline", "MC Solaar", SOLAAR)},
    {"number": 16, "song": _song(378628, "Funky Dreamer", "Jimmy Jay", 99)},
]


def test_un_titre_signe_par_un_autre_devient_morceau_principal():
    genius = _Genius({31147: ALBUM}, {12001: TRACKLIST})
    c = tg.completer(genius, _artiste(), [_connu(31147, "Caroline")])
    assert [(t.title, t.track_number) for t in c.pistes] == [("Intro", 1), ("Funky Dreamer", 16)]
    intro = c.pistes[0]
    # Décision utilisateur : son album, sa DA — principal, l'artiste Genius visible.
    assert intro.is_featuring is False and intro.primary_artist_name == "Jimmy Jay"
    assert intro.album == ALBUM["name"] and intro.release_date == datetime(1991, 10, 15)
    assert intro.release_observations[0].external_release_id == 12001
    assert c.albums_lus == 1


def test_l_artiste_en_featuring_reste_un_feat():
    piste = {"number": 2, "song": _song(5, "Duo", "Jimmy Jay", 99, invites=(SOLAAR,))}
    t = tg.piste_depuis_tracklist(piste, ALBUM, _artiste())
    assert t.is_featuring is True and t.primary_artist_name == "Jimmy Jay"


def test_un_titre_de_l_artiste_omis_par_la_liste_est_rattrape_sans_signataire():
    piste = {"number": 3, "song": _song(6, "Vas-y chante", "MC Solaar", SOLAAR)}
    t = tg.piste_depuis_tracklist(piste, ALBUM, _artiste())
    assert t.is_featuring is False and t.primary_artist_name is None


def test_l_album_d_un_autre_n_est_jamais_complete():
    """Une compilation où l'artiste a un titre principal n'est pas SON projet."""
    compil = {"id": 5, "name": "Stolen Moments", "primary_artist_ids": {42}}
    genius = _Genius({31147: compil}, {5: TRACKLIST})
    c = tg.completer(genius, _artiste(), [_connu(31147, "Caroline", album="Stolen Moments")])
    assert c.pistes == [] and c.albums_etrangers == 1
    assert ("tracks", 5) not in genius.appels


def test_un_feat_ne_sert_pas_de_graine():
    genius = _Genius({31147: ALBUM}, {12001: TRACKLIST})
    c = tg.completer(genius, _artiste(), [_connu(31147, "Caroline", feat=True)])
    assert c.pistes == [] and genius.appels == []


def test_exclus_jamais_recrees_et_album_complet_non_relu():
    genius = _Genius({31147: ALBUM}, {12001: TRACKLIST})
    connus = [_connu(31147, "Caroline"), _connu(378624, "Intro")]
    c = tg.completer(genius, _artiste(), connus, exclus={378628})
    assert c.pistes == []
    # Second passage : l'album est complet (supprimé compris) → aucun appel.
    genius.appels.clear()
    tg.completer(genius, _artiste(), connus, exclus={378628})
    assert genius.appels == []


def test_une_tracklist_illisible_est_un_echec_pas_un_album_vide():
    genius = _Genius({31147: ALBUM}, {})
    c = tg.completer(genius, _artiste(), [_connu(31147, "Caroline")])
    assert c.echecs == 1 and c.pistes == []


def test_titre_generique():
    for t in ("Intro", "Interlude *", "Intro (0.9)", "Interlude ****** (Pt. 3)", "Outro"):
        assert titre_generique(t), t
    for t in ("Matrix (Intro)", "Règlement Freestyle #17 (Outro)", "Introspection", "Caroline"):
        assert not titre_generique(t), t


def test_un_single_sans_album_n_est_pas_un_echec():
    genius = _Genius({31147: {}}, {})
    c = tg.completer(genius, _artiste(), [_connu(31147, "Caroline")])
    assert c.echecs == 0 and c.sans_album == 1
    genius.appels.clear()
    tg.completer(genius, _artiste(), [_connu(31147, "Caroline")])
    assert genius.appels == []  # mémorisé


def test_quota_genius_arrete_net_et_ne_cache_rien():
    """Mesuré le 2026-09-24 : 10 000 requêtes / 24 h. Au premier 429, on
    s'arrête (pas 32 « échecs ») et le prochain run reprend."""
    from src.api.genius_api import QuotaGeniusAtteint

    class _Epuise(_Genius):
        def album_du_morceau(self, song_id):
            self.appels.append(("album", song_id))
            raise QuotaGeniusAtteint("429")

    genius = _Epuise({}, {})
    connus = [_connu(1, "A", album="X"), _connu(2, "B", album="Y")]
    c = tg.completer(genius, _artiste(), connus)
    assert c.quota and c.echecs == 0 and len(genius.appels) == 1
    genius = _Genius({1: ALBUM, 2: ALBUM}, {12001: TRACKLIST})
    tg.completer(genius, _artiste(), connus)
    assert ("album", 1) in genius.appels  # rien n'avait été mis en cache
