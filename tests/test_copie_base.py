"""`scripts/copie_base.py` : une copie de base À SOI, par l'API de sauvegarde.

Cas d'origine (2026-10-30) : un agent a écrit `copie.db` à la racine du
scratchpad partagé et écrasé la copie d'un autre. Les refus sont prouvés ici,
ainsi que la copie des écritures encore dans le `-wal` (ce qu'un `cp` perdrait).
"""

import sqlite3
from contextlib import closing

import pytest

from scripts import copie_base


@pytest.fixture
def base(tmp_path):
    """Une base en WAL dont la dernière écriture n'est que dans le `-wal`."""
    chemin = tmp_path / "source" / "music_credits.db"
    chemin.parent.mkdir()
    conn = sqlite3.connect(chemin)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE t (x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    yield chemin
    conn.close()


def _valeurs(chemin):
    with closing(sqlite3.connect(chemin)) as conn:
        return [r[0] for r in conn.execute("SELECT x FROM t")]


def test_copie_par_l_api_de_sauvegarde_wal_compris(base, tmp_path):
    assert (base.parent / "music_credits.db-wal").stat().st_size > 0
    cible = copie_base.copier_base(tmp_path / "a_moi", base)
    assert cible == tmp_path / "a_moi" / "music_credits.db"
    assert _valeurs(cible) == [42]


def test_refuse_la_racine_du_scratchpad(base, tmp_path):
    with pytest.raises(copie_base.CopieRefusee, match="scratchpad"):
        copie_base.copier_base(tmp_path / "scratchpad", base)
    assert not (tmp_path / "scratchpad").exists()


def test_un_sous_dossier_du_scratchpad_passe(base, tmp_path):
    cible = copie_base.copier_base(tmp_path / "scratchpad" / "agent_x", base)
    assert _valeurs(cible) == [42]


def test_refuse_la_racine_d_un_depot(base, tmp_path):
    depot = tmp_path / "depot"
    (depot / ".git").mkdir(parents=True)
    with pytest.raises(copie_base.CopieRefusee, match="dépôt"):
        copie_base.copier_base(depot, base)
    # Un worktree : `.git` est un FICHIER.
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: ailleurs", encoding="utf-8")
    with pytest.raises(copie_base.CopieRefusee, match="dépôt"):
        copie_base.copier_base(worktree, base)


def test_refuse_d_ecraser_une_copie_sans_option(base, tmp_path):
    dossier = tmp_path / "a_moi"
    dossier.mkdir()
    (dossier / "music_credits.db").write_bytes(b"copie d'un autre")
    with pytest.raises(copie_base.CopieRefusee, match="--ecraser"):
        copie_base.copier_base(dossier, base)
    assert (dossier / "music_credits.db").read_bytes() == b"copie d'un autre"
    copie_base.copier_base(dossier, base, ecraser=True)
    assert _valeurs(dossier / "music_credits.db") == [42]


def test_refuse_de_s_ecraser_elle_meme(base):
    with pytest.raises(copie_base.CopieRefusee, match="EST la base source"):
        copie_base.copier_base(base.parent, base, ecraser=True)
    assert _valeurs(base) == [42]


def test_refuse_une_source_absente(tmp_path):
    with pytest.raises(copie_base.CopieRefusee, match="introuvable"):
        copie_base.copier_base(tmp_path / "a_moi", tmp_path / "rien.db")


def test_main_rend_2_sur_un_refus(base, tmp_path, capsys):
    assert copie_base.main([str(tmp_path / "scratchpad"), "--base", str(base)]) == 2
    assert "scratchpad" in capsys.readouterr().err
    assert copie_base.main([str(tmp_path / "a_moi"), "--base", str(base)]) == 0
