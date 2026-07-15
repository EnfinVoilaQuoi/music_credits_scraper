"""Tests de l'élection du canal artiste par vote (src/api/ytmusic_api).

Régression : sur un artiste niche+homonyme dont les morceaux comptent beaucoup
de featurings, les voix se dispersent. On exigeait autrefois la majorité absolue
→ le bon canal (ex. 3/8) était jeté puis le fallback-par-nom prenait le mauvais
canal. Règle actuelle : PLURALITÉ NETTE (≥ 2 voix ET strictement devant le 2ᵉ).
"""

from collections import Counter

from src.api.ytmusic_api import _channel_from_votes


def test_pluralite_nette_sans_majorite_absolue():
    # 3 voix pour le bon canal, dispersion sur 5 canaux de feats (5 voix).
    votes = Counter({"UCgood": 3, "UCa": 1, "UCb": 1, "UCc": 1, "UCd": 1, "UCe": 1})
    assert _channel_from_votes(votes) == "UCgood"


def test_majorite_absolue():
    votes = Counter({"UCgood": 5, "UCa": 1, "UCb": 1})
    assert _channel_from_votes(votes) == "UCgood"


def test_egalite_en_tete_rejetee():
    # Ordre arbitraire en cas d'égalité → aucun gagnant.
    votes = Counter({"UCa": 3, "UCb": 3, "UCc": 1})
    assert _channel_from_votes(votes) is None


def test_une_seule_voix_rejetee():
    # Plancher : un seul lien fiable ne suffit pas à épingler un canal.
    votes = Counter({"UCa": 1, "UCb": 1})
    assert _channel_from_votes(votes) is None


def test_leader_unique_deux_voix():
    votes = Counter({"UCgood": 2})
    assert _channel_from_votes(votes) == "UCgood"


def test_votes_vides():
    assert _channel_from_votes(Counter()) is None


def test_accepte_dict_simple():
    # La fonction ne dépend pas de Counter, un dict suffit.
    assert _channel_from_votes({"UCgood": 4, "UCa": 1}) == "UCgood"
