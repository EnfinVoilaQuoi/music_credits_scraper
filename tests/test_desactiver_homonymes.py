"""Les pages d'un homonyme, triées à la main, se désactivent de façon réversible."""

from src.models import Artist, Track
from src.utils import corrections_fiches as cf
from src.utils.disabled_tracks_manager import DisabledTracksManager


def _artiste(dm, nom):
    a = Artist(name=nom)
    a.id = dm.save_artist(a)
    return a


def _fiche(dm, artiste, titre, genius_id):
    t = Track(title=titre, artist=artiste)
    t.genius_id = genius_id
    return dm.save_track(t)


class TestDesactiverHomonymes:
    def test_a_blanc_rien_puis_applique_puis_reversible(self, data_manager, capsys):
        import scripts.desactiver_homonymes as script

        sniper = _artiste(data_manager, "Sniper")
        i = _fiche(data_manager, sniper, "Always", 1)
        args = ["Sniper", "--fiches", str(i), "--base", str(data_manager.db_path)]

        assert script.main(args) == 0
        assert "Always" in capsys.readouterr().out
        assert DisabledTracksManager().load_disabled_tracks("Sniper") == set()

        assert script.main([*args, "--disque", "259551272", "--apply"]) == 0
        gestion = DisabledTracksManager()
        assert gestion.load_disabled_tracks("Sniper") == {i}
        assert gestion.load_motifs("Sniper")[i]["motif"] == "homonyme"
        assert cf.disques_refuses("Sniper") == {259551272}

        gestion.reactiver("Sniper", [i])
        assert gestion.load_disabled_tracks("Sniper") == set()

    def test_supprimer_retire_la_fiche_et_la_note(self, data_manager, capsys):
        """Le morceau d'un homonyme part de la base, et la discographie ne le recrée pas."""
        import scripts.desactiver_homonymes as script
        from src.utils.deleted_tracks_manager import DeletedTracksManager

        sniper = _artiste(data_manager, "Sniper")
        i = _fiche(data_manager, sniper, "Radio Wolna Europa", 2166930)
        args = ["Sniper", "--fiches", str(i), "--base", str(data_manager.db_path), "--supprimer"]

        assert script.main(args) == 0
        assert "à supprimer" in capsys.readouterr().out
        assert [t.id for t in data_manager.get_artist_tracks(sniper.id)] == [i]

        assert script.main([*args, "--apply"]) == 0
        assert data_manager.get_artist_tracks(sniper.id) == []
        notes = DeletedTracksManager()
        assert notes.load_deleted_ids("Sniper") == {2166930}
        assert DisabledTracksManager().load_disabled_tracks("Sniper") == set()

        assert notes.remove_deleted("Sniper", 2166930)
        assert notes.load_deleted_ids("Sniper") == set()

    def test_une_ligne_soeur_est_gardee(self, data_manager, capsys):
        """Aketo porte la même page : le groupe y est, ce n'est pas l'homonyme."""
        import scripts.desactiver_homonymes as script

        sniper = _artiste(data_manager, "Sniper")
        aketo = _artiste(data_manager, "Aketo")
        i = _fiche(data_manager, sniper, "Sirocco", 2)
        _fiche(data_manager, aketo, "Sirocco", 2)
        args = ["Sniper", "--fiches", str(i), "--base", str(data_manager.db_path), "--apply"]

        assert script.main(args) == 0
        assert "gardée" in capsys.readouterr().out
        assert DisabledTracksManager().load_disabled_tracks("Sniper") == set()

    def test_la_fiche_d_un_autre_artiste_est_ignoree(self, data_manager, capsys):
        import scripts.desactiver_homonymes as script

        _artiste(data_manager, "Sniper")
        aketo = _artiste(data_manager, "Aketo")
        i = _fiche(data_manager, aketo, "Solo", 3)
        args = ["Sniper", "--fiches", str(i), "--base", str(data_manager.db_path), "--apply"]

        assert script.main(args) == 0
        assert "ignorée" in capsys.readouterr().out
        assert DisabledTracksManager().load_disabled_tracks("Aketo") == set()
