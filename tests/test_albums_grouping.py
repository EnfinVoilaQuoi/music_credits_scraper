"""Regroupement et tri des albums (`src/gui/albums_grouping.py`).

Ces trois fonctions étaient imbriquées dans une méthode de widget d'`albums_view`
alors qu'elles décident de l'ORDRE d'affichage de toute la discographie. Sorties
et testées le 2026-09-05.

Le parsing de date délègue désormais à `dates.parse_flexible` : la version
imbriquée le refaisait à la main avec un `except Exception` nu et ne gérait que
l'ISO tronqué au `T`.
"""

from datetime import UTC, datetime

import pytest

from src.gui import albums_grouping as ag
from src.models import Artist, Track


def _track(release_date):
    return Track(title="T", artist=Artist(name="A"), release_date=release_date)


class TestDateLaPlusAncienne:
    """C'est la date la plus ANCIENNE qui situe un album : un bonus ajouté plus
    tard ne doit pas faire remonter le projet dans la chronologie."""

    def test_la_plus_ancienne_gagne(self):
        tracks = [_track("2021-06-15"), _track("2019-01-01"), _track("2023-12-31")]
        assert ag.earliest_date(tracks) == datetime(2019, 1, 1)

    def test_objets_datetime(self):
        assert ag.earliest_date([_track(datetime(2020, 5, 4))]) == datetime(2020, 5, 4)

    @pytest.mark.parametrize(
        "valeur",
        ["2021-06-15", "2021-06-15T00:00:00", "2021-06-15 12:30:00", "2021-06-15T00:00:00Z"],
    )
    def test_formes_iso_acceptees(self, valeur):
        """La version imbriquée coupait au `T` à la main ; `parse_flexible` couvre
        aussi l'heure séparée par une espace et le suffixe `Z`."""
        assert ag.earliest_date([_track(valeur)]).date() == datetime(2021, 6, 15).date()

    def test_dates_illisibles_ignorees_sans_lever(self):
        tracks = [_track("pas une date"), _track("2021-06-15"), _track(None)]
        assert ag.earliest_date(tracks) == datetime(2021, 6, 15)

    def test_aucune_date_lisible(self):
        assert ag.earliest_date([_track(None), _track("")]) is None

    def test_groupe_vide(self):
        assert ag.earliest_date([]) is None

    def test_melange_aware_et_naif_ne_leve_pas(self):
        """`parse_flexible` rend un datetime AWARE sur suffixe `Z` : le comparer
        à un naïf lèverait TypeError si on ne ramenait pas tout au naïf."""
        tracks = [_track("2021-06-15T00:00:00Z"), _track("2019-01-01")]
        assert ag.earliest_date(tracks) == datetime(2019, 1, 1)

    def test_deux_dates_aware(self):
        tracks = [_track("2021-06-15T00:00:00Z"), _track("2019-01-01T00:00:00Z")]
        assert ag.earliest_date(tracks).replace(tzinfo=UTC).year == 2019


class TestRangDesSections:
    """Featurings et Singles sont des fourre-tout : ils passent en fin de liste,
    pas au milieu de la chronologie."""

    def test_album_ordinaire(self):
        assert ag.group_rank("J.O.S") == 0

    def test_featurings_avant_singles(self):
        assert ag.group_rank("🎤 Featurings (albums invités)") == 1
        assert ag.group_rank("— Singles / sans album —") == 2

    def test_un_album_dont_le_nom_commence_par_un_tiret_court(self):
        """Le préfixe des singles est un tiret CADRATIN, pas un trait d'union."""
        assert ag.group_rank("-Ultra-") == 0


class TestTri:
    def test_albums_du_plus_recent_au_plus_ancien(self):
        groups = {
            "Vieux": [_track("2015-01-01")],
            "Recent": [_track("2024-01-01")],
            "Median": [_track("2020-01-01")],
        }
        assert [nom for nom, _ in ag.sort_groups(groups)] == ["Recent", "Median", "Vieux"]

    def test_sections_fourre_tout_en_dernier(self):
        groups = {
            "🎤 Featurings": [_track("2024-06-01")],
            "— Singles —": [_track("2025-01-01")],
            "Album ancien": [_track("2015-01-01")],
        }
        noms = [nom for nom, _ in ag.sort_groups(groups)]
        assert noms == ["Album ancien", "🎤 Featurings", "— Singles —"]

    def test_album_sans_date_relegue_en_fin_de_son_rang(self):
        """BUG TROUVÉ EN EXTRAYANT, corrigé le 2026-09-05 : le tri repliait sur
        `datetime.min` puis appelait `.timestamp()`, qui lève `OSError` sous
        Windows (l'an 1 ne se convertit pas). Un groupe entièrement sans date
        faisait donc échouer le remplissage de TOUTE la vue Albums — mesuré,
        13 groupes sur 438 dans la base réelle sont dans ce cas."""
        groups = {"Sans date": [_track(None)], "Date connue": [_track("2015-01-01")]}
        assert [nom for nom, _ in ag.sort_groups(groups)] == ["Date connue", "Sans date"]

    def test_plusieurs_groupes_sans_date(self):
        groups = {"A": [_track(None)], "B": [_track(None)], "Datee": [_track("2015-01-01")]}
        assert ag.sort_groups(groups)[0][0] == "Datee"

    def test_aucun_groupe(self):
        assert ag.sort_groups({}) == []


class TestFormatageDesStreams:
    @pytest.mark.parametrize(
        ("valeur", "attendu"),
        [(1234567, "1 234 567"), (1000, "1 000"), (42, "42")],
    )
    def test_separateur_de_milliers(self, valeur, attendu):
        assert ag.format_streams(valeur) == attendu

    @pytest.mark.parametrize("valeur", [0, None, ""])
    def test_absence_rendue_vide_et_non_zero(self, valeur):
        """Un « 0 » se lirait comme une mesure ; l'absence de collecte n'en est pas une."""
        assert ag.format_streams(valeur) == ""
