"""La vue Album (`src/presentation/albums.py`) : un projet par ligne, ses
éditions dépliables, Featurings et Singles à la fin."""

from src.models import Artist, Track
from src.models.track import Credit, CreditRole
from src.presentation import albums, table
from src.utils.track_validation import Contexte


def morceau(id_, titre, *, date="2021-06-15", duree=200, sp=1000, yt=0, label=None, feat=False):
    t = Track(title=titre, artist=Artist(name="Isha"), release_date=date, duration=duree)
    t.id = id_
    t.streams.spotify_streams, t.streams.ytm_streams = sp, yt
    t.lyrics.text = "des paroles" if id_ % 2 else ""
    if label:
        t.add_credit(Credit(name=label, role=CreditRole.LABEL))
    if feat:
        t.is_featuring, t.primary_artist_name = True, "Autre"
        t.featured_artists = "Isha"
    return t


def lignes(*tracks, desactives=()):
    ctx = Contexte(types_par_album={}, desactives=frozenset(desactives))
    return table.construire_lignes(tracks, ctx, ("Isha",))


def projet(pid, titre, ids, *, editions=None, nature="album", sp=None, yt=None):
    return {
        "projet_id": pid,
        "titre": titre,
        "nature": nature,
        "spotify_streams": sp,
        "ytm_streams": yt,
        "track_ids": list(ids),
        "editions": editions or [{"release_id": pid * 10, "titre": titre, "track_ids": list(ids)}],
    }


def test_un_projet_agrege_ses_titres():
    ls = lignes(
        morceau(1, "A", date="2019-04-09", label="DKZ"),
        morceau(2, "B", date="2017-01-21", duree=3700, label="DKZ"),
        morceau(3, "C"),
        desactives={3},
    )
    a = albums.construire([projet(1, "La vie augmente", [1, 2, 3])], ls)[0]
    v = a.valeurs
    assert v["titre"] == "La vie augmente" and v["type"] == "album"
    assert v["date"] == "21/01/2017"  # la plus ANCIENNE
    assert v["morceaux"] == "3 (1❌)"
    assert v["paroles"] == "2/3"
    assert v["duree"] == "1h08"  # 200 + 3 700 + 200 s
    assert v["label"] == "DKZ"
    assert v["editions"] == "1"


def test_streams_du_projet_sinon_somme_et_source_seule():
    ls = lignes(morceau(1, "A", sp=100), morceau(2, "B", sp=50))
    (a,) = albums.construire([projet(1, "P", [1, 2])], ls)
    assert a.valeurs["streams"] == "150" and a.streams_seule == "spotify"
    (a,) = albums.construire([projet(1, "P", [1, 2], sp=1000, yt=500)], ls)
    assert a.valeurs["streams"] == "1 500" and a.streams_seule is None


def test_editions_depliables_seulement_s_il_y_en_a_plusieurs():
    ls = lignes(morceau(1, "A"), morceau(2, "B"))
    eds = [
        {"release_id": 10, "titre": "P", "track_ids": [1]},
        {"release_id": 11, "titre": "P (Deluxe)", "track_ids": [1, 2]},
    ]
    (a,) = albums.construire([projet(1, "P", [1, 2], editions=eds)], ls)
    assert [e.titre for e in a.editions] == ["P", "P (Deluxe)"]
    assert a.valeurs["editions"] == "2"
    (b,) = albums.construire([projet(2, "Q", [1])], ls[:1])
    assert b.editions == []


def test_regroupements_en_fin_et_projets_du_plus_recent():
    ls = lignes(
        morceau(1, "Vieux", date="2010-01-01"),
        morceau(2, "Neuf", date="2020-01-01"),
        morceau(3, "Invité", feat=True),
        morceau(5, "Seul"),
    )
    res = albums.construire([projet(1, "Ancien", [1]), projet(2, "Récent", [2])], ls)
    assert [a.titre for a in res] == ["Récent", "Ancien", albums.FEATURINGS, albums.SINGLES]
    assert albums.resume(res) == "2 projets • 2 éditions"


def test_projet_sans_titre_charge_ignore():
    res = albums.construire([projet(1, "Autre", [99])], lignes(morceau(1, "A")))
    assert [a.titre for a in res] == [albums.SINGLES]
