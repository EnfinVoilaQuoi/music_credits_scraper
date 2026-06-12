# théRAPie - Music_Credit_Scraper
### Contruis une Base de Données pour un artiste donné.

## Fonctionnement
- Récupère la **Discographie** complète (en tant qu'artiste principal et secondaire)
- Récupère **Credits** Complets (Producteur, Invité, Voix et Instrument Additionnel, Sample et Interpolation, Album associé, Numéro de Piste), **Paroles structurés** , **Anectodes** et **Date de Sortie**
- Associe automatiquement les **Certifications RIAA**, **SNEP** et **BRMA**
- Récupère **Données Techniques** (**BPM**, **Durée**, **Key** et **Mode**) via différentes sources, avec système de vérification
- Récupère **Nombre de Streams** (**Spotify** et **YouTubeMusic**)
- **Exporte** en plusieurs formats selon besoin (**.csv**, **SQL**)

## Fonctionnalités
- **Gestion** de **plusieurs Base de Données** (une par artiste)
- **Fenetre principale** :
  - Vue "**Morceaux**" : Tableau rassemblant tous les morceaux et leurs informations (Artiste Principal, Date de Sortie, Album, Credits, Paroles, BPM, Durée, Certifications, Nb de Streams et Statut, avec **systeme de tri par colonne**)
  - Vue "**Album**" : Tableau rassemblant tous les albums et leurs informations (Durée, Certifications, Nb de Streams)
- **Fenetres secondaires** :
  - Page de Detail pour chaque morceau (Crédits, Paroles, Données Techniques, Certifications)
  - Page de Gestion des MaJ des Certifications (Sources, Info MaJ, MaJ manuelle)
  - Page de Gestion du Scrape Credit/Paroles (Sources, Mode Réécriture)
  - Page de Gestion de l'Enrichissement (Sources, Mode Réécriture)
  - Page de Gestion du Scrape Nb de Streams (Sources)
- **Gestion** de **morceau "exclu"** (freestyle, émission)
- Récupération automatique de **liens YouTube** pour chaque morceaux, avec **système de fiabilité**, infobulle preview titre et note de fiabilité
- Récupération automatique de **liens Spotify** pour chaque morceaux, infobulle preview titre
- **Base de Données** des **Certifications** Mise à Jour Automatique et Manuelle
- Récupération du nombre de streams sur Spotify via le Spotify ID sur Kworb.et via l'ID YouTube officiel pour l'API YouTubeMusic

## APIs utilisées
- [GetSongBPM](https://getsongbpm.com/)                             - Données **BPM**, **Key**, **Mode** et **Time Signature**
- [Genius](https://docs.genius.com/)                                - Liste de **Morceaux** d'un Artiste
- [ReccoBeats](https://reccobeats.com/)                             - Données **Durée**, **BPM**, **Key** & **Mode**
- [YouTubeMusic](https://ytmusicapi.readthedocs.io/en/stable/#)     - Lien **YouTube** pour chaque Morceau
- [Deezer](https://developers.deezer.com/api)                       - Données **Durée**, Explicit Lyrics, **Picture**, **Date de Sortie**

## Données utilisées
- [Genius](https://genius.com/)                                     - **Crédits** Complets, **Paroles** Structurées, **Date de Sortie**
- [SongBPM](https://songbpm.com/)                                   - Données **Durée**, **BPM**, **Key**, **Mode** et **Time Signature**
- [Rapedia](https://rapedia.fr/)                                    - Données **BPM**, **Structure** de Morceau
- [Backpackerz](https://www.thebackpackerz.com/)                    - **Photographies**
- [SNEP](https://snepmusique.com/)                                  - **Certifications SNEP** (France)
- [RIAA](https://www.riaa.com/gold-platinum/)                       - **Certifications RIAA** (USA)
- [Ultratop](https://www.ultratop.be)                               - **Certifications BRMA** (Belgique)
- [AlbumOfTheYear](https://www.albumoftheyear.org/)                 - **Notation** d'Album
- [Spotify](https://open.spotify.com/intl-fr)                       - **Track ID** pour Reccobeats
- [Kworb.net](https://kworb.net/)                                   - **Nb de Streams* sur Spotify

## Ressources utilisées
- [Claude - **Sonnet** (depuis 3.5), **Opus** (depuis 4.0) et Fable (depuis 5.0)
- ](https://claude.ai/)              - Modèle de Langage développé par Anthropic
- [**H3nrycrosby** - RIAA Scraping Project](https://github.com/H3nrycrosby/riaa_scraping_project/)  - Database **Certification RIAA** (USA)
