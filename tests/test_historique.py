"""Historique d'une fiche (`src/services/historique.py`, 2026-10-31) : on ne
retraite pas ce qui a été tranché. Cas d'origine : « Écorchée Vive » de Diam's,
fusion proposée avec le live 2006 alors que l'utilisateur avait jugé, le 13/10,
que les deux lives étaient deux fiches."""

from types import SimpleNamespace

from src.models import Artist, Track
from src.services import historique, revue
from src.utils import corrections_fiches as cf


def _deux_lives(data_manager):
    diams = Artist(name="Diam's")
    diams.id = data_manager.save_artist(diams)
    vive = Track(title="Écorchée Vive", artist=diams)
    vive.genius_id = 2393504
    vive.album = "Au tour de ma bulle"
    data_manager.save_track(vive)
    live = Track(title="Écorchée - Live 2006", artist=diams)
    live.album = "Au Tour De Ma Bulle"
    data_manager.save_track(live)
    cf.memoriser_id_refuse(vive, "3mypc8pYVlgIuXlXkG1LF6")
    return diams, vive, live


def test_la_famille_et_ses_decisions(data_manager):
    _, vive, live = _deux_lives(data_manager)
    with data_manager.engine.connect() as conn:
        membres = historique.famille(conn, live.id)
        decisions = historique.decisions(conn, membres)
    # Le live 2006 est de la famille de la page « Écorchée Vive » (titres parents).
    assert {m.track_id for m in membres} == {vive.id, live.id}
    (refus,) = [d for d in decisions if d.sorte == "refus"]
    assert refus.humaine and refus.fiche == "Écorchée Vive" and "3mypc8" in refus.quoi


def test_le_panneau_marque_une_fiche_decidee(data_manager):
    diams, vive, live = _deux_lives(data_manager)
    decidees = data_manager.decisions_humaines_par_fiche(diams.id)
    assert set(decidees) == {vive.id}
    cas = [
        revue.Cas("lrc_trop_long", vive.id, "Écorchée Vive", "motif"),
        revue.Cas("lrc_trop_long", live.id, "Écorchée - Live 2006", "motif"),
    ]
    dm = SimpleNamespace(decisions_humaines_par_fiche=lambda _id: decidees)
    marque, intacte = revue._marquer_les_fiches_decidees(dm, diams, cas)
    assert "⚖️ décidé le" in marque.motif and marque.cle == cas[0].cle
    assert intacte.motif == "motif"


def test_une_decision_de_regle_ne_marque_pas(data_manager):
    diams, vive, _ = _deux_lives(data_manager)
    cf.memoriser_lrc_refuse(vive, "[00:01.00] x", origine="regle:lrc_original")
    with data_manager.engine.connect() as conn:
        membres = historique.famille(conn, vive.id)
        regles = [d for d in historique.decisions(conn, membres) if not d.humaine]
    assert [d.quoi.split()[0] for d in regles] == ["lrc"]
    # Seul le refus de l'ID, posé à la main, marque la fiche.
    assert data_manager.decisions_humaines_par_fiche(diams.id)[vive.id].sorte == "refus"
    assert "id_spotify" in data_manager.decisions_humaines_par_fiche(diams.id)[vive.id].quoi


def test_les_notes_qui_en_parlent(data_manager, tmp_path):
    _, vive, _ = _deux_lives(data_manager)
    (tmp_path / "CORRECTIFS.md").write_text(
        f"- Écorchée : deux lives = deux fiches.\n- Autre chose.\n- voir #{vive.id}\n",
        encoding="utf-8",
    )
    (tmp_path / "journal").mkdir()
    (tmp_path / "journal" / "2026-09.md").write_text("- ecorchee vive relue\n", encoding="utf-8")
    with data_manager.engine.connect() as conn:
        membres = historique.famille(conn, vive.id)
    lignes = historique.mentions(tmp_path, membres, vive.id)
    assert [ligne.split()[0] for ligne in lignes] == [
        "CORRECTIFS.md:3",
        "journal/2026-09.md:1",
    ]
