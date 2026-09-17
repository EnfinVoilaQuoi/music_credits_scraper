# théRAPie - Music_Credit_Scraper
## Contruis une Base de Données Complète pour un artiste donné

## Fonctionnement
- Récupère la **Discographie** complète (en tant qu'artiste principal et secondaire)
- Récupère **Credits** Complets (Producteur, Invité, Voix et Instrument Additionnel, Sample et Interpolation, Album associé, Numéro de Piste), **Paroles structurées**, **Paroles synchronisées** (timestamps LRC), **Anectodes** et **Date de Sortie**
- Associe automatiquement les **Certifications RIAA** (US), **SNEP** (FR), **BRMA** (BE) et **BPI** (UK)
- Récupère **Données Techniques** (**BPM**, **Durée**, **Key** et **Mode**) via différentes sources, avec système de vérification croisée et **arbitrage par observations** (une valeur par champ, sa source et sa fraîcheur)
- Récupère **Nombre de Streams** (**Spotify** et **YouTubeMusic**), avec plusieurs vidéos YouTube par morceau et plusieurs éditions Spotify
- Détecte et propose les **Groupes & Collectifs** d'un artiste (MusicBrainz + confirmation Discogs), à valider manuellement
- Génère des **visuels de discographie** (réseau des producteurs, structure des morceaux, frise chronologique des streams) exportables vers Illustrator
- **Exporte** en plusieurs formats selon besoin (**.csv**, **SQL**)

## Fonctionnalités
- **Gestion** de **plusieurs Base de Données** (une par artiste)
- **Pilotage sans GUI** via `python -m src.cli` (mêmes flux que l'interface, un flag par case à cocher)
- **Fenetre principale** :
  - Vue "**Morceaux**" : Tableau rassemblant tous les morceaux et leurs informations (Artiste Principal, Date de Sortie, Album, Credits, Paroles, BPM, Durée, Certifications, Nb de Streams et Statut, avec **systeme de tri par colonne**)
  - Vue "**Album**" : Tableau rassemblant tous les albums et leurs informations (Type - Album/EP/Single, Durée, Certifications, Nb de Streams)
- **Fenetres secondaires** :
  - Page de Detail pour chaque morceau (Crédits, Paroles, Données Techniques, Certifications, Vidéos YouTube)
  - Page de Gestion des MaJ des Certifications (Sources, Info MaJ, MaJ manuelle, rescrape des périodes manquantes)
  - Page de Gestion du Scrape Credit/Paroles (Sources, Mode Réécriture)
  - Page de Gestion de l'Enrichissement (Sources, Mode Réécriture)
  - Page de Gestion du Scrape Nb de Streams (Sources)
  - Page "État sources" : santé des sondes ET usage réel par source/artiste, côte à côte
  - Page "Groupes" : formations proposées/confirmées/refusées d'un artiste
  - Page "Export studio" : Création de Visuel pour Illustrator à partir des données en DB
- **Gestion** de **morceau "exclu"** (freestyle, émission)
- Récupération automatique de **liens YouTube** pour chaque morceaux (clip + version "Topic"), avec **système de fiabilité**, infobulle preview titre et note de fiabilité
- Récupération automatique de **liens Spotify** pour chaque morceaux, infobulle preview titre, avec **vérification d'identité** (bon artiste / bonne durée, pas seulement un ID unique)
- **Base de Données** des **Certifications** Mise à Jour Automatique et Manuelle

## APIs utilisées
- [GetSongBPM](https://getsongbpm.com/)                             - Données **BPM**, **Key**, **Mode** et **Time Signature**
- [Genius](https://docs.genius.com/)                                - Liste de **Morceaux** d'un Artiste
- [ReccoBeats](https://reccobeats.com/)                             - Données **Durée**, **BPM**, **Key** & **Mode**
- [YouTubeMusic](https://ytmusicapi.readthedocs.io/en/stable/#)     - Lien **YouTube** pour chaque Morceau et **Nb de Streams**
- [Deezer](https://developers.deezer.com/api)                       - Données **Durée**, **ISRC**, Explicit Lyrics, **Picture**, **Date de Sortie**, **Type d'album** (Album/EP/Single)
- [LRCLIB](https://lrclib.net/)                                     - **Paroles synchronisées** (timestamps LRC)
- [MusicBrainz](https://musicbrainz.org/)                           - **Groupes & Alias** d'un artiste

## Données utilisées
- [Genius](https://genius.com/)                                     - **Crédits** Complets, **Paroles** Structurées, **Date de Sortie**
- [SongBPM](https://songbpm.com/)                                   - Données **Durée**, **BPM**, **Key**, **Mode** et **Time Signature**
- [Discogs](https://www.discogs.com/)                               - **Crédits** additionnels (gravure, pochette, management, stylisme...), confirmation des **Groupes**
- [Backpackerz](https://www.thebackpackerz.com/)                    - **Photographies**
- [SNEP](https://snepmusique.com/)                                  - **Certifications SNEP** (France)
- [RIAA](https://www.riaa.com/gold-platinum/)                       - **Certifications RIAA** (USA, dont le programme latin)
- [Ultratop](https://www.ultratop.be)                               - **Certifications BRMA** (Belgique)
- [BPI](https://www.bpi.co.uk/)                                     - **Certifications BPI** (Royaume-Uni)
- [AlbumOfTheYear](https://www.albumoftheyear.org/)                 - **Notation** d'Album
- [Spotify](https://open.spotify.com/intl-fr)                       - **Track ID** pour Reccobeats, **Nb de Streams** et **Auditeurs mensuels**
- [Kworb](https://kworb.net/)                                       - **Nb de Streams** sur Spotify (quotidien, ventilation lead/solo/feature)

## Ressources utilisées
- [Claude - **Sonnet** (3.5, 4.0 et 4.5) et **Opus** (4.0 et 4.1) et **Fable** (depuis 5.0)](https://claude.ai/)              - Modèle de Langage développé par Anthropic
- [Ollama - **llama3.2 3B**](https://ollama.com/)                   - Extraction LLM locale (repli sur données textuelles ambiguës ou parseurs cassés)
- [**H3nrycrosby** - RIAA Scraping Project](https://github.com/H3nrycrosby/riaa_scraping_project/)  - Database **Certification RIAA** (USA)
