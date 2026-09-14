"""Builder du CSV canonique SNEP : brut `certif-.csv` → `certif_snep.csv`.

Convention « brut + clean » (voir plan certifs). Le CLEAN **accumule** : les
certifs de l'export SNEP (fenêtre glissante) sont fusionnées sans jamais rien
retirer — sinon on perd l'historique (l'ancien `certifications.db`, gitignoré,
en contenait 292 de plus que le brut courant). Aucune dépendance DB en régime
permanent ; `bootstrap_rows_from_db` ne sert qu'à la migration initiale.

Colonnes canoniques (lues ensuite par `cert_matcher._load_snep`, qui normalise
à la volée comme pour BRMA/RIAA) :
    artist, title, publisher, category, certification, release_date, certification_date
"""

import io
import re
from datetime import date
from pathlib import Path

import pandas as pd

from src.models.certification import CertificationCategory, CertificationLevel
from src.utils import cert_store
from src.utils.cert_normalize import normalize_text, repair_extra_separators, reperer_fantomes
from src.utils.logger import get_logger

logger = get_logger(__name__)

CANONICAL_COLUMNS = [
    "artist",
    "title",
    "publisher",
    "category",
    "certification",
    "release_date",
    "certification_date",
]


def read_raw_snep_csv(filepath: Path) -> pd.DataFrame:
    """Lit le brut SNEP `certif-.csv` (semicolon, encodages variés, séparateurs
    à réparer) → DataFrame aux colonnes normalisées. Pur (aucune DB).

    Repris tel quel de l'ancien `SNEPCertificationManager.load_csv`.
    """
    if not filepath.exists():
        logger.warning(f"⚠️ Fichier CSV non trouvé : {filepath}")
        return pd.DataFrame()

    try:
        encodings = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
        raw_text = None
        for encoding in encodings:
            try:
                raw_text = filepath.read_text(encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        if raw_text is None:
            logger.error("Impossible de charger le CSV avec les encodages disponibles")
            return pd.DataFrame()

        raw_text = raw_text.replace("\x00", "")

        raw_text, repaired = repair_extra_separators(raw_text)
        if repaired:
            logger.warning(f"🩹 {repaired} ligne(s) CSV réparée(s) (séparateur en trop)")

        df = pd.read_csv(
            io.StringIO(raw_text),
            sep=";",
            na_values=["", "N/A", "null", "None"],
            dtype=str,
            on_bad_lines="skip",
        )

        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].str.strip()

        for date_col in ["Date de sortie", "Date de constat"]:
            if date_col in df.columns:
                df[date_col] = pd.to_datetime(df[date_col], format="%d/%m/%Y", errors="coerce")

        df.columns = [col.strip().replace("﻿", "") for col in df.columns]

        new_columns = []
        for col in df.columns:
            if "nterpr" in col or "Interpr" in col:
                new_columns.append("Interprète")
            elif "diteur" in col or "Editeur" in col:
                new_columns.append("Éditeur / Distributeur")
            elif "at" in col and "gorie" in col:
                new_columns.append("Catégorie")
            elif col == "Titre":
                new_columns.append("Titre")
            elif col == "Certification":
                new_columns.append("Certification")
            elif "sortie" in col:
                new_columns.append("Date de sortie")
            elif "constat" in col:
                new_columns.append("Date de constat")
            else:
                new_columns.append(col)
        df.columns = new_columns

        logger.info(f"✅ CSV brut chargé : {len(df)} enregistrements")
        return df

    except (OSError, ValueError) as e:
        # pandas : ParserError/EmptyDataError/UnicodeDecodeError ⊂ ValueError.
        logger.error(f"❌ Erreur lors du chargement du CSV brut : {e}")
        return pd.DataFrame()


def _clean_value(value):
    """strip + collapse des espaces ; vide → None (repris de parse_and_import_csv)."""
    if pd.isna(value):
        return None
    cleaned = re.sub(r"\s+", " ", str(value).strip())
    return cleaned or None


def _to_date_str(value) -> str:
    """Timestamp/valeur → 'YYYY-MM-DD' (ou '' si absent), comme le fait le
    matcher (`str(x)[:10]`)."""
    if value is None or pd.isna(value):
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d")
    return str(value)[:10]


def canonical_rows_from_raw(df: pd.DataFrame) -> list[dict]:
    """Lignes canoniques depuis le brut. Mappe catégorie/niveau via les enums
    `Certification*` et **saute** les lignes au niveau inconnu/vide (comportement
    identique à l'ancien import DB, qui plantait puis `continue`)."""
    rows: list[dict] = []
    if df.empty:
        return rows
    cols = list(df.columns)
    for _, r in df.iterrows():
        try:
            artist = _clean_value(r[cols[0]] if len(cols) > 0 else "") or ""
            title = _clean_value(r[cols[1]] if len(cols) > 1 else "") or ""
            publisher = _clean_value(r[cols[2]]) if len(cols) > 2 else None
            category_str = _clean_value(r[cols[3]] if len(cols) > 3 else "Singles")
            certification_str = _clean_value(r[cols[4]] if len(cols) > 4 else "Or")

            # from_string(None) lève (mime le skip DB) ; from_string(inconnu) → None
            level = CertificationLevel.from_string(certification_str)
            if level is None:
                continue
            category = CertificationCategory.from_string(category_str)

            rows.append(
                {
                    "artist": artist,
                    "title": title,
                    "publisher": publisher or "",
                    "category": category.value,
                    "certification": level.value,
                    "release_date": _to_date_str(r[cols[5]] if len(cols) > 5 else None),
                    "certification_date": _to_date_str(r[cols[6]] if len(cols) > 6 else None),
                }
            )
        except (AttributeError, KeyError, IndexError, ValueError, TypeError):
            continue
    return rows


def _key(row: dict) -> tuple:
    """Clé de GROUPE : artiste, titre, certification **et CATÉGORIE**.

    La catégorie manquait — la clé reproduisait l'ancienne contrainte DB
    (artist_clean, title_clean, certification), héritage d'un schéma qui ne
    distinguait pas les formats. Conséquence mesurée le 2026-09-09 sur le corpus
    réel : **52 certifications masquées**, un même titre certifié dans les deux
    catégories fusionnant en une ligne dont `merge_canonical` ne gardait que la
    date la plus récente.

        NINHO | M.I.L.S      | Platine : Albums 2017-12-15  +  Singles 2025-03-27
        JUL   | ÉMOTIONS     | Diamant : Albums 2023-10-05  +  Singles 2025-09-04
        NINHO | M.I.L.S. 2.0 | Diamant : Singles 2021-03-11 +  Albums 2025-07-17

    Huit ans séparent l'album et le single de NINHO : ce sont deux événements,
    pas un doublon. BRMA l'avait déjà compris (`update_brma._cert_key` inclut la
    catégorie).

    Aucun risque de scission par variante d'orthographe : la catégorie ne prend
    que trois valeurs canoniques (`Singles`, `Albums`, `Vidéos`), vérifié
    identique dans le brut et dans le clean.

    Depuis le lot 7 (2026-09-14), ce n'est plus la clé de dédup mais la clé de
    GROUPE : à l'intérieur d'un groupe, `merge_canonical` distingue plusieurs
    ÉVÉNEMENTS de certification (cf. `_meme_evenement`).
    """
    return (
        normalize_text(row["artist"]),
        normalize_text(row["title"]),
        row["certification"],
        row["category"],
    )


#: Tolérance (jours) sous laquelle deux dates comptent pour la même : au-delà,
#: une re-sortie ou une re-certification distincte. 31 j absorbe un décalage de
#: fin de mois (une correction SNEP « 09/10 → 10/10 ») sans mordre sur les
#: écarts de re-sortie, qui se comptent en mois ou en années.
_MEME_EVENEMENT_JOURS = 31


def _parse_iso(value: str) -> date | None:
    """`'YYYY-MM-DD'` → date, ou None si vide/illisible (verbatim comme stocké)."""
    if not value:
        return None
    try:
        y, m, d = value[:10].split("-")
        return date(int(y), int(m), int(d))
    except (ValueError, TypeError):
        return None


def _proches(d1: str, d2: str) -> bool | None:
    """True/False si les deux dates sont à ≤ 31 j ; None si l'une manque.

    Le None est distinct de False : une date ABSENTE n'est pas un signal
    d'événement distinct (elle ne le refute pas non plus), elle laisse la
    décision aux autres règles — sans quoi une simple lacune d'export scinderait
    un titre en deux (mesuré : DOMINO « Baila Baila Comigo », une seule sortie
    connue, deux constats à un jour = correction SNEP à ne PAS scinder)."""
    a, b = _parse_iso(d1), _parse_iso(d2)
    if a is None or b is None:
        return None
    return abs((a - b).days) <= _MEME_EVENEMENT_JOURS


def _meme_evenement(a: dict, b: dict) -> bool:
    """Deux lignes canoniques sont-elles la MÊME certification ?

    Confronté au site (l'oracle) le 2026-09-14, un groupe `(artiste, titre,
    catégorie, palier)` à plusieurs dates de constat recouvre trois populations
    qu'aucun champ seul ne discrimine :

      - **correction SNEP** (sortie ±31 j ET constat ±31 j) — un même événement
        re-daté à quelques jours ; le label peut diverger (« EMI MUSIC FRANCE »
        vs sa concaténation corrompue), il ne tranche donc pas ici ;
      - **re-sortie** (sortie à > 31 j d'écart) — événement DISTINCT (Nathalie
        Cardone *Hasta Siempre* : Or 1997, puis Or 2025 pour la re-sortie 2019) ;
      - **re-certification sous un autre distributeur** (même sortie, constat à
        > 31 j, LABEL différent) — événement distinct (Selena Gomez : deux Or,
        Polydor/Universal puis Universal, les deux sur le site).

    D'où la relation, du plus fort au plus faible :

      1. sorties proches ET constats proches → correction, quel que soit le label ;
      2. sorties proches ET même label → re-publication ou retrait (le site ne
         montre alors que le dernier constat — JUL *MIMI*, Or 05/2025 puis
         11/2025, même label) ;

    sinon deux événements distincts (re-sortie, ou autre distributeur).
    """
    sorties = _proches(a["release_date"], b["release_date"])
    if sorties is False:
        # Sorties connues et à > 31 j : re-sortie, événement distinct.
        return False
    # Sorties proches, ou l'une inconnue (une lacune ne force pas la scission).
    if _proches(a["certification_date"], b["certification_date"]):
        return True  # règle 1 : correction SNEP
    return normalize_text(a["publisher"]) == normalize_text(b["publisher"])  # règle 2


def _fusionner_groupe(rows: list[dict]) -> list[dict]:
    """Réduit un groupe `_key` à ses ÉVÉNEMENTS distincts.

    La relation `_meme_evenement` n'est pas une clé de hachage (elle n'est pas
    transitive en général) : on forme les composantes connexes par PAIRES
    (union-find). Les groupes sont petits (≤ 6 lignes en pratique, 8 au pire),
    le coût quadratique est sans enjeu. Au sein d'un événement : la première
    occurrence gagne pour les champs, la date de constat la plus récente
    l'emporte — c'est ce que montre le site.
    """
    n = len(rows)
    if n == 1:
        return [dict(rows[0])]

    parent = list(range(n))

    def trouver(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if _meme_evenement(rows[i], rows[j]):
                parent[trouver(i)] = trouver(j)

    evenements: dict[int, dict] = {}
    ordre: list[int] = []
    for i in range(n):
        racine = trouver(i)
        if racine not in evenements:
            evenements[racine] = dict(rows[i])
            ordre.append(racine)
        else:
            cur = evenements[racine]
            if (rows[i].get("certification_date") or "") > (cur.get("certification_date") or ""):
                cur["certification_date"] = rows[i]["certification_date"]
    return [evenements[r] for r in ordre]


def merge_canonical(base: list[dict], new: list[dict]) -> list[dict]:
    """Fusion ACCUMULANTE par ÉVÉNEMENT de certification.

    Regroupe par `(artiste, titre, catégorie, palier)` puis, à l'intérieur de
    chaque groupe, distingue les événements distincts (re-sorties, autres
    distributeurs) que l'ancienne clé de hachage écrasait en une ligne — elle ne
    gardait que la date la plus récente, faisant DISPARAÎTRE l'Or d'origine.
    L'ordre de `base` est préservé, les nouvelles clés ajoutées à la suite.

    **Le SNEP RETIRE des certifications**, et le clean ne peut pas le voir seul :
    il ACCUMULE par construction (une certif ancienne peut simplement sortir de
    la fenêtre glissante de l'export). C'est `snep_vues` qui le sait, en
    confrontant chaque année relue ENTIÈREMENT à ce que le site montre encore ;
    `rebuild` exclut ce qu'il a marqué retiré (`exclure_retirees`). Mesuré le
    2026-09-14 : 8 vrais retraits sur 3 ans, contre 305 paliers intermédiaires
    que le site efface en montant — ceux-là RESTENT, c'est la valeur du magasin.

    Second risque assumé : le label est comparé via `normalize_text` ; une dérive
    d'orthographe entre deux lignes du groupe C-même-label fabriquerait un faux
    événement. Marginal (spot-check de l'oracle), non gardé automatiquement.
    """
    groupes: dict[tuple, list[dict]] = {}
    ordre: list[tuple] = []
    for row in [*base, *new]:
        k = _key(row)
        if k not in groupes:
            groupes[k] = []
            ordre.append(k)
        groupes[k].append(row)
    resultat: list[dict] = []
    for k in ordre:
        resultat.extend(_fusionner_groupe(groupes[k]))
    return resultat


def _iso_depuis_brut(s: str) -> str:
    """« JJ/MM/AAAA » du brut → « AAAA-MM-JJ » canonique ; verbatim si illisible."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", (s or "").strip())
    return f"{m[3]}-{int(m[2]):02d}-{int(m[1]):02d}" if m else (s or "").strip()[:10]


def _cle_canonique_depuis_brut(cle: tuple) -> tuple | None:
    """Traduit une clé de ligne du BRUT (`snep_vues.cle_ligne`) dans l'espace du
    clean, par les MÊMES conversions que `canonical_rows_from_raw` — sans quoi
    une exclusion raterait sa cible sur un simple « Platine » vs « platine »."""
    _, titre, categorie, palier, sortie, constat = cle
    level = CertificationLevel.from_string(palier)
    if level is None:
        return None
    return (
        titre,
        CertificationCategory.from_string(categorie).value,
        level.value,
        _iso_depuis_brut(sortie),
        _iso_depuis_brut(constat),
    )


def _cle_canonique(row: dict) -> tuple:
    """SANS l'artiste : le clean UNIFIE les crédits d'une œuvre
    (`unifier_credits`), la ligne retirée du brut porte l'ancien. Titre,
    catégorie, palier, sortie ET constat au jour près suffisent à viser."""
    return (
        normalize_text(row["title"]),
        row["category"],
        row["certification"],
        row["release_date"],
        row["certification_date"],
    )


def exclure_retirees(rows: list[dict], cles_brut: set[tuple]) -> tuple[list[dict], list[dict]]:
    """Écarte du clean les lignes que `snep_vues` a marquées RETIRÉES par le SNEP.

    Le brut les garde (sidecar réversible) ; seul le clean, lu par le matcher,
    ne doit plus les servir. Rend (conservées, exclues).
    """
    cibles = {c for c in map(_cle_canonique_depuis_brut, cles_brut) if c}
    if not cibles:
        return rows, []
    gardees, exclues = [], []
    for row in rows:
        (exclues if _cle_canonique(row) in cibles else gardees).append(row)
    return gardees, exclues


_SEPARATEURS_DE_NOMS = re.compile(r"\s*(?:,|&| feat\.?\s| x | and |;)\s*", re.IGNORECASE)


def _noms(credit: str) -> set[str]:
    """Les noms d'un crédit SNEP, normalisés : « GIMS, DAMSO » → {GIMS, DAMSO}.
    Découpé sur le crédit BRUT — `normalize_text` supprime la virgule."""
    return {normalize_text(n) for n in _SEPARATEURS_DE_NOMS.split(credit or "") if n.strip()}


def unifier_credits(rows: list[dict]) -> tuple[list[dict], int]:
    """Un seul crédit d'artiste par ŒUVRE : celui de la ligne la plus récente.

    Le SNEP RÉÉCRIT le crédit quand il monte un palier — « GIMS, DAMSO » devient
    « GIMS & DAMSO », « HAMZA FEAT. WERENOI » → « HAMZA & WERENOI », un feat
    s'ajoute (« LUIDJI » → « LUIDJI FEAT. RYAN KOFFI »), l'ordre change (« TIITOF,
    SKUNK, LETO » → « SKUNK, TIITOF & LETO »). Mesuré le 2026-09-14 : 33 œuvres
    sur trois ans. Le clean accumulant, l'Or vivait sous l'ancien crédit et le
    Platine sous le nouveau : deux « artistes » pour une œuvre, et la fiche des
    paliers d'un titre les voyait en deux lignes.

    Une œuvre = même titre, même catégorie, même date de sortie, ET au moins un
    NOM en commun entre les deux crédits — sans cette dernière condition, deux
    titres homonymes sortis le même jour chez deux artistes fusionneraient. Le
    crédit retenu est le plus RICHE en noms, le plus récent ne départageant
    qu'à égalité : « le plus récent gagne » seul faisait perdre l'invité dans
    une trentaine de cas (« MAÎTRE GIMS & STING » → « STING », « DADJU FEAT.
    TIAKOLA » → « DADJU »), et un nom perdu, c'est la discographie certifiée de
    l'invité qui perd la ligne — `cert_matcher` rattache par mots. Mesuré sur
    le clean entier : 131 lignes réécrites. Idempotent. Le brut n'est pas touché.
    """
    par_oeuvre: dict[tuple, list[dict]] = {}
    for row in rows:
        cle = (normalize_text(row["title"]), row["category"], row["release_date"])
        par_oeuvre.setdefault(cle, []).append(row)

    reecrites = 0
    for groupe in par_oeuvre.values():
        credits = {normalize_text(r["artist"]) for r in groupe}
        if len(credits) < 2:
            continue
        # Composantes de crédits qui partagent un nom (union-find minimal sur
        # des groupes de 2-4 lignes).
        retenu = max(
            groupe,
            key=lambda r: (len(_noms(r["artist"])), r.get("certification_date") or ""),
        )
        noms_retenus = _noms(retenu["artist"])
        for r in groupe:
            if r["artist"] != retenu["artist"] and _noms(r["artist"]) & noms_retenus:
                r["artist"] = retenu["artist"]
                reecrites += 1
    return rows, reecrites


def purger_fantomes(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Retire les lignes au libellé cassé dont la version SAINE est déjà là.

    C'est un défaut de `merge_canonical`, et il faut en nommer la mécanique :
    sa clé passe par `normalize_text`, qui SUPPRIME le « ? » et développe la
    ligature (« Œ » → « OE »). « AU C?UR D'IAM » devient donc « AU CUR D'IAM »
    et « AU CŒUR D'IAM » devient « AU COEUR D'IAM » — deux clés distinctes, deux
    lignes conservées, pour une seule certification.

    Le clean accumulant d'un export à l'autre — à raison, une certification
    ancienne peut disparaître de l'export — il gardait ainsi éternellement le
    libellé d'hier, même après que la source a corrigé son encodage. Mesuré le
    2026-09-06 : **130 lignes**, dont « SHURIK?N », qui coupait en deux la
    discographie certifiée de l'artiste.

    Retourne (lignes gardées, lignes retirées). Le tri de `reperer_fantomes`
    n'est jamais appliqué à l'ordre du fichier : celui de `base` est préservé,
    comme le promet `merge_canonical`.
    """
    if not rows:
        return rows, []
    colonnes = list(CANONICAL_COLUMNS)
    i_artiste, i_titre = colonnes.index("artist"), colonnes.index("title")
    autres = tuple(colonnes.index(c) for c in ("category", "certification", "certification_date"))
    lignes = [[str(r.get(c, "")) for c in colonnes] for r in rows]

    retires = set(reperer_fantomes(lignes, i_artiste=i_artiste, i_titre=i_titre, autres=autres))
    if not retires:
        return rows, []
    return (
        [r for i, r in enumerate(rows) if i not in retires],
        [r for i, r in enumerate(rows) if i in retires],
    )


def read_canonical_csv(path: Path) -> list[dict]:
    """Relit `certif_snep.csv` (tout en str, vides = '')."""
    if not path.exists():
        return []
    df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    return df.to_dict("records")


def write_canonical_csv(rows: list[dict], path: Path) -> None:
    df = pd.DataFrame(rows, columns=CANONICAL_COLUMNS)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")


def write_meta(path: Path, source: str, count: int, *, partial: str = "") -> None:
    """Sidecar de fraîcheur — la FORME est portée par `cert_store`."""
    cert_store.ecrire_fraicheur(path, source, count=count, partial=partial)


def rebuild(
    raw_path: Path,
    csv_path: Path,
    meta_path: Path,
    source: str = "GLOBAL",
    *,
    partial: str = "",
    retirees: set[tuple] | frozenset = frozenset(),
) -> int:
    """Régénère le clean en fusionnant le brut courant dans l'existant (accumule),
    puis écrit le CSV canonique + le sidecar meta. Retourne le nombre de lignes.

    `retirees` : clés de lignes du brut que le site ne montre plus (cf.
    `snep_vues.cles_retirees`) — exclues du clean, jamais du brut."""
    existing = read_canonical_csv(csv_path)
    new = canonical_rows_from_raw(read_raw_snep_csv(raw_path))
    merged, fantomes = purger_fantomes(merge_canonical(existing, new))
    merged, exclues = exclure_retirees(merged, set(retirees))
    if exclues:
        logger.info(
            f"🚫 {len(exclues)} ligne(s) retirée(s) par le SNEP exclue(s) du clean "
            "(conservées dans le brut, cf. certif-.vues.json)"
        )
    merged, reecrites = unifier_credits(merged)
    if reecrites:
        # Deux lignes d'un même événement pouvaient vivre sous deux crédits : une
        # fois le crédit unifié, elles se retrouvent dans le même groupe.
        merged = merge_canonical([], merged)
        logger.info(f"🖊️ {reecrites} crédit(s) d'artiste unifié(s) sur le crédit courant du site")
    # `rebuild` RÉÉCRIT le clean à chaque appel : il se sauvegarde donc toujours,
    # et plus seulement lorsqu'une purge de fantômes retire quelque chose. La
    # rétention de `cert_store` (les 10 plus récentes) répond au motif qui avait
    # fait choisir l'inverse — sauvegarder à chaque fois n'enterre plus rien.
    backup = cert_store.sauvegarder(csv_path)
    if fantomes:
        nom = backup.name if backup else "aucune (fichier neuf)"
        logger.info(
            f"🧹 {len(fantomes)} ligne(s) fantôme(s) retirée(s) — libellé cassé dont la "
            f"version saine est déjà présente. Sauvegarde : {nom}"
        )
    write_canonical_csv(merged, csv_path)
    write_meta(meta_path, source, len(merged), partial=partial)
    logger.info(f"📄 certif_snep.csv : {len(merged)} lignes ({len(new)} depuis le brut)")
    return len(merged)


def bootstrap_rows_from_db(db_path: Path) -> list[dict]:
    """MIGRATION UNIQUE : lit l'ancien `certifications.db` → lignes canoniques,
    pour préserver l'historique accumulé (non présent dans le brut). Ne pas
    utiliser en régime permanent (la DB est retirée en fin de chantier)."""
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.execute(
            "SELECT artist_name, title, publisher, category, certification, "
            "release_date, certification_date FROM certifications"
        )
        return [
            {
                "artist": an or "",
                "title": ti or "",
                "publisher": pub or "",
                "category": cat or "Singles",
                "certification": lvl or "Or",
                "release_date": (str(rel)[:10] if rel else ""),
                "certification_date": (str(cdate)[:10] if cdate else ""),
            }
            for an, ti, pub, cat, lvl, rel, cdate in cur.fetchall()
        ]
    finally:
        conn.close()
