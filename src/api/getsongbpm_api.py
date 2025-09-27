"""
GetSongBPM API - BPM Fetcher for Artist Discography
Free with attribution requirement (backlink to GetSongBPM.com)
Returns BPM, key, time signature, and genre data
"""

import requests
import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass
from urllib.parse import quote


@dataclass
class SongData:
    """Song metadata structure for GetSongBPM"""
    artist: str
    title: str
    bpm: Optional[float] = None
    key: Optional[str] = None
    time_signature: Optional[str] = None
    genre: Optional[str] = None
    error: Optional[str] = None


class GetSongBPMFetcher:
    """GetSongBPM API client for fetching BPM and music metadata"""
    
    # GetSongBPM API endpoints
    BASE_URL = "https://api.getsongbpm.com/v1"
    SEARCH_ENDPOINT = f"{BASE_URL}/search"
    TEMPO_ENDPOINT = f"{BASE_URL}/tempo"
    
    # Rate limiting
    RATE_LIMIT_DELAY = 2.0  # Respectful delay between requests
    MAX_RETRIES = 3
    
    def __init__(self, api_key: Optional[str] = None, cache_file: str = "getsongbpm_cache.json"):
        """
        Initialize GetSongBPM fetcher
        
        Args:
            api_key: API key if required (some endpoints may need it)
            cache_file: Path to cache file for storing results
        """
        self.api_key = api_key
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.session = requests.Session()
        
        # Set headers if API key provided
        if self.api_key:
            self.session.headers.update({
                'X-API-Key': self.api_key,
                'Accept': 'application/json'
            })
        else:
            self.session.headers.update({
                'Accept': 'application/json',
                'User-Agent': 'Mozilla/5.0 (compatible; BPM-Fetcher/1.0)'
            })
    
    def _load_cache(self) -> Dict:
        """Load cached results from file"""
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
    
    def _save_cache(self):
        """Save cache to file"""
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, indent=2, ensure_ascii=False)
    
    def _get_cache_key(self, artist: str, title: str) -> str:
        """Generate cache key for a track"""
        return f"{artist.lower().strip()}::{title.lower().strip()}"
    
    def _search_track(self, artist: str, title: str) -> Optional[Dict]:
        """
        Search for a track using artist and title
        
        Args:
            artist: Artist name
            title: Track title
            
        Returns:
            Track data dictionary or None if not found
        """
        # Prepare search query
        query = f"{artist} {title}"
        params = {
            'query': query,
            'type': 'both'  # Search both artist and song
        }
        
        # Alternative endpoint format (adjust based on actual API)
        search_url = f"https://api.getsongbpm.com/search"
        
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.session.get(
                    search_url,
                    params=params,
                    timeout=10
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Handle different response formats
                    if isinstance(data, list) and len(data) > 0:
                        # Return first match
                        return data[0]
                    elif isinstance(data, dict):
                        # Check if it has results key
                        if 'results' in data and len(data['results']) > 0:
                            return data['results'][0]
                        elif 'tracks' in data and len(data['tracks']) > 0:
                            return data['tracks'][0]
                        elif 'tempo' in data:
                            # Direct result
                            return data
                    
                    return None
                
                elif response.status_code == 429:
                    # Rate limited - wait longer
                    print(f"    ⚠ Rate limited, waiting {10 * (attempt + 1)}s...")
                    time.sleep(10 * (attempt + 1))
                    continue
                
                elif response.status_code == 404:
                    return None
                
                else:
                    print(f"    ⚠ API returned status {response.status_code}")
                    return None
                    
            except requests.exceptions.RequestException as e:
                print(f"    ⚠ Request error (attempt {attempt + 1}/{self.MAX_RETRIES}): {str(e)}")
                if attempt < self.MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)  # Exponential backoff
                continue
        
        return None
    
    def fetch_track_bpm(self, artist: str, title: str) -> SongData:
        """
        Fetch BPM and metadata for a single track
        
        Args:
            artist: Artist name
            title: Track title
            
        Returns:
            SongData object with BPM and other metadata
        """
        # Check cache first
        cache_key = self._get_cache_key(artist, title)
        if cache_key in self.cache:
            print(f"  ✓ Found in cache: {artist} - {title}")
            cached = self.cache[cache_key]
            return SongData(**cached)
        
        # Search for track
        track_data = self._search_track(artist, title)
        
        if track_data:
            # Extract metadata based on response structure
            song = SongData(
                artist=artist,
                title=title,
                bpm=track_data.get('tempo') or track_data.get('bpm'),
                key=track_data.get('key') or track_data.get('musical_key'),
                time_signature=track_data.get('time_signature'),
                genre=track_data.get('genre')
            )
            
            print(f"  ✓ Found: {artist} - {title} | BPM: {song.bpm} | Key: {song.key}")
            
        else:
            # Track not found
            song = SongData(
                artist=artist,
                title=title,
                error="Track not found in GetSongBPM database"
            )
            print(f"  ✗ Not found: {artist} - {title}")
        
        # Cache the result
        self.cache[cache_key] = song.__dict__
        self._save_cache()
        
        return song
    
    def fetch_artist_discography(self, artist: str, track_list: List[str]) -> List[SongData]:
        """
        Fetch BPM for entire artist discography
        
        Args:
            artist: Artist name
            track_list: List of track titles
            
        Returns:
            List of SongData objects
        """
        print(f"\n{'='*60}")
        print(f"GetSongBPM: Fetching BPM for {artist}")
        print(f"Processing {len(track_list)} tracks...")
        print(f"⚠ Note: Attribution required (backlink to GetSongBPM.com)")
        print(f"{'='*60}\n")
        
        results = []
        
        for i, title in enumerate(track_list, 1):
            print(f"[{i}/{len(track_list)}] Processing: {title}")
            
            song_data = self.fetch_track_bpm(artist, title)
            results.append(song_data)
            
            # Rate limiting
            if i < len(track_list):
                time.sleep(self.RATE_LIMIT_DELAY)
        
        # Summary
        successful = sum(1 for r in results if r.bpm is not None)
        print(f"\n{'='*60}")
        print(f"✅ Completed: {successful}/{len(track_list)} tracks with BPM")
        print(f"⚠ Remember to add backlink to GetSongBPM.com")
        print(f"{'='*60}\n")
        
        return results
    
    def export_to_csv(self, results: List[SongData], output_file: str = "getsongbpm_results.csv"):
        """
        Export results to CSV file
        
        Args:
            results: List of SongData objects
            output_file: Output CSV filename
        """
        import csv
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['artist', 'title', 'bpm', 'key', 'time_signature', 'genre', 'error']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            writer.writeheader()
            for song in results:
                writer.writerow({
                    'artist': song.artist,
                    'title': song.title,
                    'bpm': song.bpm,
                    'key': song.key,
                    'time_signature': song.time_signature,
                    'genre': song.genre,
                    'error': song.error
                })
        
        print(f"Results exported to {output_file}")
    
    def get_attribution_html(self) -> str:
        """
        Get the required attribution HTML for GetSongBPM
        
        Returns:
            HTML string for attribution
        """
        return """
        <!-- GetSongBPM Attribution (Required for free usage) -->
        <a href="https://getsongbpm.com" target="_blank" rel="nofollow">
            BPM data powered by GetSongBPM.com
        </a>
        """


# Example usage
if __name__ == "__main__":
    # Initialize fetcher (API key optional for basic usage)
    fetcher = GetSongBPMFetcher()
    
    # Example: Django's discography (partial list)
    artist = "Django"
    tracks = [
        "Juin",
        "Fichu",
        "Fusil",
        "Saturne",
        "Dans le noir",
        "Nuit",
        "Libre",
        "Belvédère",
        "Flocons",
        "Automne"
    ]
    
    # Fetch BPM for all tracks
    results = fetcher.fetch_artist_discography(artist, tracks)
    
    # Export to CSV
    fetcher.export_to_csv(results)
    
    # Display results
    print("\nFinal Results:")
    for song in results:
        if song.bpm:
            print(f"  • {song.artist} - {song.title}: {song.bpm} BPM, Key: {song.key}")
        else:
            print(f"  • {song.artist} - {song.title}: {song.error}")
    
    # Show required attribution
    print("\n" + "="*60)
    print("IMPORTANT: Add this attribution to your website/app:")
    print(fetcher.get_attribution_html())