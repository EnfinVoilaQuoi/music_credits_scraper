"""Normalisation des noms de crédits (producteurs, feats…) pour le regroupement.

Fonctions **pures** (aucun état, aucune DB), pensées pour le graphe de
graphe de collaborations (consommateur externe). Deux niveaux :

- `display_name(name)` : nettoyage **léger** pour l'affichage — retire les
  caractères invisibles et le suffixe Genius « (Producer) », normalise les
  espaces, mais **garde casse, accents et apostrophes** (le nom reste lisible).
- `identity_key(name)` : clé de **regroupement agressive** — deux graphies du
  même producteur doivent donner la même clé (fusion des nœuds). Dérive de
  `display_name`, puis normalise apostrophes → NFD sans marques combinantes
  (garde les lettres de base, contrairement à `title_matching` NFKD+ascii-ignore
  qui détruit les caractères non-latins) → casefold.

Ne PAS confondre avec `cert_normalize.normalize_text` (majuscules ASCII, dédié
aux titres de certifs) ni avec `title_matching` (rapprochement de titres).
Golden master : `tests/test_credit_normalize.py`.
"""

import re
import unicodedata

# Caractères Unicode invisibles rencontrés en DB (un `.strip()` ne les attrape
# pas) : zero-width space / non-joiner / joiner, word joiner, BOM.
_INVISIBLE = "​‌‍⁠﻿"
_INVISIBLE_TABLE = {ord(ch): None for ch in _INVISIBLE}

# Suffixe ajouté par Genius sur les crédits producteur : « Kalim (Producer) ».
# Ancré en fin de chaîne, insensible à la casse.
_PRODUCER_SUFFIX_RE = re.compile(r"\s*\(\s*producer\s*\)\s*$", re.IGNORECASE)

# Suffixe de désambiguïsation régionale Genius : « Lucci' (FRA) », « X (UK) ».
# 2-4 lettres MAJUSCULES uniquement (pas re.I : un vrai nom entre parenthèses
# reste minuscule/mixte et ne doit pas sauter).
_REGION_SUFFIX_RE = re.compile(r"\s*\(\s*[A-Z]{2,4}\s*\)\s*$")

# Apostrophes / accents isolés → apostrophe droite (même parti pris que
# `cert_normalize`) : ‘ ’ ` ´ sont autant de graphies du même caractère.
_APOSTROPHE_TABLE = {
    ord("‘"): "'",  # ‘ guillemet-apostrophe ouvrant
    ord("’"): "'",  # ’ guillemet-apostrophe fermant (le plus fréquent)
    ord("`"): "'",  # ` accent grave / backtick
    ord("´"): "'",  # ´ accent aigu isolé
}


def display_name(name: str | None) -> str:
    """Nettoyage léger pour l'affichage (garde casse, accents, apostrophes).

    Retire les caractères invisibles, les suffixes Genius ` (Producer)` et
    ` (FRA)`/` (UK)`… (désambiguïsation régionale), et normalise les espaces
    (y compris insécables) en une seule espace.
    """
    if not name:
        return ""
    text = name.translate(_INVISIBLE_TABLE)
    text = re.sub(r"\s+", " ", text).strip()
    text = _PRODUCER_SUFFIX_RE.sub("", text).strip()
    text = _REGION_SUFFIX_RE.sub("", text).strip()
    return text


def identity_key(name: str | None) -> str:
    """Clé de regroupement agressive : deux graphies du même nom → même clé.

    `display_name` puis apostrophes normalisées → NFD sans marques combinantes
    (les accents tombent, `é` → `e`, mais les lettres de base et les scripts
    non-latins survivent) → casefold. Vide si `name` est vide.
    """
    text = display_name(name)
    if not text:
        return ""
    text = text.translate(_APOSTROPHE_TABLE)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.casefold()


def cle_de_doublon(name: str | None) -> str:
    """Clé « même personne » d'un crédit sur UN morceau : `identity_key` sans
    ponctuation ni espaces (lettres et chiffres seuls, tous scripts).

    Plus lâche que `identity_key`, qui reste la clé du graphe du studio : sur
    un seul morceau et un seul rôle, « Lucci » = « Lucci’ (FRA) », « Babz
    Beats » = « Babzbeats », « Shurik'n » = « Shurik’n » (mesuré le 2026-11-03 :
    55 crédits YouTube doublaient ainsi un crédit déjà là). Vide si le nom n'a
    ni lettre ni chiffre."""
    return "".join(ch for ch in identity_key(name) if ch.isalnum())


#: Sources des crédits lus dans une description YouTube : ils ne s'AJOUTENT
#: qu'à ce que la fiche n'a pas déjà (`services.credits._phase_youtube`).
SOURCES_YOUTUBE = ("youtube_topic", "youtube_clip")


def credits_youtube_en_double(credits) -> list:
    """Les crédits YouTube qui redisent un crédit déjà porté (même rôle, même
    `cle_de_doublon`) : par une autre source, ou par une ligne YouTube qui les
    précède. Le juge commun de l'écrivain, de l'audit (`credit_doublon`) et de
    la réparation (`scripts/retirer_credits_youtube_doublons.py`)."""
    vus = {
        (cle_de_doublon(c.name), _role(c))
        for c in credits
        if c.source not in SOURCES_YOUTUBE and cle_de_doublon(c.name)
    }
    en_double = []
    for c in credits:
        if c.source not in SOURCES_YOUTUBE:
            continue
        cle = (cle_de_doublon(c.name), _role(c))
        if cle in vus:
            en_double.append(c)
        else:
            vus.add(cle)
    return en_double


def graphies_a_fondre(credits) -> list[tuple]:
    """Les crédits d'UN morceau à réunir en une ligne : `[(gagnant, perdants)]`
    (décision utilisateur 2026-11-25). Le groupe est celui de
    `credits_sous_deux_graphies`, sauf :

    - une même source y donne deux noms (« Manny » / « Manny (FRA) » sur la même
      page) : elle les distingue, rien ne dit qu'ils sont un ;
    - tout est hérité : l'original se fond, l'héritage se recalcule.

    Le gagnant est la ligne que donne la source la plus forte (la page Genius
    d'abord) ; un héritage cède la place à une source directe. Juge unique des
    écrivains, de l'audit (`credit_doublon`) et du rattrapage. PUR."""
    from src.utils.credit_sources import promue
    from src.utils.version_heritage import est_herite

    rendus = []
    for groupe in credits_sous_deux_graphies(credits):
        directs = [c for c in groupe if not est_herite(c.source)]
        if not directs:
            continue
        noms: dict[str, set[str]] = {}
        for c in directs:
            for s in c.sources:
                noms.setdefault(s, set()).add(c.nom_selon(s))
        if any(len(n) > 1 for n in noms.values()):
            continue
        tete = promue(noms)
        nom = next(iter(noms[tete]))
        gagnant = next((c for c in directs if c.name == nom), directs[0])
        rendus.append((gagnant, [c for c in groupe if c is not gagnant]))
    return rendus


def cles_fondues(tracks) -> dict[str, str]:
    """`identity_key` → `identity_key` retenue, pour les graphies d'UNE personne
    que le juge `credits_sous_deux_graphies` réunit sur un MÊME morceau
    (« Al Tarba » de Discogs / « Al’Tarba » de Genius). Le graphe du studio
    gardait deux nœuds — une fausse collaboration de la personne avec
    elle-même sur 22 fiches de production, 16 chez Swift Guad (2026-11-14).
    Jamais par la seule ressemblance de deux noms sur deux morceaux : sur la
    base entière, 139 paires n'ont aucune preuve sur un même morceau. La clé
    retenue est la plus fréquente parmi les crédits de `tracks`, à égalité la
    première dans l'ordre ; seules les clés à fondre sont rendues. PUR."""
    parent: dict[str, str] = {}

    def racine(k: str) -> str:
        while parent.get(k, k) != k:
            k = parent[k]
        return k

    frequence: dict[str, int] = {}
    for track in tracks:
        for c in track.credits:
            for nom in c.noms:
                k = identity_key(nom)
                if k:
                    frequence[k] = frequence.get(k, 0) + 1
        # La preuve d'une même personne : deux lignes, ou une ligne qui garde la
        # graphie de ses autres sources (e71, `Credit.graphies`).
        preuves = [[c.name for c in g] for g in credits_sous_deux_graphies(track.credits)]
        preuves += [c.noms for c in track.credits if len(c.noms) > 1]
        for noms in preuves:
            cles = sorted({identity_key(n) for n in noms} - {""})
            for k in cles[1:]:
                a, b = racine(cles[0]), racine(k)
                if a != b:
                    parent[max(a, b)] = min(a, b)
    classes: dict[str, list[str]] = {}
    for k in parent:
        classes.setdefault(racine(k), []).append(k)
    for r, membres in list(classes.items()):
        if r not in membres:
            membres.append(r)
    fondues = {}
    for membres in classes.values():
        retenue = min(membres, key=lambda k: (-frequence.get(k, 0), k))
        for k in membres:
            if k != retenue:
                fondues[k] = retenue
    return fondues


def _role(credit) -> str:
    role = credit.role
    return role if isinstance(role, str) else role.value


def _region(name: str | None) -> str | None:
    """Le suffixe régional de Genius (« FRA », « USA ») d'un nom, ou None."""
    trouve = _REGION_SUFFIX_RE.search((name or "").strip())
    return trouve.group(0).strip(" ()").upper() if trouve else None


def credits_sous_deux_graphies(credits) -> list[list]:
    """Le même crédit sur UN morceau sous plusieurs graphies, TOUTES sources
    (« Eric Chevet » de Discogs / « Éric Chevet » de Genius, « JeanJass » /
    « Jean Jass ») : groupes de crédits au même rôle, même détail de rôle et
    même `cle_de_doublon`, aux noms bruts différents. Généralise le juge des
    crédits YouTube (`credits_youtube_en_double`, dont les lignes restent à
    l'invariant `credit_doublon`). Deux suffixes régionaux différents sont
    deux personnes (« M.A.T (FRA) » / « M.A.T. (USA) »). Mesuré le
    2026-11-10 : 735 groupes, discogs + genius pour 619."""
    groupes: dict[tuple, list] = {}
    for c in credits:
        cle = cle_de_doublon(c.name)
        if cle and c.source not in SOURCES_YOUTUBE:
            groupes.setdefault((cle, _role(c), c.role_detail or ""), []).append(c)
    rendus = []
    for groupe in groupes.values():
        if len({c.name for c in groupe}) < 2:
            continue
        if len({r for r in (_region(c.name) for c in groupe) if r}) > 1:
            continue
        rendus.append(groupe)
    return rendus
