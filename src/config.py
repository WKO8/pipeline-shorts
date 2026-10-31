from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values


def _to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _to_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _to_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default


def _to_optional_str(value: str | None) -> Optional[str]:
    if value is None:
        return None
    v = value.strip()
    return v if v else None


def _parse_env_file(env_path: Path) -> dict[str, str]:
    """
    Parse .env file directly and return dict of key=value pairs.
    Ensures we read fresh values from disk instead of stale os.environ.
    """
    if not env_path.exists():
        return {}
    return {key: value for key, value in dotenv_values(env_path).items() if value is not None}


@dataclass(slots=True)
class AppConfig:
    ytdlp_format: str = (
        "bestvideo[height>=1080][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height>=1080]+bestaudio/bestvideo+bestaudio/best"
    )
    # Cookie authentication (to bypass YouTube bot-detection)
    # Use one of: browser name (e.g. "chrome", "firefox") OR a path to a cookies.txt file.
    ytdlp_cookies_from_browser: Optional[str] = None  # e.g. "chrome"
    ytdlp_cookies_file: Optional[str] = None  # e.g. "/path/to/cookies.txt"
    # JavaScript runtime for n-challenge solving (requires bgutil server for PO tokens)
    # Format: "runtime" or "runtime:/path/to/executable"
    # Supported: node, deno, bun, quickjs  (node is auto-detected via NVM if installed)
    ytdlp_js_runtime: Optional[str] = (
        None  # e.g. "node" or "node:/home/user/.nvm/versions/node/v24/bin/node"
    )
    # Extra yt-dlp extractor args (space-separated key=value pairs appended to youtube extractor)
    ytdlp_extractor_args: Optional[str] = None  # e.g. "player_client=tv,mweb"
    ytdlp_concurrent_fragments: int = 4
    ytdlp_buffersize: str = "16M"

    # Local audiovisual speaker detection (LR-ASD).
    asd_device: str = "auto"
    asd_threshold: float = 0.7
    asd_margin: float = 0.2
    asd_hold_seconds: float = 0.4
    stage_cache_enabled: bool = True
    curation_cache_enabled: bool = True
    selective_alignment: bool = True
    editorial_review_enabled: bool = True
    editorial_review_max_candidates: int = 12
    narrative_edit_enabled: bool = False
    narrative_montage_enabled: bool = False
    hook_preview_enabled: bool = False
    hook_preview_max_seconds: float = 12.0
    narrative_context_seconds: float = 45.0
    narrative_max_parts: int = 6
    semantic_montage_enabled: bool = False
    semantic_embedding_model: str = "intfloat/multilingual-e5-small"
    semantic_embedding_revision: str = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
    semantic_allow_download: bool = False
    semantic_similarity_threshold: float = 0.90
    semantic_max_pairs: int = 12
    jev_enabled: bool = False
    typesafe_api_key: Optional[str] = field(default=None, repr=False)
    jev_model: str = "jev-1.13.0"
    jev_budget_usd: float = 0.01
    jev_input_usd_per_million: float = 0.042
    jev_confidence_threshold: float = 0.8
    whisper_alignment: bool = True
    whisper_alignment_model: Optional[str] = None
    whisper_alignment_retry_padding: float = 0.25
    captions_animated: bool = False

    # Whisper
    whisper_model: str = "large-v3-turbo"
    whisper_device: str = "cuda"
    whisper_compute_type: str = "int8_float16"
    whisper_language: str = "pt"
    whisper_word_timestamps: bool = True
    stage1_cpu_only: bool = False
    allow_heuristic_fallback: bool = False

    # Ollama / local LLM
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3.5:4b"

    # Gemini / Google AI
    google_api_key: Optional[str] = None
    google_model_name: str = "gemini-3.8-flash"

    # DeepSeek
    deepseek_api_key: Optional[str] = None
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_name: str = "deepseek-v4.1-flash"
    deepseek_reasoning_effort: int = 75

    # Platform integrations
    youtube_api_key: Optional[str] = None
    youtube_client_id: Optional[str] = None
    youtube_client_secret: Optional[str] = None
    youtube_redirect_uri: Optional[str] = None
    tiktok_client_key: Optional[str] = None
    tiktok_client_secret: Optional[str] = None
    tiktok_redirect_uri: Optional[str] = None

    # Curator provider: "auto" (DeepSeek with Gemini fallback), "deepseek", "gemini", "ollama"
    curator_provider: str = "ollama"
    api_priced_model: str = ""
    api_budget_usd: float = 0.05
    api_max_output_tokens: int = 2048
    api_input_usd_per_million: float = 0.0
    api_output_usd_per_million: float = 0.0

    ollama_clips_count: int = 5
    clip_min_seconds: int = 20
    clip_max_seconds: int = 180
    clip_min_gap_seconds: int = 60
    ollama_temperature: float = 0.2
    ollama_timeout_seconds: int = 240
    virality_min_score: float = 8.5
    hook_search_window_seconds: float = 8.0
    story_arc_window_seconds: float = 10.0

    # Video output
    output_width: int = 1080
    output_height: int = 1920
    output_fps: int = 30
    enable_face_tracking: bool = True
    top_region_ratio: float = 0.58
    bottom_region_ratio: float = 0.42
    tracking_window_frames: int = 15
    tracking_ease_alpha: float = 0.28
    tracking_eye_line_ratio: float = 0.30
    tracking_confidence_threshold: float = 0.40
    tracking_focus_min_width_ratio: float = 0.14
    tracking_speaking_mouth_open_threshold: float = 0.018
    tracking_jitter_deadzone_px: float = 2.0
    tracking_max_pan_speed_px_per_s: float = 1500.0
    cinematic_zoom: bool = False
    multiface_window_min_seconds: float = 0.20
    multiface_window_merge_gap_seconds: float = 0.22
    multiface_window_padding_seconds: float = 0.35
    single_window_min_seconds: float = 0.35
    single_window_merge_gap_seconds: float = 0.30
    single_window_padding_seconds: float = 0.30
    multiface_duo_split_threshold: float = 0.22
    multiface_disable_zoom_threshold: float = 0.14
    zoom_per_frame: float = 0.001
    zoom_max_scale: float = 1.18
    zoom_min_duration_seconds: float = 8.0
    emphasis_zoom_scale: float = 1.15
    emphasis_zoom_duration_seconds: float = 0.9
    emphasis_zoom_max_events: int = 6
    topic_jump_zoom_scale: float = 1.10
    topic_jump_duration_seconds: float = 0.45
    topic_jump_max_events: int = 4
    static_pulse_zoom_scale: float = 1.10
    static_pulse_interval_seconds: float = 4.0
    static_pulse_duration_seconds: float = 0.8
    static_motion_threshold: float = 3.5
    fit_scene_full_frame_mode: bool = True

    # Background
    background_video_path: Optional[str] = None
    background_fallback_mode: str = "blur"
    top_banner_path: Optional[str] = None
    bottom_logo_path: Optional[str] = None
    top_banner_margin: int = 20
    bottom_logo_margin: int = 24
    top_banner_max_width: int = 1080
    bottom_logo_max_width: int = 320
    contextual_overlay_chest_offset: int = 400
    contextual_overlay_max_width: int = 420

    # APIs
    pexels_api_key: Optional[str] = None
    freesound_api_key: Optional[str] = None
    sound_effects_enabled: bool = False
    enrich_clip: bool = False

    # Captions
    captions_enabled: bool = True
    captions_font: str = "Arial-Bold"
    captions_font_size: int = 64
    captions_color: str = "#FFFFFF"
    captions_active_word_color: str = "#FFD400"
    captions_stroke_color: str = "#000000"
    captions_stroke_width: int = 3
    captions_position: str = "bottom"
    captions_keyword_color_a: str = "#FFD700"
    captions_keyword_color_b: str = "#00FF00"
    captions_popup_peak_frames: int = 3
    captions_popup_settle_frames: int = 6
    captions_short_phrase_max_words: int = 4
    captions_max_chars: int = 28
    captions_keyword_glow_blur: float = 1.2
    captions_shadow: float = 0.8
    captions_hook_first_scale: int = 155
    captions_hook_first_color: str = "#00FF00"
    captions_burn_in_video: bool = True
    captions_export_overlay_video: bool = False
    captions_overlay_video_codec: str = "qtrle"
    captions_overlay_video_ext: str = "mov"

    # Encoding / hardware
    ffmpeg_video_codec: str = "h264_nvenc"
    ffmpeg_nvenc_preset: str = "p6"
    ffmpeg_video_bitrate: str = "8M"
    ffmpeg_hwaccel: str = "cuda"

    # Optional AI upscaling (Real-ESRGAN)
    upscaling_enabled: bool = False
    upscaling_binary: str = "bin/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan"
    upscaling_model_dir: Optional[str] = "bin/realesrgan-ncnn-vulkan/models"
    upscaling_model: str = "realesr-general-x4v3"
    upscaling_scale: int = 2
    upscaling_tile_size: int = 128
    upscaling_threads: str = "1:2:2"
    upscaling_gpu_id: str = "auto"
    upscaling_downscale_to_original: bool = True
    upscaling_face_enhance: bool = True
    upscaling_face_model: str = "gfpgan"
    upscaling_codeformer_fidelity: float = 0.75
    upscaling_face_only_codeformer: bool = False
    upscaling_python_script: Optional[str] = (
        "bin/Real-ESRGAN-0.3.0/inference_realesrgan_video.py"
    )

    # Retention styling
    vignette_enabled: bool = False
    vignette_angle: float = 0.5
    progress_bar_enabled: bool = False
    progress_bar_height: int = 5
    progress_bar_margin: int = 14
    progress_bar_track_color: str = "#FFFFFF"
    progress_bar_fill_color: str = "#00FF00"
    emoji_enabled: bool = False
    emoji_max_events: int = 4
    emoji_font_size: int = 42
    render_parallel_jobs: int = 1
    audio_normalization_enabled: bool = True
    audio_target_lufs: float = -14.0

    # Pipeline profiles
    pipeline_profile: int = 3
    yolo_img_size: int = 640
    yolo_chunk_size: int = 16
    silence_jump_cuts_enabled: bool = False
    silence_noise_db: float = -40.0
    silence_min_duration_seconds: float = 0.8
    silence_padding_seconds: float = 0.15
    force_single_layout: bool = True
    speaker_lock_priority_enabled: bool = False
    speaker_interrupt_hold_seconds: float = 1.5
    speaker_switch_min_seconds: float = 2.0
    ultra_crop_enabled: bool = False
    ultra_crop_face_width_factor: float = 2.5
    ultra_crop_min_height_ratio: float = 0.60
    captions_force_uppercase: bool = False

    # Naming/runtime
    project_name: str = "shorts_pipeline"
    output_basename: str = "clip"
    log_level: str = "INFO"
    keep_intermediate_files: bool = True
    output_video_extension: str = "mp4"
    davinci_resolve_compatibility: bool = False

    # Paths (runtime)
    project_root: Path = Path(".")
    data_dir: Path = Path("data")
    downloads_dir: Path = Path("data/downloads")
    audio_dir: Path = Path("data/audio")
    transcripts_dir: Path = Path("data/transcripts")
    clips_dir: Path = Path("data/clips")
    subtitles_dir: Path = Path("data/subtitles")
    temp_dir: Path = Path("data/tmp")

    def validate(self) -> None:
        import math
        for value in (self.api_budget_usd, self.api_input_usd_per_million, self.api_output_usd_per_million):
            if not math.isfinite(value) or value < 0:
                raise ValueError("API budget and prices must be finite and nonnegative")
        if not 256 <= self.api_max_output_tokens <= 8192:
            raise ValueError("API_MAX_OUTPUT_TOKENS must be between 256 and 8192")
        if not 1 <= self.editorial_review_max_candidates <= 30:
            raise ValueError("EDITORIAL_REVIEW_MAX_CANDIDATES must be between 1 and 30")
        if not 0 <= self.whisper_alignment_retry_padding <= 1:
            raise ValueError("WHISPER_ALIGNMENT_RETRY_PADDING must be between 0 and 1 second")
        if self.asd_device not in {"auto", "cuda", "cpu"}:
            raise ValueError("ASD_DEVICE must be auto, cuda or cpu")
        if not 0 <= self.asd_threshold <= 1 or not 0 <= self.asd_margin <= 1:
            raise ValueError("ASD threshold and margin must be probabilities")
        if not 0.04 <= self.asd_hold_seconds <= 5:
            raise ValueError("ASD_HOLD_SECONDS must be between 0.04 and 5")
        if self.output_width <= 0 or self.output_height <= 0:
            raise ValueError("output_width and output_height must be > 0")

        if not (0.0 < self.top_region_ratio <= 1.0):
            raise ValueError("top_region_ratio must be between 0 and 1")
        if not (0.0 <= self.bottom_region_ratio <= 1.0):
            raise ValueError("bottom_region_ratio must be between 0 and 1")
        if round(self.top_region_ratio + self.bottom_region_ratio, 5) > 1.00001:
            raise ValueError("top_region_ratio + bottom_region_ratio cannot exceed 1.0")

        if self.clip_min_seconds <= 0 or self.clip_max_seconds <= 0:
            raise ValueError("clip_min_seconds and clip_max_seconds must be > 0")
        if self.clip_min_seconds > self.clip_max_seconds:
            raise ValueError("clip_min_seconds cannot be greater than clip_max_seconds")
        if self.clip_min_gap_seconds < 0:
            raise ValueError("clip_min_gap_seconds must be >= 0")
        if not (0.0 <= self.virality_min_score <= 10.0):
            raise ValueError("virality_min_score must be between 0 and 10")
        if self.hook_search_window_seconds < 1.0:
            raise ValueError("hook_search_window_seconds must be >= 1.0")
        if self.story_arc_window_seconds < 1.0:
            raise ValueError("story_arc_window_seconds must be >= 1.0")

        if self.ollama_clips_count <= 0:
            raise ValueError("ollama_clips_count must be > 0")
        if self.output_fps <= 0:
            raise ValueError("output_fps must be > 0")
        if self.captions_font_size <= 0:
            raise ValueError("captions_font_size must be > 0")
        if self.tracking_window_frames <= 0:
            raise ValueError("tracking_window_frames must be > 0")
        if not (0.01 <= self.tracking_ease_alpha <= 1.0):
            raise ValueError("tracking_ease_alpha must be between 0.01 and 1.0")
        if not (0.10 <= self.tracking_eye_line_ratio <= 0.50):
            raise ValueError("tracking_eye_line_ratio must be between 0.10 and 0.50")
        if not (0.0 <= self.tracking_confidence_threshold <= 1.0):
            raise ValueError(
                "tracking_confidence_threshold must be between 0.0 and 1.0"
            )
        if not (0.02 <= self.tracking_focus_min_width_ratio <= 0.5):
            raise ValueError(
                "tracking_focus_min_width_ratio must be between 0.02 and 0.5"
            )
        if not (0.005 <= self.tracking_speaking_mouth_open_threshold <= 0.25):
            raise ValueError(
                "tracking_speaking_mouth_open_threshold must be between 0.005 and 0.25"
            )
        if not (0.0 <= self.tracking_jitter_deadzone_px <= 40.0):
            raise ValueError("tracking_jitter_deadzone_px must be between 0.0 and 40.0")
        if not (20.0 <= self.tracking_max_pan_speed_px_per_s <= 2000.0):
            raise ValueError(
                "tracking_max_pan_speed_px_per_s must be between 20.0 and 2000.0"
            )
        if not (0.05 <= self.multiface_window_min_seconds <= 5.0):
            raise ValueError(
                "multiface_window_min_seconds must be between 0.05 and 5.0"
            )
        if not (0.0 <= self.multiface_window_merge_gap_seconds <= 5.0):
            raise ValueError(
                "multiface_window_merge_gap_seconds must be between 0.0 and 5.0"
            )
        if not (0.0 <= self.multiface_window_padding_seconds <= 2.0):
            raise ValueError(
                "multiface_window_padding_seconds must be between 0.0 and 2.0"
            )
        if not (0.05 <= self.single_window_min_seconds <= 5.0):
            raise ValueError("single_window_min_seconds must be between 0.05 and 5.0")
        if not (0.0 <= self.single_window_merge_gap_seconds <= 5.0):
            raise ValueError(
                "single_window_merge_gap_seconds must be between 0.0 and 5.0"
            )
        if not (0.0 <= self.single_window_padding_seconds <= 2.0):
            raise ValueError(
                "single_window_padding_seconds must be between 0.0 and 2.0"
            )
        if not (0.0 <= self.multiface_duo_split_threshold <= 1.0):
            raise ValueError(
                "multiface_duo_split_threshold must be between 0.0 and 1.0"
            )
        if not (0.0 <= self.multiface_disable_zoom_threshold <= 1.0):
            raise ValueError(
                "multiface_disable_zoom_threshold must be between 0.0 and 1.0"
            )
        if self.zoom_per_frame < 0.0:
            raise ValueError("zoom_per_frame must be >= 0")
        if self.zoom_max_scale < 1.0:
            raise ValueError("zoom_max_scale must be >= 1.0")
        if self.zoom_min_duration_seconds < 0.0:
            raise ValueError("zoom_min_duration_seconds must be >= 0")
        if self.emphasis_zoom_scale < 1.0:
            raise ValueError("emphasis_zoom_scale must be >= 1.0")
        if self.emphasis_zoom_duration_seconds < 0.1:
            raise ValueError("emphasis_zoom_duration_seconds must be >= 0.1")
        if self.emphasis_zoom_max_events < 0:
            raise ValueError("emphasis_zoom_max_events must be >= 0")
        if self.topic_jump_zoom_scale < 1.0:
            raise ValueError("topic_jump_zoom_scale must be >= 1.0")
        if self.topic_jump_duration_seconds < 0.1:
            raise ValueError("topic_jump_duration_seconds must be >= 0.1")
        if self.topic_jump_max_events < 0:
            raise ValueError("topic_jump_max_events must be >= 0")
        if self.static_pulse_zoom_scale < 1.0:
            raise ValueError("static_pulse_zoom_scale must be >= 1.0")
        if self.static_pulse_interval_seconds <= 0.0:
            raise ValueError("static_pulse_interval_seconds must be > 0")
        if self.static_pulse_duration_seconds <= 0.0:
            raise ValueError("static_pulse_duration_seconds must be > 0")
        if self.static_motion_threshold < 0.0:
            raise ValueError("static_motion_threshold must be >= 0")
        if self.captions_popup_peak_frames < 1:
            raise ValueError("captions_popup_peak_frames must be >= 1")
        if self.captions_popup_settle_frames <= self.captions_popup_peak_frames:
            raise ValueError(
                "captions_popup_settle_frames must be > captions_popup_peak_frames"
            )
        if self.captions_max_chars < 1:
            raise ValueError("captions_max_chars must be >= 1")
        if self.captions_short_phrase_max_words < 1:
            raise ValueError("captions_short_phrase_max_words must be >= 1")
        if self.captions_keyword_glow_blur < 0.0:
            raise ValueError("captions_keyword_glow_blur must be >= 0")
        if self.captions_hook_first_scale < 100:
            raise ValueError("captions_hook_first_scale must be >= 100")
        if self.captions_overlay_video_codec.strip().lower() not in {
            "qtrle",
            "prores_ks",
        }:
            raise ValueError(
                "captions_overlay_video_codec must be one of: qtrle, prores_ks"
            )
        if self.captions_overlay_video_ext.strip().lower() not in {"mov", "mp4"}:
            raise ValueError("captions_overlay_video_ext must be one of: mov, mp4")
        if self.progress_bar_height < 1:
            raise ValueError("progress_bar_height must be >= 1")
        if self.progress_bar_margin < 0:
            raise ValueError("progress_bar_margin must be >= 0")
        if self.emoji_max_events < 0:
            raise ValueError("emoji_max_events must be >= 0")
        if self.emoji_font_size < 8:
            raise ValueError("emoji_font_size must be >= 8")
        if self.render_parallel_jobs < 1:
            raise ValueError("render_parallel_jobs must be >= 1")
        if self.pipeline_profile != 3:
            raise ValueError("Only pipeline_profile 3 is supported")
        if self.silence_min_duration_seconds < 0.02:
            raise ValueError("silence_min_duration_seconds must be >= 0.02")
        if self.silence_padding_seconds < 0.0:
            raise ValueError("silence_padding_seconds must be >= 0.0")
        if not -100 <= self.silence_noise_db <= -5:
            raise ValueError("silence_noise_db must be between -100 and -5")
        if not 2 <= self.hook_preview_max_seconds <= 20:
            raise ValueError("hook_preview_max_seconds must be between 2 and 20")
        if not 0 <= self.narrative_context_seconds <= 120 or not 1 <= self.narrative_max_parts <= 8:
            raise ValueError("Narrative context must be 0..120s and parts 1..8")
        if not 0 <= self.semantic_similarity_threshold <= 1 or not 1 <= self.semantic_max_pairs <= 50:
            raise ValueError("Invalid semantic similarity or pair limit")
        if not 0 <= self.jev_confidence_threshold <= 1 or not 0 <= self.jev_budget_usd <= .05:
            raise ValueError("Jev confidence must be 0..1; budget must be 0..0.05 USD")
        if self.jev_input_usd_per_million <= 0:
            raise ValueError("Jev input price must be positive")
        import math
        if not all(math.isfinite(v) for v in (self.jev_input_usd_per_million,self.jev_budget_usd,
            self.silence_noise_db,self.silence_min_duration_seconds,self.silence_padding_seconds,
            self.hook_preview_max_seconds,self.narrative_context_seconds,self.api_budget_usd)):
            raise ValueError("Editorial and budget settings must be finite")
        if self.speaker_interrupt_hold_seconds <= 0.0:
            raise ValueError("speaker_interrupt_hold_seconds must be > 0")
        if self.speaker_switch_min_seconds <= 0.0:
            raise ValueError("speaker_switch_min_seconds must be > 0")
        if self.ultra_crop_face_width_factor <= 0.1:
            raise ValueError("ultra_crop_face_width_factor must be > 0.1")
        if not (0.15 <= self.ultra_crop_min_height_ratio <= 1.0):
            raise ValueError("ultra_crop_min_height_ratio must be between 0.15 and 1.0")
        if not (0.0 <= self.vignette_angle <= 3.2):
            raise ValueError("vignette_angle must be between 0 and 3.2")
        if self.top_banner_margin < 0:
            raise ValueError("top_banner_margin must be >= 0")
        if self.bottom_logo_margin < 0:
            raise ValueError("bottom_logo_margin must be >= 0")
        if self.top_banner_max_width <= 0:
            raise ValueError("top_banner_max_width must be > 0")
        if self.bottom_logo_max_width <= 0:
            raise ValueError("bottom_logo_max_width must be > 0")
        if self.contextual_overlay_chest_offset < 0:
            raise ValueError("contextual_overlay_chest_offset must be >= 0")
        if self.contextual_overlay_max_width <= 0:
            raise ValueError("contextual_overlay_max_width must be > 0")
        if self.upscaling_scale not in {2, 3, 4}:
            raise ValueError("upscaling_scale must be one of: 2, 3, 4")
        if self.upscaling_tile_size != 0 and self.upscaling_tile_size < 32:
            raise ValueError("upscaling_tile_size must be 0 or >= 32")
        if not self.upscaling_threads.strip():
            raise ValueError("upscaling_threads cannot be empty")
        if self.upscaling_face_model.strip().lower() not in {"gfpgan", "codeformer"}:
            raise ValueError("upscaling_face_model must be one of: gfpgan, codeformer")
        if self.output_video_extension.strip().lower() not in {"mp4", "mov"}:
            raise ValueError("output_video_extension must be one of: mp4, mov")

    def ensure_directories(self) -> None:
        for d in (
            self.data_dir,
            self.downloads_dir,
            self.audio_dir,
            self.transcripts_dir,
            self.clips_dir,
            self.subtitles_dir,
            self.temp_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def load_config(
    env_file: str | Path | None = ".env",
    project_root: str | Path | None = None,
) -> AppConfig:
    default_root = Path(__file__).resolve().parent.parent
    root = Path(project_root) if project_root is not None else default_root
    root = root.resolve()

    # Parse .env file directly (fresh read from disk)
    env_dict: dict[str, str] = {}
    if env_file is not None:
        env_path = Path(env_file)
        if not env_path.is_absolute():
            env_path = root / env_path
        env_dict = _parse_env_file(env_path)

    # Also parse .env.local if present in project root or web/
    for local_candidate in (root / ".env.local", root / "web" / ".env.local"):
        if local_candidate.exists():
            env_dict.update(_parse_env_file(local_candidate))

    # Helper to get value: first from parsed env_dict, then os.getenv, then default
    def _get_env(key: str, default: str = "") -> str:
        return env_dict.get(key) or os.getenv(key, default)

    cfg = AppConfig(
        asd_device=_get_env("ASD_DEVICE", "auto"),
        asd_threshold=_to_float(_get_env("ASD_THRESHOLD"), 0.7),
        asd_margin=_to_float(_get_env("ASD_MARGIN"), 0.2),
        asd_hold_seconds=_to_float(_get_env("ASD_HOLD_SECONDS"), 0.4),
        api_priced_model=_get_env("API_PRICED_MODEL", ""),
        api_budget_usd=_to_float(_get_env("API_BUDGET_USD"), 0.05),
        api_max_output_tokens=_to_int(_get_env("API_MAX_OUTPUT_TOKENS"), 2048),
        api_input_usd_per_million=_to_float(_get_env("API_INPUT_USD_PER_MILLION"), 0.0),
        api_output_usd_per_million=_to_float(_get_env("API_OUTPUT_USD_PER_MILLION"), 0.0),
        stage_cache_enabled=_to_bool(_get_env("STAGE_CACHE_ENABLED"), True),
        selective_alignment=_to_bool(_get_env("SELECTIVE_ALIGNMENT"), True),
        editorial_review_enabled=_to_bool(_get_env("EDITORIAL_REVIEW_ENABLED"), True),
        narrative_edit_enabled=_to_bool(_get_env("NARRATIVE_EDIT_ENABLED"), False),
        narrative_montage_enabled=_to_bool(_get_env("NARRATIVE_MONTAGE_ENABLED"), False),
        hook_preview_enabled=_to_bool(_get_env("HOOK_PREVIEW_ENABLED"), False),
        hook_preview_max_seconds=_to_float(_get_env("HOOK_PREVIEW_MAX_SECONDS"), 12.0),
        narrative_context_seconds=_to_float(_get_env("NARRATIVE_CONTEXT_SECONDS"), 45.0),
        narrative_max_parts=_to_int(_get_env("NARRATIVE_MAX_PARTS"), 6),
        semantic_montage_enabled=_to_bool(_get_env("SEMANTIC_MONTAGE_ENABLED"), False),
        semantic_embedding_model=_get_env("SEMANTIC_EMBEDDING_MODEL") or "intfloat/multilingual-e5-small",
        semantic_embedding_revision=_get_env("SEMANTIC_EMBEDDING_REVISION") or "614241f622f53c4eeff9890bdc4f31cfecc418b3",
        semantic_allow_download=_to_bool(_get_env("SEMANTIC_ALLOW_DOWNLOAD"), False),
        semantic_similarity_threshold=_to_float(_get_env("SEMANTIC_SIMILARITY_THRESHOLD"), .90),
        semantic_max_pairs=_to_int(_get_env("SEMANTIC_MAX_PAIRS"), 12),
        jev_enabled=_to_bool(_get_env("JEV_ENABLED"), False),
        typesafe_api_key=_to_optional_str(_get_env("TYPESAFE_API_KEY")) or _to_optional_str(_get_env("JEV_API_KEY")),
        jev_model=_get_env("JEV_MODEL") or "jev-latest",
        jev_budget_usd=_to_float(_get_env("JEV_BUDGET_USD"), .01),
        jev_input_usd_per_million=_to_float(_get_env("JEV_INPUT_USD_PER_MILLION"), .042),
        jev_confidence_threshold=_to_float(_get_env("JEV_CONFIDENCE_THRESHOLD"), .8),
        editorial_review_max_candidates=_to_int(_get_env("EDITORIAL_REVIEW_MAX_CANDIDATES"), 12),
        whisper_alignment=_to_bool(_get_env("WHISPER_ALIGNMENT"), True),
        whisper_alignment_model=_to_optional_str(_get_env("WHISPER_ALIGNMENT_MODEL")),
        whisper_alignment_retry_padding=_to_float(_get_env("WHISPER_ALIGNMENT_RETRY_PADDING"), 0.25),
        captions_animated=_to_bool(_get_env("CAPTIONS_ANIMATED"), False),
        ytdlp_format=_get_env(
            "YTDLP_FORMAT",
            "bestvideo[height>=1080][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[height>=1080]+bestaudio/bestvideo+bestaudio/best",
        ),
        ytdlp_cookies_from_browser=_to_optional_str(
            _get_env("YTDLP_COOKIES_FROM_BROWSER")
        ),
        ytdlp_cookies_file=_to_optional_str(_get_env("YTDLP_COOKIES_FILE")),
        ytdlp_js_runtime=_to_optional_str(_get_env("YTDLP_JS_RUNTIME")),
        ytdlp_extractor_args=_to_optional_str(_get_env("YTDLP_EXTRACTOR_ARGS")),
        ytdlp_concurrent_fragments=_to_int(
            _get_env("YTDLP_CONCURRENT_FRAGMENTS"), 4
        ),
        ytdlp_buffersize=_get_env("YTDLP_BUFFER_SIZE")
        or _get_env("YTDLP_BUFFERSIZE", "16M"),
        whisper_model=_get_env("WHISPER_MODEL", "large-v3-turbo"),
        whisper_device=_get_env("WHISPER_DEVICE", "cuda"),
        whisper_compute_type=_get_env("WHISPER_COMPUTE_TYPE", "int8_float16"),
        whisper_language=_get_env("WHISPER_LANGUAGE", "pt"),
        whisper_word_timestamps=_to_bool(_get_env("WHISPER_WORD_TIMESTAMPS"), True),
        stage1_cpu_only=_to_bool(_get_env("STAGE1_CPU_ONLY"), False),
        allow_heuristic_fallback=_to_bool(_get_env("ALLOW_HEURISTIC_FALLBACK"), False),
        ollama_base_url=_get_env("OLLAMA_BASE_URL", "http://localhost:11434"),
        ollama_model=_get_env("OLLAMA_MODEL", "qwen3.5:4b"),
        google_api_key=_to_optional_str(_get_env("GOOGLE_API_KEY")),
        google_model_name=_get_env("GOOGLE_MODEL_NAME", "gemini-3.8-flash"),
        deepseek_api_key=_to_optional_str(_get_env("DEEPSEEK_API_KEY")),
        deepseek_base_url=_get_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        deepseek_model_name=_get_env("DEEPSEEK_MODEL_NAME", "deepseek-v4.1-flash"),
        deepseek_reasoning_effort=_to_int(_get_env("DEEPSEEK_REASONING_EFFORT"), 75),
        curator_provider=_get_env("CURATOR_PROVIDER", "ollama"),
        ollama_clips_count=_to_int(_get_env("OLLAMA_CLIPS_COUNT"), 5),
        clip_min_seconds=_to_int(_get_env("CLIP_MIN_SECONDS"), 20),
        clip_max_seconds=_to_int(_get_env("CLIP_MAX_SECONDS"), 180),
        clip_min_gap_seconds=_to_int(_get_env("CLIP_MIN_GAP_SECONDS"), 60),
        ollama_temperature=_to_float(_get_env("OLLAMA_TEMPERATURE"), 0.2),
        ollama_timeout_seconds=_to_int(_get_env("OLLAMA_TIMEOUT_SECONDS"), 240),
        virality_min_score=_to_float(_get_env("VIRALITY_MIN_SCORE"), 8.5),
        hook_search_window_seconds=_to_float(
            _get_env("HOOK_SEARCH_WINDOW_SECONDS"), 8.0
        ),
        story_arc_window_seconds=_to_float(_get_env("STORY_ARC_WINDOW_SECONDS"), 10.0),
        output_width=_to_int(_get_env("OUTPUT_WIDTH"), 1080),
        output_height=_to_int(_get_env("OUTPUT_HEIGHT"), 1920),
        output_fps=_to_int(_get_env("OUTPUT_FPS"), 30),
        enable_face_tracking=_to_bool(_get_env("ENABLE_FACE_TRACKING"), True),
        top_region_ratio=_to_float(_get_env("TOP_REGION_RATIO"), 0.58),
        bottom_region_ratio=_to_float(_get_env("BOTTOM_REGION_RATIO"), 0.42),
        tracking_window_frames=_to_int(_get_env("TRACKING_WINDOW_FRAMES"), 15),
        tracking_ease_alpha=_to_float(_get_env("TRACKING_EASE_ALPHA"), 0.28),
        tracking_eye_line_ratio=_to_float(_get_env("TRACKING_EYE_LINE_RATIO"), 0.30),
        tracking_confidence_threshold=_to_float(
            _get_env("TRACKING_CONFIDENCE_THRESHOLD"), 0.40
        ),
        tracking_focus_min_width_ratio=_to_float(
            _get_env("TRACKING_FOCUS_MIN_WIDTH_RATIO"), 0.14
        ),
        tracking_speaking_mouth_open_threshold=_to_float(
            _get_env("TRACKING_SPEAKING_MOUTH_OPEN_THRESHOLD"), 0.018
        ),
        tracking_jitter_deadzone_px=_to_float(
            _get_env("TRACKING_JITTER_DEADZONE_PX"), 2.0
        ),
        tracking_max_pan_speed_px_per_s=_to_float(
            _get_env("TRACKING_MAX_PAN_SPEED_PX_PER_S"), 900.0
        ),
        multiface_window_min_seconds=_to_float(
            _get_env("MULTIFACE_WINDOW_MIN_SECONDS"), 0.20
        ),
        multiface_window_merge_gap_seconds=_to_float(
            _get_env("MULTIFACE_WINDOW_MERGE_GAP_SECONDS"), 0.22
        ),
        multiface_window_padding_seconds=_to_float(
            _get_env("MULTIFACE_WINDOW_PADDING_SECONDS"), 0.35
        ),
        single_window_min_seconds=_to_float(
            _get_env("SINGLE_WINDOW_MIN_SECONDS"), 0.35
        ),
        single_window_merge_gap_seconds=_to_float(
            _get_env("SINGLE_WINDOW_MERGE_GAP_SECONDS"), 0.30
        ),
        single_window_padding_seconds=_to_float(
            _get_env("SINGLE_WINDOW_PADDING_SECONDS"), 0.30
        ),
        multiface_duo_split_threshold=_to_float(
            _get_env("MULTIFACE_DUO_SPLIT_THRESHOLD"), 0.22
        ),
        multiface_disable_zoom_threshold=_to_float(
            _get_env("MULTIFACE_DISABLE_ZOOM_THRESHOLD"), 0.14
        ),
        zoom_per_frame=_to_float(_get_env("ZOOM_PER_FRAME"), 0.001),
        zoom_max_scale=_to_float(_get_env("ZOOM_MAX_SCALE"), 1.18),
        zoom_min_duration_seconds=_to_float(_get_env("ZOOM_MIN_DURATION_SECONDS"), 8.0),
        emphasis_zoom_scale=_to_float(_get_env("EMPHASIS_ZOOM_SCALE"), 1.15),
        emphasis_zoom_duration_seconds=_to_float(
            _get_env("EMPHASIS_ZOOM_DURATION_SECONDS"), 0.9
        ),
        emphasis_zoom_max_events=_to_int(_get_env("EMPHASIS_ZOOM_MAX_EVENTS"), 6),
        topic_jump_zoom_scale=_to_float(_get_env("TOPIC_JUMP_ZOOM_SCALE"), 1.10),
        topic_jump_duration_seconds=_to_float(
            _get_env("TOPIC_JUMP_DURATION_SECONDS"), 0.45
        ),
        topic_jump_max_events=_to_int(_get_env("TOPIC_JUMP_MAX_EVENTS"), 4),
        static_pulse_zoom_scale=_to_float(_get_env("STATIC_PULSE_ZOOM_SCALE"), 1.10),
        static_pulse_interval_seconds=_to_float(
            _get_env("STATIC_PULSE_INTERVAL_SECONDS"), 4.0
        ),
        static_pulse_duration_seconds=_to_float(
            _get_env("STATIC_PULSE_DURATION_SECONDS"), 0.8
        ),
        static_motion_threshold=_to_float(_get_env("STATIC_MOTION_THRESHOLD"), 3.5),
        fit_scene_full_frame_mode=_to_bool(
            _get_env("FIT_SCENE_FULL_FRAME_MODE"), True
        ),
        background_video_path=_to_optional_str(_get_env("BACKGROUND_VIDEO_PATH")),
        background_fallback_mode=_get_env("BACKGROUND_FALLBACK_MODE", "blur"),
        top_banner_path=_to_optional_str(_get_env("TOP_BANNER_PATH")),
        bottom_logo_path=_to_optional_str(_get_env("BOTTOM_LOGO_PATH")),
        top_banner_margin=_to_int(_get_env("TOP_BANNER_MARGIN"), 20),
        bottom_logo_margin=_to_int(_get_env("BOTTOM_LOGO_MARGIN"), 24),
        top_banner_max_width=_to_int(_get_env("TOP_BANNER_MAX_WIDTH"), 1080),
        bottom_logo_max_width=_to_int(_get_env("BOTTOM_LOGO_MAX_WIDTH"), 320),
        contextual_overlay_chest_offset=_to_int(
            _get_env("CONTEXTUAL_OVERLAY_CHEST_OFFSET"), 400
        ),
        contextual_overlay_max_width=_to_int(
            _get_env("CONTEXTUAL_OVERLAY_MAX_WIDTH"), 420
        ),
        captions_enabled=_to_bool(_get_env("CAPTIONS_ENABLED"), True),
        captions_font=_get_env("CAPTIONS_FONT", "Arial-Bold"),
        captions_font_size=_to_int(_get_env("CAPTIONS_FONT_SIZE"), 64),
        captions_color=_get_env("CAPTIONS_COLOR", "#FFFFFF"),
        captions_active_word_color=_get_env("CAPTIONS_ACTIVE_WORD_COLOR", "#FFD400"),
        captions_stroke_color=_get_env("CAPTIONS_STROKE_COLOR", "#000000"),
        captions_stroke_width=_to_int(_get_env("CAPTIONS_STROKE_WIDTH"), 3),
        captions_position=_get_env("CAPTIONS_POSITION", "bottom"),
        captions_keyword_color_a=_get_env("CAPTIONS_KEYWORD_COLOR_A", "#FFD700"),
        captions_keyword_color_b=_get_env("CAPTIONS_KEYWORD_COLOR_B", "#00FF00"),
        captions_popup_peak_frames=_to_int(_get_env("CAPTIONS_POPUP_PEAK_FRAMES"), 3),
        captions_popup_settle_frames=_to_int(
            _get_env("CAPTIONS_POPUP_SETTLE_FRAMES"), 6
        ),
        captions_short_phrase_max_words=_to_int(
            _get_env("CAPTIONS_SHORT_PHRASE_MAX_WORDS"), 4
        ),
        captions_max_chars=_to_int(_get_env("CAPTIONS_MAX_CHARS"), 28),
        captions_keyword_glow_blur=_to_float(
            _get_env("CAPTIONS_KEYWORD_GLOW_BLUR"), 1.2
        ),
        captions_shadow=_to_float(_get_env("CAPTIONS_SHADOW"), 0.8),
        captions_hook_first_scale=_to_int(_get_env("CAPTIONS_HOOK_FIRST_SCALE"), 155),
        captions_hook_first_color=_get_env("CAPTIONS_HOOK_FIRST_COLOR", "#00FF00"),
        captions_burn_in_video=_to_bool(_get_env("CAPTIONS_BURN_IN_VIDEO"), True),
        captions_export_overlay_video=_to_bool(
            _get_env("CAPTIONS_EXPORT_OVERLAY_VIDEO"), False
        ),
        captions_overlay_video_codec=_get_env("CAPTIONS_OVERLAY_VIDEO_CODEC", "qtrle"),
        captions_overlay_video_ext=_get_env("CAPTIONS_OVERLAY_VIDEO_EXT", "mov"),
        ffmpeg_video_codec=_get_env("FFMPEG_VIDEO_CODEC", "h264_nvenc"),
        ffmpeg_nvenc_preset=_get_env("FFMPEG_NVENC_PRESET", "p6"),
        ffmpeg_video_bitrate=_get_env("FFMPEG_VIDEO_BITRATE", "8M"),
        ffmpeg_hwaccel=_get_env("FFMPEG_HWACCEL", "cuda"),
        upscaling_enabled=_to_bool(_get_env("UPSCALING_ENABLED"), False),
        upscaling_binary=_get_env(
            "UPSCALING_BINARY",
            "bin/realesrgan-ncnn-vulkan/realesrgan-ncnn-vulkan",
        ),
        upscaling_model_dir=_to_optional_str(_get_env("UPSCALING_MODEL_DIR"))
        or "bin/realesrgan-ncnn-vulkan/models",
        upscaling_model=_get_env("UPSCALING_MODEL", "realesr-general-x4v3"),
        upscaling_scale=_to_int(_get_env("UPSCALING_SCALE"), 2),
        upscaling_tile_size=_to_int(_get_env("UPSCALING_TILE_SIZE"), 128),
        upscaling_threads=_get_env("UPSCALING_THREADS", "1:2:2"),
        upscaling_gpu_id=_get_env("UPSCALING_GPU_ID", "auto"),
        upscaling_downscale_to_original=_to_bool(
            _get_env("UPSCALING_DOWNSCALE_TO_OUTPUT"), True
        ),
        upscaling_face_enhance=_to_bool(_get_env("UPSCALING_FACE_ENHANCE"), True),
        upscaling_face_model=_get_env("UPSCALING_FACE_MODEL", "gfpgan"),
        upscaling_codeformer_fidelity=_to_float(
            _get_env("UPSCALING_CODEFORMER_FIDELITY"), 0.75
        ),
        upscaling_face_only_codeformer=_to_bool(
            _get_env("UPSCALING_FACE_ONLY_CODEFORMER"), False
        ),
        upscaling_python_script=_to_optional_str(_get_env("UPSCALING_PYTHON_SCRIPT"))
        or "bin/Real-ESRGAN-0.3.0/inference_realesrgan_video.py",
        vignette_enabled=_to_bool(_get_env("VIGNETTE_ENABLED"), False),
        vignette_angle=_to_float(_get_env("VIGNETTE_ANGLE"), 0.5),
        progress_bar_enabled=_to_bool(_get_env("PROGRESS_BAR_ENABLED"), False),
        progress_bar_height=_to_int(_get_env("PROGRESS_BAR_HEIGHT"), 5),
        progress_bar_margin=_to_int(_get_env("PROGRESS_BAR_MARGIN"), 14),
        progress_bar_track_color=_get_env("PROGRESS_BAR_TRACK_COLOR", "#FFFFFF"),
        progress_bar_fill_color=_get_env("PROGRESS_BAR_FILL_COLOR", "#00FF00"),
        emoji_enabled=_to_bool(_get_env("EMOJI_ENABLED"), False),
        emoji_max_events=_to_int(_get_env("EMOJI_MAX_EVENTS"), 4),
        emoji_font_size=_to_int(_get_env("EMOJI_FONT_SIZE"), 42),
        render_parallel_jobs=_to_int(_get_env("RENDER_PARALLEL_JOBS"), 1),
        audio_normalization_enabled=_to_bool(
            _get_env("AUDIO_NORMALIZATION_ENABLED"), True
        ),
        audio_target_lufs=_to_float(_get_env("AUDIO_TARGET_LUFS"), -14.0),
        pipeline_profile=_to_int(_get_env("PIPELINE_PROFILE"), 3),
        yolo_img_size=_to_int(_get_env("YOLO_IMG_SIZE"), 640),
        yolo_chunk_size=_to_int(_get_env("YOLO_CHUNK_SIZE"), 16),
        silence_jump_cuts_enabled=_to_bool(_get_env("SILENCE_JUMP_CUTS_ENABLED"), False),
        silence_noise_db=_to_float(_get_env("SILENCE_NOISE_DB"), -40.0),
        silence_min_duration_seconds=_to_float(
            _get_env("SILENCE_MIN_DURATION_SECONDS"), 0.8
        ),
        silence_padding_seconds=_to_float(_get_env("SILENCE_PADDING_SECONDS"), 0.15),
        force_single_layout=_to_bool(_get_env("FORCE_SINGLE_LAYOUT"), True),
        speaker_lock_priority_enabled=_to_bool(
            _get_env("SPEAKER_LOCK_PRIORITY_ENABLED"), False
        ),
        speaker_interrupt_hold_seconds=_to_float(
            _get_env("SPEAKER_INTERRUPT_HOLD_SECONDS"), 1.5
        ),
        speaker_switch_min_seconds=_to_float(
            _get_env("SPEAKER_SWITCH_MIN_SECONDS"), 2.0
        ),
        ultra_crop_enabled=_to_bool(_get_env("ULTRA_CROP_ENABLED"), False),
        ultra_crop_face_width_factor=_to_float(
            _get_env("ULTRA_CROP_FACE_WIDTH_FACTOR"), 2.5
        ),
        ultra_crop_min_height_ratio=_to_float(
            _get_env("ULTRA_CROP_MIN_HEIGHT_RATIO"), 0.60
        ),
        captions_force_uppercase=_to_bool(_get_env("CAPTIONS_FORCE_UPPERCASE"), False),
        project_name=_get_env("PROJECT_NAME", "shorts_pipeline"),
        output_basename=_get_env("OUTPUT_BASENAME", "clip"),
        log_level=_get_env("LOG_LEVEL", "INFO"),
        keep_intermediate_files=_to_bool(_get_env("KEEP_INTERMEDIATE_FILES"), True),
        output_video_extension=_get_env("OUTPUT_VIDEO_EXTENSION", "mp4"),
        davinci_resolve_compatibility=_to_bool(_get_env("DAVINCI_RESOLVE_COMPATIBILITY"), False),
        pexels_api_key=_to_optional_str(_get_env("PEXELS_API_KEY")),
        youtube_api_key=_to_optional_str(_get_env("YOUTUBE_API_KEY")),
        youtube_client_id=_to_optional_str(_get_env("YOUTUBE_CLIENT_ID")),
        youtube_client_secret=_to_optional_str(_get_env("YOUTUBE_CLIENT_SECRET")),
        youtube_redirect_uri=_to_optional_str(_get_env("YOUTUBE_REDIRECT_URI")),
        tiktok_client_key=_to_optional_str(_get_env("TIKTOK_CLIENT_KEY")),
        tiktok_client_secret=_to_optional_str(_get_env("TIKTOK_CLIENT_SECRET")),
        tiktok_redirect_uri=_to_optional_str(_get_env("TIKTOK_REDIRECT_URI")),
        project_root=root,
        data_dir=(root / "data"),
        downloads_dir=(root / "data" / "downloads"),
        audio_dir=(root / "data" / "audio"),
        transcripts_dir=(root / "data" / "transcripts"),
        clips_dir=(root / "data" / "clips"),
        subtitles_dir=(
            root / (_to_optional_str(_get_env("SUBTITLES_DIR")) or "data/subtitles")
        ),
        temp_dir=(root / "data" / "tmp"),
    )

    cfg.validate()
    return cfg
