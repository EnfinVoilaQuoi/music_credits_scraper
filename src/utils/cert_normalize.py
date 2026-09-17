"""Normalisation de texte pour le rapprochement des certifications.

Fonctions **pures** (aucun état, aucune DB) partagées par le matcher unifié
(`cert_matcher`) et les « clean steps » des quatre sources (SNEP/BRMA/RIAA/BPI).
Extrait de `SNEPCertificationManager.normalize_text` — la parité de
normalisation entre sources en dépend (test de caractérisation
`tests/test_cert_normalize.py`). Ne pas modifier la logique sans mettre à jour
le golden master.
"""

import re
import unicodedata
from collections.abc import Sequence
from datetime import date, datetime


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
    #
    # ⚠️ Cette table est un SOUS-ENSEMBLE de `title_matching._LIGATURES` : elle
    # ignore `ß`, `ø`, `Ø`, `ð`, présents dans les fichiers de certifications
    # (mesuré le 2026-09-09 : 6, 12, 63 et 6 occurrences). L'aligner changerait
    # les clés de `merge_canonical`, et ce magasin ACCUMULE — on fabriquerait des
    # lignes fantômes comme le 2026-09-06. À faire dans une passe dédiée, avec
    # reconstruction du clean et comptage des fantômes, pas en passant.
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


#: Mots qui ANNONCENT un nom : après eux, une lettre seule est un mot coupé.
#: « LA MAIN SUR LE C », « Les plus grandes chansons du si », « LA TOUR DE M ».
#: « a » en est délibérément ABSENT — il ferait de « A A A » (un vrai titre, des
#: lettres espacées) un suspect, sans rattraper aucune troncature réelle.
_ANNONCE_UN_NOM = frozenset(
    [
        "le",
        "la",
        "les",
        "l",
        "du",
        "de",
        "des",
        "d",
        "au",
        "aux",
        "en",
        "un",
        "une",
        "nos",
        "notre",
        "mon",
        "ma",
        "mes",
        "ton",
        "ta",
        "tes",
        "son",
        "sa",
        "ses",
        "ce",
        "cet",
        "cette",
        "sur",
        "dans",
        "pour",
        "avec",
        "sans",
        "chez",
        "vers",
        "par",
        "the",
        "of",
        "my",
        "your",
    ]
)

#: Mots courts en minuscules qui terminent LÉGITIMEMENT un titre. Sans eux,
#: « I want to know what love is » passerait pour tronqué.
_FINS_MINUSCULES_LEGITIMES = frozenset(
    [
        "is",
        "in",
        "on",
        "up",
        "to",
        "me",
        "my",
        "it",
        "of",
        "so",
        "no",
        "go",
        "do",
        "be",
        "we",
        "us",
        "or",
        "at",
        "if",
        "an",
        "as",
        "by",
        "he",
        "she",
        "you",
        "the",
        "and",
        "for",
        "out",
        "off",
        "ok",
        "oh",
        "ah",
        "eh",
        "yo",
        "ça",
        "la",
        "le",
        "tu",
        "on",
        "je",
    ]
)


def libelle_tronque(valeur: str) -> bool:
    """Le libellé s'arrête-t-il au milieu d'un mot ?

    Le SNEP a exporté certains titres COUPÉS à l'endroit d'un caractère
    accentué : « L'empire du c » pour « L'empire du côté obscur »,
    « LA MAIN SUR LE C » pour « LA MAIN SUR LE CŒUR », « Les plus grandes
    chansons du si » pour « du siècle ». La coupure est dans la SOURCE — les
    octets du brut s'arrêtent là — donc rien ne la répare automatiquement.

    Il n'existe pas non plus d'oracle interne : cherché le 2026-09-06, sur les
    12 127 lignes, AUCUN titre tronqué n'a sa version complète ailleurs dans le
    corpus pour la même certification (les 5 paires « préfixe » trouvées sont
    des œuvres réellement distinctes, « KILL BILL » contre « KILL BILL Vol.2 »).
    D'où le choix : SIGNALER pour saisie manuelle, jamais deviner le texte
    manquant.

    Deux marques, mesurées sur le corpus réel — les signaux évidents ayant
    d'abord été essayés et écartés : « finit par un mot de 1-2 lettres » sortait
    759 titres presque tous légitimes (« BEST OF », « AS I AM », « BAD GUY »),
    et filtrer par la rareté du mot final n'y changeait rien (333 candidats).
    Ce qui distingue vraiment un fragment, c'est son VOISIN :

      1. un mot de 1-2 lettres en minuscules, hors mots courts légitimes
         (« du si », « chansons pr », « une g ») ;
      2. une lettre SEULE précédée d'un mot qui annonce un nom (« LE C »,
         « DE M », « Nos R ») — un déterminant n'introduit jamais une lettre.

    Rendement mesuré : **24 titres sur 9 764**, dont une vingtaine réellement
    tronqués. Les faux positifs connus (« C'EST CARRÉ LE S » de SCH, « Le Z »)
    se règlent par la case « ✓ correct » de la fenêtre de correction, qui
    mémorise qu'un libellé est bon tel quel — le mécanisme existe déjà pour les
    vrais points d'interrogation.
    """
    mots = (valeur or "").split()
    if len(mots) < 2:
        return False
    # Libellé tout en lettres espacées : une graphie, pas une coupure. Le
    # corpus en compte plusieurs, et des vrais (« T L C », « K D D », « D O D O »,
    # « A A A »). Aucune troncature réelle n'a cette forme — toutes gardent au
    # moins un mot entier avant le fragment.
    if all(len(m) == 1 for m in mots):
        return False
    fin, avant = mots[-1], mots[-2].lower().rstrip("'")
    if fin.islower() and len(fin) <= 2 and fin not in _FINS_MINUSCULES_LEGITIMES:
        return True
    return len(fin) == 1 and fin.isalpha() and avant in _ANNONCE_UN_NOM


#: Ce qui reste d'un libellé quand on ne garde que les lettres et les chiffres.
#: Deux libellés de même squelette ne diffèrent QUE par leur ponctuation et
#: leurs accents — c'est exactement le périmètre d'une corruption d'encodage.
_NON_ALPHANUM_RE = re.compile(r"[^a-z0-9]+")

#: Les deux traces d'un encodage cassé : le « ? » de substitution (exports SNEP)
#: et les caractères de contrôle C1 U+0080-U+009F, qui n'existent dans aucun
#: libellé — c'est un octet cp1252 (0x9C = œ, 0x95 = •) lu comme du latin-1.
#: Mesuré le 2026-09-17 : 21 lignes du brut BRMA, toutes d'un scrape de 2025
#: (l'ancien scraper `requests`), doublant 20 certifications à œ dans le clean.
_ENCODAGE_CASSE_RE = re.compile(r"[?\x80-\x9f]")


def ecriture_cassee(*libelles: str) -> bool:
    """Vrai si l'un des libellés porte la trace d'un encodage cassé."""
    return any(_ENCODAGE_CASSE_RE.search(v or "") for v in libelles)


def squelette_libelle(valeur: str) -> str:
    """Le libellé réduit à ses lettres et chiffres, en minuscules.

    Sert à reconnaître DEUX ÉCRITURES DU MÊME LIBELLÉ quand l'une est corrompue :
    « AU C?UR D'IAM » et « AU CŒUR D'IAM » ont le même squelette. Plus brutal
    que `cle_plate`, qui conserve justement le « ? » — les deux coexistent parce
    qu'ils répondent à des questions opposées : `cle_plate` pour retrouver un
    libellé « tel qu'écrit », celui-ci pour l'apparier malgré son écriture.

    Les accents sont RETIRÉS avant (2026-09-17) : le site SNEP sert « BEYAH »
    là où le brut porte « BEYĀH », et un « Ā » supprimé comme non-alphanumérique
    faisait deux squelettes (« beyh » / « beyah ») — un Platine « retiré » alors
    que son Double Platine est sur le site. Mesuré sur le brut entier : les 133
    groupes de jumelles sont les mêmes avec et sans cette étape (l'œ, non
    décomposable, reste supprimé comme le « ? » qui le remplace).
    """
    sans_accents = "".join(
        c for c in unicodedata.normalize("NFD", valeur or "") if unicodedata.category(c) != "Mn"
    )
    return _NON_ALPHANUM_RE.sub("", sans_accents.lower())


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
        cassee = ecriture_cassee(artiste, titre)
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

#: Identité d'un organisme : code pays ISO et drapeau. `cert_matcher` portait un
#: `_FLAG` et un `_COUNTRY`… MORTS, chaque chargeur écrivant ses deux littéraux
#: en dur — et `_FLAG` avait d'ailleurs perdu BPI en route, sans conséquence
#: puisque personne ne le lisait. Cinq déclarations pour une information, dont
#: une fausse et invisible : exactement ce qu'une table unique évite.
ORGANISMES = {
    "SNEP": ("FR", "🇫🇷"),
    "BRMA": ("BE", "🇧🇪"),
    "RIAA": ("US", "🇺🇸"),
    "BPI": ("GB", "🇬🇧"),
}


def drapeau(organisme: str) -> str:
    """Le drapeau d'un organisme, « 🏳️ » si inconnu."""
    return ORGANISMES.get(organisme, ("", "🏳️"))[1]


#: Vocabulaire de certification BELGE (Ultratop), en MINUSCULES : c'est un jeu
#: de COMPARAISON, pas des formes canoniques d'affichage — d'où l'absence de
#: détection de « variantes de casse » côté validateur BRMA, contrairement à
#: SNEP. (Y brancher la formule SNEP signalerait 5 148 des 5 826 lignes réelles.
#: Mesuré le 2026-09-03, ne pas refaire.)
#:
#: Il vivait dans `brma_validator`, seul des quatre vocabulaires à ne pas être
#: ici. L'échelle belge n'est PAS celle du SNEP — « quadruple platine » existe
#: chez Ultratop et pas au SNEP — donc ce n'est pas un doublon de `LEVEL_CANON`,
#: seulement un référentiel rangé ailleurs que ses pairs.
NIVEAUX_BRMA = {
    "or",
    "platine",
    "double platine",
    "triple platine",
    "quadruple platine",
    "diamant",
    "double diamant",
    "triple diamant",
}

#: Paliers belges qui acceptent un multiplicateur (« 2x Platine », « 12x Platine »).
_PALIERS_BRMA_MULTIPLIABLES = {"or", "platine", "diamant"}


def brma_niveau_connu(level: str) -> bool:
    """Ce libellé appartient-il au vocabulaire de certification belge ?

    Le multiplicateur passe par `decouper_multiplicateur`, comme partout
    ailleurs : c'était la QUATRIÈME copie de la même expression rationnelle.
    """
    multiplicateur, palier = decouper_multiplicateur(level)
    if multiplicateur > 1:
        return palier in _PALIERS_BRMA_MULTIPLIABLES
    return palier in NIVEAUX_BRMA


#: Rang d'un palier dans une échelle qui DESCEND (1 = le plus haut).
#:
#: Il ne sert qu'à TRIER des certifications entre elles, à l'intérieur d'un même
#: corps : un Platino et un Platinum partagent le rang 7 sans valoir la même
#: chose, et la comparaison entre échelles se fait en unités (`riaa_units`).
#:
#: Il vit ici parce que c'est un RÉFÉRENTIEL, et que les référentiels de ce
#: projet vivent dans ce module. Il était défini dans `cert_matcher`, d'où
#: `cert_artist` l'importait — en allant chercher un nom PRIVÉ dans un autre
#: module, ce qui est la façon la plus discrète de créer une dépendance qu'aucun
#: outil ne signale. `cert_matcher` le ré-exporte, comme il ré-exporte déjà
#: `riaa_level`.
RANG_PALIERS = {
    "quadruple diamant": 1,
    "triple diamant": 2,
    "double diamant": 3,
    "diamant": 4,
    "triple platine": 5,
    "double platine": 6,
    "platine": 7,
    "triple or": 8,
    "double or": 9,
    "or": 10,
    # RIAA (anglais)
    "diamond": 4,
    "platinum": 7,
    "gold": 10,
    # RIAA — programme LATIN. Même ORDRE que les autres échelles.
    "diamante": 4,
    "platino": 7,
    "oro": 10,
    # BPI (UK) : le Silver est un palier SOUS l'or, que les trois autres corps
    # n'ont pas. Il ne s'insère pas dans l'échelle existante, il la prolonge.
    "silver": 11,
}

#: « 4x Platine », « 2X PLATINO » → le multiplicateur et le palier nu.
_MULTIPLICATEUR_RE = re.compile(r"^(\d+)\s*x\s*(.+)$", re.I)


def decouper_multiplicateur(niveau: str) -> tuple[int, str]:
    """« 4x Platine » → (4, « platine »). Sans multiplicateur → (1, le niveau).

    Le DÉCOUPAGE seul, sans jugement sur ce qui a le droit d'être multiplié —
    cette politique-là vit dans `_PALIERS_MULTIPLIABLES`, parce qu'elle ne
    concerne que les unités. Trois copies de cette expression rationnelle
    coexistaient (`cert_matcher._level_rank`, `cert_artist._ordre_palier`,
    `_decoder_niveau_riaa`), et elles ne s'accordaient déjà pas sur les paliers
    acceptés.
    """
    lvl = re.sub(r"\s+", " ", (niveau or "").strip()).lower()
    if (m := _MULTIPLICATEUR_RE.match(lvl)) is not None:
        return int(m.group(1)), m.group(2).strip()
    return 1, lvl


FORMAT_JOUR_CLI = "JJ-MM-AAAA"
_JOUR_CLI = re.compile(r"^\s*(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\s*$")
_JOUR_ISO = re.compile(r"^\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*$")


def lire_jour_cli(s: str) -> date:
    """Une date tapée sur la ligne de commande des scripts de certifs.

    Forme attendue : **« JJ-MM-AAAA »** (2026-09-14, demande utilisateur : avec
    `--from 2000-01-01` on ne sait jamais lequel est le mois). « JJ/MM/AAAA »
    passe aussi ; et l'ISO « AAAA-MM-JJ » reste ACCEPTÉ — l'année en tête lève
    toute ambiguïté, et les commandes fabriquées avant ce jour (GUI, scripts,
    historique du shell) ne doivent pas casser. Lève `ValueError` sinon.
    """
    m = _JOUR_CLI.match(s or "")
    if m:
        return date(int(m[3]), int(m[2]), int(m[1]))
    m = _JOUR_ISO.match(s or "")
    if m:
        return date(int(m[1]), int(m[2]), int(m[3]))
    raise ValueError(f"date illisible « {s} » — attendu {FORMAT_JOUR_CLI}")


def jour_cli(d: date) -> str:
    """L'inverse : une date → « JJ-MM-AAAA », pour fabriquer ou afficher une
    commande."""
    return d.strftime("%d-%m-%Y")


def date_riaa(s: str, *, verbatim: bool = False) -> str:
    """« October 17, 2017 » → « 2017-10-17 ». Tolère déjà-ISO et vide.

    `verbatim` décide de ce qu'on fait d'une date ILLISIBLE, et ce n'est pas un
    détail de goût : les deux usages sont incompatibles, et c'est exactement
    pourquoi trois copies de cette fonction coexistaient en divergeant sur ce
    seul point.

    · `verbatim=False` (défaut) rend "" — le VALIDATEUR compte les dates
      illisibles, il lui faut un résultat reconnaissable ;
    · `verbatim=True` rend la valeur telle quelle — le NETTOYEUR met la date
      dans sa clé de dédup, où deux valeurs illisibles différentes doivent
      rester deux lignes.
    """
    s = (s or "").strip()
    if not s or s.lower() == "none":
        return ""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s.title() if "," in s else s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s if verbatim else ""


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


# ---------------------------------------------------------------------------
# Niveaux BPI (Royaume-Uni) — « BRIT Certified ».
#
# Trois paliers, et le multiplicateur ne s'applique qu'au platine, comme chez la
# RIAA (« 4x Gold » n'existe pas plus à Londres qu'à Washington). Le site écrit
# aussi bien « Platinum » que « 2x Platinum » et « Multi-Platinum » ; son filtre
# expose 1x à 26x Platinum.
#
# **L'échelle dépend du FORMAT**, et c'est la différence de fond avec la RIAA :
# un Silver d'album (60 000) et un Silver de single (200 000) portent le même mot
# sans valoir la même chose. Une fonction d'unités qui ignorerait le format
# rendrait un chiffre faux deux fois sur trois.
#
# ⚠️ **Le Music DVD n'a pas de Silver** : le barème BPI commence à Gold pour ce
# format. `bpi_units("Silver", format_type="Music DVDs")` rend donc None — ce
# n'est pas un trou de la table, c'est le barème.
#
# ⚠️ **Seuils d'époque : NON traités, délibérément.** La BPI a bougé ses seuils
# au fil des décennies (et a basculé des expéditions vers les ventes réelles en
# juillet 2013), mais aucune source datée fiable n'a été retenue ici. Le barème
# ACTUEL est donc appliqué à tout l'historique. Contrairement à la RIAA, où les
# communiqués d'origine documentent précisément les deux exceptions, on ne
# fabrique pas ici de seuils d'époque : mieux vaut un barème uniforme et dit que
# des chiffres inventés. Si une source datée apparaît, c'est ici que ça se règle.
# ---------------------------------------------------------------------------

#: Mot du palier → forme canonique. Pas de programme parallèle chez la BPI
#: (le BRIT Billion est un award d'ARTISTE, hors de cette échelle de titres).
_PALIERS_BPI = {
    "silver": "Silver",
    "gold": "Gold",
    "platinum": "Platinum",
}

#: Seuls les platine se multiplient (cf. RIAA, même raison).
_PALIERS_BPI_MULTIPLIABLES = {"platinum"}

#: « 2x Platinum », « Multi-Platinum », « SILVER »…
_NIVEAU_BPI_RE = re.compile(r"^(?:(\d+)\s*x\s*)?(?:multi-?\s*)?([A-Za-z]+)$", re.I)

#: Format canonique → palier → unités. Les libellés du site (« Album »,
#: « Single », « Music DVDs ») passent d'abord par `_format_bpi`.
_SEUILS_BPI = {
    "album": {"Silver": 60_000, "Gold": 100_000, "Platinum": 300_000},
    "single": {"Silver": 200_000, "Gold": 400_000, "Platinum": 600_000},
    "video": {"Gold": 25_000, "Platinum": 50_000},  # pas de Silver : voir plus haut
}


def _format_bpi(format_type: str) -> str:
    """Libellé de format BPI → clé de `_SEUILS_BPI` (« » si inconnu)."""
    f = re.sub(r"\s+", " ", (format_type or "").strip().lower())
    if f.startswith("album"):
        return "album"
    if f.startswith("single"):
        return "single"
    if "dvd" in f or "video" in f:
        return "video"
    return ""


def _decoder_niveau_bpi(level: str) -> tuple[int, str] | None:
    """(multiplicateur, palier canonique) d'un niveau BPI, ou None si inconnu."""
    m = _NIVEAU_BPI_RE.match(re.sub(r"\s+", " ", (level or "").strip()))
    if not m:
        return None
    mot = m.group(2).lower()
    canon = _PALIERS_BPI.get(mot)
    if not canon:
        return None
    multiplicateur = int(m.group(1) or 1)
    if multiplicateur > 1 and mot not in _PALIERS_BPI_MULTIPLIABLES:
        return None
    return multiplicateur, canon


def bpi_level(s: str) -> str:
    """Forme canonique d'un niveau BPI.

    « 2X PLATINUM » → « 2x Platinum » ・ « Multi-Platinum » → « Platinum »
    ・ « silver » → « Silver ».

    Un libellé non reconnu est rendu TEL QUEL (espaces normalisés), comme
    `riaa_level` : recopier la source vaut mieux qu'inventer une correspondance.
    Pour SAVOIR si le libellé a été compris, demander `bpi_level_connu` — c'est
    la question que pose le garde-fou du scraper, et elle ne se déduit pas de la
    valeur rendue ici.
    """
    s = re.sub(r"\s+", " ", (s or "").strip())
    decode = _decoder_niveau_bpi(s)
    if decode is None:
        return s
    mult, canon = decode
    return f"{mult}x {canon}" if mult > 1 else canon


def bpi_level_connu(s: str) -> bool:
    """Ce libellé de niveau appartient-il au vocabulaire BPI ?

    Prédicat SÉPARÉ de `bpi_units` à dessein : un Silver de Music DVD est un
    niveau parfaitement connu dont les unités sont indéfinies. Confondre les deux
    ferait crier le garde-fou sur une donnée saine.
    """
    return _decoder_niveau_bpi(s) is not None


def bpi_units(level: str, *, format_type: str = "") -> int | None:
    """Unités d'un niveau BPI POUR CE FORMAT, ou None.

    « Gold » + « Album » → 100 000 ・ « Gold » + « Single » → 400 000
    ・ « 2x Platinum » + « Album » → 600 000.

    None quand le niveau est inconnu, quand le format n'est pas fourni (l'échelle
    en dépend : répondre sans lui serait deviner) ou quand le couple n'existe pas
    au barème (Silver de Music DVD).
    """
    decode = _decoder_niveau_bpi(level)
    if decode is None:
        return None
    mult, canon = decode
    unites = _SEUILS_BPI.get(_format_bpi(format_type), {}).get(canon)
    return None if unites is None else unites * mult


def canon_category(cat: str) -> str:
    return CATEGORY_CANON.get(cat.lower(), cat)


def canon_level(lvl: str) -> str:
    return LEVEL_CANON.get(lvl.lower(), lvl)
