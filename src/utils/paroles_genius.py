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

`paroles_factices` (2026-11-06) dit quand ce reste n'est pas un texte : 108 fiches
« à paroles » portaient « . », « TBD », « [? : Kanye West] » ou leur seul titre.
Liste FERMÉE : un vrai texte court (« Ken Kaniff sucks a fat dick ») reste.
"""

from __future__ import annotations

import re
import unicodedata

from src.utils.title_matching import normalize_title

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
#: Un texte ENTIER qui annonce un morceau sans voix (Logic : « Just beat »,
#: « [just beat : Logic] », 18 pages du même projet ; Lucio Bukowski : « Instrumentale »).
_TEXTE_INSTRUMENTAL = frozenset(
    {
        "instrumental",
        "instrumentale",
        "just beat",
        "beat only",
        "only beat",
        "this is a instrumental",
        "interlude instrumental",
    }
)
#: De même, l'étiquette d'un en-tête, avant son « : » (« instrumental » s'y lit déjà).
_ENTETE_INSTRUMENTAL = frozenset(
    {"just beat", "beat only", "only beat", "beat", "interlude musical", "morceau instumental"}
)
#: Ce que les contributeurs écrivent À LA PLACE des paroles.
_PLACEHOLDERS = frozenset(
    {
        "tbd",
        "scribing",
        "none yet",
        "lyrics",
        "verse",
        "adding lyrics later",
        "song is currently lost",
        "these lyrics are still processing",
        "lyrics have not been transcribed for this song",
        "the lyrics for this song have yet to be transcribed",
        "lyrics for this song have yet to be transcribed",
        "paroles bientot en ligne",
        "paroles en cours de retranscription",
        "paroles redigees et annotees par la communaute francaise de rap genius",
        "unreleased confirmed song",
        "do not edit to be deleted",
    }
)
#: « Lyrics of The Game We Run L A » en compte 9 ; au-delà, une phrase.
MOTS_D_UNE_ETIQUETTE = 10
INSTRUMENTAL = "instrumental"
NON_TRANSCRITES = "non_transcrites"


def _norme(texte: str) -> str:
    """Le texte en minuscules, sans accent ni ponctuation."""
    ascii_ = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^\w\s]|_", " ", ascii_).lower().split())


def _etiquette(entete: str) -> str:
    return _norme(entete.strip().strip("[]").split(":")[0])


def _lignes(texte: str | None) -> list[str]:
    return [ligne for ligne in (texte or "").splitlines() if ligne.strip()]


def instrumental_par_le_texte(texte: str | None) -> bool:
    """Le « texte » n'est que des en-têtes, dont un annonce un instrumental."""
    lignes = _lignes(texte)
    if _norme(texte or "") in _TEXTE_INSTRUMENTAL:
        return True
    return (
        bool(lignes)
        and all(_ENTETE.match(ligne) for ligne in lignes)
        and any(
            _INSTRUMENTAL.search(ligne) or _etiquette(ligne) in _ENTETE_INSTRUMENTAL
            for ligne in lignes
        )
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


def paroles_factices(texte: str | None, titre: str | None = None) -> str | None:
    """PUR. Ce « texte » n'est pas des paroles : `INSTRUMENTAL` s'il annonce un
    morceau sans voix, `NON_TRANSCRITES` s'il n'en tient que la place (rien
    qu'un en-tête ou une lettre, un placeholder, le seul titre du morceau) ;
    None pour un vrai texte, ou pas de texte du tout."""
    if not (texte or "").strip():
        return None
    if instrumental_par_le_texte(texte):
        return INSTRUMENTAL
    # Une didascalie décrit le morceau (« [Curtains open, followed by a crowd's
    # applause… : Eminem] », un skit sans parole) : pas des paroles, mais pas du
    # vide. Une phrase, là où une étiquette tient en quelques mots.
    if any(
        _ENTETE.match(ligne) and len(_etiquette(ligne).split()) > MOTS_D_UNE_ETIQUETTE
        for ligne in _lignes(texte)
    ):
        return None
    utile = texte_utile(texte)
    if sum(ch.isalnum() for ch in utile) <= 1 or _norme(utile) in _PLACEHOLDERS:
        return NON_TRANSCRITES
    # Au moins une lettre latine : un titre cyrillique se normalise en vide, et
    # « Ярик и Лёня » valait n'importe quel texte cyrillique.
    seul = normalize_title(utile).replace(" ", "")
    if seul and titre and seul == normalize_title(titre).replace(" ", ""):
        return NON_TRANSCRITES
    return None


def a_des_paroles(texte: str | None, titre: str | None = None) -> bool:
    """PUR. Le verdict de la colonne `has_lyrics` : un texte, et un vrai."""
    return bool((texte or "").strip()) and paroles_factices(texte, titre) is None


#: Les sections qu'une voix interprète, en toutes lettres (sans accent ni
#: ponctuation). Liste FERMÉE : une tête qu'elle ne connaît pas ne reçoit aucun
#: nom (« Produced by… », « Paroles de… », « Rest of the lyrics… », une
#: didascalie, un nom seul, « Couplet 1 - Tunisiano » qui nomme déjà sa voix).
_MOTS_DE_SECTION = (
    "couplet|verse|open verse|vers|verso|strofa|zwrotka|куплет"
    "|refrain|chorus|hook|coro|estribillo|ritornello|refren|припев|nakarat"
    "|pre refrain|prerefrain|pre chorus|pre hook|pre verse"
    "|post refrain|post chorus|post hook"
    "|intro|outro|pont|bridge|interlude|intermede|transition|break|breakdown"
)
#: Le mot de la section, son numéro, son multiplicateur : rien d'autre.
_TETE_DE_SECTION = re.compile(
    rf"^(?:{_MOTS_DE_SECTION})"
    r"(?: (?:\d+|[ivx]+|one|two|three|four|five|unique|un|deux|trois|quatre|cinq))?"
    r"(?: ?x ?\d+)?$"
)
#: Une ligne faite d'une seule tête : son étiquette en groupe 1.
_LIGNE_DE_TETE = re.compile(r"(?m)^\[([^\]\n]+)\]\s*$")


def _cle_de_section(etiquette: str) -> str:
    """L'étiquette sans accent ni ponctuation, en gardant les autres alphabets
    (`_norme` retire le cyrillique : « Куплет » n'y serait plus rien)."""
    sans_accent = "".join(
        ch for ch in unicodedata.normalize("NFKD", etiquette) if not unicodedata.combining(ch)
    )
    return " ".join(re.sub(r"[^\w\s]|_", " ", sans_accent).casefold().split())


def est_tete_de_section(etiquette: str) -> bool:
    """PUR. Cette étiquette (sans crochets) est-elle une section interprétée, sans
    attribution ? « Couplet 1 », « Refrain x2 » oui ; « Produced by X »,
    « Paroles de "Y" », « Couplet 1 - Tunisiano », « Couplet 1 : Isha » non."""
    return ":" not in etiquette and bool(_TETE_DE_SECTION.match(_cle_de_section(etiquette)))


def interprete_des_sections(
    ligne: str | None,
    principal: str | None,
    est_feat: bool | None,
    role_secondaire: str | None = None,
) -> str | None:
    """PUR. Le nom à donner aux sections que Genius n'attribue pas : l'interprète
    principal de la FICHE, le même chez toutes ses lignes sœurs. L'artiste de la
    ligne n'en vaut que s'il est lui-même le principal (rien d'autre n'est
    renseigné et la ligne n'est ni un feat, ni un rôle secondaire) ; sinon None :
    aucun nom plutôt qu'un nom deviné (« Fenwick » de Niro, lu sur la ligne de
    Booba : « [Couplet 1 : Booba] », 2026-11-07)."""
    if principal and principal.strip():
        return principal.strip()
    if est_feat is False and not role_secondaire and ligne and ligne.strip():
        return ligne.strip()
    return None


def attribuer_sections(paroles: str, interprete: str | None) -> str:
    """PUR. Ajoute ` : <interprete>` aux seules têtes de section sans attribution,
    seules sur leur ligne. Sans interprète, le texte reste celui de Genius."""
    if not paroles or not interprete:
        return paroles

    def attribuer(tete: re.Match) -> str:
        etiquette = tete.group(1)
        if not est_tete_de_section(etiquette):
            return tete.group(0)
        return f"[{etiquette.rstrip()} : {interprete}]"

    return _LIGNE_DE_TETE.sub(attribuer, paroles)
