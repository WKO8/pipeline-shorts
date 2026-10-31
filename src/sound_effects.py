"""
Sound Effects (SFX) management module.
Downloads and manages sound effects from Freesound.org API for use in video editing.
"""

import logging
from pathlib import Path
from typing import Optional

import requests

from src.config import AppConfig

LOGGER = logging.getLogger(__name__)

# Mapping of Portuguese keywords to English Freesound search terms
SFX_KEYWORD_MAPPING = {
    "documento": ["document", "paper", "typing"],
    "favela": ["ambient", "city noise", "street noise"],
    "operação": ["action", "gunshot", "police siren"],
    "criança": ["child laugh", "baby cry", "kids playing"],
    "estudando": ["writing", "pen", "focus"],
    "polícia": ["police siren", "alarm", "warning"],
    "suspense": ["suspenseful", "tension", "dramatic"],
    "impacto": ["impact", "hit", "crash"],
    "transição": ["whoosh", "transition", "beep"],
    "ênfase": ["bell", "ding", "pop"],
}


def get_sfx_candidates(keyword: str) -> list[str]:
    """
    Get possible SFX search terms for a given keyword.
    Maps Portuguese keywords to English Freesound search terms.
    """
    keyword_lower = keyword.lower().strip()
    
    # Check if keyword is in mapping
    if keyword_lower in SFX_KEYWORD_MAPPING:
        return SFX_KEYWORD_MAPPING[keyword_lower]
    
    # Fallback: use keyword as-is
    return [keyword_lower]


def fetch_freesound_sfx(
    api_key: str,
    keyword: str,
    dest_dir: Path,
    max_duration: float = 3.0,
    target_format: str = "mp3",
) -> Optional[Path]:
    """
    Fetches a sound effect from Freesound.org based on keyword.
    
    Args:
        api_key: Freesound API key
        keyword: Search keyword (English)
        dest_dir: Directory to save downloaded SFX
        max_duration: Maximum duration in seconds (default 3.0)
        target_format: Audio format (default 'mp3')
    
    Returns:
        Path to downloaded SFX file or None if download failed
    """
    if not api_key or not keyword:
        return None
    
    url = "https://freesound.org/api/v2/search/text/"
    headers = {"Authorization": f"Token {api_key}"}
    
    params = {
        "query": keyword,
        "fields": "id,name,download,duration,filesize,previews",
        "filter": f"duration:[0 TO {max_duration}]",
        "sort": "rating",
        "page_size": 10,
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        results = data.get("results") or []
        if not results:
            LOGGER.info(f"No Freesound SFX found for keyword: {keyword}")
            return None
        
        # Prefer the shortest usable sound among the top-rated results.
        # This tends to produce cleaner hits, whooshes and pops for short-form edits.
        downloadable = [item for item in results if item.get("download")]
        sound = min(
            downloadable,
            key=lambda item: float(item.get("duration") or max_duration),
            default=None,
        )
        if sound is None:
            LOGGER.warning(f"No downloadable Freesound result found for keyword: {keyword}")
            return None

        download_url = sound.get("download")
        
        if not download_url:
            LOGGER.warning(f"No download URL found for SFX: {sound.get('name')}")
            return None
        
        dest_dir.mkdir(parents=True, exist_ok=True)
        
        # Sanitize filename
        safe_name = "".join([c if c.isalnum() else "_" for c in sound.get("name", "sfx").lower()])
        sfx_path = dest_dir / f"sfx_{safe_name}_{sound['id']}.{target_format}"
        
        if sfx_path.exists():
            LOGGER.info(f"SFX already exists: {sfx_path.name}")
            return sfx_path
        
        LOGGER.info(f"Downloading SFX '{keyword}' -> {sfx_path.name}")
        
        # Download the sound
        dl_response = requests.get(download_url, headers=headers, stream=True, timeout=60)
        dl_response.raise_for_status()
        
        with open(sfx_path, 'wb') as f:
            for chunk in dl_response.iter_content(chunk_size=8192):
                f.write(chunk)
        
        LOGGER.info(f"Successfully downloaded SFX: {sfx_path.name}")
        return sfx_path
        
    except Exception as e:
        LOGGER.warning(f"Failed to fetch Freesound SFX for '{keyword}': {e}")
        return None


def fetch_multiple_sfx(
    api_key: str,
    keywords: list[str],
    dest_dir: Path,
    max_duration: float = 3.0,
) -> dict[str, Path]:
    """
    Fetches multiple sound effects from Freesound.org.
    
    Args:
        api_key: Freesound API key
        keywords: List of search keywords
        dest_dir: Directory to save downloaded SFX
        max_duration: Maximum duration in seconds
    
    Returns:
        Dictionary mapping keywords to downloaded SFX paths
    """
    if not api_key:
        LOGGER.info("Freesound API key not set. Skipping SFX generation.")
        return {}
    
    results = {}
    
    for keyword in keywords:
        if not keyword or not keyword.strip():
            continue
        
        try:
            sfx_path = fetch_freesound_sfx(
                api_key,
                keyword.strip(),
                dest_dir,
                max_duration=max_duration,
            )
            if sfx_path:
                results[keyword] = sfx_path
        except Exception as e:
            LOGGER.warning(f"Error fetching SFX for '{keyword}': {e}")
            continue
    
    return results


def create_sfx_from_keywords(
    keywords_str: str,
    api_key: str,
    dest_dir: Path,
    sfx_per_keyword: int = 3,
) -> list[tuple[str, Path]]:
    """
    Creates a list of (keyword, path) tuples for SFX based on keyword string.
    
    Args:
        keywords_str: Comma or semicolon-separated keywords
        api_key: Freesound API key
        dest_dir: Directory to save SFX files
        sfx_per_keyword: Number of SFX to download per keyword (default 3)
    
    Returns:
        List of (keyword, path) tuples for successfully downloaded SFX
    """
    if not keywords_str or not api_key:
        return []
    
    # Parse keywords
    keywords = [kw.strip() for kw in keywords_str.replace(";", ",").split(",") if kw.strip()]
    
    results: list[tuple[str, Path]] = []
    seen_paths: set[str] = set()
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    for keyword in keywords:
        # Get SFX search candidates
        search_terms = get_sfx_candidates(keyword)
        sfx_count = 0
        
        # Try to download multiple SFX for each search term
        for search_term in search_terms:
            if sfx_count >= sfx_per_keyword:
                break
            
            # Attempt to fetch multiple sounds by varying the query
            for attempt in range(sfx_per_keyword):
                if sfx_count >= sfx_per_keyword:
                    break
                
                query_variant = f"{search_term}" if attempt == 0 else f"{search_term} {attempt}"
                sfx_path = fetch_freesound_sfx(api_key, query_variant, dest_dir)
                if sfx_path and str(sfx_path) not in seen_paths:
                    results.append((keyword, sfx_path))
                    seen_paths.add(str(sfx_path))
                    sfx_count += 1
    
    return results
