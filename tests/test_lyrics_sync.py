"""Tests de lyrics_sync — parsing LRC, arbitrage LRCLIB/YTM, aligneur de sections.

Verrouille en particulier la MONOTONIE de `annotate_sections` (piège documenté
dans CLAUDE.md : un refrain répété ne doit PAS se caler sur sa 1ʳᵉ occurrence).
"""

from src.utils.lyrics_sync import (
    annotate_sections,
    compare_synced,
    lrc_last_timestamp,
    parse_lrc,
    sync_error,
)


class TestParseLrc:
    def test_format_standard(self):
        assert parse_lrc("[00:12.34] Hello") == [(12.34, "Hello")]

    def test_sans_centisecondes(self):
        assert parse_lrc("[01:02]Ligne") == [(62.0, "Ligne")]

    def test_separateur_deux_points(self):
        # Variante [mm:ss:cs] acceptée par la regex
        assert parse_lrc("[00:12:34] Hello") == [(12.34, "Hello")]

    def test_lignes_non_lrc_ignorees(self):
        lrc = "titre du morceau\n[00:05.00] Première\npas un timestamp\n[00:10.00] Deuxième"
        assert parse_lrc(lrc) == [(5.0, "Première"), (10.0, "Deuxième")]

    def test_vide_et_none(self):
        assert parse_lrc("") == []
        assert parse_lrc(None) == []


class TestSyncError:
    def test_depassement_penalise_double(self):
        # Dernier timestamp 110 s pour une durée de 100 s → over=10 → erreur 20
        assert sync_error("[01:50.00] fin", 100.0) == 20.0

    def test_deficit_penalite_simple(self):
        # Dernier timestamp 90 s pour une durée de 100 s → under=10 → erreur 10
        assert sync_error("[01:30.00] fin", 100.0) == 10.0

    def test_sans_duree_ou_sans_lrc(self):
        assert sync_error("[01:30.00] fin", None) is None
        assert sync_error("", 100.0) is None

    def test_last_timestamp(self):
        assert lrc_last_timestamp("[00:05.00] a\n[00:42.00] b") == 42.0
        assert lrc_last_timestamp("pas de lrc") is None


class TestCompareSynced:
    def _lrc(self, *lignes: tuple[int, str]) -> str:
        return "\n".join(f"[{t // 60:02d}:{t % 60:02d}.00] {txt}" for t, txt in lignes)

    def test_aucune_source(self):
        assert compare_synced(None, None) is None
        assert compare_synced("pas du lrc", "", duration=100) is None

    def test_source_unique_lrclib(self):
        lrc = self._lrc((5, "hello"))
        res = compare_synced(lrc, None)
        assert res["source"] == "LRCLIB"
        assert res["confidence"] == 1

    def test_source_unique_ytm(self):
        lrc = self._lrc((5, "hello"))
        res = compare_synced(None, lrc)
        assert res["source"] == "YouTube Music"
        assert res["confidence"] == 1

    def test_concordance_confidence_2_garde_lrclib(self):
        # Mêmes lignes, décalage < 2 s → concordant, LRCLIB retenu
        a = self._lrc((5, "premiere ligne"), (15, "deuxieme ligne"), (25, "troisieme ligne"))
        b = self._lrc((6, "premiere ligne"), (16, "deuxieme ligne"), (26, "troisieme ligne"))
        res = compare_synced(a, b, duration=30)
        assert res["source"] == "LRCLIB"
        assert res["confidence"] == 2
        assert res["lrc"] == a

    def test_divergence_duree_favorise_la_source_coherente(self):
        # LRCLIB déborde largement la durée réelle, YTM colle → YTM gagne
        lrclib = self._lrc((5, "premiere"), (15, "deuxieme"), (290, "fin tardive"))
        ytm = self._lrc((5, "autre un"), (15, "autre deux"), (95, "autre fin"))
        res = compare_synced(lrclib, ytm, duration=100)
        assert res["source"] == "YouTube Music"
        assert res["confidence"] == 1

    def test_divergence_sans_duree_priorite_lrclib(self):
        lrclib = self._lrc((5, "premiere"), (15, "deuxieme"), (290, "fin tardive"))
        ytm = self._lrc((5, "autre un"), (15, "autre deux"), (95, "autre fin"))
        res = compare_synced(lrclib, ytm, duration=None)
        assert res["source"] == "LRCLIB"
        assert res["confidence"] == 1


class TestAnnotateSections:
    def test_sections_annotees_avec_intervalles(self):
        structured = "\n".join(
            [
                "[Couplet 1]",
                "premiere ligne du couplet",
                "",
                "[Refrain]",
                "la ligne du refrain",
            ]
        )
        lrc = "\n".join(
            [
                "[00:05.00] premiere ligne du couplet",
                "[00:30.00] la ligne du refrain",
                "[00:55.00] derniere ligne",
            ]
        )
        out = annotate_sections(structured, lrc)
        lignes = out.splitlines()
        assert lignes[0] == "[Couplet 1  ⏱ 0:05 → 0:30]"
        assert lignes[3] == "[Refrain  ⏱ 0:30 → 0:55]"

    def test_monotonie_refrain_repete(self):
        """RÉGRESSION (CLAUDE.md) : un refrain répété doit se caler sur son
        occurrence SUIVANTE, pas revenir à la première → intervalles croissants."""
        structured = "\n".join(
            [
                "[Couplet 1]",
                "ligne unique du couplet un",
                "",
                "[Refrain]",
                "la la la le refrain",
                "",
                "[Couplet 2]",
                "ligne unique du couplet deux",
                "",
                "[Refrain]",
                "la la la le refrain",
            ]
        )
        lrc = "\n".join(
            [
                "[00:00.00] ligne unique du couplet un",
                "[00:10.00] la la la le refrain",
                "[00:30.00] ligne unique du couplet deux",
                "[00:50.00] la la la le refrain",
                "[01:10.00] outro",
            ]
        )
        out = annotate_sections(structured, lrc)
        lignes = out.splitlines()
        assert "0:10" in lignes[3]  # 1er refrain à 0:10
        assert "0:50" in lignes[9]  # 2e refrain à 0:50, PAS 0:10
        assert "0:10" not in lignes[9]

    def test_sans_lrc_inchange(self):
        structured = "[Couplet]\nune ligne"
        assert annotate_sections(structured, "") == structured

    def test_sans_en_tetes_inchange(self):
        structured = "juste des lignes\nsans sections"
        assert annotate_sections(structured, "[00:05.00] juste des lignes") == structured
