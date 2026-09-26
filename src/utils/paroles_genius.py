"""Ce qu'un texte de paroles Genius dit de LUI-MÊME (2026-09-26). Module PUR.

Genius met parfois dans le conteneur de paroles autre chose que des paroles,
et le scrape l'enregistrait tel quel :

  · un morceau INSTRUMENTAL décrit par un simple en-tête — « [Morceau
    instrumental : Lucio Bukowski] », « [Instrumentale : 2Fingz] » (11 fiches
    en base, aucune reconnue : le constat e27 ne lisait que le placeholder
    officiel « This song is an instrumental ») ;
  · le placeholder d'un INÉDIT — « Unreleased », « Please check back once the
    song has been released » ;
  · la mention « Lyrics from snippet » : seule, elle ne dit rien (4 fiches) ;
    suivie des paroles d'un extrait qui a fuité (81 fiches), elle précède de
    vraies paroles, qu'on garde.

`texte_utile` est ce qui reste une fois tout cela retiré : c'est lui que juge
« 🕳️ sans info » (`track_validation.sans_info`).
"""

from __future__ import annotations

import re

#: Une ligne qui n'est qu'un en-tête de section : « [Couplet 1 : Isha] ».
_ENTETE = re.compile(r"^\s*\[[^\]]*\]\s*$")
#: « Lyrics from snippet », « *Lyrics from Snippets* », « (Lyrics from snippet) ».
_SNIPPET = re.compile(r"^\W*lyrics from (?:the )?snippets?\W*$", re.IGNORECASE)
_INSTRUMENTAL = re.compile(r"instrumental", re.IGNORECASE)
#: Placeholder d'inédit : sur le texte ENTIER (« Unreleased » de Kid Cudi est un
#: titre qui a des paroles).
_INEDIT = re.compile(
    r"please check back once the song has been released|^\W*unreleased\W*$",
    re.IGNORECASE,
)


def _lignes(texte: str | None) -> list[str]:
    return [ligne for ligne in (texte or "").splitlines() if ligne.strip()]


def instrumental_par_le_texte(texte: str | None) -> bool:
    """Le « texte » n'est que des en-têtes, dont un annonce un instrumental."""
    lignes = _lignes(texte)
    return (
        bool(lignes)
        and all(_ENTETE.match(ligne) for ligne in lignes)
        and any(_INSTRUMENTAL.search(ligne) for ligne in lignes)
    )


def inedit_par_le_texte(texte: str | None) -> bool:
    """Le texte est le placeholder d'un morceau pas encore sorti."""
    return bool(_INEDIT.search((texte or "").strip()))


def texte_utile(texte: str | None) -> str:
    """Les paroles sans en-têtes, sans mention de snippet ni placeholder."""
    if inedit_par_le_texte(texte):
        return ""
    return "\n".join(
        ligne for ligne in _lignes(texte) if not _ENTETE.match(ligne) and not _SNIPPET.match(ligne)
    ).strip()
