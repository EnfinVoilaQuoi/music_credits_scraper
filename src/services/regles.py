"""Une règle changée se REJOUE seule (chantier D, 2026-10-12).

Le recalcul du chantier A part d'une ÉCRITURE de faits. Une règle changée dans
le code n'écrit rien : la garde des parties du portier des certifs n'aurait
pris effet qu'au prochain « Appliquer les certifs » de chaque artiste, lancé à
la main (question utilisateur du 2026-10-12 : « ça devrait pas se déclencher
auto, comme pour les lignes sœurs ? »).

Chaque PORTIER (la question « cette donnée va-t-elle sur cette fiche ? ») a une
EMPREINTE : la structure du code de ses modules (arbre syntaxique, sans
docstrings — un commentaire, un reformatage ou une doc ne comptent pas). Quand
un portier a calculé les verdicts d'un artiste, l'empreinte est notée en base
(`regles_appliquees`) ; un run qui en trouve une autre rejoue le portier, en fin
de run (`revue_auto.corriger_apres_run`, le point d'accroche commun).
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from collections.abc import Callable
from datetime import datetime
from functools import cache

from src.utils.logger import get_logger

logger = get_logger(__name__)

#: Les modules dont le CODE décide de chaque portier.
PORTIERS: dict[str, tuple[str, ...]] = {
    "certifs": (
        "src.utils.cert_titres",
        "src.utils.cert_matcher",
        "src.utils.cert_normalize",
        "src.utils.certification_enricher",
        "src.utils.version_descriptors",
        "src.utils.title_matching",
    ),
}


def _sans_docstrings(arbre: ast.AST) -> ast.AST:
    for noeud in ast.walk(arbre):
        corps = getattr(noeud, "body", None)
        if (
            isinstance(noeud, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
            and corps
            and isinstance(corps[0], ast.Expr)
            and isinstance(corps[0].value, ast.Constant)
            and isinstance(corps[0].value.value, str)
        ):
            noeud.body = corps[1:] or [ast.Pass()]
    return arbre


def empreinte_du_code(source: str) -> str:
    """PUR. L'empreinte d'un code source : son arbre syntaxique sans docstrings
    (les commentaires et la mise en forme n'y sont pas)."""
    arbre = _sans_docstrings(ast.parse(source))
    return hashlib.sha1(ast.dump(arbre, annotate_fields=False).encode("utf-8")).hexdigest()


@cache
def empreinte(portier: str) -> str:
    """L'empreinte d'un portier : celle de ses modules, dans l'ordre."""
    h = hashlib.sha1()
    for module in PORTIERS[portier]:
        spec = importlib.util.find_spec(module)
        with open(spec.origin, encoding="utf-8") as f:
            h.update(empreinte_du_code(f.read()).encode("ascii"))
    return h.hexdigest()[:16]


def empreinte_appliquee(dm, artiste: str, portier: str) -> str | None:
    from sqlalchemy import text

    with dm.engine.connect() as conn:
        return conn.execute(
            text("SELECT empreinte FROM regles_appliquees WHERE artiste = :a AND portier = :p"),
            {"a": artiste, "p": portier},
        ).scalar()


def marquer(dm, artiste: str, portier: str) -> None:
    """Les verdicts de `portier` pour `artiste` viennent de la règle ACTUELLE."""
    from sqlalchemy import text

    with dm.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO regles_appliquees (artiste, portier, empreinte, applique_le) "
                "VALUES (:a, :p, :e, :le) ON CONFLICT (artiste, portier) DO UPDATE SET "
                "empreinte = excluded.empreinte, applique_le = excluded.applique_le"
            ),
            {"a": artiste, "p": portier, "e": empreinte(portier), "le": datetime.now()},
        )


#: Empreinte posée par une DÉCISION (alias, formation… — `ArtistRepository.
#: _ecrire_artiste`) : différente de toute empreinte de code, elle rend le
#: portier périmé, donc rejoué en fin du prochain run (2026-10-20).
A_REJOUER = "decision"


def marquer_a_rejouer(conn, artiste: str, portier: str) -> None:
    """Dans la transaction de l'appelant : les verdicts de `portier` pour
    `artiste` ne valent plus (une décision a changé ce qu'il juge)."""
    from sqlalchemy import text

    conn.execute(
        text(
            "INSERT INTO regles_appliquees (artiste, portier, empreinte, applique_le) "
            "VALUES (:a, :p, :e, :le) ON CONFLICT (artiste, portier) DO UPDATE SET "
            "empreinte = excluded.empreinte, applique_le = excluded.applique_le"
        ),
        {"a": artiste, "p": portier, "e": A_REJOUER, "le": datetime.now()},
    )


def perimes(dm, artiste: str) -> list[str]:
    """Les portiers dont la règle a changé depuis leur dernier passage sur
    `artiste`. Un portier jamais passé N'EST PAS périmé : rien n'a été
    calculé par une ancienne règle (son premier run le fera)."""
    sortie = []
    for portier in PORTIERS:
        appliquee = empreinte_appliquee(dm, artiste, portier)
        if appliquee is not None and appliquee != empreinte(portier):
            sortie.append(portier)
    return sortie


def _rejouer_certifs(dm, artiste) -> str:
    from src.services import certifs

    return certifs.recalculer(dm, artiste)


#: Comment REJOUER chaque portier, hors ligne, sur un artiste chargé.
REJOUEURS: dict[str, Callable] = {"certifs": _rejouer_certifs}


def rejouer_perimes(dm, artiste) -> list[str]:
    """Rejoue les portiers périmés de l'artiste (discographie chargée) ; rend
    un compte rendu par portier rejoué."""
    comptes = []
    for portier in perimes(dm, artiste.name):
        logger.info(f"🔁 Règle « {portier} » changée depuis son passage sur {artiste.name} : rejeu")
        comptes.append(f"règle « {portier} » rejouée : {REJOUEURS[portier](dm, artiste)}")
    return comptes
