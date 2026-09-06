"""Normalisation de texte pour le rapprochement des certifications.

Fonctions **pures** (aucun état, aucune DB) partagées par le matcher unifié
(`cert_matcher`) et les « clean steps » des trois sources (SNEP/BRMA/RIAA).
Extrait de `SNEPCertificationManager.normalize_text` — la parité de
normalisation entre sources en dépend (test de caractérisation
`tests/test_cert_normalize.py`). Ne pas modifier la logique sans mettre à jour
le golden master.
"""

import re
import unicodedata
from collections.abc import Sequence


def normalize_text(text: str) -> str:
    """Normalise le texte pour les comparaisons.

    Accents retirés, majuscules, ligatures/symboles usuels remplacés (& → AND,
    $ → S, guillemets courbes → droits, tirets longs → '-', …), ponctuation
    supprimée sauf apostrophe et trait d'union (conservés pour les featurings),
    espaces normalisés.
    """
    if not text:
        return ""

    # ÉTAPE 1: Nettoyer les espaces/tabulations (AVANT tout traitement)
    text = re.sub(r"\s+", " ", text.strip())

    # ÉTAPE 2: Supprimer les accents
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")

    # ÉTAPE 3: Mettre en majuscules
    text = text.upper()

    # ÉTAPE 4: Remplacer les caractères spéciaux et ligatures
    replacements = {
        "&": "AND",
        "$": "S",
        "Œ": "OE",
        "OE": "OE",
        "Æ": "AE",
        "AE": "AE",
        # Échappements Unicode explicites : les guillemets courbes avaient été
        # aplatis en ASCII par un éditeur → entrées dupliquées no-op (AUDIT.md §3.5)
        "‘": "'",  # ‘ apostrophe ouvrante
        "’": "'",  # ’ apostrophe fermante (la plus fréquente dans les titres)
        "`": "'",
        "´": "'",  # ´ accent aigu isolé
        "“": '"',  # “ guillemet double ouvrant
        "”": '"',  # ” guillemet double fermant
        "«": '"',
        "»": '"',
        "–": "-",
        "—": "-",
        "…": "...",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    # ÉTAPE 5: Supprimer tous les caractères de ponctuation sauf lettres, chiffres et espaces
    # Garder les apostrophes pour les featuring
    text = re.sub(r"[^\w\s\'-]", "", text)

    # ÉTAPE 6: Remplacer espaces multiples par un seul (final cleanup)
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def repair_extra_separators(text: str, sep: str = ";") -> tuple:
    """
    Répare les lignes ayant plus de colonnes que l'en-tête : les champs
    excédentaires sont fusionnés dans la 3e colonne (Éditeur/Distributeur),
    seule colonne susceptible de contenir le séparateur (noms de labels).
    Retourne (texte_réparé, nombre_de_lignes_réparées).
    """
    lines = text.splitlines()
    if not lines:
        return text, 0

    expected = lines[0].count(sep) + 1
    repaired = 0
    out = [lines[0]]

    for line in lines[1:]:
        fields = line.split(sep)
        # Ne pas toucher aux lignes vides, conformes, ou contenant des quotes
        if len(fields) > expected and '"' not in line and line.strip():
            extra = len(fields) - expected
            # Fusionner la colonne Éditeur avec les champs excédentaires,
            # en QUOTANT le champ pour que le ';' interne ne re-splitte pas
            label = sep.join(fields[2 : 3 + extra])
            merged = fields[:2] + [f'"{label}"'] + fields[3 + extra :]
            line = sep.join(merged)
            repaired += 1
        out.append(line)

    return "\n".join(out), repaired


# ---------------------------------------------------------------------------
# Canonicalisation SNEP — PARTAGÉE entre le validateur et le nettoyeur.
#
# Ces fonctions vivaient dans `snep_cleaner`, où le validateur ne pouvait pas les
# atteindre sans créer un import entre deux pairs. Résultat mesuré le 2026-09-03 :
# le nettoyeur retirait 722 doublons là où le validateur n'en signalait que 151,
# parce que lui seul normalisait avant de construire sa clé. Elles rejoignent donc
# `repair_extra_separators` ici — même statut, même raison.
# ---------------------------------------------------------------------------
# Casse canonique des niveaux (clé = forme minuscule)
LEVEL_CANON = {
    lvl.lower(): lvl
    for lvl in (
        "Or",
        "Double Or",
        "Triple Or",
        "Platine",
        "Double Platine",
        "Triple Platine",
        "Diamant",
        "Double Diamant",
        "Triple Diamant",
        "Quadruple Diamant",
    )
}

# Catégories : singulier → pluriel + casse canonique
CATEGORY_CANON = {
    "single": "Singles",
    "singles": "Singles",
    "album": "Albums",
    "albums": "Albums",
    "vidéo": "Vidéos",
    "vidéos": "Vidéos",
    "video": "Vidéos",
    "videos": "Vidéos",
}


# --- Restauration des caractères corrompus par le SNEP ---
# Le '?' (codepoint 63) gravé dans la donnée SNEP remplace N'IMPORTE QUEL
# caractère perdu lors d'une vieille migration — PAS seulement l'apostrophe :
#   • la ligature œ  (C?UR → CŒUR, S?UR → SŒUR…)
#   • l'apostrophe d'élision/contraction (L?empire → L'empire, it?s → it's)
# On ne traite QUE les contextes à haute confiance ; tout '?' ambigu (ex:
# "SMILE?IT", "Who… are ?") est LAISSÉ tel quel pour révision manuelle.

# 1) Ligature œ : mots français connus (la liste évite les faux positifs).
_OE_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bC\?URS?\b",  # CŒUR(S)
        r"\bS\?URS?\b",  # SŒUR(S)
        r"\bV\?UX?\b",  # VŒU(X)
        r"\bB\?UFS?\b",  # BŒUF(S)
        r"\bN\?UDS?\b",  # NŒUD(S)
        r"\bM\?URS\b",  # MŒURS
        r"\bF\?TUS\b",  # FŒTUS
        # Ajoutés le 2026-09-06 après comptage sur le CSV réel : le motif du
        # CŒUR (`\bC\?URS?\b`) ne couvre pas CHŒUR — la lettre H s'intercale —
        # et « ENFOIRES EN CH?UR » restait donc corrompu.
        r"\bCH\?URS?\b",  # CHŒUR(S)
        r"\bMAN\?UVRES?\b",  # MANŒUVRE(S)
        r"(?<![A-Za-zÀ-ÿ])\?UVRES?\b",  # ŒUVRE(S)
        r"(?<![A-Za-zÀ-ÿ])\?UFS?\b",  # ŒUF(S)
        r"(?<![A-Za-zÀ-ÿ])\?IL\b",  # ŒIL
        r"(?<![A-Za-zÀ-ÿ])\?DIPE\b",  # ŒDIPE
    )
]


def _oe_sub(m):
    g = m.group(0)
    lig = "Œ" if g == g.upper() else "œ"
    return g.replace("?", lig, 1)


# 2) Apostrophe : UNIQUEMENT élisions françaises et contractions anglaises.
_ELISION_FR = re.compile(r"\b([CDJLMNST])\?(?=[A-Za-zÀ-ÿ])", re.I)
_ELISION_FR2 = re.compile(r"\b(QU|JUSQU|LORSQU|PUISQU|QUOIQU|AUJOURD)\?(?=[A-Za-zÀ-ÿ])", re.I)
_CONTRACTION_EN = re.compile(r"([A-Za-zÀ-ÿ])\?(S|T|RE|VE|LL|D|M)\b", re.I)


def clean_field(s: str) -> str:
    """Strip + écrase tab/espaces multiples en un seul espace."""
    return re.sub(r"\s+", " ", (s or "")).strip()


def restore_apostrophes(s: str) -> tuple[str, int]:
    """Restaure les caractères corrompus (?) en contexte sûr : ligature œ puis
    apostrophe d'élision/contraction. Retourne (texte, nb de '?' restaurés).
    Les '?' ambigus restent intacts (à signaler par le validateur)."""
    before = s.count("?")
    if not before:
        return s, 0
    for pat in _OE_PATTERNS:
        s = pat.sub(_oe_sub, s)
    s = _ELISION_FR.sub(lambda m: m.group(1) + "'", s)
    s = _ELISION_FR2.sub(lambda m: m.group(1) + "'", s)
    s = _CONTRACTION_EN.sub(lambda m: m.group(1) + "'" + m.group(2), s)
    return s, before - s.count("?")


# Un « ? » ENTRE DEUX LETTRES est une corruption (L?empire, CH?UR, DES?REE) ;
# un « ? » précédé d'une espace ou en fin de champ est un vrai point
# d'interrogation (« QUI SAIT ? », « ...READY FOR IT? »). Mesuré le 2026-09-06 :
# sur les 102 « ? » du CSV SNEP, l'écrasante majorité sont légitimes — d'où le
# choix de ne JAMAIS toucher au reste automatiquement.
_CORRUPTION_RE = re.compile(r"[^\W\d_]\?[^\W\d_]")


def contient_caractere_corrompu(s: str) -> bool:
    """Le champ porte-t-il un « ? » en position de corruption ?

    Même motif que `snep_validator` : c'est le critère qui décide ce qu'on
    propose à la revue manuelle, il ne doit pas exister en deux exemplaires.
    """
    return bool(_CORRUPTION_RE.search(s or ""))


#: Ce qui reste d'un libellé quand on ne garde que les lettres et les chiffres.
#: Deux libellés de même squelette ne diffèrent QUE par leur ponctuation et
#: leurs accents — c'est exactement le périmètre d'une corruption d'encodage.
_NON_ALPHANUM_RE = re.compile(r"[^a-z0-9]+")


def squelette_libelle(valeur: str) -> str:
    """Le libellé réduit à ses lettres et chiffres, en minuscules.

    Sert à reconnaître DEUX ÉCRITURES DU MÊME LIBELLÉ quand l'une est corrompue :
    « AU C?UR D'IAM » et « AU CŒUR D'IAM » ont le même squelette. Plus brutal
    que `cle_plate`, qui conserve justement le « ? » — les deux coexistent parce
    qu'ils répondent à des questions opposées : `cle_plate` pour retrouver un
    libellé « tel qu'écrit », celui-ci pour l'apparier malgré son écriture.
    """
    return _NON_ALPHANUM_RE.sub("", (valeur or "").lower())


def reperer_fantomes(
    lignes: Sequence[Sequence[str]],
    *,
    i_artiste: int,
    i_titre: int,
    autres: Sequence[int],
) -> list[int]:
    """Indices des lignes CASSÉES dont la version saine est déjà dans le fichier.

    Un fantôme n'est pas une ligne que le nettoyeur aurait ratée : c'est le
    même événement écrit deux fois, une fois avec un caractère corrompu et une
    fois sans, les deux coexistant parce que le libellé fait partie de la clé de
    dédoublonnage et que les deux formes ne s'y reconnaissent pas.

    D'où viennent-ils : le CSV clean ACCUMULE d'un export à l'autre — à raison,
    une certification ancienne peut disparaître de l'export — et garde donc
    éternellement le libellé d'hier, même quand la source a corrigé son
    encodage depuis. Mesuré le 2026-09-06 : **130 lignes** côté SNEP (dont
    « SHURIK?N », qui coupait en deux la discographie certifiée de l'artiste),
    et **0** côté BRMA comme RIAA — leurs bruts sont de vraies accumulations,
    qui ne se corrigent jamais toutes seules.

    Ce n'est PAS `restore_apostrophes`, qui devine une réparation et reste donc
    délibérément timide : ici la version correcte est DÉJÀ dans le fichier, il
    n'y a rien à deviner — seulement à préférer celle qui n'est pas cassée.

    Deux règles ont été comparées sur les données réelles avant de choisir : un
    joker caractère à caractère (110 lignes) et ce squelette (114 sur le seul
    titre). Les 4 de l'écart étaient de vrais doublons que le joker manquait,
    la corruption ayant mangé un caractère ET son espace (« DONT?SAY GOODBYE »
    contre « DON'T SAY GOODBYE »). Risque inverse vérifié : **aucune** des
    lignes retirées ne porte un « ? » en position de vraie question (final ou
    suivi d'un espace), donc aucune ponctuation légitime n'est perdue.
    """
    besoin = max(i_artiste, i_titre, *autres) + 1 if autres else max(i_artiste, i_titre) + 1
    groupes: dict[tuple, list[tuple[int, bool]]] = {}
    for i, ligne in enumerate(lignes):
        # Une ligne malformée est CONSERVÉE telle quelle par les nettoyeurs :
        # on ne peut pas juger ce qu'on ne sait pas lire, et l'indexer
        # aveuglément ferait planter le nettoyage sur la seule ligne qu'il
        # avait justement décidé de ne pas toucher.
        if len(ligne) < besoin:
            continue
        artiste, titre = ligne[i_artiste], ligne[i_titre]
        cle = (
            squelette_libelle(artiste),
            squelette_libelle(titre),
            *(ligne[j] for j in autres),
        )
        cassee = "?" in artiste or "?" in titre
        groupes.setdefault(cle, []).append((i, cassee))

    fantomes = []
    for membres in groupes.values():
        # Il faut une version saine SOUS LA MAIN : sans elle, la ligne cassée
        # est la seule trace de la certification et la retirer perdrait la
        # donnée. C'est la différence entre nettoyer et effacer.
        if any(not cassee for _i, cassee in membres):
            fantomes.extend(i for i, cassee in membres if cassee)
    return sorted(fantomes)


def cle_plate(valeur: str) -> str:
    """Forme comparable d'un libellé : majuscules, espaces normalisés.

    Volontairement PLUS timide que `normalize_text` : elle ne retire ni accents
    ni ponctuation, donc elle conserve le « ? » d'une corruption ou l'esperluette
    d'un crédit. Sert de clé partout où l'on veut comparer des libellés « tels
    qu'écrits », à la casse et aux espaces près.
    """
    return re.sub(r"\s+", " ", (valeur or "")).strip().upper()


def cle_correction(artist: str, title: str) -> str:
    """Clé stable d'une correction manuelle : « ARTISTE|TITRE » normalisés.

    Normalisée pour survivre à un ré-import dont la casse ou les espaces
    diffèrent — mais PAS via `normalize_text`, qui supprime justement le « ? »
    qu'on cherche à retrouver.
    """
    return f"{cle_plate(artist)}|{cle_plate(title)}"


def apply_manual_fixes(artist: str, title: str, fixes: dict) -> tuple[str, str]:
    """Applique une correction manuelle si (artiste, titre) en a une.

    Les corrections vivent dans un fichier à part et sont réappliquées à CHAQUE
    nettoyage : une ré-importation depuis le SNEP réintroduit le « ? » d'origine,
    corriger le CSV en place ne tiendrait donc qu'un tour. Retourne le couple
    inchangé quand aucune correction ne correspond.
    """
    if not fixes:
        return artist, title
    correction = fixes.get(cle_correction(artist, title))
    if not correction:
        return artist, title
    return correction.get("artist") or artist, correction.get("title") or title


# ---------------------------------------------------------------------------
# Niveaux RIAA — vocabulaire canonique et ÉCHELLES, partagés.
#
# Cette normalisation vivait en DOUBLE, byte pour byte, dans `cert_matcher` et
# `update_riaa` : le piège des jumeaux, en attente d'une correction appliquée à
# un seul des deux. Elle n'existe plus qu'ici.
#
# **Deux programmes distincts, deux échelles.** La RIAA décerne des awards
# « classiques » et des awards **latins**, et ce ne sont PAS des paliers d'une
# même échelle : un « 55x Platino » ne vaut pas 55 millions d'unités mais 3,3.
# Les fondre est une erreur de mesure, pas un détail de libellé — c'est ce que
# faisait l'ancien scraper, qui lisait `la_61_big.png` comme « 61x Platinum ».
#
# Le programme latin (« Los Premios de Oro y de Platino », depuis 2000) ne
# dépend pas du pays mais du CONTENU : au moins 51 % en espagnol. C'est pourquoi
# il ne concernera jamais le rap FR — mais il pollue le corpus RIAA global.
#
# **Historique des seuils, et ce qu'il implique** (communiqués RIAA d'origine,
# consultés le 2026-09-06 via archive.org) :
#
#   · Latin à la création (2000) : Oro 100 000, Platino 200 000,
#     Multi-Platino 400 000 ;
#   · Latin au 01/01/2008 : Oro 50 000, Platino 100 000, Multi-Platino 200 000.
#     Le communiqué est EXPLICITE sur le sort de l'existant : « All titles
#     certified under the Latin program prior to January 1, 2008 received
#     automatic amendments to their certification levels. » Les anciens titres
#     ont donc été REQUALIFIÉS sur la nouvelle échelle ;
#   · Latin au 20/12/2013 : Oro 30 000, Platino 60 000, Multi-Platino 120 000,
#     et création du Diamante (600 000, soit 10× Platino).
#
# Conséquence pour le programme LATIN : le palier affiché aujourd'hui est sur
# l'échelle d'aujourd'hui, et `riaa_units` dit donc juste — ce n'est pas une
# approximation.
#
# ⚠️ **Il reste deux exceptions, côté US, où aucune requalification n'est
# documentée** :
#   · singles d'avant le 01/01/1989 : Gold 1 000 000 et Platinum 2 000 000, soit
#     le DOUBLE des seuils actuels (mesuré : 1 020 lignes du corpus) ;
#   · singles numériques de 2004 à juillet 2006 : Gold à 100 000, et ces Gold-là
#     ont été CONSERVÉS tels quels quand le seuil a changé en 2006 (les Platinum
#     numériques de la période, eux, ont été retirés).
#
# Pour ces deux cas, `riaa_units` SOUS-ESTIME. Rendre la fonction sensible à la
# date exigerait aussi de distinguer single physique et single numérique, que la
# donnée ne porte pas.
# ---------------------------------------------------------------------------
PROGRAMME_US = "US"
PROGRAMME_LATIN = "LATIN"

#: Familles d'award telles que le site les écrit dans l'`alt` du badge
#: (« badge DI level 0 »). Mesuré le 2026-09-06 : les certifications de 1975 sont
#: toutes `ST`, celles de 2005 sur des singles toutes `DI`. La famille est donc
#: le discriminant PHYSIQUE / NUMÉRIQUE, que le libellé de format ne donne pas
#: (le site écrit « SINGLE » dans les deux cas).
FAMILLE_STANDARD = "ST"  # support physique
FAMILLE_NUMERIQUE = "DI"  # digital
FAMILLE_LATINE = "LA"

#: Mot du palier → (forme canonique, programme, unités du palier simple).
#: Le multiplicateur « Nx » ne s'applique qu'au palier PLATINE de chaque échelle
#: (« 4x Gold » n'existe pas ; « Diamond » est un palier fixe). Dans les DEUX
#: échelles, le palier diamant vaut exactement 10× le palier platine
#: (10 000 000 = 10 × 1 000 000 ; 600 000 = 10 × 60 000) — ce qui explique le
#: « niveau 10 » des badges du site.
_PALIERS_RIAA = {
    "gold": ("Gold", PROGRAMME_US, 500_000),
    "platinum": ("Platinum", PROGRAMME_US, 1_000_000),
    "diamond": ("Diamond", PROGRAMME_US, 10_000_000),
    "oro": ("Oro", PROGRAMME_LATIN, 30_000),
    "platino": ("Platino", PROGRAMME_LATIN, 60_000),
    "diamante": ("Diamante", PROGRAMME_LATIN, 600_000),
}

#: « 4x Multi-Platinum », « 2X PLATINO », « Multi-Platinum », « GOLD »…
_NIVEAU_RIAA_RE = re.compile(r"^(?:(\d+)\s*x\s*)?(?:multi-?\s*)?([A-Za-zÀ-ÿ]+)$", re.I)


#: Seuls ces paliers acceptent un multiplicateur. « 4x Gold » n'existe pas — la
#: RIAA compte les multiples à partir du platine, et le diamant est un palier
#: fixe. Le commentaire le disait déjà ; le code, lui, multipliait n'importe
#: quel palier, si bien que `riaa_units("4x Gold")` rendait 2 000 000 et que le
#: validateur acceptait un niveau qui n'existe pas (attrapé par ses tests).
_PALIERS_MULTIPLIABLES = {"platinum", "platino"}


def _decoder_niveau_riaa(level: str) -> tuple[int, tuple] | None:
    """(multiplicateur, palier) d'un niveau RIAA, ou None s'il est inconnu."""
    m = _NIVEAU_RIAA_RE.match(re.sub(r"\s+", " ", (level or "").strip()))
    if not m:
        return None
    mot = m.group(2).lower()
    palier = _PALIERS_RIAA.get(mot)
    if not palier:
        return None
    multiplicateur = int(m.group(1) or 1)
    if multiplicateur > 1 and mot not in _PALIERS_MULTIPLIABLES:
        return None
    return multiplicateur, palier


def riaa_level(s: str) -> str:
    """Forme canonique d'un niveau RIAA (les deux programmes).

    « 4x Multi-Platinum » → « 4x Platinum » ・ « 2X PLATINO » → « 2x Platino »
    ・ « GOLD » → « Gold » ・ « Multi-Platinum » → « Platinum ».

    Un libellé non reconnu est rendu tel quel (espaces normalisés) : inventer une
    correspondance serait pire que de recopier la source.
    """
    s = re.sub(r"\s+", " ", (s or "").strip())
    decode = _decoder_niveau_riaa(s)
    if decode is None:
        return s
    mult, (canon, _programme, _unites) = decode
    return f"{mult}x {canon}" if mult > 1 else canon


def programme_riaa(level: str) -> str:
    """Programme d'un niveau RIAA : `PROGRAMME_LATIN` ou `PROGRAMME_US`.

    Repli quand la source ne le dit pas explicitement (lignes du corpus
    historique, antérieures à la collecte de la famille d'award). Un libellé
    inconnu est rendu au programme US, qui est le cas courant.
    """
    decode = _decoder_niveau_riaa(level)
    return decode[1][1] if decode else PROGRAMME_US


#: Seuils d'ÉPOQUE, pour les cas où la RIAA n'a PAS requalifié l'existant.
#: Chaque entrée : (date de fin d'application, palier → unités).
#: · Singles physiques d'avant le 01/01/1989 : Gold 1 000 000, Platinum
#:   2 000 000 — le double des seuils actuels, sans requalification documentée.
#: · Singles NUMÉRIQUES de 2004 à juillet 2006 : Gold 100 000. Les Platinum
#:   numériques de cette période ont été RETIRÉS en août 2006 ; les Gold, eux,
#:   ont été conservés tels quels — c'est justement pourquoi ils subsistent avec
#:   leur ancien seuil.
_SEUILS_SINGLE_PHYSIQUE_AVANT_1989 = {"Gold": 1_000_000, "Platinum": 2_000_000}
_SEUILS_SINGLE_NUMERIQUE_2004_2006 = {"Gold": 100_000, "Platinum": 200_000}


def _seuils_d_epoque(palier: str, date: str, format_type: str, famille: str) -> int | None:
    """Seuil applicable à cette certification-là, ou None (= barème actuel).

    Les dates sont comparées en ISO (« 1989-01-01 »), ce qui suffit : on ne
    compare que des bornes d'année.
    """
    if (format_type or "").strip().upper() != "SINGLE" or not date:
        return None
    famille = (famille or "").strip().upper()
    if date < "1989-01-01" and famille != FAMILLE_NUMERIQUE:
        # Avant 1989 le numérique n'existe pas : l'absence de famille ne crée
        # aucune ambiguïté, seul un `DI` explicite (impossible) l'écarterait.
        return _SEUILS_SINGLE_PHYSIQUE_AVANT_1989.get(palier)
    if famille == FAMILLE_NUMERIQUE and "2004-01-01" <= date < "2006-08-01":
        return _SEUILS_SINGLE_NUMERIQUE_2004_2006.get(palier)
    return None


def riaa_units(
    level: str,
    *,
    date: str = "",
    format_type: str = "",
    famille: str = "",
) -> int | None:
    """Unités correspondant à un niveau RIAA, sur l'échelle de SON programme.

    « Platinum » → 1 000 000 ・ « Platino » → 60 000 ・ « 55x Platino » → 3 300 000.
    None si le libellé n'est pas reconnu — mieux vaut ne rien dire qu'annoncer un
    chiffre inventé.

    `date`, `format_type` et `famille` (ST/DI/LA, cf. l'`alt` du badge) permettent
    d'appliquer les seuils de l'ÉPOQUE dans les deux cas où la RIAA n'a pas
    requalifié l'existant. Sans eux, le barème actuel s'applique — ce qui reste
    juste pour l'écrasante majorité des certifications, programme latin compris
    (celui-là, la RIAA l'a bien requalifié en 2008).
    """
    decode = _decoder_niveau_riaa(level)
    if decode is None:
        return None
    mult, (canon, _programme, unites) = decode
    epoque = _seuils_d_epoque(canon, date, format_type, famille)
    return (epoque if epoque is not None else unites) * mult


def canon_category(cat: str) -> str:
    return CATEGORY_CANON.get(cat.lower(), cat)


def canon_level(lvl: str) -> str:
    return LEVEL_CANON.get(lvl.lower(), lvl)
