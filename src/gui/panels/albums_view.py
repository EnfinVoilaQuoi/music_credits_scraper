"""Vue « albums » : table agrégée, préférences d'affichage, import d'album Genius.
Le Treeview lui-même appartient à MainWindow (widget partagé avec la vue morceaux)."""

import tkinter
from datetime import datetime
from tkinter import messagebox

from sqlalchemy.exc import SQLAlchemyError

from src.enrichment.album_types import LIBELLES, RECORD_TYPES
from src.enrichment.observation import Observation
from src.gui import albums_grouping, helpers
from src.gui.panels import tracks_table
from src.models import ReleaseObservation, Track
from src.utils.logger import get_logger

logger = get_logger(__name__)


def configure_tree_for_albums(app):
    """Colonnes de la vue Albums (stats agrégées)"""
    app.tree.configure(columns=app.ALBUM_COLUMNS)
    app.tree.heading("#0", text="💿")
    app.tree.column("#0", width=40, stretch=False)
    widths = {
        "Album": (260, "w"),
        "Date sortie": (90, "w"),
        # Nature du disque telle que le distributeur la déclare (Deezer, e26) ou
        # saisie au clic droit (« ✎ »). VIDE sans donnée : ici c'est une donnée,
        # pas un libellé de visuel — un « Album » par défaut mentirait.
        "Type": (70, "center"),
        "Morceaux": (80, "center"),
        "Crédits": (70, "center"),
        "Paroles": (80, "center"),
        "Durée totale": (90, "center"),
        "Streams Spotify": (130, "e"),
        # Provenance du total ci-contre : Kworb et Spotify ne comptent pas la
        # même chose, et « Σ morceaux » n'est pas un total d'album du tout.
        "Source": (90, "center"),
        "Streams YTM": (130, "e"),
    }
    for col in app.ALBUM_COLUMNS:
        w, anchor = widths[col]
        app.tree.heading(col, text=col, command="")
        app.tree.column(col, width=w, anchor=anchor)


def set_view_mode(app, value):
    """Callback du sélecteur Morceaux / Albums"""
    mode = "albums" if value == "Albums" else "tracks"
    if mode == getattr(app, "view_mode", "tracks"):
        return
    app.view_mode = mode
    if mode == "albums":
        populate_albums_table(app)
    else:
        tracks_table.configure_tree_for_tracks(app)
        tracks_table.populate_tracks_table(app)


# ── Préférences d'affichage de la vue Albums (classement VISUEL) ──────────
# Classer un album hôte dans Featurings/Singles sans toucher au champ
# `album` des morceaux (≠ « Retirer de l'album » qui modifie la base).
# Stockage : data/album_view/<artiste>.json — {clé_normalisée: {target, label}}


def album_view_prefs_path(app):
    import re as _re
    from pathlib import Path as _Path

    from src.config import DATA_DIR

    d = _Path(DATA_DIR) / "album_view"
    d.mkdir(parents=True, exist_ok=True)
    safe = _re.sub(r"[^\w\- ]", "_", app.current_artist.name)
    return d / f"{safe}.json"


def load_album_view_prefs(app) -> dict:
    import json as _json

    try:
        return _json.loads(album_view_prefs_path(app).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_album_view_prefs(app, prefs: dict):
    import json as _json

    try:
        album_view_prefs_path(app).write_text(
            _json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    except Exception as e:
        logger.error(f"Sauvegarde préférences vue Albums échouée: {e}")


def set_album_view_pref(app, key: str, label: str, target: str):
    prefs = load_album_view_prefs(app)
    prefs[key] = {"target": target, "label": label}
    save_album_view_prefs(app, prefs)
    logger.info(f"👁 Album « {label} » classé visuellement → {target}")
    populate_albums_table(app)


def unset_album_view_pref(app, key: str):
    prefs = load_album_view_prefs(app)
    removed = prefs.pop(key, None)
    save_album_view_prefs(app, prefs)
    if removed:
        logger.info(f"👁 Ligne d'album rétablie : « {removed.get('label', key)} »")
    populate_albums_table(app)


def type_album_str(db: dict) -> str:
    """Cellule « Type » : libellé du `record_type`, « ✎ » si saisi à la main, vide sinon."""
    rt = db.get("record_type") if db else None
    if not rt:
        return ""
    libelle = LIBELLES.get(rt, rt)
    return f"{libelle} ✎" if db.get("record_type_source") == "manual" else libelle


def set_album_record_type(app, album: str, record_type: str | None):
    """Saisie manuelle de la nature du disque (clic droit) : `manual` prime sur
    Deezer et n'est jamais écrasé par un run ; `None` efface la saisie."""
    ok = app.data_manager.set_album_record_type(
        app.current_artist.id, album, record_type, source="manual"
    )
    if not ok:
        messagebox.showerror("Type d'album", f"Écriture impossible pour « {album} ».")
        return
    populate_albums_table(app)


def populate_albums_table(app):
    """Remplit le tableau avec les albums et leurs stats agrégées"""
    configure_tree_for_albums(app)
    for item in app.tree.get_children():
        app.tree.delete(item)

    # Mapping ligne → (album, tracks) pour le menu contextuel (clic droit)
    app._album_rows = {}
    # Lignes grisées quand TOUS les morceaux du groupe sont désactivés
    app.tree.tag_configure("disabled", foreground="gray", background="#2a2a2a")

    if not app.current_artist or not app.current_artist.tracks:
        return

    # Stats streams par album (table albums : Kworb Spotify + YTMusic)
    albums_db = {}
    try:
        for a in app.data_manager.get_albums_for_artist(app.current_artist.id):
            albums_db[helpers.normalize_album_title(a["title"])] = a
    except (SQLAlchemyError, KeyError, TypeError) as e:
        logger.debug(f"Albums DB indisponibles: {e}")

    FEAT_LABEL = "🎤 Featurings (albums invités)"
    SINGLES_LABEL = "— Singles / sans album —"

    # Le catalogue e31 est la source de la vue principale : une fiche peut
    # apparaître dans plusieurs éditions sans être dupliquée. Les fiches sans
    # lien (base ancienne ouverte avant migration, ou single) retombent sur le
    # champ historique `track.album`.
    groups = {}
    by_id = {track.id: track for track in app.current_artist.tracks if track.id}
    linked_ids = set()
    try:
        memberships = app.data_manager.get_release_tracks_for_artist(
            app.current_artist.id, scope="own"
        )
    except (SQLAlchemyError, KeyError, TypeError) as e:
        logger.debug(f"Catalogue de parutions indisponible: {e}")
        memberships = []
    for link in memberships:
        track = by_id.get(link["track_id"])
        if track is None:
            continue
        linked_ids.add(track.id)
        # L'ID évite de confondre deux éditions homonymes; le libellé visible
        # reste le titre naturel dans le cas courant.
        key = f"release:{link['release_id']}"
        if key not in groups:
            groups[key] = [link["title"], []]
        groups[key][1].append(track)
    for track in app.current_artist.tracks:
        if track.id in linked_ids:
            continue
        album = (track.album or "").strip()
        if not album:
            album = FEAT_LABEL if track.is_featuring else SINGLES_LABEL
        key = helpers.normalize_album_title(album)
        if key not in groups:
            groups[key] = [album, []]
        groups[key][1].append(track)

    # Classement VISUEL (clic droit → 👁) : albums entiers déplacés dans
    # Featurings/Singles sans toucher à la base (ex. album hôte avec 2
    # feats, compilation). Réversible via clic droit sur la ligne cible.
    view_prefs = load_album_view_prefs(app)
    visual_feats, visual_singles = [], []
    for key in list(groups.keys()):
        pref = view_prefs.get(key)
        if not pref:
            continue
        if pref.get("target") == "feat":
            visual_feats.extend(groups.pop(key)[1])
        elif pref.get("target") == "single":
            visual_singles.extend(groups.pop(key)[1])

    # Les apparitions isolées (1 seul morceau, en feat, sur un album hôte)
    # sont regroupées dans la ligne Featurings au lieu d'une ligne par
    # album invité. Les projets communs (≥2 morceaux) gardent leur ligne.
    feat_tracks = []
    for key in list(groups.keys()):
        display, group_tracks = groups[key]
        if (
            len(group_tracks) == 1
            and group_tracks[0].is_featuring
            and display not in (FEAT_LABEL, SINGLES_LABEL)
        ):
            feat_tracks.append(group_tracks[0])
            del groups[key]

    # Deux parutions distinctes peuvent partager un titre (réédition, compile
    # homonyme). Treeview exige une clé visible : on garde le titre lisible et
    # ne suffixe qu'en cas de collision réelle.
    rendered_groups = {}
    for display, grouped_tracks in groups.values():
        rendered = display
        if rendered in rendered_groups:
            rendered = f"{display} (édition {len(rendered_groups) + 1})"
        rendered_groups[rendered] = grouped_tracks
    groups = rendered_groups
    if feat_tracks or visual_feats:
        groups.setdefault(FEAT_LABEL, []).extend(feat_tracks + visual_feats)
    if visual_singles:
        groups.setdefault(SINGLES_LABEL, []).extend(visual_singles)

    # Regroupement/tri : logique pure, extraite dans `albums_grouping` (testée).
    ordered = albums_grouping.sort_groups(groups)
    fmt_streams = albums_grouping.format_streams

    for album, tracks in ordered:
        n = len(tracks)
        # Part de morceaux désactivés visible par ligne : "12 (2❌)"
        try:
            n_disabled = sum(1 for t in tracks if app._is_track_disabled(t))
        except Exception:
            n_disabled = 0
        n_display = f"{n} ({n_disabled}❌)" if n_disabled else n
        credits = sum(len(t.credits or []) for t in tracks)
        lyrics = sum(1 for t in tracks if t.lyrics.text and str(t.lyrics.text).strip())
        total_sec = 0
        for t in tracks:
            d = t.duration
            if isinstance(d, int):
                total_sec += d
            elif isinstance(d, str) and ":" in d:
                try:
                    parts = [int(p) for p in d.split(":")]
                    total_sec += (
                        parts[-1] + parts[-2] * 60 + (parts[-3] * 3600 if len(parts) > 2 else 0)
                    )
                except Exception:
                    pass
        if total_sec:
            h, rem = divmod(total_sec, 3600)
            m, s = divmod(rem, 60)
            duree = f"{h}h{m:02d}" if h else f"{m}:{s:02d}"
        else:
            duree = ""

        date = albums_grouping.earliest_date(tracks)
        date_str = date.strftime("%d/%m/%Y") if date else ""

        db = albums_db.get(helpers.normalize_album_title(album), {})
        sp = db.get("spotify_streams")
        yt = db.get("ytm_streams")
        # Pas de stats album Kworb (ligne Featurings, apparitions écartées,
        # singles) → fallback : somme des streams MORCEAU du groupe
        somme_morceaux = not sp
        if not sp:
            sp = sum(t.streams.spotify_streams or 0 for t in tracks) or None
        if not yt:
            yt = sum(t.streams.ytm_streams or 0 for t in tracks) or None
        sp_streams = fmt_streams(sp)
        ytm_streams = fmt_streams(yt)
        source_str = (
            albums_grouping.streams_source_label(
                db.get("spotify_streams_source"), somme_de_morceaux=somme_morceaux
            )
            if sp
            else ""
        )

        type_str = type_album_str(db)

        all_disabled = n > 0 and n_disabled == n
        item = app.tree.insert(
            "",
            "end",
            text="🎤" if album.startswith("🎤") else "💿",
            values=(
                album,
                date_str,
                type_str,
                n_display,
                credits,
                f"{lyrics}/{n}",
                duree,
                sp_streams,
                source_str,
                ytm_streams,
            ),
            tags=("disabled",) if all_disabled else (),
        )
        app._album_rows[item] = (album, tracks)


def import_genius_album(app):
    """Importe la tracklist COMPLÈTE d'un album Genius depuis son URL.

    Récupère aussi les morceaux aux paroles incomplètes, que
    /artists/{id}/songs omet (cas « Vas-y chante » : 2/14 récupérés).
    """
    from tkinter import simpledialog

    url = simpledialog.askstring(
        "Importer un album Genius",
        "URL de l'album (https://genius.com/albums/…) :",
        parent=app.root,
    )
    if not url or not url.strip():
        return
    data = app.genius_api.get_album_tracks_from_url(url.strip())
    if not data:
        messagebox.showerror("Import album", "Album introuvable — vérifier l'URL (voir logs).")
        return

    album_name = data["album"]["name"]
    release_date = None
    try:
        if data["album"].get("release_date"):
            release_date = datetime.fromisoformat(data["album"]["release_date"])
    except Exception:
        pass

    from src.utils.title_matching import normalize_title as _nt

    artist_key = _nt(app.current_artist.name)
    by_genius_id = {int(t.genius_id): t for t in app.current_artist.tracks if t.genius_id}
    known_ids = set(by_genius_id)
    try:
        deleted_ids = app.deleted_tracks_manager.load_deleted_ids(app.current_artist.name)
    except Exception:
        deleted_ids = set()

    created, skipped_known, skipped_deleted, renumbered = [], 0, 0, 0
    for tr in data["tracks"]:
        gid = int(tr["genius_id"])
        if gid in known_ids:
            skipped_known += 1
            # Le morceau existe déjà, mais Genius nous donne son RANG dans
            # l'album — la seule source du `track_number`. Sans ce backfill il
            # était jeté, et tout ce qui trie une tracklist
            # retombait sur l'ordre alphabétique.
            number = tr.get("track_number")
            existing = by_genius_id[gid]
            existing.release_observations.append(
                ReleaseObservation(
                    title=album_name,
                    source="genius",
                    external_release_id=data["album"].get("id"),
                    external_track_id=gid,
                    release_date=release_date,
                    scope="own",
                    confidence="identified" if data["album"].get("id") else "suggested",
                )
            )
            if number and existing.track_number != number:
                existing.track_number = number
                renumbered += 1
            try:
                app.data_manager.save_track(existing)
            except Exception as e:
                logger.error(f"N° de piste/parution '{existing.title}' échoué: {e}")
            continue
        if gid in deleted_ids:
            skipped_deleted += 1
            continue
        track = Track(title=tr["title"])
        track.artist = app.current_artist
        track.genius_id = gid
        track.genius_url = tr.get("url")
        track.album = album_name
        track.track_number = tr.get("track_number")
        track.release_date = release_date
        track.release_observations.append(
            ReleaseObservation(
                title=album_name,
                source="genius",
                external_release_id=data["album"].get("id"),
                external_track_id=gid,
                release_date=release_date,
                scope="own",
                confidence="identified" if data["album"].get("id") else "suggested",
            )
        )
        if release_date:
            # La date est un champ ARBITRÉ depuis le lot B : une écriture qui
            # n'émet pas d'observation ne vit que dans la colonne, et la
            # réconciliation la remplace à la relecture par ce que portent les
            # observations. C'est le défaut qui a annulé une durée saisie à la
            # main le 2026-09-08 — 230 posé, 249 relu. Inoffensif ici tant que
            # personne ne contredit Genius, mais on ne laisse pas une écriture
            # devenir une suggestion.
            track.observations.append(
                Observation(field="release_date", value=release_date, source="genius")
            )
        primary = tr.get("primary_artist") or ""
        if primary and _nt(primary) != artist_key:
            track.is_featuring = True
            track.primary_artist_name = primary
        else:
            track.is_featuring = False
        try:
            app.data_manager.save_track(track)
            app.current_artist.tracks.append(track)
            created.append(tr["title"])
            logger.info(f"➕ Importé depuis l'album « {album_name} » : {tr['title']}")
        except Exception as e:
            logger.error(f"Import '{tr['title']}' échoué: {e}")

    app._reload_tracks_and_refresh()
    messagebox.showinfo(
        "Import album",
        f"« {album_name} » — {len(data['tracks'])} morceaux sur Genius :\n\n"
        f"➕ {len(created)} ajoutés\n"
        f"⏭️ {skipped_known} déjà en base"
        + (f" (dont 🔢 {renumbered} numérotés)" if renumbered else "")
        + f"\n🗂️ {skipped_deleted} ignorés (supprimés par toi)\n\n"
        + (
            "Lance une MàJ Discographie (case media) puis l'enrichissement\n"
            "pour compléter les nouveaux morceaux."
            if created
            else ""
        ),
    )


def _apply_album_numbers(app, album: str, tracks) -> tuple[int, list[str]]:
    """Numérote les morceaux d'UN album depuis Genius. Renvoie (posés, manquants).

    Aucune URL à saisir : l'album se déduit du `genius_id` d'un morceau déjà en
    base. Passe par l'API AUTHENTIFIÉE (`api.genius.com`) et non par les endpoints
    web `genius.com/api/…`, en 403 Cloudflare depuis 2026-08.

    Cœur partagé par l'action d'un album et par le lot sur tout l'artiste — pas
    de dialogue ici, l'appelant s'en charge (le lot tourne hors du thread Tk).
    """
    seeds = [t for t in tracks if t.genius_id]
    if not seeds:
        return 0, [t.title for t in tracks]

    numbers = None
    for seed in seeds[:3]:  # un morceau peut être rattaché à un autre album
        numbers = app.genius_api.get_album_track_numbers(int(seed.genius_id))
        if numbers:
            break
    if not numbers:
        return 0, [t.title for t in tracks]

    updated, missing = 0, []
    for track in tracks:
        number = numbers.get(int(track.genius_id)) if track.genius_id else None
        if not number:
            missing.append(track.title)
            continue
        if track.track_number != number:
            track.track_number = number
            try:
                app.data_manager.save_track(track)
                updated += 1
            except Exception as e:
                logger.error(f"N° de piste '{track.title}' échoué: {e}")
    return updated, missing


def number_album_tracks(app, album: str, tracks):
    """Numérote un seul album (2 appels API : rapide, donc en ligne)."""
    updated, missing = _apply_album_numbers(app, album, tracks)
    app._reload_tracks_and_refresh()
    messagebox.showinfo(
        "Numéroter les pistes",
        f"« {album} » :\n\n🔢 {updated} morceau(x) numéroté(s)\n"
        + (
            f"⚠️ {len(missing)} absent(s) de la tracklist Genius :\n"
            + "\n".join(f"  • {t}" for t in missing[:8])
            if missing
            else "✅ tous les morceaux ont leur numéro"
        ),
    )


def number_all_albums(app):
    """Numérote TOUS les albums de l'artiste courant, en une passe.

    Deux appels API par album : la boucle est trop longue pour le thread Tk, elle
    part donc en worker (réseau + DB purement sync → `start_worker`).
    """
    from src.concurrency.lifecycle import start_worker, stop_requested
    from src.gui.dialogs import report

    if not app.current_artist or not app.current_artist.tracks:
        messagebox.showinfo("Numéroter les pistes", "Charge un artiste d'abord.")
        return

    # Snapshot SUR LE THREAD TK. Les lignes synthétiques (Featurings, Singles)
    # ne sont pas des albums : rien à numéroter.
    groups: dict[str, list] = {}
    for track in app.current_artist.tracks:
        album = (track.album or "").strip()
        if album:
            groups.setdefault(album, []).append(track)
    if not groups:
        messagebox.showinfo("Numéroter les pistes", "Aucun album à numéroter.")
        return

    artist_name = app.current_artist.name

    def worker():
        lines, total = [], 0
        for album, tracks in sorted(groups.items()):
            if stop_requested():
                lines.append("⏹ Interrompu.")
                break
            updated, missing = _apply_album_numbers(app, album, tracks)
            total += updated
            flag = "🔢" if updated else ("⚠️" if missing else "✅")
            lines.append(f"{flag} {album} — {updated} numéroté(s), {len(missing)} sans numéro")
            for title in missing[:5]:
                lines.append(f"      • {title}")
        summary = f"{artist_name} — {total} morceau(x) numéroté(s) sur {len(groups)} album(s)\n\n"
        app.root.after(0, lambda: _on_numbering_done(app, summary + "\n".join(lines), report))

    start_worker(worker, name="albums:number_all")


def _on_numbering_done(app, text: str, report):
    app._reload_tracks_and_refresh()
    report.show_scrollable_report(app, "Numérotation des pistes", text)


def on_album_right_click(app, event):
    """Menu contextuel de la vue Albums : détacher des morceaux de l'album."""
    item = app.tree.identify_row(event.y)
    rows = getattr(app, "_album_rows", {})
    if not item or item not in rows:
        # Clic dans le vide : import d'album par URL
        context_menu = tkinter.Menu(app.root, tearoff=0)
        context_menu.add_command(
            label="🔢 Numéroter les pistes de TOUS les albums (Genius)",
            command=lambda: number_all_albums(app),
        )
        context_menu.add_separator()
        context_menu.add_command(
            label="➕ Importer un album Genius (URL…)", command=lambda: import_genius_album(app)
        )
        try:
            context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            context_menu.grab_release()
        return
    album, tracks = rows[item]
    context_menu = tkinter.Menu(app.root, tearoff=0)

    if album.startswith("—") or album.startswith("🎤"):
        # Lignes synthétiques : proposer de RÉTABLIR les albums classés
        # visuellement vers cette ligne
        target = "feat" if album.startswith("🎤") else "single"
        prefs = load_album_view_prefs(app)
        entries = [(k, v.get("label", k)) for k, v in prefs.items() if v.get("target") == target]
        if not entries:
            return
        restore_menu = tkinter.Menu(context_menu, tearoff=0)
        for key, label in sorted(entries, key=lambda e: e[1].lower()):
            restore_menu.add_command(
                label=label[:60], command=lambda k=key: unset_album_view_pref(app, k)
            )
        context_menu.add_cascade(label="👁 Rétablir la ligne d'album", menu=restore_menu)
    else:
        key = helpers.normalize_album_title(album)
        context_menu.add_command(
            label="🔢 Numéroter les pistes (Genius)",
            command=lambda a=album, t=tracks: number_album_tracks(app, a, t),
        )
        context_menu.add_separator()
        # Nature du disque, à la main : ce que Deezer n'a pas (ou a mal) vu.
        type_menu = tkinter.Menu(context_menu, tearoff=0)
        for rt in RECORD_TYPES:
            type_menu.add_command(
                label=LIBELLES[rt],
                command=lambda a=album, r=rt: set_album_record_type(app, a, r),
            )
        type_menu.add_separator()
        type_menu.add_command(
            label="Effacer la saisie (retour à Deezer)",
            command=lambda a=album: set_album_record_type(app, a, None),
        )
        context_menu.add_cascade(label="💿 Type (Album / EP / Single…)", menu=type_menu)
        context_menu.add_separator()
        # Classement VISUEL (réversible, base intacte) — pour les albums
        # hôtes (feats) ou compilations qu'on ne veut pas voir en ligne
        context_menu.add_command(
            label="👁 Classer dans Featurings (visuel, réversible)",
            command=lambda: set_album_view_pref(app, key, album, "feat"),
        )
        context_menu.add_command(
            label="👁 Classer dans Singles (visuel, réversible)",
            command=lambda: set_album_view_pref(app, key, album, "single"),
        )
        context_menu.add_separator()
        # Détachement DÉFINITIF (modifie la base : album retiré du morceau)
        detach_menu = tkinter.Menu(context_menu, tearoff=0)
        detach_menu.add_command(
            label=f"Tous les morceaux ({len(tracks)})",
            command=lambda: detach_tracks_from_album(app, list(tracks), album),
        )
        detach_menu.add_separator()
        for t in sorted(tracks, key=lambda x: x.title.lower())[:30]:
            detach_menu.add_command(
                label=t.title[:60], command=lambda tr=t: detach_tracks_from_album(app, [tr], album)
            )
        context_menu.add_cascade(
            label="🧹 Retirer de l'album (⚠️ modifie la base)", menu=detach_menu
        )

    context_menu.add_separator()
    context_menu.add_command(
        label="➕ Importer un album Genius (URL…)", command=lambda: import_genius_album(app)
    )

    try:
        context_menu.tk_popup(event.x_root, event.y_root)
    finally:
        context_menu.grab_release()


def detach_tracks_from_album(app, tracks_to_detach, album):
    """Détache des morceaux de leur album (édition manuelle persistante).

    Le morceau rejoint la ligne « — Singles » (solo) ou « 🎤 Featurings »
    (feat) ; `album_override=1` empêche l'API Genius de re-remplir l'album
    au prochain prefill.
    """
    if not messagebox.askyesno(
        "Retirer de l'album",
        f"Retirer {len(tracks_to_detach)} morceau(x) de « {album} » ?\n\n"
        "Ils rejoindront la ligne Singles (solo) ou Featurings (feat).\n"
        "L'API ne re-remplira pas l'album (édition manuelle).",
    ):
        return
    moved = 0
    for t in tracks_to_detach:
        if t.id and app.data_manager.clear_track_album(t.id):
            t.album = None
            t.album_override = 1
            moved += 1
            logger.info(f"🧹 '{t.title}' détaché de l'album « {album} »")
    if moved:
        populate_albums_table(app)
