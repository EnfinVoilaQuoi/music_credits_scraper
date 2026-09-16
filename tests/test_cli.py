"""`python -m src.cli` : chaque drapeau atterrit dans le bon champ d'options, les
codes de sortie disent l'état du run, et l'artiste ambigu est LISTÉ, jamais
choisi. Aucun service réel n'est appelé."""

from types import SimpleNamespace

import pytest

from src import cli
from src.services import artiste, cycle
from src.services.runtime import Bilan


def _parse(*argv):
    return cli.build_parser().parse_args(list(argv))


class TestParsingDisco:
    def test_defauts_gui(self):
        o = cli.options_disco(_parse("disco", "Swing"))
        assert o.max_songs is None and o.include_features and o.prefill and o.download_images
        assert not o.update_only and not o.include_secondary and o.respect_deleted

    def test_chaque_case(self):
        o = cli.options_disco(
            _parse(
                "disco",
                "Swing",
                "--max-songs",
                "10",
                "--no-features",
                "--no-prefill",
                "--secondaires",
                "--no-respecter-supprimes",
                "--no-images",
                "--maj",
            )
        )
        assert o.max_songs == 10 and not o.include_features and not o.prefill
        assert o.include_secondary and not o.respect_deleted and not o.download_images
        assert o.update_only


class TestParsingCredits:
    def test_defauts_et_forces(self):
        o = cli.options_credits(_parse("credits", "Swing"))
        assert o.genius and o.discogs and o.paroles_genius and o.paroles_ytm
        assert o.sync_lrclib and o.sync_ytm and not o.sync_musixmatch
        o = cli.options_credits(
            _parse(
                "credits",
                "Swing",
                "--no-discogs",
                "--force-credits",
                "--no-paroles-ytm",
                "--musixmatch",
                "--force-sync",
                "--no-ytm",
            )
        )
        assert not o.discogs and o.force_credits and not o.paroles_ytm
        assert o.sync_musixmatch and o.force_sync and not o.sync_ytm

    def test_manquants(self):
        assert _parse("credits", "Swing", "--manquants").manquants
        assert not _parse("credits", "Swing").manquants


class TestParsingEnrichStreams:
    def test_enrich(self):
        o = cli.options_enrich(_parse("enrich", "S", "--sources", "reccobeats, deezer", "--force"))
        assert o.sources == ("reccobeats", "deezer") and o.force_update and o.clear_on_failure
        assert o.musicbrainz  # identité en fin de run : cochée par défaut
        assert cli.options_enrich(_parse("enrich", "S")).sources is None
        assert not cli.options_enrich(_parse("enrich", "S", "--no-musicbrainz")).musicbrainz

    def test_streams(self):
        o = cli.options_streams(
            _parse("streams", "S", "--no-kworb", "--spotify-full", "--ytm-channel", "@x")
        )
        assert not o.kworb and o.spotify_full and o.ytm and o.ytm_channel == "@x"


class TestParsingCycle:
    def test_only_et_skip_exclusifs(self):
        with pytest.raises(SystemExit):
            _parse("cycle", "S", "--only", "disco", "--skip", "enrich")

    def test_etapes(self):
        o = cli.options_cycle(_parse("cycle", "S", "--skip", "streams,certifs", "--manquants"))
        assert o.etapes() == ["disco", "credits", "enrich"] and o.manquants
        o = cli.options_cycle(_parse("cycle", "S", "--only", "enrich"))
        assert o.etapes() == ["enrich"]

    def test_etape_inconnue(self):
        with pytest.raises(ValueError):
            cli.options_cycle(_parse("cycle", "S", "--only", "bidule")).etapes()

    def test_genius_id_et_sources_certifs(self):
        o = cli.options_cycle(
            _parse("cycle", "S", "--genius-id", "42", "--certifs-sources", "SNEP")
        )
        assert o.genius_id == 42 and o.certifs_sources == ("SNEP",)


class TestCertifsUpdate:
    def test_sources_positionnelles(self):
        a = _parse("certifs", "update", "SNEP", "BPI")
        assert a.sources == ["SNEP", "BPI"]
        assert _parse("certifs", "update").sources == []


@pytest.fixture
def sans_effets(monkeypatch):
    """Ni Runtime réel, ni fermeture, ni observabilité."""
    rt = SimpleNamespace(data_manager=SimpleNamespace(engine=None), data_enricher=None)
    monkeypatch.setattr(cli.Runtime, "build", staticmethod(lambda: rt))
    monkeypatch.setattr(cli, "_fermer", lambda runtime: None)
    monkeypatch.setattr(cli, "_installer_ctrl_c", lambda: None)
    monkeypatch.setattr(cli.usage_repository, "attach", lambda engine: None)
    return rt


class TestCodesDeSortie:
    def test_ambigu_liste_les_candidats(self, sans_effets, monkeypatch, capsys):
        def lever(runtime, nom, genius_id=None):
            raise artiste.ArtisteAmbigu(nom, [SimpleNamespace(name="Swing", genius_id=7)])

        monkeypatch.setattr(cli.artiste, "charger_ou_ajouter", lever)
        assert cli.main(["artiste", "add", "Swing"]) == cli.AMBIGU
        assert "--genius-id 7" in capsys.readouterr().out

    def test_cycle_complet_et_partiel(self, sans_effets, monkeypatch):
        art = SimpleNamespace(name="S", id=1, tracks=[])
        monkeypatch.setattr(cli.artiste, "charger", lambda rt, nom: art)

        def faux_run(runtime, nom, options, hooks):
            b = cycle.BilanCycle(artist_name="S")
            b.etapes["disco"] = Bilan()
            return b

        monkeypatch.setattr(cli.cycle, "run", faux_run)
        monkeypatch.setattr(cli.cycle, "resume_etape", lambda *a: "ok")
        assert cli.main(["cycle", "S"]) == cli.COMPLET

        def faux_partiel(runtime, nom, options, hooks):
            b = cycle.BilanCycle(artist_name="S")
            b.interrompu("arrêt demandé")
            return b

        monkeypatch.setattr(cli.cycle, "run", faux_partiel)
        assert cli.main(["cycle", "S"]) == cli.PARTIEL

    def test_etape_seule_sur_artiste_absent_est_une_erreur(self, sans_effets, monkeypatch, capsys):
        monkeypatch.setattr(cli.artiste, "charger", lambda rt, nom: None)
        assert cli.main(["credits", "Inconnu"]) == cli.ERREUR
        assert "artiste add" in capsys.readouterr().out

    def test_exception_est_une_erreur_tracee(self, sans_effets, monkeypatch):
        def boom(*a, **k):
            raise RuntimeError("réseau mort")

        monkeypatch.setattr(cli.artiste, "charger_ou_ajouter", boom)
        assert cli.main(["artiste", "add", "S"]) == cli.ERREUR


class TestEtapeSeule:
    def test_credits_passe_par_executer_etape_avec_manquants(self, sans_effets, monkeypatch):
        art = SimpleNamespace(name="S", id=1, tracks=[])
        monkeypatch.setattr(cli.artiste, "charger", lambda rt, nom: art)
        vus = {}

        def faux(runtime, artist, etape, options, hooks):
            vus.update(etape=etape, manquants=options.manquants, discogs=options.credits.discogs)
            return Bilan()

        monkeypatch.setattr(cli.cycle, "executer_etape", faux)
        monkeypatch.setattr(cli.cycle, "resume_etape", lambda *a: "ok")
        assert cli.main(["credits", "S", "--manquants", "--no-discogs"]) == cli.COMPLET
        assert vus == {"etape": "credits", "manquants": True, "discogs": False}
