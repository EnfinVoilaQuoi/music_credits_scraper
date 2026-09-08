"""Les producteurs DÉCLARENT durée, date et ISRC — ils ne font plus que les poser.

Avant le lot B, ces trois champs arrivaient en colonne sans dire d'où ils
venaient. Le contraste était net avec le reste de la base : `observations` porte
`(field, value, source, confidence, seen_at)` pour neuf champs et six sources.

Et l'absence n'était pas théorique : le 2026-09-08, 66 morceaux portaient une
durée venue d'un identifiant Spotify fautif (ReccoBeats s'interroge PAR le Track
ID), et rien ne pouvait le dire — le nettoyage a dû se rabattre sur l'inférence
« il y avait une observation ReccoBeats, donc la durée en vient probablement ».
"""

from src.enrichment.context import EnrichmentContext
from src.enrichment.providers.deezer import DeezerProvider
from src.enrichment.providers.reccobeats import ReccoBeatsProvider
from src.models import Artist, Track


def _track(**kw):
    t = Track(title="Un pour la plume", artist=Artist(name="Flynt"))
    for k, v in kw.items():
        setattr(t, k, v)
    return t


def _resultat_deezer(**data):
    base = {
        "deezer_duration": 249,
        "deezer_release_date": "2006-01-01",
        "deezer_isrc": "FRPJQ1501290",
    }
    base.update(data)
    return {"success": True, "data": base, "verifications": {}}


def _observations(ctx):
    return {(o.field, o.value, o.source) for o in ctx.observations}


class TestDeezer:
    """Deezer est la source CANONIQUE des trois : son ordre de priorité le dit."""

    def test_les_trois_champs_sont_declares(self):
        provider = DeezerProvider(client=object())
        ctx = EnrichmentContext()
        track = _track()

        provider._apply_result(track, ctx, _resultat_deezer(), None, None)

        assert _observations(ctx) == {
            ("duration", 249, "deezer"),
            ("release_date", "2006-01-01", "deezer"),
            ("isrc", "FRPJQ1501290", "deezer"),
        }

    def test_une_valeur_REFUSEE_n_est_pas_declaree(self):
        """Une observation n'est pas une note de bas de page, c'est un BULLETIN.

        Ce test affirmait d'abord le CONTRAIRE — « déclarer même ce qu'on ne suit
        pas, c'est tout l'intérêt de la provenance » — et il était faux : Deezer
        étant en TÊTE de l'ordre de priorité, la valeur écartée ici pour
        incohérence regagnait la colonne à la relecture suivante (mesuré :
        refusée à 999, relue à 999). Le contrôle de cohérence juge de la
        VALIDITÉ de la mesure, pas d'une préférence : ce qu'on tient pour
        invalide n'entre pas au vote, comme un ID Spotify refusé n'est pas
        enregistré « pour mémoire ».
        """
        provider = DeezerProvider(client=object())
        ctx = EnrichmentContext()
        track = _track(duration=230)
        resultat = _resultat_deezer()
        resultat["verifications"] = {"duration": {"is_valid": False, "message": "écart"}}

        provider._apply_result(track, ctx, resultat, 230, None)

        assert track.duration == 230
        assert not any(o.field == "duration" for o in ctx.observations)

    def test_une_date_REFUSEE_n_est_pas_declaree_non_plus(self):
        """Même règle : une date qui contredit le scrape ne doit pas revenir par
        l'observation. Les deux champs partagent le défaut, donc le correctif."""
        provider = DeezerProvider(client=object())
        ctx = EnrichmentContext()
        track = _track(release_date="2006-01-01")
        resultat = _resultat_deezer(deezer_release_date="2019-09-26")
        resultat["verifications"] = {
            # `is_valid` est lu par la journalisation des vérifications : le
            # producteur l'attend sur CHAQUE entrée, quel qu'en soit le champ.
            "release_date": {"is_valid": False, "dates_match": False, "message": "écart"}
        }

        provider._apply_result(track, ctx, resultat, None, "2006-01-01")

        assert track.release_date == "2006-01-01"
        assert not any(o.field == "release_date" for o in ctx.observations)

    def test_une_valeur_RETENUE_est_bien_declaree(self):
        """Le pendant : sans observation, la valeur ne vit que dans la colonne et
        n'a toujours aucune provenance — ce que le lot B vient corriger."""
        provider = DeezerProvider(client=object())
        ctx = EnrichmentContext()
        track = _track(duration=230)
        resultat = _resultat_deezer()
        resultat["verifications"] = {"duration": {"is_valid": True, "message": "ok"}}

        provider._apply_result(track, ctx, resultat, 230, None)

        assert track.duration == 249
        assert ("duration", 249, "deezer") in _observations(ctx)

    def test_un_champ_absent_ne_declare_rien(self):
        provider = DeezerProvider(client=object())
        ctx = EnrichmentContext()

        provider._apply_result(
            _track(),
            ctx,
            _resultat_deezer(deezer_duration=None, deezer_release_date=None, deezer_isrc=None),
            None,
            None,
        )

        assert _observations(ctx) == set()


class TestReccoBeats:
    """La source qui a contaminé 66 durées. La DÉCLARER est ce qui aurait permis
    de le voir — et l'ordre de priorité la place en queue, derrière Deezer."""

    def test_la_duree_est_declaree_meme_si_la_colonne_est_deja_remplie(self):
        """Avant le lot B, ReccoBeats n'écrivait la durée QUE si elle était vide
        (« non destructif ») : sa mesure disparaissait donc sans laisser de trace
        dès qu'une autre source était passée avant. Elle est désormais déclarée
        dans tous les cas — c'est le moteur qui arbitre, pas l'ordre d'arrivée."""
        provider = ReccoBeatsProvider(client=object())
        ctx = EnrichmentContext()
        track = _track(duration=249)

        provider._apply_result(track, {"duration": 241}, ctx)

        assert track.duration == 249  # la colonne reste à la valeur déjà connue
        assert ("duration", 241, "reccobeats") in _observations(ctx)

    def test_une_duree_nulle_ou_absente_ne_declare_rien(self):
        provider = ReccoBeatsProvider(client=object())
        ctx = EnrichmentContext()

        provider._apply_result(_track(), {"duration": 0}, ctx)

        assert not any(o.field == "duration" for o in ctx.observations)
