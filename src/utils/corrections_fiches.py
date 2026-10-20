"""Corrections de fiches décidées à la main — et leur MÉMOIRE (étape 3, 2026-09-24).

Une entrée désigne UNE fiche (`{"genius_id": N}`, sinon `{"titre": …, "album":
…}` pour une fiche Deezer / Kworb) et porte ses actions ;
`scripts/appliquer_corrections_fiches.py` les applique. Depuis le chantier C
(2026-10-12) elles vivent EN BASE : les refus dans la table `refus` (lot 2), le
reste dans `corrections` (lot 3). `data/corrections/fiches.json` ne sert plus
qu'à la SAISIE à la main — versé en base par le script, puis retiré du fichier.

Corriger en base ne tient qu'un tour : le run suivant réécrit ce que sa source
dit. Le fichier est donc relu par les PRODUCTEURS, à trois endroits :

  · `ids_refuses` — le gate d'identité Spotify refuse un ID retiré à la main
    (sinon Kworb ou le scraper le reposent au prochain run) ;
  · `deezer_refuses` — même chose pour une piste Deezer DÉLIÉE (écarts Deezer,
    provider par morceau) : sans elle, « L.U.N.A.T.I.C. » et « Par Amour »
    reprenaient au run suivant l'ID qu'on venait de leur retirer (2026-10-10) ;
  · `credits_refuses` — `save_track` n'écrit pas un crédit retiré (Genius
    ressert « Matthieu Cabaret — Composer », qui est le compositing du clip) ;
  · `genius_ids_absorbes` — une page Genius FUSIONNÉE dans une autre fiche n'est
    pas recréée par le run discographie.

Aucune de ces lectures ne lève : un fichier illisible vaut « aucune correction »,
et c'est dit une fois au log.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import DATA_DIR
from src.utils.fichiers_json import MemoireIllisible, ecrire_atomique, lire_memoire
from src.utils.logger import get_logger
from src.utils.title_matching import normalize_name, normalize_title

logger = get_logger(__name__)

FICHIER = Path(DATA_DIR) / "corrections" / "fiches.json"

_cache: dict = {"chemin": None, "mtime": None, "donnees": {}}


def charger() -> dict:
    """Toutes les corrections, `{artiste: [entrée, …]}` : celles de la BASE
    (table `corrections`, chantier C lot 3), puis celles encore saisies à la
    main dans le fichier et pas encore versées."""
    donnees: dict[str, list] = {}
    for artiste, entrees in corrections_en_base().items():
        donnees.setdefault(artiste, []).extend(entrees)
    for artiste, entrees in _charger_fichier().items():
        donnees.setdefault(artiste, []).extend(entrees)
    return donnees


def _charger_fichier() -> dict:
    """Le fichier, relu seulement s'il a changé."""
    try:
        mtime = FICHIER.stat().st_mtime
    except OSError:
        return {}
    if _cache["chemin"] == FICHIER and _cache["mtime"] == mtime:
        return _cache["donnees"]
    try:
        donnees = json.loads(FICHIER.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.error(f"Corrections de fiches illisibles ({FICHIER}) : {e} — ignorées")
        donnees = {}
    _cache.update(chemin=FICHIER, mtime=mtime, donnees=donnees)
    return donnees


def _relire_pour_ecrire() -> dict | None:
    """Le fichier, pour le MODIFIER. `None` s'il est illisible : on refuse alors
    d'écrire — réécrire par-dessus effacerait toutes les décisions qu'il garde
    (avant le 2026-10-11, un fichier illisible était remplacé par la seule
    entrée qu'on ajoutait)."""
    try:
        return lire_memoire(FICHIER)
    except MemoireIllisible as e:
        logger.error(f"Corrections de fiches illisibles, rien n'y est écrit : {e}")
        return None


def designe(designation: dict, *, genius_id, titre: str | None, album: str | None) -> bool:
    """PUR. La désignation d'une entrée vise-t-elle cette fiche ?"""
    if designation.get("genius_id") is not None:
        return genius_id is not None and int(genius_id) == int(designation["genius_id"])
    # Titre EXACT : normalisé, « Boss » désignait aussi « BOSS » — et une fois
    # « Boss » fusionnée, sa désignation retombait sur la fiche GARDÉE.
    if (designation.get("titre") or "").strip() != (titre or "").strip():
        return False
    if "album" in designation:
        return normalize_title(designation["album"] or "") == normalize_title(album or "")
    return True


def entrees_du_morceau(track) -> list[dict]:
    artiste = track.artist.name if track.artist else None
    if not artiste:
        return []
    return [
        e
        for e in charger().get(artiste, [])
        if designe(
            e.get("fiche") or {}, genius_id=track.genius_id, titre=track.title, album=track.album
        )
    ]


# ── Les REFUS, en base (table `refus`, e48 — chantier C lot 2, 2026-10-12) ─────

#: Moteur de la base, posé par `DataManager` à sa construction (même motif que
#: `spotify_identity.fournisseur_titres`). Sans lui (aucune base ouverte), les
#: lectures valent « aucun refus » et les écritures ne font rien.
moteur = None

#: Clé historique de `fiches.json` → nature en base.
NATURES = {
    "retirer_id_spotify": "id_spotify",
    "retirer_id_deezer": "id_deezer",
    "retirer_videos": "video",
    "retirer_lrc": "lrc",
    "retirer_credits": "credit",
    "retirer_certifs": "certif",
    "retirer_lien_youtube": "lien_youtube",
    "retirer_paroles": "paroles",
}

#: Ce que les lignes sœurs PARTAGENT : le refus vaut pour tout l'enregistrement
#: (même page Genius, tout artiste) — un refus décidé sur la ligne de Kanye
#: protège celle de Kid Cudi (chantier C lot 1). Les certifications, recalculées
#: PAR ARTISTE, gardent leur refus à la ligne.
PAR_ENREGISTREMENT = frozenset(set(NATURES.values()) - {"certif"})


def _cle_fiche(designation: dict) -> str:
    if designation.get("genius_id") is not None:
        return f"g{int(designation['genius_id'])}"
    return f"t{(designation.get('titre') or '').strip()}|{normalize_title(designation.get('album') or '')}"


def _cle(nature: str, valeur) -> str:
    """La CLÉ normalisée d'un refus (unicité en base) ; l'entrée d'origine va
    dans `detail`."""
    if nature == "certif":
        return json.dumps(list(cle_certif(valeur)), ensure_ascii=False)
    if nature == "credit":
        return json.dumps([normalize_name(valeur["nom"]), valeur["role"]], ensure_ascii=False)
    if nature == "id_deezer":
        return str(int(valeur))
    return str(valeur)


def _lignes(track, nature: str, table: str = "refus") -> list[dict]:
    """Les refus (ou les `acceptations`) de `nature` qui visent la fiche — et,
    pour une donnée que les sœurs partagent, la même page Genius chez tout
    artiste."""
    artiste = track.artist.name if track.artist else None
    if moteur is None or not artiste:
        return []
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with moteur.connect() as conn:
            lignes = (
                conn.execute(
                    text(
                        "SELECT genius_id, artiste, titre, album, valeur, detail"
                        f"{', origine' if table == 'refus' else ''} FROM {table} "
                        "WHERE nature = :n AND (artiste = :a OR genius_id = :g)"
                    ),
                    {"n": nature, "a": artiste, "g": track.genius_id},
                )
                .mappings()
                .all()
            )
    except SQLAlchemyError as e:
        logger.warning(f"{table.capitalize()} illisibles ({nature}) : {e} — rien d'appliqué")
        return []
    gardees = []
    for r in lignes:
        if r["genius_id"] is not None:
            if track.genius_id is None or int(r["genius_id"]) != int(track.genius_id):
                continue
            if r["artiste"] != artiste and nature not in PAR_ENREGISTREMENT:
                continue
        elif r["artiste"] != artiste or not designe(
            {"titre": r["titre"], "album": r["album"]},
            genius_id=track.genius_id,
            titre=track.title,
            album=track.album,
        ):
            continue
        gardees.append(dict(r))
    return gardees


def _memoriser(
    track, nature: str, valeur, table: str = "refus", origine: str | None = None
) -> bool:
    """Consigne un refus (ou une acceptation) pour la fiche. Rend False s'il y
    était déjà."""
    artiste = track.artist.name if track.artist else None
    if moteur is None or not artiste or valeur in (None, ""):
        return False
    from datetime import datetime

    designation = designation_de(track)
    return _inserer(
        designation,
        artiste,
        track.title,
        track.album,
        nature,
        valeur,
        datetime.now().isoformat(timespec="seconds"),
        table=table,
        origine=origine,
    )


def _inserer(
    designation,
    artiste,
    titre,
    album,
    nature,
    valeur,
    le,
    conn=None,
    table: str = "refus",
    origine: str | None = None,
) -> bool:
    from sqlalchemy import text

    params = {
        "cle_fiche": _cle_fiche(designation),
        "gid": designation.get("genius_id"),
        "artiste": artiste,
        "titre": titre,
        "album": album,
        "nature": nature,
        "valeur": _cle(nature, valeur),
        "detail": json.dumps(valeur, ensure_ascii=False) if isinstance(valeur, dict) else None,
        "le": le,
    }
    # `origine` n'existe que dans `refus` (e59) : les acceptations sont toujours
    # à la main.
    avec_origine = table == "refus" and origine is not None
    if avec_origine:
        params["origine"] = origine
    sql = text(
        f"INSERT OR IGNORE INTO {table} (cle_fiche, genius_id, artiste, titre, album, nature, "
        f"valeur, detail, decide_le{', origine' if avec_origine else ''}) VALUES (:cle_fiche, "
        ":gid, :artiste, :titre, :album, :nature, :valeur, :detail, "
        f":le{', :origine' if avec_origine else ''})"
    )
    if conn is not None:
        return bool(conn.execute(sql, params).rowcount)
    with moteur.begin() as c:
        return bool(c.execute(sql, params).rowcount)


def _oublier(track, nature: str, valeur, table: str = "refus") -> bool:
    """Pendant de `_memoriser` : retire le refus de la fiche (une correction
    automatique qu'on RÉTABLIT). Rend False s'il n'y était pas."""
    artiste = track.artist.name if track.artist else None
    if moteur is None or not artiste:
        return False
    from sqlalchemy import text

    with moteur.begin() as conn:
        return bool(
            conn.execute(
                text(
                    f"DELETE FROM {table} WHERE cle_fiche = :c AND artiste = :a "
                    "AND nature = :n AND valeur = :v"
                ),
                {
                    "c": _cle_fiche(designation_de(track)),
                    "a": artiste,
                    "n": nature,
                    "v": _cle(nature, valeur),
                },
            ).rowcount
        )


def ids_refuses(track) -> set[str]:
    return {r["valeur"] for r in _lignes(track, "id_spotify")}


def origine_du_refus(track, nature: str, valeur) -> str | None:
    """« regle:<code> » quand une RÈGLE a posé ce refus, None quand c'est une
    décision à la main (ou un refus d'avant e59, 2026-10-19). Le gate s'en sert
    pour ne plus dire « retiré à la main » d'un refus automatique."""
    cle = _cle(nature, valeur)
    return next((r.get("origine") for r in _lignes(track, nature) if r["valeur"] == cle), None)


def refus_videos_a_la_fusion(conn, keep_id: int, delete_id: int) -> set[str]:
    """Une fusion dans `keep_id` (2026-10-19) : les vidéos de `delete_id` que
    `keep_id` a refusées. Le refus d'une RÈGLE tombe — son motif, « elle est à
    l'autre fiche », disparaît avec la fusion (Médine « 17-Oct » fusionnée dans
    « 17 Octobre » le 2026-10-13 : le refus restait, la vidéo revenait). Celui
    fait À LA MAIN tient : ces vidéos sont rendues pour ne PAS être transférées.
    `conn` : la transaction de la fusion (une seconde connexion se bloquerait)."""
    from sqlalchemy import text

    fiche = conn.execute(
        text(
            "SELECT t.genius_id, t.title, t.album, a.name FROM tracks t "
            "LEFT JOIN artists a ON a.id = t.artist_id WHERE t.id = :k"
        ),
        {"k": keep_id},
    ).first()
    videos = [
        r[0]
        for r in conn.execute(
            text("SELECT video_id FROM track_videos WHERE track_id = :d"), {"d": delete_id}
        )
    ]
    if fiche is None or not fiche[3] or not videos:
        return set()
    genius_id, titre, album, artiste = fiche
    designation = {"genius_id": genius_id} if genius_id else {"titre": titre, "album": album}
    marques = ", ".join(f":v{n}" for n in range(len(videos)))
    params = {
        "c": _cle_fiche(designation),
        "a": artiste,
        **{f"v{n}": v for n, v in enumerate(videos)},
    }
    # Clé par page Genius : vaut pour tout artiste (PAR_ENREGISTREMENT) ; par
    # titre + album : pour l'artiste de la fiche seulement.
    portee = "cle_fiche = :c" + ("" if genius_id else " AND artiste = :a")
    conn.execute(
        text(
            f"DELETE FROM refus WHERE {portee} AND nature = 'video' "
            f"AND origine LIKE 'regle:%' AND valeur IN ({marques})"
        ),
        params,
    )
    return {
        r[0]
        for r in conn.execute(
            text(
                f"SELECT valeur FROM refus WHERE {portee} AND nature = 'video' "
                f"AND valeur IN ({marques})"
            ),
            params,
        )
    }


def ids_acceptes(track) -> set[str]:
    """IDs Spotify ACCEPTÉS à la main (table `acceptations`, e58) : le gate ne
    leur oppose plus ni l'artiste, ni l'homonyme, ni le titre — la durée garde.
    Spotify ne crédite que 113 sur le « Température - Live 2006 » de Diam's ;
    SDM « Dans la ride » est rangé sous le profil d'un autre SDM."""
    return {r["valeur"] for r in _lignes(track, "id_spotify", "acceptations")}


def memoriser_id_accepte(track, spotify_id: str) -> bool:
    return _memoriser(track, "id_spotify", spotify_id, "acceptations")


def oublier_id_accepte(track, spotify_id: str) -> bool:
    return _oublier(track, "id_spotify", spotify_id, "acceptations")


def deezer_refuses(track) -> set[int]:
    return {int(r["valeur"]) for r in _lignes(track, "id_deezer")}


def credits_refuses(track) -> set[tuple[str, str]]:
    """{(nom normalisé, rôle)} — le rôle est la valeur de `CreditRole`."""
    return {tuple(json.loads(r["valeur"])) for r in _lignes(track, "credit")}


def genius_ids_absorbes(artiste: str) -> set[int]:
    """Pages Genius fusionnées dans une autre fiche de `artiste` (relu par
    l'import discographie : une page absorbée n'est pas recréée)."""
    return {
        int(e["fiche"]["genius_id"])
        for e in charger().get(artiste, [])
        if e.get("fusionner_dans") and (e.get("fiche") or {}).get("genius_id") is not None
    }


def fiches_par_id(track_ids) -> dict:
    """`{track_id: Track}` réduits à leur DÉSIGNATION (artiste, titre, album,
    page Genius) et à ce que le gate Spotify regarde (durée, durées observées,
    featuring) — de quoi lire leurs refus et acceptations hors de l'app
    (scripts de réparation et d'acceptation)."""
    ids = sorted({int(t) for t in track_ids or []})
    if moteur is None or not ids:
        return {}
    from sqlalchemy import text

    from src.models import Artist, Track

    marques = ", ".join(f":t{n}" for n in range(len(ids)))
    with moteur.connect() as conn:
        lignes = conn.execute(
            text(
                "SELECT t.id, t.title, t.album, t.genius_id, a.name, t.duration, "
                "t.is_featuring, t.primary_artist_name FROM tracks t "
                f"JOIN artists a ON a.id = t.artist_id WHERE t.id IN ({marques})"
            ),
            {f"t{n}": tid for n, tid in enumerate(ids)},
        ).all()
        durees = conn.execute(
            text(
                "SELECT track_id, source, value FROM observations "
                f"WHERE field = 'duration' AND track_id IN ({marques})"
            ),
            {f"t{n}": tid for n, tid in enumerate(ids)},
        ).all()
    fiches = {}
    for tid, titre, album, genius_id, artiste, duree, feat, principal in lignes:
        fiche = Track(title=titre, artist=Artist(name=artiste), album=album)
        fiche.id, fiche.genius_id, fiche.duration = tid, genius_id, duree
        fiche.is_featuring, fiche.primary_artist_name = bool(feat), principal
        fiche.durations_observees = {}
        fiches[tid] = fiche
    for tid, source, valeur in durees:
        try:
            fiches[tid].durations_observees[source] = int(float(valeur))
        except (TypeError, ValueError):
            continue
    return fiches


def designation_de(track) -> dict:
    """La désignation d'une fiche dans le fichier : sa page Genius, sinon titre
    + album (fiche Deezer)."""
    if track.genius_id:
        return {"genius_id": int(track.genius_id)}
    return {"titre": track.title, "album": track.album}


def memoriser_fusion(artiste: str, absorbee: dict, gardee: dict) -> None:
    """Consigne une fusion décidée par une RÈGLE (pas à la main) : sans elle, le
    prochain import recréerait la page absorbée (`genius_ids_absorbes`). En
    base quand elle est ouverte ; sinon dans le fichier, d'où la prochaine
    migration la versera."""
    if moteur is not None:
        from datetime import datetime

        with moteur.begin() as conn:
            _inserer_correction(
                conn,
                artiste,
                absorbee,
                "fusionner_dans",
                gardee,
                datetime.now().isoformat(timespec="seconds"),
            )
        return
    donnees = _relire_pour_ecrire()
    if donnees is None:
        return
    entrees = donnees.setdefault(artiste, [])
    if any(e.get("fiche") == absorbee and e.get("fusionner_dans") for e in entrees):
        return
    entrees.append({"fiche": absorbee, "fusionner_dans": gardee})
    ecrire_atomique(FICHIER, donnees, fin="\n")


def memoriser_id_refuse(track, spotify_id: str, origine: str | None = None) -> None:
    """Consigne un ID Spotify retiré (panneau « À trancher » à la main, ou une
    règle — `origine` « regle:<code> ») : relu par le gate d'identité
    (`ids_refuses`), sans quoi Kworb ou le scraper le reposeraient au prochain run."""
    _memoriser(track, "id_spotify", spotify_id, origine=origine)


def oublier_id_refuse(track, spotify_id: str) -> bool:
    """Pendant : une correction automatique de l'ID qu'on RÉTABLIT."""
    return _oublier(track, "id_spotify", spotify_id)


def memoriser_deezer_refuse(track, deezer_id: int) -> None:
    """Consigne une piste Deezer DÉLIÉE : relue par les écarts Deezer et le
    provider par morceau (`deezer_refuses`)."""
    _memoriser(track, "id_deezer", int(deezer_id))


def memoriser_credit_refuse(track, nom: str, role: str) -> None:
    """Consigne un crédit retiré : `save_track` ne l'écrit plus (`credits_refuses`)."""
    _memoriser(track, "credit", {"nom": nom, "role": role})


def cle_certif(entree: dict) -> tuple:
    """PUR. L'identité d'une certification rattachée : organisme, titre certifié,
    palier, date — ce qui la distingue d'une autre certif du même titre."""
    return (
        (entree.get("body") or "").strip().upper(),
        normalize_title(entree.get("title") or ""),
        (entree.get("certification") or "").strip().casefold(),
        str(entree.get("certification_date") or "")[:10],
    )


def _sous_entree_certif(entree: dict) -> dict:
    return {k: entree.get(k) for k in ("body", "title", "certification", "certification_date")}


def certifs_refusees(track) -> set[tuple]:
    """Certifications retirées À LA MAIN de cette fiche (panneau « À trancher ») :
    `apply_certifications` ne les rattache plus, sans quoi le matcher les
    reposerait au prochain « Appliquer les certifs ». PAR ARTISTE."""
    return {tuple(json.loads(r["valeur"])) for r in _lignes(track, "certif")}


def memoriser_certif_refusee(track, entree: dict, origine: str | None = None) -> None:
    _memoriser(track, "certif", _sous_entree_certif(entree), origine=origine)


def oublier_certif_refusee(track, entree: dict) -> bool:
    return _oublier(track, "certif", _sous_entree_certif(entree))


def videos_refusees(track) -> set[str]:
    """Vidéos rejetées À LA MAIN (✖️ de la fiche ou du panneau) : ni Genius, ni la
    recherche, ni le canal YTM ne les rattachent plus à cette fiche (2026-09-27 —
    avant, le run discographie reposait le lien Genius rejeté)."""
    return {r["valeur"] for r in _lignes(track, "video")}


def memoriser_video_refusee(track, video_id: str, origine: str | None = None) -> None:
    _memoriser(track, "video", video_id, origine=origine)


def empreinte_lrc(lrc: str | None) -> str:
    """PUR. L'identité d'un LRC : son TEXTE (espaces normalisés), pas sa source —
    la même source peut servir plus tard un autre LRC, qui ne doit pas être
    bloqué par le refus du premier."""
    import hashlib
    import re

    return hashlib.sha1(re.sub(r"\s+", " ", (lrc or "").strip()).encode("utf-8")).hexdigest()[:16]


def lrc_refuses(track) -> set[str]:
    """Empreintes des LRC retirés À LA MAIN : le résolveur ne les retient plus."""
    return {r["valeur"] for r in _lignes(track, "lrc")}


def memoriser_lrc_refuse(track, lrc: str, origine: str | None = None) -> None:
    _memoriser(track, "lrc", empreinte_lrc(lrc), origine=origine)


def oublier_lrc_refuse(track, lrc: str) -> bool:
    return _oublier(track, "lrc", empreinte_lrc(lrc))


def paroles_refusees(track) -> set[str]:
    """Empreintes (même calcul que les LRC : le TEXTE, espaces normalisés) des
    paroles de SECOURS retirées à la main : le résolveur ne les reprend plus —
    A2H « Pardonne moi » portait celles de « Pardonnez-moi » (2026-10-12)."""
    return {r["valeur"] for r in _lignes(track, "paroles")}


def memoriser_paroles_refusees(track, texte: str) -> None:
    _memoriser(track, "paroles", empreinte_lrc(texte))


# ── Verser les refus de `fiches.json` dans la base ────────────────────────────


def _valeurs_de(cle: str, brut) -> list:
    """Les valeurs d'une clé `retirer_*` du fichier (une liste, ou un seul
    élément pour `retirer_lien_youtube`)."""
    if brut in (None, "", []):
        return []
    valeurs = brut if isinstance(brut, list) else [brut]
    if cle == "retirer_certifs":
        return [_sous_entree_certif(v) for v in valeurs]
    return valeurs


def migrer_refus_vers_la_base() -> dict[str, int]:
    """Verse dans la table `refus` les clés `retirer_*` de `fiches.json`, puis
    les RETIRE du fichier (décision utilisateur 2026-10-12 : une seule source de
    vérité) — une copie horodatée du fichier est gardée à côté. Une entrée qui
    ne garde que sa désignation disparaît. IDEMPOTENT : sans clé `retirer_*`,
    rien n'est écrit. Sert la migration initiale ET le script de saisie à la
    main (`appliquer_corrections_fiches`), où l'on peut encore écrire un refus
    dans le fichier. Rend `{nature: refus versés}`."""
    if moteur is None:
        raise RuntimeError("aucune base ouverte (construire un DataManager d'abord)")
    donnees = _relire_pour_ecrire()
    if not donnees:
        return {}
    a_verser = [
        (artiste, entree, cle)
        for artiste, entrees in donnees.items()
        for entree in entrees
        for cle in NATURES
        if entree.get(cle)
    ]
    if not a_verser:
        return {}
    import shutil
    from datetime import datetime

    maintenant = datetime.now()
    copie = FICHIER.with_name(f"{FICHIER.stem}.avant_refus_{maintenant:%Y%m%d_%H%M%S}.json")
    shutil.copy2(FICHIER, copie)
    comptes: dict[str, int] = {}
    with moteur.begin() as conn:
        for artiste, entree, cle in a_verser:
            designation = entree.get("fiche") or {}
            for valeur in _valeurs_de(cle, entree[cle]):
                nature = NATURES[cle]
                if _inserer(
                    designation,
                    artiste,
                    designation.get("titre"),
                    designation.get("album"),
                    nature,
                    valeur,
                    maintenant.isoformat(timespec="seconds"),
                    conn=conn,
                ):
                    comptes[nature] = comptes.get(nature, 0) + 1
    for _artiste, entree, cle in a_verser:
        entree.pop(cle, None)
    for artiste in list(donnees):
        donnees[artiste] = [e for e in donnees[artiste] if set(e) - {"fiche"}]
        if not donnees[artiste]:
            del donnees[artiste]
    ecrire_atomique(FICHIER, donnees, fin="\n")
    logger.info(f"🗂️ Refus versés en base : {comptes} (copie du fichier : {copie.name})")
    return comptes


# ── Les CORRECTIONS, en base (table `corrections`, e49 — chantier C lot 3) ─────

#: Les actions d'une entrée que la table garde (les `retirer_*` sont des refus,
#: table `refus`).
ACTIONS = (
    "supprimer",
    "fusionner_dans",
    "departager_id_avec",
    "lien_youtube",
    "inedit",
    "version_de",
    "cover_de",
    "remix_de",
    "sample_de",
    "tiers",
)


def corrections_en_base() -> dict[str, list[dict]]:
    """`{artiste: [entrée, …]}` reconstruit depuis la table, une entrée par fiche
    désignée (ses actions réunies), dans l'ordre où elles ont été décidées.
    Sans base ouverte, ou illisible : rien (c'est dit au log)."""
    if moteur is None:
        return {}
    from sqlalchemy import text
    from sqlalchemy.exc import SQLAlchemyError

    try:
        with moteur.connect() as conn:
            lignes = conn.execute(
                text("SELECT artiste, fiche, action, valeur FROM corrections ORDER BY id")
            ).all()
    except SQLAlchemyError as e:
        logger.warning(f"Corrections illisibles en base : {e} — aucune appliquée")
        return {}
    donnees: dict[str, list[dict]] = {}
    index: dict[tuple[str, str], dict] = {}
    for artiste, fiche, action, valeur in lignes:
        cle = (artiste, fiche)
        if cle not in index:
            index[cle] = {"fiche": json.loads(fiche)}
            donnees.setdefault(artiste, []).append(index[cle])
        index[cle][action] = json.loads(valeur)
    return donnees


def _inserer_correction(conn, artiste: str, designation: dict, action: str, valeur, le) -> bool:
    from sqlalchemy import text

    return bool(
        conn.execute(
            text(
                "INSERT OR IGNORE INTO corrections (cle_fiche, genius_id, artiste, fiche, "
                "action, valeur, decide_le) VALUES (:cle, :gid, :artiste, :fiche, :action, "
                ":valeur, :le)"
            ),
            {
                "cle": _cle_fiche(designation),
                "gid": designation.get("genius_id"),
                "artiste": artiste,
                "fiche": json.dumps(designation, ensure_ascii=False, sort_keys=True),
                "action": action,
                "valeur": json.dumps(valeur, ensure_ascii=False, sort_keys=True),
                "le": le,
            },
        ).rowcount
    )


def migrer_corrections_vers_la_base() -> dict[str, int]:
    """Verse dans la table `corrections` les actions de `fiches.json` (hors
    refus), puis les RETIRE du fichier — même règle que les refus (décision
    utilisateur 2026-10-12) : copie horodatée gardée à côté, une entrée réduite
    à sa désignation disparaît. IDEMPOTENT. Rend `{action: lignes versées}`.

    Une clé inconnue reste dans le fichier (rien ne se perd en silence)."""
    if moteur is None:
        raise RuntimeError("aucune base ouverte (construire un DataManager d'abord)")
    donnees = _relire_pour_ecrire()
    if not donnees:
        return {}
    a_verser = [
        (artiste, entree, action)
        for artiste, entrees in donnees.items()
        for entree in entrees
        for action in ACTIONS
        if action in entree
    ]
    if not a_verser:
        return {}
    import shutil
    from datetime import datetime

    maintenant = datetime.now()
    copie = FICHIER.with_name(f"{FICHIER.stem}.avant_corrections_{maintenant:%Y%m%d_%H%M%S}.json")
    shutil.copy2(FICHIER, copie)
    comptes: dict[str, int] = {}
    with moteur.begin() as conn:
        for artiste, entree, action in a_verser:
            if _inserer_correction(
                conn,
                artiste,
                entree.get("fiche") or {},
                action,
                entree[action],
                maintenant.isoformat(timespec="seconds"),
            ):
                comptes[action] = comptes.get(action, 0) + 1
    for _artiste, entree, action in a_verser:
        entree.pop(action, None)
    for artiste in list(donnees):
        donnees[artiste] = [e for e in donnees[artiste] if set(e) - {"fiche"}]
        if not donnees[artiste]:
            del donnees[artiste]
    ecrire_atomique(FICHIER, donnees, fin="\n")
    logger.info(f"🗂️ Corrections versées en base : {comptes} (copie du fichier : {copie.name})")
    return comptes
