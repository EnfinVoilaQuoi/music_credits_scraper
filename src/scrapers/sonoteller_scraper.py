"""
Sonoteller.ai - Advanced AI Music Analysis via YouTube Links
Fetches BPM, key, genres, moods, and detailed music analysis
Uses YouTube links as input (already collected in the process)
"""

import requests
import json
import time
import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException


@dataclass
class SonotellerAnalysis:
    """Complete music analysis from Sonoteller.ai"""
    artist: str
    title: str
    youtube_id: str
    
    # Music Analysis
    bpm: Optional[int] = None
    key: Optional[str] = None
    genres: Optional[List[Tuple[str, int]]] = None  # (genre, confidence%)
    subgenres: Optional[List[Tuple[str, int]]] = None
    moods: Optional[List[Tuple[str, int]]] = None
    instruments: Optional[List[str]] = None
    vocals_pitch: Optional[str] = None
    
    # Lyrics Analysis (if available)
    lyrics_summary: Optional[str] = None
    lyrics_moods: Optional[List[Tuple[str, int]]] = None
    lyrics_themes: Optional[List[Tuple[str, int]]] = None
    language: Optional[str] = None
    explicit: Optional[bool] = None
    
    # Metadata
    analysis_url: Optional[str] = None
    error: Optional[str] = None


class SonotellerFetcher:
    """Sonoteller.ai fetcher using YouTube links"""
    
    BASE_URL = "https://sonoteller.ai"
    ANALYSIS_WAIT_TIME = 30  # Maximum seconds to wait for analysis
    RATE_LIMIT_DELAY = 5.0  # Delay between requests
    
    def fetch_with_youtube_link(self, artist: str, title: str, youtube_url: str) -> SonotellerAnalysis:
        """
        Fetch analysis for a track using YouTube link
        
        Args:
            artist: Artist name
            title: Track title
            youtube_url: YouTube URL or video ID
            
        Returns:
            SonotellerAnalysis object with complete analysis
        """
        # Extract YouTube ID
        youtube_id = self._extract_youtube_id(youtube_url)
        if not youtube_id:
            return SonotellerAnalysis(
                artist=artist,
                title=title,
                youtube_id=youtube_url,
                error="Invalid YouTube URL or ID"
            )
        
        # Check cache
        cache_key = youtube_id
        if cache_key in self.cache:
            print(f"  ✓ Found in cache: {artist} - {title}")
            cached = self.cache[cache_key]
            cached['artist'] = artist
            cached['title'] = title
            return SonotellerAnalysis(**cached)
        
        print(f"  ⏳ Analyzing: {artist} - {title}")
        print(f"     YouTube ID: {youtube_id}")
        
        # Scrape analysis
        analysis_data = self._scrape_analysis(youtube_id)
        
        if analysis_data:
            # Create analysis object
            analysis = SonotellerAnalysis(
                artist=artist,
                title=title,
                youtube_id=youtube_id,
                bpm=analysis_data.get('bpm'),
                key=analysis_data.get('key'),
                genres=analysis_data.get('genres'),
                subgenres=analysis_data.get('subgenres'),
                moods=analysis_data.get('moods'),
                instruments=analysis_data.get('instruments'),
                vocals_pitch=analysis_data.get('vocals_pitch'),
                lyrics_summary=analysis_data.get('lyrics_summary'),
                language=analysis_data.get('language'),
                explicit=analysis_data.get('explicit'),
                analysis_url=analysis_data.get('analysis_url')
            )
            
            print(f"  ✓ Analysis complete: BPM={analysis.bpm}, Key={analysis.key}")
            
        else:
            analysis = SonotellerAnalysis(
                artist=artist,
                title=title,
                youtube_id=youtube_id,
                error="Failed to analyze track"
            )
            print(f"  ✗ Analysis failed for {artist} - {title}")
        
        # Cache the result
        self.cache[cache_key] = analysis.__dict__
        self._save_cache()
        
        return analysis
    
    def fetch_artist_discography(self, artist: str, track_youtube_pairs: List[Tuple[str, str]]) -> List[SonotellerAnalysis]:
        """
        Fetch analysis for entire artist discography using YouTube links
        
        Args:
            artist: Artist name
            track_youtube_pairs: List of (track_title, youtube_url) tuples
            
        Returns:
            List of SonotellerAnalysis objects
        """
        print(f"\n{'='*60}")
        print(f"Sonoteller.ai: Advanced AI Analysis for {artist}")
        print(f"Processing {len(track_youtube_pairs)} tracks...")
        print(f"Note: This may take some time as each track requires analysis")
        print(f"{'='*60}\n")
        
        results = []
        
        try:
            for i, (title, youtube_url) in enumerate(track_youtube_pairs, 1):
                print(f"[{i}/{len(track_youtube_pairs)}] Processing: {title}")
                
                analysis = self.fetch_with_youtube_link(artist, title, youtube_url)
                results.append(analysis)
                
                # Rate limiting
                if i < len(track_youtube_pairs):
                    print(f"  ⏸ Waiting {self.RATE_LIMIT_DELAY}s before next request...")
                    time.sleep(self.RATE_LIMIT_DELAY)
            
        finally:
            # Always close the driver when done
            self._close_driver()
        
        # Summary
        successful = sum(1 for r in results if r.bpm is not None)
        print(f"\n{'='*60}")
        print(f"✅ Completed: {successful}/{len(track_youtube_pairs)} tracks analyzed")
        print(f"{'='*60}\n")
        
        return results
    
    def export_to_csv(self, results: List[SonotellerAnalysis], output_file: str = "sonoteller_analysis.csv"):
        """
        Export results to comprehensive CSV file
        
        Args:
            results: List of SonotellerAnalysis objects
            output_file: Output CSV filename
        """
        import csv
        
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = [
                'artist', 'title', 'youtube_id', 'bpm', 'key',
                'top_genre', 'genre_confidence', 'top_subgenre', 'subgenre_confidence',
                'top_mood', 'mood_confidence', 'instruments', 'vocals_pitch',
                'language', 'explicit', 'analysis_url', 'error'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            writer.writeheader()
            for analysis in results:
                row = {
                    'artist': analysis.artist,
                    'title': analysis.title,
                    'youtube_id': analysis.youtube_id,
                    'bpm': analysis.bpm,
                    'key': analysis.key,
                    'instruments': ', '.join(analysis.instruments) if analysis.instruments else '',
                    'vocals_pitch': analysis.vocals_pitch,
                    'language': analysis.language,
                    'explicit': analysis.explicit,
                    'analysis_url': analysis.analysis_url,
                    'error': analysis.error
                }
                
                # Add top genre/subgenre/mood
                if analysis.genres and len(analysis.genres) > 0:
                    row['top_genre'] = analysis.genres[0][0]
                    row['genre_confidence'] = analysis.genres[0][1]
                
                if analysis.subgenres and len(analysis.subgenres) > 0:
                    row['top_subgenre'] = analysis.subgenres[0][0]
                    row['subgenre_confidence'] = analysis.subgenres[0][1]
                
                if analysis.moods and len(analysis.moods) > 0:
                    row['top_mood'] = analysis.moods[0][0]
                    row['mood_confidence'] = analysis.moods[0][1]
                
                writer.writerow(row)
        
        print(f"Results exported to {output_file}")
    
    def export_detailed_json(self, results: List[SonotellerAnalysis], output_file: str = "sonoteller_detailed.json"):
        """
        Export complete analysis results to JSON
        
        Args:
            results: List of SonotellerAnalysis objects
            output_file: Output JSON filename
        """
        data = []
        for analysis in results:
            # Convert to dict and clean None values
            item = {k: v for k, v in analysis.__dict__.items() if v is not None}
            data.append(item)
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print(f"Detailed results exported to {output_file}")
    
    def print_analysis_summary(self, analysis: SonotellerAnalysis):
        """
        Print a formatted summary of the analysis
        
        Args:
            analysis: SonotellerAnalysis object
        """
        print(f"\n{'='*60}")
        print(f"🎵 {analysis.artist} - {analysis.title}")
        print(f"{'='*60}")
        
        if analysis.error:
            print(f"❌ Error: {analysis.error}")
        else:
            print(f"📊 MUSIC ANALYSIS")
            print(f"  • BPM: {analysis.bpm}")
            print(f"  • Key: {analysis.key}")
            
            if analysis.genres:
                print(f"  • Genres: {', '.join([f'{g[0]} ({g[1]}%)' for g in analysis.genres[:3]])}")
            
            if analysis.subgenres:
                print(f"  • Subgenres: {', '.join([f'{s[0]} ({s[1]}%)' for s in analysis.subgenres[:3]])}")
            
            if analysis.moods:
                print(f"  • Moods: {', '.join([f'{m[0]} ({m[1]}%)' for m in analysis.moods[:3]])}")
            
            if analysis.instruments:
                print(f"  • Instruments: {', '.join(analysis.instruments)}")
            
            if analysis.vocals_pitch:
                print(f"  • Vocals: {analysis.vocals_pitch}")
            
            if analysis.language:
                print(f"\n📝 LYRICS ANALYSIS")
                print(f"  • Language: {analysis.language}")
                print(f"  • Explicit: {'Yes' if analysis.explicit else 'No'}")
            
            print(f"\n🔗 Analysis URL: {analysis.analysis_url}")


# Example usage
if __name__ == "__main__":
    # Initialize fetcher
    fetcher = SonotellerFetcher(headless=False)  # Set to True for production
    
    # Example: Django's discography with YouTube links
    # These would be collected earlier in your process
    artist = "Django"
    track_youtube_pairs = [
        ("Juin", "https://www.youtube.com/watch?v=EQVqf7LCT18"),
        ("Fichu", "https://youtu.be/EXAMPLE_ID_2"),
        ("Fusil", "EXAMPLE_ID_3"),  # Can also use just the ID
        # Add more tracks with their YouTube URLs/IDs
    ]
    
    # Fetch analysis for all tracks
    results = fetcher.fetch_artist_discography(artist, track_youtube_pairs)
    
    # Export results
    fetcher.export_to_csv(results)
    fetcher.export_detailed_json(results)
    
    # Display detailed summary for each track
    print("\n" + "="*60)
    print("FINAL RESULTS - DETAILED ANALYSIS")
    print("="*60)
    
    for analysis in results:
        fetcher.print_analysis_summary(analysis)
    
    print("\n" + "="*60)
    print("✅ All analyses complete!")
    print(f"📁 Results saved to:")
    print(f"   • sonoteller_analysis.csv (summary)")
    print(f"   • sonoteller_detailed.json (complete data)")
    print("="*60)
    
    # Note about Selenium
    print("\n⚠️  IMPORTANT: This script requires Selenium WebDriver")
    print("   Install with: pip install selenium")
    print("   Also need ChromeDriver or GeckoDriver installed")
    print("   See: https://selenium-python.readthedocs.io/installation.html")
    
    def __init__(self, cache_file: str = "sonoteller_cache.json", headless: bool = True):
        """
        Initialize Sonoteller fetcher
        
        Args:
            cache_file: Path to cache file for storing results
            headless: Run browser in headless mode
        """
        self.cache_file = cache_file
        self.cache = self._load_cache()
        self.headless = headless
        self.driver = None
    
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
    
    def _extract_youtube_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from URL"""
        patterns = [
            r'(?:youtube\.com/watch\?v=|youtu\.be/)([^&\n]+)',
            r'youtube\.com/embed/([^&\n]+)',
            r'youtube\.com/v/([^&\n]+)'
        ]
        
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        
        # If no pattern matches, assume it's already an ID
        if len(url) == 11:
            return url
        
        return None
    
    def _init_driver(self):
        """Initialize Selenium WebDriver"""
        if self.driver is None:
            options = webdriver.ChromeOptions()
            if self.headless:
                options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            
            try:
                self.driver = webdriver.Chrome(options=options)
            except WebDriverException:
                print("Chrome WebDriver not found. Trying Firefox...")
                options = webdriver.FirefoxOptions()
                if self.headless:
                    options.add_argument('--headless')
                self.driver = webdriver.Firefox(options=options)
    
    def _close_driver(self):
        """Close WebDriver if open"""
        if self.driver:
            self.driver.quit()
            self.driver = None
    
    def _parse_percentage_list(self, text: str) -> List[Tuple[str, int]]:
        """Parse text like 'Hip Hop (75), Pop (70)' into list of tuples"""
        items = []
        pattern = r'([^(]+)\s*\((\d+)\)'
        matches = re.findall(pattern, text)
        for name, percentage in matches:
            items.append((name.strip(), int(percentage)))
        return items
    
    def _scrape_analysis(self, youtube_id: str) -> Optional[Dict]:
        """
        Scrape analysis results from Sonoteller.ai
        
        Args:
            youtube_id: YouTube video ID
            
        Returns:
            Dictionary with analysis results or None if failed
        """
        self._init_driver()
        
        try:
            # Navigate to Sonoteller with YouTube ID
            url = f"{self.BASE_URL}/{youtube_id}"
            self.driver.get(url)
            
            # Wait for analysis to complete (check for BPM element)
            wait = WebDriverWait(self.driver, self.ANALYSIS_WAIT_TIME)
            
            # Wait for the analysis results to appear
            try:
                # Look for BPM & Key section
                bpm_element = wait.until(
                    EC.presence_of_element_located(
                        (By.XPATH, "//h3[contains(text(), 'BPM & Key')]/following-sibling::*")
                    )
                )
            except TimeoutException:
                print(f"    ⚠ Analysis timeout for {youtube_id}")
                return None
            
            # Extract all analysis data
            analysis = {}
            
            # BPM & Key
            try:
                bpm_key_text = bpm_element.text
                # Parse "136BPM, Eb Major" format
                bpm_match = re.search(r'(\d+)\s*BPM', bpm_key_text)
                if bpm_match:
                    analysis['bpm'] = int(bpm_match.group(1))
                
                key_match = re.search(r'BPM,?\s*(.+)', bpm_key_text)
                if key_match:
                    analysis['key'] = key_match.group(1).strip()
            except:
                pass
            
            # Genres
            try:
                genres_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Genres']/following-sibling::*"
                )
                analysis['genres'] = self._parse_percentage_list(genres_element.text)
            except:
                pass
            
            # Subgenres
            try:
                subgenres_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Subgenres']/following-sibling::*"
                )
                analysis['subgenres'] = self._parse_percentage_list(subgenres_element.text)
            except:
                pass
            
            # Moods
            try:
                moods_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Moods']/following-sibling::*"
                )
                analysis['moods'] = self._parse_percentage_list(moods_element.text)
            except:
                pass
            
            # Instruments
            try:
                instruments_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Instruments']/following-sibling::*"
                )
                analysis['instruments'] = [i.strip() for i in instruments_element.text.split(',')]
            except:
                pass
            
            # Vocals
            try:
                vocals_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Vocals']/following-sibling::*"
                )
                analysis['vocals_pitch'] = vocals_element.text.strip()
            except:
                pass
            
            # Lyrics Analysis (if present)
            try:
                summary_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Summary']/following-sibling::*"
                )
                analysis['lyrics_summary'] = summary_element.text.strip()
            except:
                pass
            
            # Language
            try:
                language_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Language']/following-sibling::*"
                )
                analysis['language'] = language_element.text.strip()
            except:
                pass
            
            # Explicit
            try:
                explicit_element = self.driver.find_element(
                    By.XPATH, "//h3[text()='Explicit']/following-sibling::*"
                )
                analysis['explicit'] = explicit_element.text.strip().lower() == 'yes'
            except:
                pass
            
            analysis['analysis_url'] = url
            
            return analysis
            
        except Exception as e:
            print(f"    ✗ Scraping error: {str(e)}")
            return None
    
    def