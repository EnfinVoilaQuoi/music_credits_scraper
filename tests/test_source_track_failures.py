"""Suivi des échecs PAR MORCEAU (`source_track_failures`, e44, 2026-09-30).

« Garder de côté ce qui ne passe pas » : une ligne par (morceau, source), posée
au premier échec de communication, retirée dès que la source RÉPOND pour ce
morceau (`ok` ou `absent`). C'est ce que `--echecs` retente. Base réelle
(vrai bootstrap Alembic), aucun réseau.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from src.models import Artist, Track
from src.observability.issues import IssueKind
from src.observability.repository import SourceUsageRepository
from src.observability.source_usage import Verdict
from src.services import cycle, enrichissement
from src.services.credits import OptionsCredits
from src.services.runtime import Manque, Runtime, selection_morceaux

_T0 = datetime(2026, 9, 30, 10, 0, 0)


def _v(issue, track_id=1, source="deezer", at=_T0, **extra):
    return Verdict(
        source_key=source,
        issue=issue,
        flow=extra.get("flow", "enrichment"),
        artist_id=extra.get("artist_id", 3),
        track_id=track_id,
        detail=extra.get("detail", ""),
        status_code=extra.get("status_code"),
        at=at,
    )


@pytest.fixture
def repo(data_manager):
    return SourceUsageRepository(data_manager.engine)


# ── L'écriture ────────────────────────────────────────────────────────────────
class TestSuivi:
    def test_un_echec_est_garde(self, repo):
        repo.record([_v(IssueKind.TIMEOUT, detail="délai", status_code=None)])
        (ligne,) = repo.echecs_suivis()
        assert ligne["track_id"] == 1 and ligne["source_key"] == "deezer"
        assert ligne["issue"] == "timeout" and ligne["message"] == "délai"
        assert ligne["n_echecs"] == 1

    def test_les_echecs_suivants_mettent_a_jour_sans_dupliquer(self, repo):
        repo.record([_v(IssueKind.TIMEOUT)])
        repo.record([_v(IssueKind.BLOCKED, at=_T0 + timedelta(hours=1), status_code=403)])
        (ligne,) = repo.echecs_suivis()
        assert ligne["n_echecs"] == 2
        assert ligne["issue"] == "blocked" and ligne["status_code"] == 403
        assert str(ligne["premier_echec"]).startswith("2026-09-30T10:00")
        assert str(ligne["dernier_echec"]).startswith("2026-09-30T11:00")

    @pytest.mark.parametrize("reponse", [IssueKind.OK, IssueKind.ABSENT])
    def test_une_reponse_de_la_source_retire_la_ligne(self, repo, reponse):
        """Une ABSENCE est une réponse : il n'y a rien à retenter."""
        repo.record([_v(IssueKind.UNREACHABLE)])
        repo.record([_v(reponse)])
        assert repo.echecs_suivis() == []

    @pytest.mark.parametrize(
        "neutre", [IssueKind.SKIPPED, IssueKind.CONFIG, IssueKind.INDETERMINATE]
    )
    def test_sans_reponse_ni_echec_rien_ne_bouge(self, repo, neutre):
        """Pas appelée, pas configurée, aucun signal : on ne sait pas si la source
        aurait répondu — la ligne reste, et aucune n'est créée."""
        repo.record([_v(IssueKind.TIMEOUT, track_id=1), _v(neutre, track_id=2)])
        repo.record([_v(neutre, track_id=1)])
        assert [r["track_id"] for r in repo.echecs_suivis()] == [1]

    def test_un_verdict_sans_morceau_est_ignore(self, repo):
        repo.record([_v(IssueKind.TIMEOUT, track_id=None)])
        assert repo.echecs_suivis() == []
        # … et un succès sans morceau n'efface rien.
        repo.record([_v(IssueKind.TIMEOUT, track_id=1)])
        repo.record([_v(IssueKind.OK, track_id=None)])
        assert len(repo.echecs_suivis()) == 1

    def test_l_ordre_du_lot_est_respecte(self, repo):
        repo.record([_v(IssueKind.TIMEOUT), _v(IssueKind.OK)])
        assert repo.echecs_suivis() == []
        repo.record([_v(IssueKind.OK), _v(IssueKind.TIMEOUT)])
        assert len(repo.echecs_suivis()) == 1

    def test_par_source_et_par_morceau(self, repo):
        repo.record(
            [
                _v(IssueKind.TIMEOUT, track_id=1, source="deezer"),
                _v(IssueKind.TIMEOUT, track_id=1, source="lrclib"),
                _v(IssueKind.PARSE, track_id=2, source="lrclib"),
            ]
        )
        repo.record([_v(IssueKind.OK, track_id=1, source="lrclib")])
        assert repo.morceaux_en_echec(["lrclib"]) == {2}
        assert repo.morceaux_en_echec(["deezer", "lrclib"]) == {1, 2}
        assert repo.morceaux_en_echec() == {1, 2}
        assert repo.morceaux_en_echec([]) == set()
        assert [r["source_key"] for r in repo.echecs_suivis(track_ids=[1])] == ["deezer"]

    def test_rien_n_est_rogne(self, repo):
        """Contrairement à la fenêtre de diagnostic (50 par source)."""
        repo.record([_v(IssueKind.TIMEOUT, track_id=i) for i in range(1, 121)])
        assert len(repo.morceaux_en_echec(["deezer"])) == 120


# ── Suppression et fusion de fiches : pas de cascade FK ───────────────────────
def _fiche(dm, artiste, titre):
    t = Track(title=titre, artist=artiste)
    return dm.save_track(t)


def _lignes(dm):
    with dm.engine.connect() as conn:
        return {
            tuple(r)
            for r in conn.execute(text("SELECT track_id, source_key FROM source_track_failures"))
        }


class TestFiches:
    def test_delete_track_purge(self, data_manager, repo):
        a = Artist(name="ISHA")
        a.id = data_manager.save_artist(a)
        t1, t2 = _fiche(data_manager, a, "Un"), _fiche(data_manager, a, "Deux")
        repo.record([_v(IssueKind.TIMEOUT, track_id=t1), _v(IssueKind.TIMEOUT, track_id=t2)])
        assert data_manager.delete_track(t1)
        assert _lignes(data_manager) == {(t2, "deezer")}

    def test_merge_tracks_garde_les_echecs_de_la_fiche_conservee(self, data_manager, repo):
        a = Artist(name="ISHA")
        a.id = data_manager.save_artist(a)
        garde, doublon = _fiche(data_manager, a, "Garde"), _fiche(data_manager, a, "Doublon")
        repo.record(
            [
                _v(IssueKind.TIMEOUT, track_id=garde, source="lrclib"),
                _v(IssueKind.TIMEOUT, track_id=doublon, source="deezer"),
            ]
        )
        assert data_manager.merge_tracks(garde, doublon)
        assert _lignes(data_manager) == {(garde, "lrclib")}


# ── La sélection `--echecs` ───────────────────────────────────────────────────
def _runtime(dm, disabled=frozenset()):
    return Runtime(
        data_manager=dm,
        genius_api=None,
        data_enricher=SimpleNamespace(get_available_sources=lambda: ["deezer", "spotify_id"]),
        deleted=None,
        disabled=SimpleNamespace(load_disabled_tracks=lambda nom: set(disabled)),
    )


def _artiste():
    a = Artist(name="A")
    a.tracks = [Track(title=f"T{i}", artist=a) for i in (1, 2, 3)]
    for i, t in enumerate(a.tracks, 1):
        t.id = i
        t.audio.bpm, t.audio.key = 100, 2  # rien ne « manque »
    return a


class TestSelection:
    def test_echecs_retient_les_morceaux_non_servis_par_ces_sources(self, data_manager, repo):
        repo.record(
            [
                _v(IssueKind.TIMEOUT, track_id=2, source="lrclib"),
                _v(IssueKind.TIMEOUT, track_id=3, source="deezer"),
            ]
        )
        sel = selection_morceaux(_runtime(data_manager), _artiste(), echecs=["lrclib"])
        assert [t.id for t in sel] == [2]

    def test_union_avec_manquants(self, data_manager, repo):
        a = _artiste()
        a.tracks[0].audio.bpm = None  # 1 manque son audio
        repo.record([_v(IssueKind.TIMEOUT, track_id=3, source="deezer")])
        sel = selection_morceaux(
            _runtime(data_manager), a, manquants=[Manque.AUDIO], echecs=["deezer"]
        )
        assert [t.id for t in sel] == [1, 3]

    def test_les_desactives_restent_exclus(self, data_manager, repo):
        repo.record([_v(IssueKind.TIMEOUT, track_id=2, source="deezer")])
        sel = selection_morceaux(
            _runtime(data_manager, disabled={2}), _artiste(), echecs=["deezer"]
        )
        assert sel == []

    def test_sources_suivies_de_l_enrichissement(self, data_manager):
        rt = _runtime(data_manager)
        # Par défaut, pas de recherche d'ID Spotify (étape Identité).
        assert enrichissement.sources_suivies(rt, enrichissement.OptionsEnrich()) == ("deezer",)
        explicite = enrichissement.OptionsEnrich(sources=("spotify_id", "reccobeats"))
        assert enrichissement.sources_suivies(rt, explicite) == ("spotify_embed", "reccobeats")

    def test_sources_suivies_des_credits(self):
        o = OptionsCredits(discogs=False, sync_musixmatch=True)
        assert o.sources_suivies() == (
            "genius_scrape",
            "genius_api",
            "lrclib",
            "ytmusic",
            "musixmatch",
        )

    def test_le_cycle_passe_les_sources_du_flux(self, monkeypatch):
        vus = {}

        def selection(runtime, artist, *, manquants=(), echecs=()):
            vus["echecs"] = tuple(echecs)
            return []

        monkeypatch.setattr(cycle, "selection_morceaux", selection)
        monkeypatch.setattr(cycle.credits, "run", lambda *a, **k: None)
        opts = cycle.OptionsCycle(
            echecs=True, credits=OptionsCredits(genius=False, paroles_genius=False)
        )
        cycle.executer_etape(
            SimpleNamespace(), Artist(name="A"), "credits", opts, SimpleNamespace()
        )
        assert "discogs" in vus["echecs"] and "genius_scrape" not in vus["echecs"]
