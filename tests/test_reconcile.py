"""Tests du moteur de réconciliation (src/enrichment/reconcile).

Le moteur est PUR : il prend des `Observation` et rend un verdict par champ.
- BPM : on vérifie que la sémantique de `reconcile_bpm` (§8.3) est préservée à
  travers la couche observations (mêmes cas que test_bpm_vote, format différent).
- key/mode : la paire d'une source complète bat toute source incomplète.
- défaut : « meilleure confiance » puis fiabilité de source.
"""

from src.enrichment.observation import Observation
from src.enrichment.reconcile import apply_resolutions, reconcile
from src.models import Artist, Track


def _obs(field, value, source, confidence=None):
    return Observation(field=field, value=value, source=source, confidence=confidence)


class TestReconcileBpm:
    def test_deux_octaves_double_confirme(self):
        # Parité avec test_bpm_vote : 74 + 145 → valeur haute mesurée, alt basse.
        res = reconcile([_obs("bpm", 74, "deezer"), _obs("bpm", 145, "reccobeats")])
        bpm = res["bpm"]
        assert bpm.value == 145
        assert bpm.alt == 74
        assert bpm.confidence == 2
        assert bpm.source == "reccobeats+deezer"

    def test_halftime_isole_double(self):
        res = reconcile([_obs("bpm", 71, "deezer")])
        assert res["bpm"].value == 142
        assert res["bpm"].alt == 71

    def test_valeurs_invalides_ignorees(self):
        # 900 hors borne, "abc" non numérique → seul 120 subsiste.
        res = reconcile(
            [
                _obs("bpm", 900, "deezer"),
                _obs("bpm", "abc", "songbpm"),
                _obs("bpm", 120, "getsongbpm"),
            ]
        )
        assert res["bpm"].value == 120

    def test_aucun_bpm_valide_pas_de_resolution(self):
        res = reconcile([_obs("bpm", None, "deezer"), _obs("bpm", 900, "songbpm")])
        assert "bpm" not in res

    def test_le_vote_prime_sur_la_fiabilite(self):
        res = reconcile(
            [
                _obs("bpm", 100, "deezer"),
                _obs("bpm", 101, "songbpm"),
                _obs("bpm", 150, "reccobeats"),
            ]
        )
        assert res["bpm"].value == 101
        assert res["bpm"].confidence == 2


class TestReconcileKeyMode:
    def test_source_complete_bat_source_incomplete(self):
        # reccobeats fournit key+mode ; getsongbpm seulement key → paire reccobeats.
        res = reconcile(
            [
                _obs("key", 5, "reccobeats"),
                _obs("mode", 1, "reccobeats"),
                _obs("key", 7, "getsongbpm"),
            ]
        )
        assert res["key"].value == 5
        assert res["key"].source == "reccobeats"
        assert res["mode"].value == 1
        assert res["mode"].source == "reccobeats"

    def test_complete_bat_incomplete_meme_avec_confiance_moindre(self):
        # getsongbpm complet (conf None) bat reccobeats incomplet (conf haute).
        res = reconcile(
            [
                _obs("key", 3, "getsongbpm"),
                _obs("mode", 0, "getsongbpm"),
                _obs("key", 9, "reccobeats", confidence=5),
            ]
        )
        assert res["key"].source == "getsongbpm"
        assert res["mode"].source == "getsongbpm"

    def test_deux_sources_completes_la_fiabilite_tranche(self):
        # reccobeats (rang 3) > getsongbpm (rang 2) à confiance égale (None).
        res = reconcile(
            [
                _obs("key", 5, "reccobeats"),
                _obs("mode", 1, "reccobeats"),
                _obs("key", 7, "getsongbpm"),
                _obs("mode", 0, "getsongbpm"),
            ]
        )
        assert res["key"].value == 5
        assert res["mode"].value == 1

    def test_aucune_source_complete_rien_emis(self):
        # deezer ne donne que key, songbpm que mode : aucune paire complète →
        # aucun verdict (mais les observations sont conservées côté persistance
        # pour le run suivant, hors périmètre du moteur pur).
        res = reconcile([_obs("key", 4, "deezer"), _obs("mode", 1, "songbpm")])
        assert "key" not in res
        assert "mode" not in res

    def test_key_seul_sans_mode_rien_emis(self):
        res = reconcile([_obs("key", 4, "deezer")])
        assert res == {}


class TestReconcileManual:
    """La source `manual` court-circuite le vote (correction humaine gagne toujours)."""

    def test_manual_bat_un_vote_bpm_a_trois_sources(self):
        # 3 sources concordent sur 120 (confiance 3), mais une saisie manuelle à
        # 95 doit primer : verdict verbatim, sans départage demi/double.
        res = reconcile(
            [
                _obs("bpm", 120, "deezer"),
                _obs("bpm", 120, "getsongbpm"),
                _obs("bpm", 120, "reccobeats"),
                _obs("bpm", 95, "manual"),
            ]
        )
        assert res["bpm"].value == 95
        assert res["bpm"].source == "manual"
        assert res["bpm"].alt is None

    def test_manual_bpm_isole_non_double(self):
        # 71 seul via une source auto serait doublé (142) ; manuel = verbatim.
        res = reconcile([_obs("bpm", 71, "manual")])
        assert res["bpm"].value == 71
        assert res["bpm"].source == "manual"

    def test_manual_bpm_invalide_retombe_sur_le_vote(self):
        # Valeur manuelle hors borne → ignorée, le vote reprend la main.
        res = reconcile([_obs("bpm", 900, "manual"), _obs("bpm", 120, "deezer")])
        assert res["bpm"].value == 120
        assert res["bpm"].source == "deezer"

    def test_manual_key_mode_bat_une_paire_complete(self):
        # reccobeats fournit une paire complète, mais la saisie manuelle prime.
        res = reconcile(
            [
                _obs("key", 5, "reccobeats"),
                _obs("mode", 1, "reccobeats"),
                _obs("key", 8, "manual"),
                _obs("mode", 0, "manual"),
            ]
        )
        assert res["key"].value == 8
        assert res["key"].source == "manual"
        assert res["mode"].value == 0
        assert res["mode"].source == "manual"

    def test_manual_key_mode_seuls_emettent(self):
        # Manuel = paire complète à lui seul → verdict même sans autre source.
        res = reconcile([_obs("key", 2, "manual"), _obs("mode", 1, "manual")])
        assert res["key"].value == 2
        assert res["mode"].value == 1


class TestReconcileDefault:
    def test_meilleure_confiance_gagne(self):
        res = reconcile(
            [
                _obs("duration", 200, "deezer", confidence=1),
                _obs("duration", 210, "songbpm", confidence=3),
            ]
        )
        assert res["duration"].value == 210
        assert res["duration"].source == "songbpm"

    def test_confiance_none_perd_contre_numerique(self):
        res = reconcile(
            [_obs("duration", 200, "deezer"), _obs("duration", 210, "songbpm", confidence=0)]
        )
        assert res["duration"].value == 210

    def test_egalite_confiance_la_fiabilite_tranche(self):
        # reccobeats (rang 3) > deezer (rang 0) à confiance égale.
        res = reconcile(
            [
                _obs("genre", "rap", "deezer", confidence=1),
                _obs("genre", "trap", "reccobeats", confidence=1),
            ]
        )
        assert res["genre"].value == "trap"


class TestReconcileEmpty:
    def test_aucune_observation(self):
        assert reconcile([]) == {}


class TestApplyResolutions:
    """apply_resolutions pilote les colonnes legacy (remplace BpmBallot.finalize)."""

    def _track(self, **attrs):
        track = Track(title="X", artist=Artist(name="A"))
        for name, value in attrs.items():
            setattr(track, name, value)
        return track

    def test_bpm_pilote_les_quatre_colonnes(self):
        track = self._track()
        apply_resolutions(
            track, reconcile([_obs("bpm", 74, "deezer"), _obs("bpm", 145, "reccobeats")])
        )
        assert track.bpm == 145
        assert track.bpm_alt == 74
        assert track.bpm_source == "reccobeats+deezer"
        assert track.bpm_confidence == 2  # INTEGER legacy (float du moteur casté)

    def test_key_mode_normalises_et_musical_key(self):
        # songbpm "minor" + reccobeats numériques → paire complète, mode=0 (fix bug).
        track = self._track()
        obs = [
            _obs("key", 8, "reccobeats"),
            _obs("mode", 0, "reccobeats"),
            _obs("key", 8, "songbpm"),
            _obs("mode", 0, "songbpm"),
        ]
        apply_resolutions(track, reconcile(obs))
        assert track.key == 8
        assert track.mode == 0
        assert track.key_mode_source == "reccobeats"
        assert track.musical_key == "Sol#/Lab mineur"

    def test_champ_absent_non_touche(self):
        # Aucune observation bpm → bpm existant préservé (pas d'écrasement).
        track = self._track(bpm=99, bpm_source="manuel")
        apply_resolutions(track, reconcile([_obs("key", 5, "deezer")]))
        assert track.bpm == 99
        assert track.bpm_source == "manuel"

    def test_paire_incomplete_ne_pilote_pas(self):
        # key seul (pas de mode) → moteur n'émet rien → track.key inchangé.
        track = self._track()
        apply_resolutions(track, reconcile([_obs("key", 5, "deezer")]))
        assert getattr(track, "key", None) is None
