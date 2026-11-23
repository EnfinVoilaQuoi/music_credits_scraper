"""Inventaire des correcteurs du panneau (CORRECTIFS, « File À trancher »,
2026-10-19/20, refait le 2026-11-22) : une règle VALIDÉE (`REGLES_*` de
`politiques_revue`) n'a de sens que si `revue_auto` sait l'appliquer — et
une action jamais validée n'y entre pas en douce."""

from src.services import politiques_revue, revue_auto

#: Actions que le correcteur applique sans geste propre : `normal` pose le
#: verdict « ✓ Normal », `indecidable` le verdict « indécidable » (`corriger`).
_SANS_CORRECTEUR = {"normal", "indecidable"}

#: Écrites dans une politique, mais qui attendent la décision de l'utilisateur.
#: Vide depuis le 2026-11-23 : `kworb_rendition` / `kworb_existant` (inventaire
#: du 2026-11-22) ont laissé la place à la règle décidée, `kworb_sommer` /
#: `kworb_version` (`politiques_revue.juger_variante_kworb`).
_EN_ATTENTE_DE_DECISION: set[str] = set()


def _regles_validees() -> set[tuple[str, str]]:
    regles = set()
    for nom in dir(politiques_revue):
        valeur = getattr(politiques_revue, nom)
        if nom.startswith("REGLES_") and isinstance(valeur, frozenset):
            regles |= {r for r in valeur if isinstance(r, tuple) and len(r) == 2}
    return regles


def test_toute_action_validee_a_son_correcteur():
    regles = _regles_validees()
    assert regles, "aucune règle lue : le nommage REGLES_* a changé"
    sans = sorted(
        (d, a)
        for d, a in regles
        if a not in _SANS_CORRECTEUR and a not in revue_auto._CORRECTEURS_DE_REGLE
    )
    assert sans == []


def test_une_action_en_attente_n_est_dans_aucune_regle():
    actions = {a for _, a in _regles_validees()}
    assert not actions & _EN_ATTENTE_DE_DECISION
    assert not set(revue_auto._CORRECTEURS_DE_REGLE) & _EN_ATTENTE_DE_DECISION
