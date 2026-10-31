"""
Platform integrations for YouTube Shorts and TikTok.
Handles OAuth, YouTube Data API v3, YouTube Analytics API,
TikTok Developer API, and agile yt-dlp profile/video imports.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlencode

import requests

from ..config import AppConfig, load_config
from .models import ChannelVideo

LOGGER = logging.getLogger(__name__)

DATA_AUTH_DIR = Path("data/auth")


class YouTubePlatform:
    """
    YouTube Shorts integration:
    - Lists user's Shorts and videos with metrics (views, likes, comments)
    - Retrieves retention data when OAuth is available
    - Downloads video for audit via yt-dlp
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()
        self.auth_file = DATA_AUTH_DIR / "youtube_tokens.json"

    def get_api_key(self) -> Optional[str]:
        return getattr(self.config, "youtube_api_key", None) or os.environ.get("YOUTUBE_API_KEY")

    def get_client_credentials(self) -> tuple[Optional[str], Optional[str]]:
        cid = getattr(self.config, "youtube_client_id", None) or os.environ.get("YOUTUBE_CLIENT_ID")
        sec = getattr(self.config, "youtube_client_secret", None) or os.environ.get("YOUTUBE_CLIENT_SECRET")
        return cid, sec

    def get_oauth_auth_url(self, redirect_uri: str) -> str:
        client_id, _ = self.get_client_credentials()
        if not client_id:
            raise ValueError("YOUTUBE_CLIENT_ID não configurado")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "https://www.googleapis.com/auth/youtube.readonly https://www.googleapis.com/auth/yt-analytics.readonly",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        client_id, client_secret = self.get_client_credentials()
        if not client_id or not client_secret:
            raise ValueError("Credenciais OAuth do YouTube ausentes")

        token_url = "https://oauth2.googleapis.com/token"
        data = {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        resp = requests.post(token_url, data=data, timeout=30)
        resp.raise_for_status()
        tokens = resp.json()

        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        return tokens

    def get_access_token(self) -> Optional[str]:
        if not self.auth_file.exists():
            return None
        try:
            tokens = json.loads(self.auth_file.read_text(encoding="utf-8"))
            return tokens.get("access_token")
        except Exception:
            return None

    def list_videos(self, channel_id: Optional[str] = None, max_results: int = 15) -> List[ChannelVideo]:
        """
        Lists channel videos using OAuth access_token or public API key.
        """
        access_token = self.get_access_token()
        api_key = self.get_api_key()

        if not access_token and not api_key:
            LOGGER.warning("Nenhuma credencial do YouTube configurada (nem OAuth nem API Key)")
            return []

        headers = {}
        params: dict[str, Any] = {
            "part": "snippet",
            "maxResults": max_results,
        }

        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
            params["mine"] = "true"
            params["type"] = "video"
        elif channel_id and api_key:
            target_cid = channel_id.strip()
            if target_cid.startswith("@") or not target_cid.startswith("UC"):
                handle = target_cid if target_cid.startswith("@") else f"@{target_cid}"
                try:
                    h_resp = requests.get(
                        "https://www.googleapis.com/youtube/v3/channels",
                        params={"part": "id", "forHandle": handle, "key": api_key},
                        timeout=15,
                    )
                    if h_resp.ok and h_resp.json().get("items"):
                        target_cid = h_resp.json()["items"][0]["id"]
                except Exception as exc:
                    LOGGER.warning("Falha ao resolver handle %s no YouTube: %s", handle, exc)

            params["key"] = api_key
            params["channelId"] = target_cid
            params["order"] = "date"
            params["type"] = "video"
        else:
            return []

        search_url = "https://www.googleapis.com/youtube/v3/search"
        try:
            resp = requests.get(search_url, headers=headers, params=params, timeout=30)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            video_ids = [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]

            if not video_ids:
                return []

            # Busca estatísticas detalhadas dos vídeos
            stats_url = "https://www.googleapis.com/youtube/v3/videos"
            stats_params: dict[str, Any] = {
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids),
            }
            if access_token:
                stats_headers = {"Authorization": f"Bearer {access_token}"}
            else:
                stats_headers = {}
                stats_params["key"] = api_key

            s_resp = requests.get(stats_url, headers=stats_headers, params=stats_params, timeout=30)
            s_resp.raise_for_status()
            detail_items = s_resp.json().get("items", [])

            results = []
            for item in detail_items:
                vid = item["id"]
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                results.append(
                    ChannelVideo(
                        id=vid,
                        title=snippet.get("title", "Sem título"),
                        url=f"https://www.youtube.com/shorts/{vid}",
                        thumbnail=snippet.get("thumbnails", {}).get("high", {}).get("url")
                        or snippet.get("thumbnails", {}).get("default", {}).get("url", ""),
                        views_count=int(stats.get("viewCount", 0)),
                        likes_count=int(stats.get("likeCount", 0)),
                        comments_count=int(stats.get("commentCount", 0)),
                        published_at=snippet.get("publishedAt", ""),
                        platform="youtube",
                    )
                )
            return results
        except Exception as exc:
            LOGGER.error("Falha ao buscar vídeos do YouTube: %s", exc)
            return []


class TikTokPlatform:
    """
    TikTok integration:
    - TikTok Login Kit & Display API (OAuth 2.0)
    - Fallback: yt-dlp profile/video extractor for instant zero-friction imports
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()
        self.auth_file = DATA_AUTH_DIR / "tiktok_tokens.json"

    def get_client_credentials(self) -> tuple[Optional[str], Optional[str]]:
        key = getattr(self.config, "tiktok_client_key", None) or os.environ.get("TIKTOK_CLIENT_KEY")
        sec = getattr(self.config, "tiktok_client_secret", None) or os.environ.get("TIKTOK_CLIENT_SECRET")
        return key, sec

    def get_oauth_auth_url(self, redirect_uri: str) -> str:
        client_key, _ = self.get_client_credentials()
        if not client_key:
            raise ValueError("TIKTOK_CLIENT_KEY não configurado")
        params = {
            "client_key": client_key,
            "response_type": "code",
            "scope": "user.info.profile,user.info.stats,video.list",
            "redirect_uri": redirect_uri,
            "state": "pipeline_shorts_tiktok_auth",
        }
        return f"https://www.tiktok.com/v2/auth/authorize/?{urlencode(params)}"

    def exchange_code(self, code: str, redirect_uri: str) -> dict[str, Any]:
        client_key, client_secret = self.get_client_credentials()
        if not client_key or not client_secret:
            raise ValueError("Credenciais do TikTok ausentes")

        token_url = "https://open.tiktokapis.com/v2/oauth/token/"
        data = {
            "client_key": client_key,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        resp = requests.post(token_url, data=data, headers=headers, timeout=30)
        resp.raise_for_status()
        tokens = resp.json()

        self.auth_file.parent.mkdir(parents=True, exist_ok=True)
        self.auth_file.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
        return tokens

    def list_profile_videos_via_ytdlp(self, profile_url_or_username: str, max_results: int = 10) -> List[ChannelVideo]:
        """
        Instant fallback: extracts user's TikTok videos via yt-dlp without needing
        developer portal approval.
        """
        target = profile_url_or_username.strip()
        if not target.startswith("http"):
            username = target.lstrip("@")
            target = f"https://www.tiktok.com/@{username}"

        cmd = [
            "yt-dlp",
            "--flat-playlist",
            "--dump-json",
            f"--playlist-end={max_results}",
            target,
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
            videos = []
            for line in res.stdout.splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                    vid = data.get("id", "")
                    title = data.get("title") or data.get("description") or "Vídeo TikTok"
                    url = data.get("url") or data.get("webpage_url") or f"https://www.tiktok.com/@user/video/{vid}"
                    thumbnails = data.get("thumbnails", [])
                    thumb = thumbnails[-1]["url"] if thumbnails else ""
                    videos.append(
                        ChannelVideo(
                            id=vid,
                            title=title[:80],
                            url=url,
                            thumbnail=thumb,
                            views_count=data.get("view_count") or 0,
                            likes_count=data.get("like_count") or 0,
                            comments_count=data.get("comment_count") or 0,
                            shares_count=data.get("repost_count") or 0,
                            platform="tiktok",
                        )
                    )
                except Exception:
                    continue
            return videos
        except Exception as exc:
            LOGGER.error("Falha ao extrair perfil TikTok via yt-dlp: %s", exc)
            return []


def download_video_for_audit(url: str, output_dir: Path) -> Path:
    """
    Downloads any YouTube Shorts or TikTok video using yt-dlp for local virality auditing.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    out_template = str(output_dir / "audit_video.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=180)
    if res.returncode != 0:
        raise RuntimeError(f"Erro ao baixar vídeo com yt-dlp: {res.stderr}")

    downloaded = list(output_dir.glob("audit_video.*"))
    if not downloaded:
        raise FileNotFoundError("Vídeo baixado não foi encontrado.")
    return downloaded[0]
