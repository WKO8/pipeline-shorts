from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import yt_dlp

LOGGER = logging.getLogger(__name__)


class SelectiveRangeDownloader:
    """
    Downloads only targeted cut ranges from a long YouTube video via HTTP Range requests.
    Supports concurrency with micro-staggering and node.js EJS challenge resolution.
    """

    def __init__(
        self,
        format_spec: str = "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/best",
        cookies_file: Optional[str] = None,
        cookies_from_browser: Optional[str] = None,
    ) -> None:
        self.format_spec = format_spec
        self.cookies_file = cookies_file
        self.cookies_from_browser = cookies_from_browser

    def _build_ydl_opts(
        self,
        start_sec: float,
        end_sec: float,
        out_path: Path,
        force_keyframes: bool = False,
    ) -> Dict[str, Any]:
        """Builds yt-dlp options for downloading a selective second range."""
        opts: Dict[str, Any] = {
            "format": self.format_spec,
            "outtmpl": str(out_path),
            "download_ranges": yt_dlp.utils.download_range_func(None, [(start_sec, end_sec)]),
            "force_keyframes_at_cuts": force_keyframes,
            "cachedir": False,
            "no_warnings": True,
            "nocheckcertificate": True,
            "js_runtimes": {"node": {}},
            "external_downloader_args": {
                "ffmpeg_i": [
                    "-nostdin",
                    "-reconnect",
                    "1",
                    "-reconnect_streamed",
                    "1",
                    "-reconnect_delay_max",
                    "5",
                ]
            },
        }
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        if self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)

        return opts

    def download_single_cut(
        self,
        url: str,
        start_sec: float,
        end_sec: float,
        out_path: Path,
        stagger_delay: float = 0.0,
        max_retries: int = 3,
    ) -> Path:
        """
        Downloads a single cut interval using HTTP Range with retries and backoff.
        """
        if stagger_delay > 0:
            time.sleep(stagger_delay)

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if out_path.exists() and out_path.stat().st_size > 1024:
            LOGGER.info("Cut already exists on disk: %s", out_path)
            return out_path

        from .console_ui import print_thread, print_success, print_error, print_warning
        LOGGER.info(
            "Downloading range [%.1fs - %.1fs] (%.1fs) to %s",
            start_sec,
            end_sec,
            end_sec - start_sec,
            out_path.name,
        )

        last_error = None
        for attempt in range(1, max_retries + 1):
            try:
                # Clean up any partial files from previous failed attempts
                for part_file in out_path.parent.glob(f"{out_path.stem}.*part*"):
                    try:
                        part_file.unlink(missing_ok=True)
                    except Exception:
                        pass

                ydl_opts = self._build_ydl_opts(start_sec, end_sec, out_path, force_keyframes=False)

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ret = ydl.download([url])
                    if ret != 0:
                        raise RuntimeError(f"yt-dlp exited with code {ret} downloading {url}")

                if not out_path.exists():
                    candidates = sorted(out_path.parent.glob(f"{out_path.stem}.*"))
                    valid = [c for c in candidates if not c.name.endswith(".part") and c.stat().st_size > 1024]
                    if valid:
                        out_path = valid[-1]
                    else:
                        raise FileNotFoundError(f"Downloaded file not found for {out_path.name}")

                LOGGER.info("Successfully downloaded cut: %s", out_path)
                return out_path
            except Exception as exc:
                last_error = exc
                if attempt < max_retries:
                    backoff = attempt * 2.0
                    print_warning(
                        "Range Downloader",
                        f"Tentativa {attempt}/{max_retries} para {out_path.name} falhou ({exc}). Tentando novamente em {backoff:.1f}s...",
                    )
                    time.sleep(backoff)
                else:
                    LOGGER.error("All %d download attempts for %s failed.", max_retries, out_path.name)

        raise RuntimeError(f"Failed to download cut after {max_retries} attempts: {last_error}") from last_error

    def download_cuts_stream(
        self,
        url: str,
        cuts: List[Dict[str, Any]],
        output_dir: Path,
        video_slug: str = "cut",
        max_workers: int = 3,
        stagger_seconds: float = 0.25,
    ) -> Generator[Dict[str, Any], None, None]:
        """
        Downloads multiple cuts in parallel using ThreadPoolExecutor with micro-staggering.
        Yields each cut dictionary as soon as its download completes, enabling pipelined streaming.
        """
        from .console_ui import print_thread, print_success, print_error

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        futures: Dict[Any, Tuple[int, Dict[str, Any], Path]] = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for idx, cut in enumerate(cuts):
                start_s = float(cut["start_second"])
                end_s = float(cut["end_second"])
                cut_id = cut.get("cut_id", idx + 1)
                filename = f"{video_slug}_corte_{cut_id}_{int(start_s)}s_{int(end_s)}s.mp4"
                out_path = output_dir / filename

                print_thread(cut_id, f"Iniciando download HTTP Range [{start_s:.1f}s - {end_s:.1f}s]...")
                delay = idx * stagger_seconds
                fut = executor.submit(
                    self.download_single_cut,
                    url=url,
                    start_sec=start_s,
                    end_sec=end_s,
                    out_path=out_path,
                    stagger_delay=delay,
                )
                futures[fut] = (cut_id, cut, out_path)

            for fut in as_completed(futures):
                cut_id, cut, out_path = futures[fut]
                try:
                    downloaded_path = fut.result()
                    cut["video_file"] = str(downloaded_path)
                    print_success("Range Downloader", f"Corte #{cut_id} baixado com sucesso: {downloaded_path.name}")
                    yield cut
                except Exception as exc:
                    LOGGER.error("Failed downloading cut %s: %s", cut_id, exc)
                    print_error("Range Downloader", f"Falha no download do corte #{cut_id}: {exc}")

    def download_cuts_parallel(
        self,
        url: str,
        cuts: List[Dict[str, Any]],
        output_dir: Path,
        video_slug: str = "cut",
        max_workers: int = 3,
        stagger_seconds: float = 0.25,
    ) -> List[Dict[str, Any]]:
        """
        Downloads multiple cuts in parallel and returns the complete list of cuts.
        Preserves original ordering of cuts.
        """
        streamed = list(
            self.download_cuts_stream(
                url=url,
                cuts=cuts,
                output_dir=output_dir,
                video_slug=video_slug,
                max_workers=max_workers,
                stagger_seconds=stagger_seconds,
            )
        )
        order_map = {c.get("cut_id"): c for c in streamed}
        return [order_map.get(c.get("cut_id"), c) for c in cuts]
