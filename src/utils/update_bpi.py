"""Mise à jour des certifications BPI (Royaume-Uni) — CLI + brut → clean.

Même chaîne que les trois autres organismes : un BRUT permanent qui accumule
tout ce qu'on a vu, un CLEAN dérivé qui est le SEUL fichier lu par le matcher,
et un sidecar de fraîcheur. Rien d'original ici, et c'est voulu.

**Ce qui est propre à la BPI**, en revanche, tient en deux points :

· `--full` existe, et les autres sources n'en ont pas besoin. La fenêtre de
  dates du site ne filtre que la DERNIÈRE certification d'un titre : une ligne
  réhaussée quitte la fenêtre de son palier d'origine. Un balayage par dates ne
  reconstitue donc PAS l'historique, seul un balayage non filtré le donne.

· La clé de dédup est le triplet d'ids STABLES (format, artiste, titre) plus le
  palier et sa date, là où les trois autres sources se rabattent sur des clés
  textuelles. La dédup reste ADDITIVE : le niveau étant dans la clé, les paliers
  successifs d'un même titre coexistent au lieu de s'écraser.

Lancement (la GUI l'appelle en sous-processus) :

    python -u src/utils/update_bpi.py --auto
    python -u src/utils/update_bpi.py --artist "Shurik'N" --artist IAM
    python -u src/utils/update_bpi.py --from 01-01-2020 --to 31-01-2020
    python -u src/utils/update_bpi.py --full
    python -u src/utils/update_bpi.py --clean --dry-run
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import pandas as pd

from src.concurrency import async_loop
from src.observability import repository as usage_repository
from src.observability.registry import Flow
from src.scrapers.bpi_scraper import BpiScraper
from src.utils import cert_clean_report, cert_store
from src.utils.cert_normalize import FORMAT_JOUR_CLI, bpi_level, bpi_units, jour_cli, lire_jour_cli
from src.utils.logger import get_logger

if sys.stdout and "pytest" not in sys.modules:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logger = get_logger(__name__)

_BPI_DIR = Path(__file__).parent.parent.parent / "data" / "certifications" / "bpi"
CERTIF_CSV = _BPI_DIR / "certif_bpi.csv"  # CLEAN (lu par le matcher)
BPI_RAW = _BPI_DIR / "bpi_raw.csv"  # BRUT permanent (union des scrapes)
BPI_META = _BPI_DIR / "metadata.json"  # fraîcheur (sidecar)

CERTIF_COLUMNS = [
    "artist",
    "title",
    "category",
    "certification_level",
    "certification_date",
    "release_date",
    "label",
    # `units` : ce que vaut le palier POUR CE FORMAT. Calculée ici et écrite,
    # pas jetée à la frontière du CSV — c'est exactement ce que faisait le
    # scraper RIAA, et c'est pourquoi son erreur d'échelle est restée invisible
    # des années. Un Silver d'album (60 000) et un Silver de single (200 000)
    # portent le même mot sans valoir la même chose.
    "units",
    # Identité STABLE de la ligne côté source. C'est elle qui fait la clé de
    # dédup, bien plus sûre que les clés textuelles des trois autres sources.
    "format_id",
    "artist_id",
    "title_id",
    "detail_url",
    "scraped_at",
]


# ── Brut / clean ──────────────────────────────────────────────────────────────
def _texte(valeur) -> str:
    """Valeur → texte, l'absence donnant «  » et jamais « None » ni « nan ».

    Un `str(None)` qui produit la chaîne « None » est le genre de littéral qui
    finit par se retrouver dans un CSV puis affiché à l'utilisateur — le projet
    en a déjà nettoyé une génération à la frontière DB→objet.
    """
    if valeur is None or (isinstance(valeur, float) and pd.isna(valeur)):
        return ""
    return str(valeur).strip()


def _align_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Restreint/complète aux CERTIF_COLUMNS et RAMÈNE TOUT AU TEXTE.

    La coercition n'est pas cosmétique, c'est ce qui rend l'accumulation
    idempotente : le brut est relu depuis un CSV, donc en `str`, alors que les
    lignes fraîches du scraper portent de vrais entiers (`format_id`,
    `artist_id`, `title_id`, `units`). Sans normalisation commune, la dédup
    EXACTE de `_merge_certif_csv` ne reconnaît pas ses propres lignes et le brut
    double à chaque ré-import — en silence, puisque le clean, lui, dédoublonne
    sur sa clé métier et reste juste.
    """
    df = df[[c for c in df.columns if c in CERTIF_COLUMNS]].copy()
    for col in CERTIF_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[CERTIF_COLUMNS]
    return df.apply(lambda colonne: colonne.map(_texte))


def _load_bpi_raw() -> pd.DataFrame:
    """Brut permanent. Seedé depuis le clean au premier appel (meilleur dispo)."""
    for chemin in (BPI_RAW, CERTIF_CSV):
        if chemin.exists():
            return _align_columns(pd.read_csv(chemin, encoding="utf-8-sig", dtype=str).fillna(""))
    return pd.DataFrame(columns=CERTIF_COLUMNS)


def _write_bpi_raw(df: pd.DataFrame, backup: bool = True) -> None:
    """Écrit le brut, backup horodaté avant réécriture.

    `backup=False` : l'unité de travail qu'on protège est le RUN, pas le vidage.
    Un `--full` vide son lot tous les N titres ; sauvegarder à chaque fois
    laissait une vingtaine de copies de plusieurs Mo, et le bruit finit par
    cacher la sauvegarde qui compte (règle tranchée sur RIAA le 2026-09-09).
    """
    _BPI_DIR.mkdir(parents=True, exist_ok=True)
    if backup:
        cert_store.sauvegarder(BPI_RAW)
    df.to_csv(BPI_RAW, index=False, encoding="utf-8-sig")


def _norm(valeur) -> str:
    return re.sub(r"\s+", " ", str(valeur or "")).strip().upper()


def _cle_dedup(df: pd.DataFrame) -> pd.Series:
    """Identité métier d'une ligne.

    Le triplet d'ids quand la source l'a donné ; sinon un repli textuel, pour
    que les lignes d'un parseur dégradé ne se dédoublonnent pas toutes ensemble
    sur « 0|0|0 ».

    **Le palier fait partie de la clé** : c'est ce qui rend la dédup ADDITIVE et
    préserve l'historique d'un titre réhaussé, exactement comme sur les trois
    autres sources.
    """
    ids = (
        df["format_id"].map(_norm)
        + "|"
        + df["artist_id"].map(_norm)
        + "|"
        + df["title_id"].map(_norm)
    )
    textuel = (
        df["artist"].map(_norm) + "|" + df["title"].map(_norm) + "|" + df["category"].map(_norm)
    )
    identite = ids.where(~ids.isin(["0|0|0", "||"]), textuel)
    return (
        identite
        + "|"
        + df["certification_level"].map(lambda x: _norm(bpi_level(x)))
        + "|"
        + df["certification_date"].map(_norm)
    )


def _clean_from_raw(raw_df: pd.DataFrame, report: dict | None = None) -> pd.DataFrame:
    """Dérive le CLEAN : retire les lignes creuses, canonise, dédoublonne, trie.

    `report` recueille le DÉTAIL de ce qui a été fait — sans lui, un nettoyage ne
    dit qu'une chose (« X → Y lignes »), ce qui ne permet ni de valider avant
    d'appliquer, ni de comprendre après coup d'où vient l'écart.
    """
    df = _align_columns(raw_df)

    vides = df[(df["artist"].str.strip() == "") | (df["title"].str.strip() == "")]
    if report is not None:
        report["empty_removed"] = len(vides)
        report["empty_examples"] = [
            f"{r.artist!r} — {r.title!r} ({r.certification_date})"
            for r in vides.head(8).itertuples()
        ]
    df = df.drop(vides.index)

    if report is not None:
        report["level_changes"] = _compter_changements(df["certification_level"], bpi_level)
    df["certification_level"] = df["certification_level"].map(bpi_level)

    # Les unités sont RECALCULÉES à chaque nettoyage : elles dérivent du couple
    # (niveau, format) et n'ont pas à être une donnée de saisie qu'on traînerait.
    df["units"] = [
        str(bpi_units(niv, format_type=cat) or "")
        for niv, cat in zip(df["certification_level"], df["category"], strict=False)
    ]

    avant = len(df)
    df = df.assign(_k=_cle_dedup(df)).drop_duplicates("_k", keep="first").drop(columns="_k")
    if report is not None:
        report["duplicates_removed"] = avant - len(df)

    return df.sort_values(
        ["artist", "title", "certification_date"], kind="stable", ignore_index=True
    )


def _compter_changements(colonne, canoniser) -> dict[str, int]:
    """{« brut → canonique »: n} pour les valeurs que `canoniser` modifierait."""
    change: dict[str, int] = {}
    for brut in colonne:
        canon = canoniser(brut)
        if canon != brut:
            cle = f"{brut} → {canon}"
            change[cle] = change.get(cle, 0) + 1
    return change


def _write_bpi_meta(source: str = "GLOBAL", count: int | None = None, *, partial: str = "") -> None:
    """Sidecar de fraîcheur — la FORME est portée par `cert_store`."""
    if count is None and CERTIF_CSV.exists():
        try:
            count = len(pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str))
        except (OSError, ValueError):
            count = None
    cert_store.ecrire_fraicheur(BPI_META, source, count=count, partial=partial)


#: Colonnes qui font l'identité d'une ligne du BRUT — c'est-à-dire toutes SAUF
#: la date de collecte. `scraped_at` est de la provenance, pas de la donnée : le
#: laisser dans la dédup fait que deux scrapes du même jour produisent deux
#: lignes différant d'une seconde, et le brut DOUBLE à chaque ré-import. Le
#: clean, lui, resterait juste (il dédoublonne sur sa clé métier), donc le
#: gonflement passerait inaperçu — mesuré sur un vrai run : 18 lignes devenues
#: 36 alors que les tests unitaires étaient verts, faute d'y avoir mis un
#: `scraped_at`.
_COLONNES_IDENTITE = [c for c in CERTIF_COLUMNS if c != "scraped_at"]


def _merge_certif_csv(
    new_rows: list[dict], source: str = "GLOBAL", backup: bool = True
) -> tuple[int, int]:
    """Accumule dans le BRUT (dédup hors provenance) puis dérive le CLEAN.

    Retourne (total_clean, ajoutées_au_brut).

    **Aucune ligne = aucune écriture, et surtout aucun horodatage.** C'est le
    garde-fou G6 : la RIAA horodatait sa fraîcheur sur un run vide, si bien que
    deux mois de panne se sont lus comme deux mois de succès.
    """
    if not new_rows:
        return (0, 0)

    new_df = _align_columns(pd.DataFrame(new_rows))
    raw = _load_bpi_raw()

    # Le brut est d'abord dédoublonné SUR LUI-MÊME, avant la fusion. Sans cette
    # étape, « ajoutées » mélangeait deux mouvements de sens contraire et
    # pouvait sortir NÉGATIF (mesuré : « -18 ajoutée(s) » en réparant un brut
    # gonflé). Un compteur qui agrège un ajout et une purge n'apprend rien ;
    # deux compteurs disent ce qui s'est passé.
    avant = len(raw)
    if not raw.empty:
        raw = raw.drop_duplicates(subset=_COLONNES_IDENTITE, keep="first", ignore_index=True)
    purgees = avant - len(raw)
    if purgees:
        logger.warning(f"BPI : {purgees} doublon(s) purgé(s) du brut au passage")

    combine = (
        pd.concat([raw, new_df], ignore_index=True) if not raw.empty else new_df
    ).drop_duplicates(subset=_COLONNES_IDENTITE, keep="first", ignore_index=True)
    ajoutees = len(combine) - len(raw)
    _write_bpi_raw(combine, backup=backup)

    clean = _clean_from_raw(combine)
    if backup:
        cert_store.sauvegarder(CERTIF_CSV)
    clean.to_csv(CERTIF_CSV, index=False, encoding="utf-8-sig")
    _write_bpi_meta(source=source, count=len(clean))

    from src.utils.cert_matcher import reset_cert_matcher

    reset_cert_matcher()
    return (len(clean), ajoutees)


# ── Exécution des scrapes ─────────────────────────────────────────────────────
class Collecte(NamedTuple):
    """Ce qu'une collecte a rendu, ET si elle est complète.

    Le scraper est créé puis fermé DANS `_collecte` : sans ce retour, son
    drapeau de troncature mourait avec lui et l'appelant annonçait un balayage
    terminé sur un corpus coupé.
    """

    lignes: list[dict]
    tronque: bool


def _collecte(travail) -> Collecte:
    """Lance `travail(scraper)` sur LA boucle applicative et ferme la session.

    Un seul chemin async, pas de jumeau sync : c'est la voie que la production
    emprunte, donc la seule que les tests aient à couvrir. Deux voies jumelles
    ont déjà laissé un garde-fou sur celle que personne n'emprunte.
    """

    async def _run():
        scraper = BpiScraper()
        try:
            return Collecte(await travail(scraper), scraper.tronque)
        finally:
            await scraper.aclose()

    return async_loop.run_sync(_run())


def _dire_troncature(quoi: str) -> None:
    print(
        f"⚠️  {quoi} TRONQUÉ : le plafond de pagination a été atteint. "
        "Relever `bpi_max_pages` puis relancer — le corpus est incomplet."
    )


def _cle_palier(ligne) -> tuple:
    """Identité d'un palier : le titre côté source, son niveau, sa date.

    Sert la REPRISE du balayage complet. Le niveau et la date en font partie
    parce qu'un titre réhaussé depuis notre dernier passage doit être redemandé :
    sa page de détail porte un palier de plus.
    """
    lire = ligne.get if hasattr(ligne, "get") else (lambda k, d="": ligne[k])
    return (
        _texte(lire("format_id", "")),
        _texte(lire("artist_id", "")),
        _texte(lire("title_id", "")),
        _norm(bpi_level(_texte(lire("certification_level", "")))),
        _texte(lire("certification_date", "")),
    )


def _paliers_connus() -> set[tuple]:
    """Paliers déjà présents dans le brut, pour ne pas re-scraper leurs détails."""
    raw = _load_bpi_raw()
    if raw.empty:
        return set()
    return {_cle_palier(r) for r in raw.to_dict("records")}


def _horodater(lignes: list[dict]) -> list[dict]:
    marque = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for ligne in lignes:
        ligne.setdefault("scraped_at", marque)
        ligne["units"] = bpi_units(
            ligne.get("certification_level", ""), format_type=ligne.get("category", "")
        )
    return lignes


def fetch_artists(noms: list[str]) -> bool:
    """Certifications BPI d'un ou plusieurs artistes (avec paliers datés).

    `--artist` est RÉPÉTABLE : un membre de groupe est crédité sous son nom ET
    sous celui de sa formation, chercher un seul des deux ampute la moitié de sa
    discographie certifiée.
    """

    async def travail(scraper):
        sorties: list[dict] = []
        for nom in noms:
            print(f"--- BPI : {nom}")
            sorties.extend(await scraper.scrape_by_artist(nom, get_details=True))
        return sorties

    collecte = _collecte(travail)
    lignes = _horodater(collecte.lignes)
    if not lignes:
        print("❌ Aucune certification BPI trouvée pour ces artistes")
        return False
    total, ajoutees = _merge_certif_csv(lignes, source="ARTIST")
    print(f"✅ BPI : {len(lignes)} vue(s), {ajoutees} ajoutée(s) au brut (clean : {total})")
    if collecte.tronque:
        _dire_troncature("Relevé par artiste")
        return False
    return True


def fetch_periode(debut: str, fin: str) -> bool:
    """Rescrape une fenêtre PRÉCISE (dates de dernière certification).

    ⚠️ Ne reconstitue pas l'historique : un titre réhaussé depuis ne ressort pas
    dans la fenêtre de son palier d'origine. Pour l'historique, `--full`.
    """
    try:
        d0, d1 = lire_jour_cli(debut), lire_jour_cli(fin)
    except ValueError as e:
        print(f"❌ {e}")
        return False
    print(f"=== BPI, période {jour_cli(d0)} → {jour_cli(d1)} ===")
    # Le site, lui, parle ISO.
    debut, fin = d0.isoformat(), d1.isoformat()
    collecte = _collecte(lambda s: s.scrape_by_date_range(debut, fin, get_details=True))
    lignes = _horodater(collecte.lignes)
    if not lignes:
        # G6 : on ne consigne RIEN. Une période réellement vide et un accès cassé
        # se ressemblent ; horodater les confondrait pour toujours.
        print("❌ Aucune certification vue — période réellement vide, ou source cassée")
        return False
    total, ajoutees = _merge_certif_csv(lignes, source="SCRAPE")
    print(f"✅ {len(lignes)} vue(s), {ajoutees} ajoutée(s) (clean : {total})")
    if collecte.tronque:
        _dire_troncature("Balayage de la période")
        _write_bpi_meta(source="SCRAPE", partial=f"{debut} → {fin} : plafond atteint")
        return False
    _write_bpi_meta(source="SCRAPE")
    return True


def update_auto(months: int = 1) -> bool:
    """Fenêtre glissante des N derniers mois."""
    fin = datetime.now().date()
    debut = fin - timedelta(days=31 * max(1, months))
    return fetch_periode(debut.isoformat(), fin.isoformat())


def full_sweep(get_details: bool = True) -> bool:
    """Balayage COMPLET du corpus — la reprise initiale.

    ~26 500 lignes sur ~1 105 pages au 2026-09-07, plus une page de détail par
    titre dont le dernier palier n'est pas Silver (Silver étant le plancher, ces
    titres n'ont qu'un palier par construction).
    """
    print("=== BPI : balayage COMPLET (long — ~1 100 pages + les détails) ===")

    # REPRISE : ce qu'on a déjà, à l'identité ET au palier près. Un titre dont
    # on connaît déjà le dernier palier a déjà livré son historique — inutile de
    # redemander sa page de détail. Le re-balayage des listes coûte ~20 minutes,
    # les détails plusieurs heures : c'est là que la reprise se joue.
    connus = _paliers_connus()
    if connus:
        print(f"   {len(connus)} palier(s) déjà en base — leurs détails ne seront pas redemandés")

    total_ecrit = 0

    vidages = 0

    def vider(lot: list[dict]) -> None:
        """Écrit un lot en cours de route.

        Sans cela, ~26 500 titres restaient en mémoire jusqu'à la fin : une
        coupure à la troisième heure perdait les trois heures.

        La sauvegarde n'a lieu qu'au PREMIER vidage : l'unité de travail qu'on
        protège est le run, et un balayage complet en compte une vingtaine.
        """
        nonlocal total_ecrit, vidages
        if not lot:
            return
        total, _ = _merge_certif_csv(_horodater(lot), source="GLOBAL", backup=(vidages == 0))
        vidages += 1
        total_ecrit = total
        print(f"   … {total} ligne(s) en base", flush=True)

    collecte = _collecte(
        lambda s: s.scrape_all(
            get_details=get_details,
            deja_connu=lambda ligne: _cle_palier(ligne) in connus,
            vidage=vider,
        )
    )
    if not collecte.lignes and not total_ecrit:
        print("❌ Balayage vide — la source est cassée (le corpus n'est jamais vide)")
        return False
    vider(collecte.lignes)
    if collecte.tronque:
        _dire_troncature("Balayage COMPLET")
        _write_bpi_meta(source="GLOBAL", partial="plafond de pagination atteint — corpus tronqué")
        return False
    _write_bpi_meta(source="GLOBAL")  # efface un éventuel motif précédent
    print(f"✅ Balayage terminé — {total_ecrit} ligne(s) dans le clean")
    return True


# ── Nettoyage ─────────────────────────────────────────────────────────────────
def clean_certif_csv(apply: bool = True) -> dict:
    """Régénère le clean depuis le brut. `apply=False` = DRY-RUN."""
    report = cert_clean_report.rapport_vierge(CERTIF_CSV)
    raw = _load_bpi_raw()
    report["rows_in"] = len(raw)
    clean = _clean_from_raw(raw, report)
    report["rows_out"] = len(clean)
    report["deja_propre"], report["lignes_modifiees"] = cert_clean_report.comparer_au_fichier(
        clean, CERTIF_CSV
    )
    if apply and not clean.empty:
        if (sauvegarde := cert_store.sauvegarder(CERTIF_CSV)) is not None:
            report["backup"] = str(sauvegarde)
        clean.to_csv(CERTIF_CSV, index=False, encoding="utf-8-sig")
        _write_bpi_meta(source="CLEAN", count=len(clean))
        report["applied"] = True
    return report


def format_clean_report(report: dict) -> str:
    """Rendu par le formateur UNIQUE des trois sources (`cert_clean_report`)."""
    return cert_clean_report.render(
        report,
        titre="NETTOYAGE BPI",
        counters=[
            ("Lignes en entrée (brut)", report.get("rows_in", 0)),
            ("Lignes en sortie (clean)", report.get("rows_out", 0)),
            ("Lignes creuses retirées", report.get("empty_removed", 0)),
            ("Doublons retirés", report.get("duplicates_removed", 0)),
        ],
        sections=[("Niveaux canonisés", report.get("level_changes", {}))],
        examples=[("Lignes creuses", report.get("empty_examples", []))],
        note_dry_run="Relancer sans --dry-run pour appliquer.",
    )


def stats() -> None:
    if not CERTIF_CSV.exists():
        print("Aucun CSV BPI — lancer --full pour la reprise initiale.")
        return
    df = pd.read_csv(CERTIF_CSV, encoding="utf-8-sig", dtype=str).fillna("")
    print(f"=== BPI : {len(df)} certification(s) ===")
    print(f"Artistes distincts : {df['artist'].nunique()}")
    for colonne in ("category", "certification_level"):
        print(f"\n{colonne} :")
        for valeur, n in df[colonne].value_counts().head(12).items():
            print(f"  {valeur or '(vide)':20} {n}")
    dates = df["certification_date"][df["certification_date"] != ""]
    if not dates.empty:
        print(f"\nPériode couverte : {dates.min()} → {dates.max()}")


# ── CLI ───────────────────────────────────────────────────────────────────────
def main() -> int:
    parseur = argparse.ArgumentParser(description="Certifications BPI (UK)")
    parseur.add_argument("--auto", action="store_true", help="fenêtre des N derniers mois")
    parseur.add_argument("--months", type=int, default=1)
    parseur.add_argument("--full", action="store_true", help="balayage complet (reprise initiale)")
    parseur.add_argument("--artist", action="append", default=[], help="répétable")
    parseur.add_argument("--from", dest="debut", default="", metavar=FORMAT_JOUR_CLI)
    parseur.add_argument("--to", dest="fin", default="", metavar=FORMAT_JOUR_CLI)
    parseur.add_argument("--clean", action="store_true")
    parseur.add_argument("--dry-run", action="store_true")
    parseur.add_argument("--stats", action="store_true")
    args = parseur.parse_args()

    try:
        if args.clean:
            rapport = clean_certif_csv(apply=not args.dry_run)
            print(format_clean_report(rapport))
            # Un rapport porteur d'`error` (« brut vide, rien à nettoyer ») sortait
            # en 0 : la GUI concluait au succès et proposait « Appliquer » pour une
            # opération qui ne pouvait rien faire.
            return 1 if rapport.get("error") else 0
        if args.stats:
            stats()
            return 0
        if args.artist:
            return 0 if fetch_artists(args.artist) else 1
        if args.debut or args.fin:
            # `and` laissait `--from` SEUL tomber dans `print_help()` et sortir en
            # 0 : rien n'était scrapé, et le code de sortie disait que tout allait
            # bien. Sur le chemin GUI, une commande mal formée passait donc pour
            # une période relancée. RIAA exigeait déjà les deux.
            if not (args.debut and args.fin):
                print("❌ --from et --to vont ensemble")
                return 2
            return 0 if fetch_periode(args.debut, args.fin) else 1
        if args.full:
            return 0 if full_sweep() else 1
        if args.auto:
            return 0 if update_auto(args.months) else 1
        parseur.print_help()
        return 0
    finally:
        async_loop.shutdown()


if __name__ == "__main__":
    with usage_repository.script_scope(Flow.CERTS):
        sys.exit(main())
