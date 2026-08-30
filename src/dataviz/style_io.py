"""Briques communes aux fichiers de réglages des générateurs d'Export studio.

Chaque générateur (`Structure`, `Bubble`) persiste ses réglages dans un fichier
`data/<nom>_style.json` **annoté** : chaque valeur y est précédée d'une ligne de
commentaire `//` qui dit à quoi elle sert et où elle agit. JSON n'admettant pas
les commentaires, ils sont retirés avant le parse — seules les lignes
ENTIÈREMENT commentées sont supportées (pas de `//` en fin de ligne de valeur,
ce qui obligerait à distinguer un commentaire d'un `//` dans une chaîne).

Ce module ne contient que ce qui est vrai pour TOUS les générateurs : le rendu
annoté, le nettoyage des commentaires, et la relecture vers une dataclass de
style. Ce qui relève d'un générateur (quelles clés, quel texte d'aide, quel
fichier) reste chez lui.
"""

import json
import re
from dataclasses import fields, replace

from src.utils.logger import get_logger

logger = get_logger(__name__)

# Ligne entièrement commentée (éventuellement indentée).
_COMMENT_LINE_RE = re.compile(r"^\s*//")

# Une section du fichier : un titre, puis des couples (clé, texte d'aide).
Section = tuple[str, tuple[tuple[str, str], ...]]


def strip_comments(text: str) -> str:
    """Retire les lignes ENTIÈREMENT commentées (`//`), en préservant la numérotation.

    Les lignes sont blanchies plutôt que supprimées : un message d'erreur de
    `json` continue de pointer la bonne ligne du fichier réel.
    """
    return "\n".join("" if _COMMENT_LINE_RE.match(ln) else ln for ln in text.splitlines())


def wrap(text: str, width: int) -> list[str]:
    """Découpe un texte d'aide en lignes de `width` caractères max, sur les mots."""
    out, current = [], ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            out.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        out.append(current)
    return out


def section_header(title: str) -> list[str]:
    """Titre de section : le tiret cadratin sur la 1ʳᵉ ligne seulement."""
    out = []
    for i, line in enumerate(title.split("\n")):
        out.append(f"  // ── {line}" if i == 0 else f"  //    {line}")
    return out


def render_commented(
    intro: list[str],
    sections: tuple[Section, ...],
    payload: dict,
    trailing: tuple[str, str] | None = None,
) -> str:
    """Sérialise `payload` en JSON annoté (lignes `//` avant chaque réglage).

    `trailing` = `(texte_d_aide, clé)` d'un réglage STRUCTURÉ (un objet, pas un
    scalaire) posé en dernier : son aide sert de titre de section et sa valeur
    est rendue indentée. C'est le cas du bloc `colors` de « Structure ».
    """
    lines = [*intro, "{"]
    total = len(payload)
    written = 0
    for title, entries in sections:
        lines.append("")
        lines.extend(section_header(title))
        for key, help_text in entries:
            if key not in payload:
                continue
            for chunk in wrap(help_text, 74):
                lines.append(f"  // {chunk}")
            written += 1
            comma = "," if written < total else ""
            lines.append(
                f"  {json.dumps(key)}: {json.dumps(payload[key], ensure_ascii=False)}{comma}"
            )

    if trailing is not None:
        help_text, key = trailing
        if key in payload:
            lines.append("")
            lines.extend(section_header(help_text))
            body = json.dumps(payload[key], ensure_ascii=False, indent=2)
            body = "\n".join(("  " + ln) if i else ln for i, ln in enumerate(body.split("\n")))
            lines.append(f"  {json.dumps(key)}: {body}")

    lines.append("}")
    return "\n".join(lines) + "\n"


def editable_fields(style_cls, locked: set[str]) -> set[str]:
    """Noms des champs réglables depuis le fichier (tout sauf `locked`)."""
    return {f.name for f in fields(style_cls) if f.name not in locked}


def read_overrides(path, style_cls, locked: set[str], label: str) -> tuple[dict, dict] | None:
    """Lit le fichier → `(surcharges, json_brut)`, ou `None` s'il est inexploitable.

    Une clé inconnue est ignorée avec un avertissement (une faute de frappe ne
    doit pas faire échouer tout un export), un fichier partiel reste valide. Le
    JSON brut est rendu tel quel : les réglages STRUCTURÉS (le bloc `colors` de
    « Structure ») se fusionnent clé à clé chez l'appelant, qui seul sait
    lesquels.
    """
    try:
        raw = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"Réglages « {label} » illisibles ({path}) : {exc} — valeurs par défaut")
        return None
    if not isinstance(raw, dict):
        logger.warning(f"Réglages « {label} » : objet JSON attendu dans {path}")
        return None

    editable = editable_fields(style_cls, locked)
    overrides, unknown = {}, []
    for key, value in raw.items():
        if key in locked:
            continue  # figé, ou fusionné par l'appelant
        if key not in editable:
            unknown.append(key)
            continue
        overrides[key] = value
    if unknown:
        logger.warning(f"Réglages « {label} » : clés inconnues ignorées — {', '.join(unknown)}")
    return overrides, raw


def build_style(style_cls, overrides: dict, label: str, **forced):
    """`style_cls(**défauts)` surchargé par `overrides` ; défauts si une valeur casse."""
    try:
        return replace(style_cls(), **forced, **overrides)
    except TypeError as exc:
        logger.warning(f"Réglages « {label} » invalides : {exc} — valeurs par défaut")
        return style_cls()
