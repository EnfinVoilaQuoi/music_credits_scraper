"""Une certification appartient-elle à CE morceau ? (CORRECTIFS 1.9, 2026-10-09)

Le matcher (`cert_matcher`) rapproche par MOTS CONTENUS, dans les deux sens :
un titre court y prenait la certif d'un titre plus long (Booba « Life » ←
« TUBA LIFE », Diamant ; « 92i » ← « 92I VEYRON » ; « La zone » ← « J'FAIS
PLAISIR À LA ZONE »), et un titre de certif qui n'était que le DÉBUT du titre
de la fiche passait pour une troncature (« Remote Control pt 2 » ←
« REMOTE CONTROL », « Drill FR 4 » ← « DRILL FR »). Ce module tranche APRÈS le
matcher, qui reste un pré-filtre rapide.

Décision utilisateur du 2026-10-09 — titres équivalents, plus ces seuls écarts :
featuring ; édition du même enregistrement (« REMAST 2016 », radio edit) ;
titre COUPÉ par la source (« QUI SEME LE VENT » pour « Qui sème le vent
récolte le tempo ») ; sous-titre entre parenthèses (« Make Her Say (I Poke Her
Face) » ← « MAKE HER SAY »). Refusés : mots en plus côté certif hors
parenthèses, numéro de partie, version (live, remix, freestyle — l'écho ↩
seulement), certif d'album sur un morceau (filtrée par l'appelant).

Le sous-titre est admis des DEUX côtés, entre parenthèses seulement : « DAY 'N'
NITE (NIGHTMARE) » est le même morceau que « Day 'N' Nite ». Quand une AUTRE
fiche porte exactement le titre de la certif, elle seule la reçoit
(`certification_enricher`, exclusivité) — c'est ce qui départage un original et
sa version au descripteur inconnu (« Mr. Rager (Prime Day Show x Kid Cudi) »).
"""

from __future__ import annotations

import re

from src.utils.cert_normalize import normalize_text
from src.utils.version_descriptors import (
    Kind,
    est_edition_de_diffusion,
    parse_variant,
    titre_generique,
    titres_equivalents,
)

_FEAT = re.compile(
    r"[\(\[]\s*(?:feat|ft|featuring)\b[^\)\]]*[\)\]]|\s+(?:feat|ft|featuring)\b.*$",
    re.IGNORECASE,
)
#: Ce qui reste d'un titre et désigne une AUTRE partie : « PT 2 », « PART 2 »,
#: « PARTIE 2 », « VOL 3 », « 4 », « II ».
#: Un nombre ou un chiffre romain SEUL n'est une partie que s'il est tout le
#: reste (« I Poke Her Face » n'est pas la partie I).
_PARTIE = re.compile(
    r"^(?:(?:PT|PART|PARTIE|VOL|VOLUME|CHAPITRE|CHAPTER|EPISODE|EP)\s*(?:\d+|[IVX]+)\b"
    r"|(?:\d+|II|III|IV|V|VI|VII|VIII|IX|X)$)"
)
_PARENTHESE_FINALE = re.compile(r"^(.*?)\s*[\(\[]([^\)\]]*)[\)\]]\s*$")


def cle(titre: str | None) -> str:
    """Titre comparable : sans featuring, normalisé comme le magasin des certifs."""
    return normalize_text(_FEAT.sub("", titre or "")).strip()


def _sous_titre(titre: str, autre_cle: str) -> bool:
    """`titre` = « <autre> (<sous-titre>) », le sous-titre n'étant ni une
    version, ni une édition, ni un numéro de partie."""
    m = _PARENTHESE_FINALE.match(_FEAT.sub("", titre or "").strip())
    if not m or cle(m.group(1)) != autre_cle:
        return False
    interieur = normalize_text(m.group(2))
    if not interieur or _PARTIE.match(interieur):
        return False
    return parse_variant(titre).kind == Kind.NONE


def certif_d_une_version(titre_fiche: str | None, titre_certif: str | None) -> bool:
    """La certif est celle d'une VERSION (« DOLCE CAMARA (SNIGHT B REMIX) ») que
    la fiche — l'original — porte faute de fiche pour la version : l'écran le
    dit (« ↩ certif de … »), décision utilisateur 2026-10-09."""
    return (
        parse_variant(titre_certif).kind != Kind.NONE
        and parse_variant(titre_fiche).kind == Kind.NONE
    )


def certif_du_morceau(titre_fiche: str | None, titre_certif: str | None) -> bool:
    f, c = cle(titre_fiche), cle(titre_certif)
    if not f or not c:
        return f == c
    if f == c or titres_equivalents(titre_fiche, titre_certif):
        return True
    vc = parse_variant(titre_certif)
    if vc.kind != Kind.NONE and parse_variant(titre_fiche).kind == Kind.NONE:
        # Certif d'une VERSION (« SUZY (LIVE 2006) ») sur l'ORIGINAL : gardée
        # tant que la version n'a pas sa fiche (règle existante) ; quand elle
        # l'a, `echos_de_versions` la lui rend et l'original n'en garde que
        # l'écho ↩. Le socle doit être le titre de la fiche, rien de plus.
        return cle(vc.socle) == f
    if c.startswith(f + " "):
        # Mots EN PLUS côté certif : seulement une édition du même
        # enregistrement, ou un sous-titre entre parenthèses.
        reste = c[len(f) :].strip()
        if titre_generique(reste):
            # « ALL OF THE LIGHTS (INTERLUDE) » : une PLACE dans le projet n'est
            # pas un sous-titre, c'est un autre morceau (l'Interlude de *My
            # Beautiful Dark Twisted Fantasy*, 2026-10-10). Côté fiche, l'inverse
            # reste vrai : « Matrix (Intro) » est le morceau Matrix.
            return False
        return est_edition_de_diffusion(reste.lower()) or _sous_titre(titre_certif, f)
    if f.startswith(c):
        # Certif plus COURTE : coupée par la source (en plein mot, au moins 8
        # caractères), ou la fiche porte un sous-titre — jamais une autre
        # partie, jamais une version de la fiche.
        reste = f[len(c) :].strip()
        if _PARTIE.match(reste) or parse_variant(titre_fiche).kind != Kind.NONE:
            return False
        # Un titre de certif COURT ne vaut que par un sous-titre entre
        # parenthèses : sinon « LA » irait sur « La zone ».
        return len(c) >= 8 or _sous_titre(titre_fiche, c)
    return False
