"""Dialogues de saisie manuelle par morceau (BPM/key, lien YouTube, renommage, BPM Finder local)"""

from tkinter import filedialog, messagebox

import customtkinter as ctk

from src.gui import helpers
from src.gui.workers.lifecycle import start_worker
from src.utils.logger import get_logger
from src.utils.youtube_integration import youtube_integration

logger = get_logger(__name__)


def manual_audio_entry(app, index: int):
    """Saisie manuelle BPM / Tonalité / Durée (source 'manual') — UN dialogue.

    Pour les morceaux hors de portée des sources auto : valeurs relevées
    sur Sonoteller/BPM Finder, ou analyse d'un fichier local.
    Tonalité EN ou FR ("G# minor", "Sol# mineur"…). Durée "3:24" ou secondes.
    """
    try:
        track = app.current_artist.tracks[index]
    except (IndexError, TypeError):
        return

    _dur = track.duration
    _dur_init = f"{_dur // 60}:{_dur % 60:02d}" if isinstance(_dur, int) and _dur else ""

    # Dialogue unique à 3 champs
    dlg = ctk.CTkToplevel(app.root)
    dlg.title("Saisie manuelle — BPM / Tonalité / Durée")
    dlg.geometry("460x420")
    dlg.minsize(460, 420)
    dlg.transient(app.root)
    dlg.grab_set()

    ctk.CTkLabel(dlg, text=f"« {track.title} »", font=("Arial", 13, "bold")).pack(pady=(15, 10))

    fields = ctk.CTkFrame(dlg, fg_color="transparent")
    fields.pack(fill="x", padx=25)

    def _row(label, value, hint):
        ctk.CTkLabel(fields, text=label, anchor="w").pack(fill="x", pady=(8, 0))
        e = ctk.CTkEntry(fields, placeholder_text=hint)
        if value:
            e.insert(0, value)
        e.pack(fill="x")
        return e

    bpm_entry = _row("BPM", str(track.bpm) if track.bpm else "", "ex. 95")
    key_entry = _row("Tonalité", track.musical_key or "", "ex. G# minor / Sol# mineur")
    dur_entry = _row("Durée", _dur_init, "ex. 3:24 ou 204")

    _result = {"ok": False}

    def _submit():
        _result["ok"] = True
        _result["bpm"] = bpm_entry.get()
        _result["key"] = key_entry.get()
        _result["dur"] = dur_entry.get()
        dlg.destroy()

    btns = ctk.CTkFrame(dlg, fg_color="transparent")
    btns.pack(pady=15)
    ctk.CTkButton(btns, text="Enregistrer", command=_submit).pack(side="left", padx=5)
    ctk.CTkButton(btns, text="Annuler", fg_color="gray", command=dlg.destroy).pack(
        side="left", padx=5
    )

    dlg.wait_window()
    if not _result["ok"]:
        return

    bpm_str, key_str, duration_str = _result["bpm"], _result["key"], _result["dur"]
    changed = []
    bpm_str = (bpm_str or "").strip()
    if bpm_str:
        try:
            track.bpm = int(round(float(bpm_str.replace(",", "."))))
            track.bpm_source = "manual"
            changed.append(f"BPM = {track.bpm}")
        except ValueError:
            messagebox.showerror("BPM manuel", f"BPM invalide : {bpm_str!r}")
            return

    key_str = (key_str or "").strip()
    if key_str:
        from src.utils.music_theory import (
            normalize_musical_key,
            note_to_pitch_class,
            parse_mode,
        )

        canonical = normalize_musical_key(key_str)
        if not canonical:
            messagebox.showerror(
                "Tonalité manuelle",
                f"Tonalité non comprise : {key_str!r}\n"
                'Format attendu : note + mode (ex. "G# minor", "Sol# mineur").',
            )
            return
        tokens = key_str.split()
        track.key = note_to_pitch_class(" ".join(tokens[:-1]))
        track.mode = parse_mode(tokens[-1])
        track.musical_key = canonical
        track.key_mode_source = "manual"
        changed.append(f"Tonalité = {canonical}")

    duration_str = (duration_str or "").strip()
    if duration_str:
        seconds = None
        if ":" in duration_str:
            parts = duration_str.split(":")
            try:
                if len(parts) == 2:
                    seconds = int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except ValueError:
                seconds = None
        else:
            try:
                seconds = int(round(float(duration_str.replace(",", "."))))
            except ValueError:
                seconds = None
        if seconds is None or seconds <= 0:
            messagebox.showerror(
                "Durée manuelle",
                f"Durée invalide : {duration_str!r}\n"
                'Format attendu : "3:24" ou secondes ("204").',
            )
            return
        track.duration = seconds
        changed.append(f"Durée = {seconds // 60}:{seconds % 60:02d}")

    if not changed:
        return
    try:
        app.data_manager.save_track(track)
        logger.info(f"✏️ Saisie manuelle '{track.title}': {', '.join(changed)}")
        app._populate_tracks_table()
    except Exception as e:
        logger.error(f"Saisie manuelle échouée: {e}")
        messagebox.showerror("Saisie manuelle", f"Sauvegarde échouée : {e}")


def manual_youtube_link(app, index: int):
    """Définit/valide manuellement le lien YouTube d'un morceau.

    Propose le lien en base (Genius/persisté) ou le meilleur résultat de
    recherche comme valeur pré-remplie ; l'utilisateur colle/valide. Stocké
    en source 'manual' (priorité maximale : ni la recherche ni Genius ne
    l'écrasent — choix explicite). Sert notamment aux feats dont la
    recherche auto reste sous le seuil de confiance (ex. Lundi de Pâques).
    """
    from tkinter import simpledialog

    try:
        track = app.current_artist.tracks[index]
    except (IndexError, TypeError):
        return

    proposed = track.youtube_url
    if not proposed:
        # Proposer le meilleur résultat de recherche (même sous le seuil)
        try:
            artist_name = track.artist.name if track.artist else app.current_artist.name
            res = youtube_integration.get_youtube_link_for_track(
                (track.primary_artist_name if track.is_featuring else None) or artist_name,
                track.title,
                track.album,
                helpers.get_release_year_safely(track),
            )
            if res.get("type") == "direct" and res.get("url"):
                proposed = res["url"]
        except Exception:
            pass

    url = simpledialog.askstring(
        "Lien YouTube",
        f"« {track.title} »\n\nColle ou valide le lien YouTube du morceau\n"
        "(la valeur proposée vient de la recherche auto — vérifie qu'elle\n"
        "correspond bien AU MORCEAU avant de valider) :",
        initialvalue=proposed or "",
        parent=app.root,
    )
    if url is None:
        return
    url = url.strip()

    if not url:
        # Vider = repasser en recherche live (source effacée)
        track.youtube_url = None
        track.youtube_url_source = None
        app.data_manager.clear_track_youtube_link(track.id)
        logger.info(f"🔗 Lien YouTube retiré : '{track.title}'")
    else:
        if "youtube.com/watch" not in url and "youtu.be/" not in url:
            messagebox.showerror("Lien YouTube", f"Lien YouTube invalide : {url!r}")
            return
        track.youtube_url = url
        track.youtube_url_source = "manual"
        app.data_manager.update_track_youtube_url(track.id, url, "manual")
        logger.info(f"🔗 Lien YouTube validé (manuel) : '{track.title}' → {url}")
    app._populate_tracks_table()


def rename_track(app, index: int):
    """Renomme un morceau en base (ex. aligner « Matrix (Intro) » sur Kworb « Matrix »)."""
    from tkinter import simpledialog

    try:
        track = app.current_artist.tracks[index]
    except (IndexError, TypeError):
        return
    new_title = simpledialog.askstring(
        "Renommer le morceau",
        f"Nouveau titre pour « {track.title} » :",
        initialvalue=track.title,
        parent=app.root,
    )
    if not new_title or new_title.strip() == track.title:
        return
    if app.data_manager.rename_track(track.id, new_title.strip()):
        track.title = new_title.strip()
        logger.info(f"🏷️ Renommé : #{track.id} → '{track.title}'")
        app._populate_tracks_table()
    else:
        messagebox.showerror(
            "Renommer",
            "Échec : un morceau porte peut-être déjà ce titre (titre unique par artiste).",
        )


def bpmfinder_local_file(app, index: int):
    """Analyse un fichier audio LOCAL via BPM Finder → BPM/Key (source 'bpmfinder').

    Pour les morceaux absents de YouTube. Lance l'analyse dans un thread
    (le scraper est bloquant) et écrit les manques uniquement.
    """
    try:
        track = app.current_artist.tracks[index]
    except (IndexError, TypeError):
        return

    scraper = getattr(app.data_enricher, "bpmfinder_scraper", None)
    if not scraper:
        messagebox.showwarning("BPM Finder", "BPM Finder non configuré (BPMFINDER_EMAIL/PASSWORD).")
        return

    path = filedialog.askopenfilename(
        title=f"Fichier audio pour « {track.title} »",
        filetypes=[("Audio/Vidéo", "*.wav *.mp3 *.ogg *.flac *.m4a *.mp4 *.aac"), ("Tous", "*.*")],
    )
    if not path:
        return

    def run():
        try:
            app.root.after(
                0,
                lambda: (
                    app.progress_label.configure(text=f"BPM Finder (fichier) : {track.title}…")
                    if hasattr(app, "progress_label")
                    else None
                ),
            )
            res = scraper.analyze_file(path)
        except Exception as e:
            logger.error(f"BPM Finder fichier échec '{track.title}': {e}")
            res = None
        finally:
            # Fermer le navigateur DANS ce thread (évite l'EPIPE Playwright
            # à l'arrêt de l'app, cf. batch d'enrichissement).
            try:
                scraper.close()
            except Exception:
                pass

        def done():
            if not res:
                messagebox.showerror(
                    "BPM Finder", f"Analyse échouée pour « {track.title} » (voir logs)."
                )
                return
            applied = []
            if not track.bpm and res.get("bpm"):
                track.bpm = res["bpm"]
                track.bpm_source = "bpmfinder"
                applied.append(f"BPM={res['bpm']}")
            if (
                (getattr(track, "key", None) is None or getattr(track, "mode", None) is None)
                and res.get("key") is not None
                and res.get("mode") is not None
            ):
                from src.utils.music_theory import key_mode_to_french

                track.key = res["key"]
                track.mode = res["mode"]
                track.musical_key = key_mode_to_french(res["key"], res["mode"])
                track.key_mode_source = "bpmfinder"
                applied.append(f"Tonalité={track.musical_key}")
            if applied:
                app.data_manager.save_track(track)
                app._populate_tracks_table()
                messagebox.showinfo("BPM Finder", f"« {track.title} » : {', '.join(applied)}")
            else:
                messagebox.showinfo(
                    "BPM Finder", f"« {track.title} » : rien à compléter (déjà rempli)."
                )

        app.root.after(0, done)

    start_worker(run)
