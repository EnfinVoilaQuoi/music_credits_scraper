"""Lot B — `duration`, `release_date` et `isrc` ont enfin une PROVENANCE.

Ces trois champs vivaient en colonne nue, sans source ni date. C'est exactement
ce qui manquait le 2026-09-08 pour voir qu'une durée avait suivi un mauvais
identifiant Spotify : 99 lignes portaient la durée, le BPM et la tonalité d'un
autre morceau, et rien en base ne pouvait le dire.

Ils sont réellement multi-sources (Deezer canonique, YTM secours, ReccoBeats,
Genius), donc arbitrés — mais **sans vote numérique** : ces sources mesurent la
MÊME grandeur, un écart est un décalage d'édition ou d'encodage, pas un
désaccord. Le résolveur n'est pas neuf : c'est `reconcile_spotify_streams`
généralisé, qui appliquait déjà exactement ces règles.
"""

from src.enrichment.observation import Observation
from src.enrichment.reconcile import (
    DISCOGRAPHY_PRIORITIES,
    apply_resolutions,
    reconcile,
    reconcile_spotify_streams,
    resolve_by_priority,
)
from src.models import Artist, Track


def _obs(field, value, source, confidence=None):
    return Observation(field=field, value=value, source=source, confidence=confidence)


class TestResolveByPriority:
    def test_l_ordre_prime_sur_la_confiance(self):
        """Pas de vote : la première source de l'ordre qui a la donnée gagne,
        quelle que soit la « confiance » que porte une autre."""
        res = resolve_by_priority(
            [_obs("duration", 210, "ytmusic", confidence=9), _obs("duration", 200, "deezer")],
            "duration",
            ("deezer", "ytmusic"),
        )
        assert (res.value, res.source) == (200, "deezer")

    def test_la_suivante_sert_de_repli(self):
        res = resolve_by_priority(
            [_obs("duration", 210, "ytmusic")], "duration", ("deezer", "ytmusic")
        )
        assert (res.value, res.source) == (210, "ytmusic")

    def test_manual_court_circuite(self):
        """Une saisie humaine prime sur toute mesure, sinon elle serait écrasée
        à la relecture suivante (le bug corrigé en tête de phase E7)."""
        res = resolve_by_priority(
            [_obs("duration", 200, "deezer"), _obs("duration", 199, "manual")],
            "duration",
            ("deezer", "ytmusic"),
        )
        assert (res.value, res.source) == (199, "manual")

    def test_legacy_vaut_verbatim_s_il_est_SEUL(self):
        res = resolve_by_priority([_obs("duration", 248, "legacy")], "duration", ("deezer",))
        assert (res.value, res.source) == (248, "legacy")

    def test_legacy_s_efface_des_qu_une_source_reelle_existe(self):
        res = resolve_by_priority(
            [_obs("duration", 248, "legacy"), _obs("duration", 200, "deezer")],
            "duration",
            ("deezer",),
        )
        assert (res.value, res.source) == (200, "deezer")

    def test_une_source_hors_ordre_ne_fait_pas_perdre_la_donnee(self):
        """Renommage, source non déclarée : on garde la valeur, mais on ne la
        fait pas passer pour le verdict d'une source connue."""
        res = resolve_by_priority([_obs("duration", 205, "inconnue")], "duration", ("deezer",))
        assert (res.value, res.source) == (205, "inconnue")

    def test_aucune_observation_ne_fabrique_rien(self):
        assert resolve_by_priority([], "duration", ("deezer",)) is None


class TestLesStreamsNOntPasChange:
    """`reconcile_spotify_streams` devient un APPEL du résolveur généralisé :
    son comportement doit être identique au byte près."""

    def test_le_maitre_gagne(self):
        res = reconcile_spotify_streams(
            [_obs("spotify_streams", 100, "kworb"), _obs("spotify_streams", 200, "spotify_web")],
            "spotify_web",
        )
        assert (res.value, res.source) == (200, "spotify_web")

    def test_repli_quand_le_maitre_n_a_rien(self):
        res = reconcile_spotify_streams([_obs("spotify_streams", 100, "kworb")], "spotify_web")
        assert (res.value, res.source) == (100, "kworb")


class TestOrdresDeclares:
    def test_reccobeats_est_en_QUEUE_de_l_ordre_duration(self):
        """C'est la source qui a écrit les durées contaminées : elle s'interroge
        PAR le Track ID Spotify et rend la durée du morceau que cet ID désigne.
        La mettre en tête referait le dégât que le lot A-bis vient de réparer."""
        ordre = DISCOGRAPHY_PRIORITIES["duration"]
        assert ordre[0] == "deezer"
        assert ordre.index("reccobeats") == len(ordre) - 1

    def test_deezer_devant_ytm(self):
        """Ce que `CLAUDE.md` énonçait déjà en prose — « Deezer canonique, YTM
        secours » — descend enfin dans le moteur."""
        ordre = DISCOGRAPHY_PRIORITIES["duration"]
        assert ordre.index("deezer") < ordre.index("ytmusic")

    def test_reconcile_applique_l_ordre_et_non_le_repli_generique(self):
        """Sans branche dédiée, ces champs tomberaient dans `_best`, qui départage
        par la fiabilité BPM des sources — un classement sans aucun sens pour une
        durée (`reccobeats` y a le rang 3, `deezer` le rang 0 : l'ordre serait
        exactement INVERSÉ)."""
        res = reconcile([_obs("duration", 241, "reccobeats"), _obs("duration", 248, "deezer")])
        assert (res["duration"].value, res["duration"].source) == (248, "deezer")


class TestMaterialisation:
    """La colonne SUBSISTE : la GUI, les exports, `cert_matcher` et `structure`
    lisent tous `track.duration`. Triple écriture, comme paroles et streams."""

    def _track(self):
        return Track(title="Un pour la plume", artist=Artist(name="Flynt"))

    def test_la_duree_est_posee_sur_le_track(self):
        track = self._track()
        apply_resolutions(track, reconcile([_obs("duration", 249, "deezer")]))
        assert track.duration == 249

    def test_une_duree_legacy_en_CHAINE_est_coercee(self):
        """Le backfill e24 reprend la colonne telle quelle, et elle est
        hétérogène : 19 valeurs y sont des chaînes « 2:30 ». La coercition est
        celle du mapper, PARTAGÉE — en écrire une seconde ici serait un second
        verdict, et c'est ce qui a fait planter l'audit au premier passage."""
        track = self._track()
        apply_resolutions(track, reconcile([_obs("duration", "2:30", "legacy")]))
        assert track.duration == 150

    def test_isrc_et_date_sont_poses(self):
        track = self._track()
        apply_resolutions(
            track,
            reconcile(
                [
                    _obs("isrc", "FRPJQ1501290", "deezer"),
                    _obs("release_date", "2006-01-01", "genius"),
                ]
            ),
        )
        assert track.isrc == "FRPJQ1501290"
        assert track.release_date == "2006-01-01"

    def test_un_champ_absent_du_verdict_n_est_pas_touche(self):
        """Le COALESCE de `save_track` préserve l'existant : le moteur ne doit
        jamais poser un `None` qui ferait passer « pas mesuré » pour « vide »."""
        track = self._track()
        track.duration = 249
        apply_resolutions(track, reconcile([_obs("bpm", 92, "deezer")]))
        assert track.duration == 249


class TestRepliGeneriqueSAnnonce:
    """Un champ sans règle déclarée est arbitré par la fiabilité **BPM**.

    C'est le sens de `_best` : il classe par `BPM_SOURCE_RANK`. Tant qu'un champ
    n'a qu'une source, cela ne se voit pas ; à deux, il est tranché par une
    échelle qui ne le concerne en rien — « genre » revient à ReccoBeats contre
    Deezer parce que ReccoBeats a le rang BPM 3.

    Le piège était SILENCIEUX. Il ne l'est plus : la réponse attendue est de
    déclarer un ordre dans `DISCOGRAPHY_PRIORITIES`, ou une stratégie dédiée.
    """

    def test_deux_sources_sur_un_champ_sans_regle_declenchent_un_avertissement(self, caplog):
        with caplog.at_level("WARNING"):
            reconcile([_obs("genre", "Rap", "deezer"), _obs("genre", "Hip-Hop", "reccobeats")])

        assert any("repli GÉNÉRIQUE" in m for m in caplog.messages)
        assert any("genre" in m for m in caplog.messages)

    def test_une_seule_source_reste_muette(self, caplog):
        """Un garde-fou qui crie toujours ne garde rien : le cas mono-source est
        le cas NORMAL de ce repli."""
        with caplog.at_level("WARNING"):
            reconcile([_obs("genre", "Rap", "deezer")])

        assert not any("repli GÉNÉRIQUE" in m for m in caplog.messages)

    def test_les_champs_a_regle_ne_declenchent_rien(self, caplog):
        """`duration` a un ordre déclaré : il ne passe pas par le repli."""
        with caplog.at_level("WARNING"):
            reconcile([_obs("duration", 249, "deezer"), _obs("duration", 241, "reccobeats")])

        assert not any("repli GÉNÉRIQUE" in m for m in caplog.messages)
