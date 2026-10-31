import os
import random
import logging
from pathlib import Path
import requests
from src.config import AppConfig

LOGGER = logging.getLogger(__name__)

def extract_keywords(text: str, top_n: int = 5) -> list[str]:
    """Extracts multiple keywords from a given text block using KeyBERT."""
    if not text or not text.strip():
        return []
        
    try:
        from keybert import KeyBERT
    except ImportError:
        LOGGER.warning("keybert not installed. Skipping keyword extraction.")
        return []
    
    # Use a multilingual model to support Portuguese
    try:
        kw_model = KeyBERT(model='paraphrase-multilingual-MiniLM-L12-v2')
        # Extract multiple keywords
        keywords = kw_model.extract_keywords(
            text, 
            keyphrase_ngram_range=(1, 2), 
            stop_words=None, 
            top_n=top_n,
            language="portuguese"
        )
        if keywords:
            return [kw[0] for kw in keywords]
    except Exception as e:
        LOGGER.warning(f"KeyBERT extraction failed: {e}")
        
    return []


def extract_keyword(text: str) -> str:
    """Extracts the top keyword from a given text block using KeyBERT. DEPRECATED: Use extract_keywords instead."""
    keywords = extract_keywords(text, top_n=1)
    return keywords[0] if keywords else ""


def fetch_pexels_images(cfg: AppConfig, keywords: list[str], dest_dir: Path) -> list[Path]:
    """
    Fetches portrait images from Pexels based on multiple keywords.
    Downloads one image per keyword to create a diverse B-roll asset library.
    """
    if not cfg.pexels_api_key:
        LOGGER.info("Pexels API key not set. Skipping image B-Roll generation.")
        return []
    
    if not keywords:
        return []
    
    downloaded_images = []
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    for keyword in keywords:
        if not keyword or not keyword.strip():
            continue
        
        try:
            # Search for portrait images
            url = f"https://api.pexels.com/v1/search?query={keyword}&per_page=10&orientation=portrait"
            headers = {"Authorization": cfg.pexels_api_key}
            
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if not data.get("photos"):
                LOGGER.info(f"No Pexels images found for keyword: {keyword}")
                continue
            
            # Pick a random image from top results to avoid repetition
            image = random.choice(data["photos"])
            image_url = image.get("src", {}).get("original")
            
            if not image_url:
                LOGGER.warning(f"No download link found for image keyword: {keyword}")
                continue
            
            # Sanitize keyword for filename
            safe_kw = "".join([c if c.isalnum() else "_" for c in keyword.lower()])
            image_ext = image_url.split(".")[-1].split("?")[0]  # Handle query params
            if image_ext not in ["jpg", "jpeg", "png", "webp"]:
                image_ext = "jpg"
            
            image_path = dest_dir / f"broll_{safe_kw}_{image['id']}.{image_ext}"
            
            if image_path.exists():
                LOGGER.info(f"B-Roll image already exists: {image_path.name}")
                downloaded_images.append(image_path)
                continue
            
            LOGGER.info(f"Downloading B-Roll image '{keyword}' -> {image_path.name}")
            dl_resp = requests.get(image_url, stream=True, timeout=60)
            dl_resp.raise_for_status()
            
            with open(image_path, 'wb') as f:
                for chunk in dl_resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            downloaded_images.append(image_path)
            LOGGER.info(f"Successfully downloaded: {image_path.name}")
            
        except Exception as e:
            LOGGER.warning(f"Failed to fetch Pexels image for '{keyword}': {e}")
            continue
    
    return downloaded_images


def fetch_pexels_broll(cfg: AppConfig, keyword: str, dest_dir: Path) -> Path | None:
    """
    Fetches a portrait B-Roll image from Pexels based on the keyword.
    (Wrapper for backwards compatibility - now returns first image from fetch_pexels_images)
    """
    if not keyword:
        return None
    
    images = fetch_pexels_images(cfg, [keyword], dest_dir)
    return images[0] if images else None

def fetch_youtube_sfx(keyword: str, dest_dir: Path) -> Path | None:
    """
    Downloads a sound effect from YouTube using yt-dlp based on the given keyword.
    Uses 'ytsearch1' to find the first result for '{keyword} sound effect no copyright'.
    """
    import subprocess
    if not keyword:
        return None
        
    dest_dir.mkdir(parents=True, exist_ok=True)
    safe_kw = "".join([c if c.isalnum() else "_" for c in keyword.lower()])
    out_path = dest_dir / f"{safe_kw}.mp3"
    
    if out_path.exists():
        LOGGER.info(f"SFX already exists: {out_path.name}")
        return out_path
        
    search_query = f"ytsearch1:{keyword} sound effect no copyright"
    LOGGER.info(f"Downloading SFX via YouTube: '{keyword}' -> {out_path.name}")
    
    try:
        # Use yt-dlp from the virtual environment if available, else system yt-dlp
        venv_yt_dlp = Path(".venv/bin/yt-dlp")
        yt_dlp_cmd = str(venv_yt_dlp) if venv_yt_dlp.exists() else "yt-dlp"
        
        cmd = [
            yt_dlp_cmd,
            "--no-warnings",
            "--extract-audio",
            "--audio-format", "mp3",
            "-o", str(out_path).replace(".mp3", ".%(ext)s"),
            search_query
        ]
        
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.returncode == 0 and out_path.exists():
            LOGGER.info(f"Successfully downloaded SFX: {out_path.name}")
            return out_path
        else:
            LOGGER.warning(f"Failed to download SFX '{keyword}'. yt-dlp output: {result.stderr}")
            return None
            
    except Exception as e:
        LOGGER.warning(f"Exception while downloading SFX '{keyword}': {e}")
        return None
