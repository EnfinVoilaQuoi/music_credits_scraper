# théRAPie - Music_Credit_Scraper
### Contruis une Base de Données pour un artiste donné.

## Fonctionnement
- Récupère la **Discographie** complète (en tant qu'artiste principal et secondaire)
- Récupère **Credits** Complets (Producteur, Invité, Voix et Instrument Additionnel, Sample et Interpolation, Album associé, Numéro de Piste), **Paroles structurés** , **Anectodes** et **Date de Sortie**
- Associe automatiquement les **Certifications RIAA**, **SNEP** et **BRMA**
- Récupère **Données Techniques** (**BPM**, **Durée**, **Key** et **Mode**) avec système de vérification
- **Exporte** en plusieurs formats selon besoin (**.csv**, **SQL**)

## Fonctionnalités
- **Gestion** de **plusieurs Base de Données** (une par artiste)
- **Fenetre principale** : Tableau rassemblant tous les morceaux et leurs informations (Artiste Principal, Date de Sortie, Album, Credits, Paroles, BPM, Durée, Certifications et Statut, avec **systeme de tri par colonne**)
- **Fenetres secondaires** : Page de Detail pour chaque morceau (Crédits, Paroles, Données Techniques, Certifications), Page de Gestion des MaJ des Certifications (Sources, Info MaJ, MaJ manuelle), Page de Gestion du Scrape Credit/Paroles (Sources, Mode Réécriture), Page de Gestion de l'Enrichissement (Sources, Mode Réécriture)
- **Gestion** de **morceau "exclu"** (freestyle, émission)
- Récupération automatique de **liens YouTube** pour chaque morceaux, avec **système de fiabilité**, infobulle preview titre et note de fiabilité
- Récupération automatique de **liens Spotify** pour chaque morceaux, infobulle preview titre
- **Base de Données** des **Certifications** Mise à Jour Automatiquement chaque début de mois (delai de MaJ de la SNEP) et Manuelle

## APIs utilisées
- [GetSongBPM](https://getsongbpm.com/)                             - ~~Données BPM~~ (non utilisé ATM)
- [Genius](https://docs.genius.com/)                                - Liste de **Morceaux** d'un Artiste
- [ReccoBeats](https://reccobeats.com/)                             - Données **Durée**, **BPM**, **Key** & **Mode**
- [YouTubeMusic](https://ytmusicapi.readthedocs.io/en/stable/#)     - Lien **YouTube** pour chaque Morceau
- [Deezer](https://developers.deezer.com/api)                       - Données **Durée**, Explicit Lyrics, **Picture**, **Date de Sortie**

## Données utilisées
- [Genius](https://genius.com/)                                     - **Crédits** Complets, **Paroles** Structurées, **Date de Sortie**
- [SongBPM](https://songbpm.com/)                                   - Données **Durée**, **BPM**, **Key** & **Mode**
- [Rapedia](https://rapedia.fr/)                                    - Données **BPM**, **Structure** de Morceau
- [Backpackerz](https://www.thebackpackerz.com/)                    - **Photographies**
- [SNEP](https://snepmusique.com/)                                  - **Certifications SNEP** (France)
- [RIAA](https://www.riaa.com/gold-platinum/)                       - **Certifications RIAA** (USA)
- [Ultratop](https://www.ultratop.be)                               - **Certifications BRMA** (Belgique)
- [AlbumOfTheYear](https://www.albumoftheyear.org/)                 - **Notation** d'Album
- [Spotify](https://open.spotify.com/intl-fr)                       - **Track ID** pour Reccobeats

## Ressources utilisées
- [Claude - **Sonnet** (3.5, 4.0 et 4.5) et **Opus** (4.0 et 4.1)](https://claude.ai/)              - Modèle de Langage développé par Anthropic
- [**H3nrycrosby** - RIAA Scraping Project](https://github.com/H3nrycrosby/riaa_scraping_project/)  - Database **Certification RIAA** (USA)
