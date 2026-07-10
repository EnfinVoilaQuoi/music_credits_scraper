"""Dialogue de désambiguïsation d'artiste Genius (choix parmi candidats, slug manuel)"""
from tkinter import messagebox
from typing import Optional

from src.models import Artist
from src.scrapers.genius_scraper_v2 import GeniusScraper
from src.utils.logger import get_logger

logger = get_logger(__name__)


def fetch_artist_from_genius_url(app, url: str, fallback_name: str) -> "Optional[Artist]":
    """Charge la page Genius d'un artiste via Playwright et extrait l'ID depuis le meta tag.

    Utilise :
        JSON.parse(document.querySelector('meta[itemprop="page_data"]').content).artist.id
    """
    scraper = None
    try:
        scraper = GeniusScraper(headless=True)
        scraper.page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        result = scraper.page.evaluate("""() => {
            const meta = document.querySelector('meta[itemprop="page_data"]');
            if (!meta) return null;
            try {
                const data = JSON.parse(meta.content);
                if (!data.artist || !data.artist.id) return null;
                return {id: data.artist.id, name: data.artist.name};
            } catch(e) {
                return null;
            }
        }""")
        if result and result.get('id'):
            return Artist(
                name=result.get('name') or fallback_name,
                genius_id=result['id']
            )
    except Exception as e:
        logger.debug(f"Fetch artiste depuis {url} échoué: {e}")
    finally:
        if scraper:
            try:
                scraper.close()
            except Exception:
                pass
    return None

def show_artist_selection_dialog(app, candidates, artist_name: str, result_queue):
    """Dialog modal pour choisir parmi plusieurs artistes candidats Genius.

    Si l'artiste n'apparaît pas dans la liste, l'utilisateur peut entrer
    son ID Genius manuellement (visible sur genius.com/artists/NomArtiste).
    """
    import customtkinter as ctk

    dialog = ctk.CTkToplevel(app.root)
    dialog.title("Choisir un artiste")
    dialog.resizable(False, False)
    dialog.transient(app.root)
    dialog.grab_set()
    dialog.lift()
    dialog.focus_force()

    def _put(value):
        result_queue.put(value)
        dialog.destroy()

    def _confirm_manual_id():
        raw = id_entry.get().strip()
        # Accepte un ID numérique ou une URL genius.com/artists/NomArtiste
        genius_id = None
        if raw.isdigit():
            genius_id = int(raw)
        elif "genius.com/artists/" in raw:
            # Extraire le slug puis résoudre via l'API
            slug = raw.split("genius.com/artists/")[-1].split("/")[0].split("?")[0]
            resolve_genius_slug(app, slug, artist_name, result_queue, dialog)
            return
        if genius_id:
            _put(Artist(name=artist_name, genius_id=genius_id))
        else:
            id_entry.configure(border_color="red")

    # ── Titre ────────────────────────────────────────────────────────────
    if candidates:
        label_text = f"Plusieurs résultats pour « {artist_name} ».\nChoisissez l'artiste :"
    else:
        label_text = f"Aucun résultat automatique pour « {artist_name} »."
    ctk.CTkLabel(
        dialog,
        text=label_text,
        font=("Arial", 13),
        justify="left"
    ).pack(padx=20, pady=(16, 8), anchor="w")

    # ── Boutons candidats ─────────────────────────────────────────────────
    for artist in candidates:
        ctk.CTkButton(
            dialog,
            text=f"{artist.name}   (ID Genius : {artist.genius_id})",
            anchor="w",
            command=lambda a=artist: _put(a)
        ).pack(padx=20, pady=3, fill="x")

    # ── Saisie manuelle ───────────────────────────────────────────────────
    ctk.CTkLabel(
        dialog,
        text="Artiste absent ? Entrez l'ID Genius ou l'URL genius.com/artists/… :",
        font=("Arial", 11),
        text_color="gray"
    ).pack(padx=20, pady=(14, 2), anchor="w")

    id_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    id_frame.pack(padx=20, pady=(0, 4), fill="x")

    id_entry = ctk.CTkEntry(id_frame, placeholder_text="Ex : 123456  ou  genius.com/artists/Isha")
    id_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
    id_entry.bind("<Return>", lambda e: _confirm_manual_id())

    ctk.CTkButton(id_frame, text="OK", width=50, command=_confirm_manual_id).pack(side="left")

    # ── Annuler ───────────────────────────────────────────────────────────
    ctk.CTkButton(
        dialog,
        text="Annuler",
        fg_color="gray",
        command=lambda: _put(None)
    ).pack(padx=20, pady=(8, 16))

    height = 130 + len(candidates) * 46 + 90
    dialog.geometry(f"460x{height}")

    dialog.protocol("WM_DELETE_WINDOW", lambda: _put(None))
    dialog.wait_window()

def resolve_genius_slug(app, slug: str, artist_name: str, result_queue, parent_dialog):
    """Charge genius.com/artists/{slug} via Playwright et extrait l'ID artiste."""
    import threading

    def fetch():
        url = f"https://genius.com/artists/{slug}"
        artist = fetch_artist_from_genius_url(app, url, artist_name)
        if artist and artist.genius_id:
            result_queue.put(artist)
            app.root.after(0, parent_dialog.destroy)
        else:
            app.root.after(0, lambda: messagebox.showwarning(
                "Introuvable",
                f"Aucun artiste trouvé sur genius.com/artists/{slug}.\n"
                "Vérifiez l'orthographe ou entrez l'ID numérique directement."
            ))

    threading.Thread(target=fetch, daemon=True).start()
