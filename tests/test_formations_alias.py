"""Alias proposés par l'enrichissement (2026-09-16) : garde-fous contre les faux positifs.

Ce que ces tests gèlent : pas d'oracle MusicBrainz ⇒ rien n'est proposé ; seuls
les « Artist name » (MB) et les pages d'artiste Discogs sont proposés, le reste
part « pour info » ; un alias égal au nom de l'artiste n'existe pas ; ce qui a
déjà un statut n'est jamais reproposé ; une panne MB est une PANNE.
"""

from types import SimpleNamespace

import pytest

from src.api.musicbrainz_api import AliasArtiste, aliases_de
from src.models import Artist, ArtistRelation, Track
from src.utils.formations import (
    Candidat,
    RapportFormations,
    chercher_formations,
    fusionner,
    liens_a_proposer,
    trier_decisions,
)


def _alias(nom, type="Artist name", **kw):
    return AliasArtiste(nom=nom, type=type, **kw)


def _dg_alias(nom, detail=None):
    return ArtistRelation(related_name=nom, kind="alias", source="discogs", detail=detail)


# ── aliases_de (MusicBrainz, pure) ───────────────────────────────────────────


def test_aliases_de_keeps_all_types_and_dedups():
    detail = {
        "aliases": [
            {"name": "Psmaker", "type": "Artist name", "locale": None, "primary": None},
            {"name": "PSMAKER", "type": "Search hint"},
            {"name": "Malcolm McCormick", "type": "Legal name", "begin-date": "1992-01-19"},
            {"name": "", "type": "Artist name"},
        ]
    }
    out = aliases_de(detail)
    assert [(a.nom, a.type) for a in out] == [
        ("Psmaker", "Artist name"),
        ("Malcolm McCormick", "Legal name"),
    ]
    assert out[0].proposable and not out[1].proposable
    assert out[1].begin == "1992-01-19"
    assert aliases_de({}) == []


# ── fusionner ────────────────────────────────────────────────────────────────


def test_fusionner_adds_mb_aliases_with_their_type_and_skips_own_name():
    cands = fusionner(
        [],
        [],
        set(),
        [_alias("Psmaker"), _alias("Ishaa", "Search hint"), _alias("ISHA", "Artist name")],
        "Isha",
    )
    # « ISHA » = le nom lui-même (normalisé) : écarté, ce n'est pas une information.
    assert {(c.related_name, c.kind, c.detail, c.proposable) for c in cands} == {
        ("Psmaker", "alias", "Artist name", True),
        ("Ishaa", "alias", "Search hint", False),
    }


def test_fusionner_crosses_discogs_alias_and_variations():
    cands = fusionner(
        [],
        [_dg_alias("Psmaker"), _dg_alias("Isha (2)", detail="name_variation"), _dg_alias("isha")],
        set(),
        [_alias("Psmaker"), _alias("Larry", "Search hint")],
        "Isha",
    )
    par_nom = {c.related_name: c for c in cands}
    assert par_nom["Psmaker"].croise and par_nom["Psmaker"].proposable
    assert not par_nom["Isha (2)"].proposable and par_nom["Isha (2)"].detail == "name_variation"
    assert "isha" not in par_nom
    # 2026-09-23 : le type MB est GARDÉ quand Discogs croise — un « Search hint »
    # ou un « Legal name » vu aussi comme page Discogs reste ce qu'il est.
    cands = fusionner([], [_dg_alias("Larry")], set(), [_alias("Larry", "Search hint")], "X")
    assert cands[0].croise and cands[0].detail == "Search hint" and not cands[0].proposable
    cands = fusionner([], [_dg_alias("Malcolm")], set(), [_alias("Malcolm", "Legal name")], "X")
    assert cands[0].nature == "etat_civil" and not cands[0].proposable
    # Un alias MB SANS type croisé avec une page Discogs : c'est Discogs qui
    # dit que c'est une identité.
    cands = fusionner([], [_dg_alias("Psmaker")], set(), [_alias("Psmaker", None)], "Isha")
    assert cands[0].nature == "scene" and cands[0].proposable


# ── liens_a_proposer ───────────────────────────────────────────────────────


def _rapport(mbid="mb-1", **kw):
    return RapportFormations(mbid=mbid, **kw)


def test_nothing_without_oracle():
    c = Candidat(related_name="Psmaker", kind="alias", sources={"discogs"})
    assert liens_a_proposer(_rapport(mbid=None, candidats=[c]), "Isha") == ([], [])


def test_split_proposed_and_info_and_skip_known():
    cands = [
        Candidat(
            related_name="Psmaker", kind="alias", sources={"musicbrainz"}, detail="Artist name"
        ),
        Candidat(
            related_name="Malcolm", kind="alias", sources={"musicbrainz"}, detail="Legal name"
        ),
        Candidat(
            related_name="Isha (2)", kind="alias", sources={"discogs"}, detail="name_variation"
        ),
        Candidat(related_name="Refusé", kind="alias", sources={"musicbrainz"}, status="refused"),
        Candidat(related_name="Déjà", kind="alias", sources={"musicbrainz"}, status="confirmed"),
        Candidat(related_name="IAM", kind="member_of", sources={"musicbrainz"}),
        Candidat(related_name="isha", kind="alias", sources={"discogs"}),
    ]
    proposes, infos = liens_a_proposer(_rapport(candidats=cands), "Isha")
    # Les FORMATIONS sont proposées aussi (2026-09-17 : PLK sans Panama Bende
    # après un run), sans nature — c'est l'utilisateur qui la choisit.
    assert [(r.related_name, r.kind) for r in proposes] == [
        ("Psmaker", "alias"),
        ("IAM", "member_of"),
    ]
    assert proposes[0].detail == "Artist name" and proposes[0].source == "musicbrainz"
    assert proposes[1].formation is None
    assert [(r.related_name, r.detail) for r in infos] == [
        ("Malcolm", "Legal name"),
        ("Isha (2)", "name_variation"),
    ]


# ── trier_decisions ──────────────────────────────────────────────────────────


def test_trier_decisions_returns_only_changes():
    nouveau = Candidat(related_name="Psmaker", kind="alias", sources={"musicbrainz"})
    propose = Candidat(
        related_name="IAM", kind="member_of", sources={"musicbrainz"}, status="proposed"
    )
    confirme = Candidat(related_name="Vieux", kind="member_of", status="confirmed")
    changes = trier_decisions(
        [
            (nouveau, "proposed", "groupe"),  # jamais vu, laissé proposé → à insérer
            (propose, "confirmed", "collectif"),
            (confirme, "confirmed", "groupe"),  # inchangé
            (confirme, "refused", "groupe"),
        ]
    )
    assert [(c.related_name, s, n) for c, s, n in changes] == [
        ("Psmaker", "proposed", None),  # alias : jamais de nature
        ("IAM", "confirmed", "collectif"),
        ("Vieux", "refused", "groupe"),
    ]


# ── chercher_formations : statut, mbid, panne ────────────────────────────────


class _MB:
    def __init__(self, retenu=True, boum=False, aliases=()):
        self.retenu, self.boum, self.aliases = retenu, boum, list(aliases)

    def resoudre_artiste(self, nom, nos_albums):
        if self.boum:
            raise RuntimeError("503 saturé")
        if not self.retenu:
            return None
        return SimpleNamespace(
            mbid="mb-1", relations=[], aliases=self.aliases, desambiguation="rapper", type=None
        )


class _Discogs:
    def candidats_artiste(self, nom):
        return []

    def artistes_du_disque(self, release_id):
        return None

    def get_artist_groups(self, nom, attendues=None, artist_id=None):
        return {"proposees": [], "confirmees": set(), "candidats": 0}


class _DM:
    def __init__(self, relations=()):
        self._relations = list(relations)

    def get_artist_tracks(self, artist_id):
        return [Track(title="Durag", album="La vie augmente")]

    def get_artist_relations(self, artist_id, status="confirmed"):
        return [r for r in self._relations if status is None or r.status == status]

    def nature_connue_pour(self, nom):
        return None


def test_chercher_reads_all_statuses_and_reports_mbid():
    dm = _DM([ArtistRelation(related_name="Psmaker", kind="alias", status="refused")])
    rapport = chercher_formations(
        Artist(id=1, name="Isha"),
        dm,
        mb=_MB(aliases=[_alias("Psmaker"), _alias("PS")]),
        discogs=_Discogs(),
    )
    assert rapport.mbid == "mb-1" and rapport.panne_mb is None
    statuts = {c.related_name: c.status for c in rapport.candidats}
    assert statuts == {"Psmaker": "refused", "PS": None}
    proposes, _ = liens_a_proposer(rapport, "Isha")
    assert [r.related_name for r in proposes] == ["PS"]


def test_chercher_reports_mb_outage_as_a_failure():
    rapport = chercher_formations(
        Artist(id=1, name="Isha"), _DM(), mb=_MB(boum=True), discogs=_Discogs()
    )
    assert rapport.panne_mb == "503 saturé" and rapport.mbid is None
    assert liens_a_proposer(rapport, "Isha") == ([], [])


# ── candidats_de_base / reunir ───────────────────────────────────────────────


def test_candidats_de_base_and_reunir():
    from src.utils.formations import candidats_de_base, reunir

    base = candidats_de_base(
        [
            ArtistRelation(
                related_name="Psmaker",
                kind="alias",
                source="musicbrainz+discogs",
                status="proposed",
            ),
            ArtistRelation(
                related_name="IAM", kind="member_of", formation="groupe", status="confirmed"
            ),
        ]
    )
    assert [(c.related_name, c.status, c.croise) for c in base] == [
        ("Psmaker", "proposed", True),
        ("IAM", "confirmed", False),
    ]
    trouves = [
        Candidat(related_name="psmaker", kind="alias", sources={"discogs"}, detail=None),
        Candidat(related_name="Nouveau", kind="member_of", sources={"musicbrainz"}),
    ]
    reunis = reunir(base, trouves)
    par_nom = {c.related_name: c for c in reunis}
    assert set(par_nom) == {"Psmaker", "IAM", "Nouveau"}  # la base garde sa graphie et son statut
    assert par_nom["Psmaker"].status == "proposed"
    assert par_nom["Nouveau"].status is None


# ── Fenêtre : ouverture sans réseau, décisions appliquées ────────────────────


@pytest.fixture
def racine(racine_tk):
    return racine_tk


def test_window_opens_from_base_and_applies_decisions(racine, data_manager, monkeypatch):
    from src.gui.windows import formations as fen
    from src.models import Artist

    artist = Artist(name="Isha")
    artist.id = data_manager.save_artist(artist)
    data_manager.propose_artist_relations(
        artist.id, [ArtistRelation(related_name="Psmaker", kind="alias", source="musicbrainz")]
    )
    data_manager.propose_artist_relations(
        artist.id,
        [ArtistRelation(related_name="Malcolm", kind="alias", detail="Legal name")],
        status="info",
    )
    # Le réseau ne doit PAS être touché à l'ouverture.
    monkeypatch.setattr(
        fen, "chercher_formations", lambda *a, **k: pytest.fail("réseau à l'ouverture")
    )

    class App:
        root = racine
        current_artist = artist

        def __init__(self):
            self.data_manager = data_manager
            self.reloads = 0

        def _reload_tracks_and_refresh(self):
            self.reloads += 1

    app = App()
    w = fen.FormationsWindow(app)
    try:
        # « Pour info » est ARBITRABLE depuis le 2026-09-23 (un état civil se
        # confirme) : ses lignes ont aussi leur sélecteur.
        assert [ligne["candidat"].related_name for ligne in w._lignes] == ["Psmaker", "Malcolm"]
        assert "1 à arbitrer" in w.statut.cget("text")
        w._lignes[0]["statut"].set("Confirmé")
        w.enregistrer()
        assert [r.related_name for r in data_manager.get_artist_relations(artist.id)] == ["Psmaker"]
        assert app.reloads == 1
        # Puis refusé : sort des lecteurs, reste en mémoire.
        w._lignes[0]["statut"].set("Refusé")
        w.enregistrer()
        assert data_manager.get_artist_relations(artist.id) == []
        assert data_manager.get_artist_relations(artist.id, "refused")[0].related_name == "Psmaker"
    finally:
        w.window.destroy()


# ── nature_alias : UNE règle, des usages distincts (2026-09-23) ──────────────


@pytest.mark.parametrize(
    ("detail", "source", "nature"),
    [
        ("Artist name", "musicbrainz", "scene"),
        ("Legal name", "musicbrainz", "etat_civil"),
        ("Search hint", "musicbrainz", "indice"),
        ("name_variation", "discogs", "graphie"),
        (None, "discogs", "scene"),  # page d'alias Discogs = une identité
        (None, "musicbrainz+discogs", "scene"),
        (None, "musicbrainz", "graphie"),  # alias MB sans type : une écriture
        (None, None, "graphie"),
        (None, "manual", "graphie"),
        ("Type inconnu", "musicbrainz", "indice"),  # le plus prudent
    ],
)
def test_nature_alias(detail, source, nature):
    from src.utils.formations import nature_alias

    assert nature_alias(detail, source) == nature


@pytest.mark.parametrize("type_mb", ["Artist name", "Legal name", "Search hint", None])
def test_les_deux_proposable_disent_la_meme_chose(type_mb):
    """Il y avait deux règles : `AliasArtiste.proposable` (MB) refusait un type
    absent, `Candidat.proposable` l'acceptait quelle qu'en soit la source."""
    alias = _alias("X", type_mb)
    candidat = fusionner([], [], set(), [alias], "Artiste")[0]
    assert alias.proposable == candidat.proposable


def test_un_candidat_neuf_non_proposable_va_pour_info():
    """La fenêtre rangeait tout candidat neuf avec les proposés, et
    l'enregistrait `proposed` s'il était laissé tel quel (20 variantes)."""
    from src.gui.windows.formations import section_de

    graphie = Candidat(
        related_name="ISHA", kind="alias", sources={"discogs"}, detail="name_variation"
    )
    scene = Candidat(related_name="Psmaker", kind="alias", sources={"discogs"})
    groupe = Candidat(related_name="IAM", kind="member_of", sources={"musicbrainz"})
    confirme = Candidat(
        related_name="Travis $cott", kind="alias", detail="name_variation", status="confirmed"
    )
    assert section_de(graphie) == "info"
    assert section_de(scene) == "proposed"
    assert section_de(groupe) == "proposed"
    assert section_de(confirme) == "confirmed"


def test_recherche_de_certifs_sans_etat_civil_ni_indice(data_manager):
    """Confirmés tous les quatre ; seuls le nom de scène, la graphie et la
    formation servent à chercher des certifications."""
    from src.services.certifs import noms_de_recherche_pour

    artist = Artist(name="Travis Scott")
    artist.id = data_manager.save_artist(artist)
    data_manager.record_artist_relations(
        artist.id,
        [
            ArtistRelation(related_name="La Flame", kind="alias", source="discogs"),
            ArtistRelation(
                related_name="Travis $cott", kind="alias", source="discogs", detail="name_variation"
            ),
            ArtistRelation(
                related_name="Jacques Webster",
                kind="alias",
                source="musicbrainz",
                detail="Legal name",
            ),
            ArtistRelation(
                related_name="Travis Scot", kind="alias", source="musicbrainz", detail="Search hint"
            ),
            ArtistRelation(related_name="JackBoys", kind="member_of", formation="groupe"),
        ],
    )
    runtime = SimpleNamespace(data_manager=data_manager)
    noms = noms_de_recherche_pour(runtime, artist)
    assert "Jacques Webster" not in noms and "Travis Scot" not in noms
    assert {"La Flame", "Travis $cott", "JackBoys"} <= set(noms)
    # La présence dans un collectif lit TOUS les alias confirmés : les crédits
    # d'écriture portent l'état civil.
    assert "Jacques Webster" in data_manager.noms_de_lartiste(artist.id, artist.name)
