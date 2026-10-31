from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional, List

LOGGER = logging.getLogger(__name__)


# ---------------------------
# Filesystem helpers
# ---------------------------


def ensure_dir(path: str | Path) -> Path:
    """Create directory (and parents) if missing and return Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_parent_dir(path: str | Path) -> Path:
    """Ensure a file's parent directory exists and return the file Path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def safe_unlink(path: str | Path) -> bool:
    """Automatic deletion is disabled; cleanup requires a separate user action."""
    LOGGER.debug("Preserving file: %s", path)
    return False


def safe_rmtree(path: str | Path) -> bool:
    """Automatic directory deletion is disabled."""
    LOGGER.debug("Preserving directory: %s", path)
    return False


def new_run_dir(parent: str | Path) -> Path:
    from datetime import datetime, timezone
    from uuid import uuid4
    path = Path(parent) / (datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%S-") + uuid4().hex[:8])
    path.mkdir(parents=True, exist_ok=False)
    return path


def preserve_existing(path: str | Path) -> None:
    """Keep a byte-for-byte revision before replacing a generated artifact."""
    from uuid import uuid4
    path = Path(path)
    if path.is_file():
        history = path.parent / ".history"
        history.mkdir(exist_ok=True)
        shutil.copy2(path, history / f"{path.stem}.{uuid4().hex}{path.suffix}")


def write_text(path: str | Path, content: str, encoding: str = "utf-8") -> Path:
    """Write text content ensuring parent directories."""
    p = ensure_parent_dir(path)
    p.write_text(content, encoding=encoding)
    return p


def _json_default(obj: Any) -> Any:
    """Handle numpy types, Path, and other non-standard objects during JSON serialization."""
    if hasattr(obj, "tolist"):
        return obj.tolist()
    if hasattr(obj, "item"):
        return obj.item()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def write_json(path: str | Path, payload: Any, indent: int = 2) -> Path:
    """Write JSON, preserving any prior version in a sibling .history directory."""
    p = ensure_parent_dir(path)
    preserve_existing(p)
    p.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=indent,
            default=_json_default,
        ),
        encoding="utf-8",
    )
    return p


def read_json(path: str | Path, default: Any = None) -> Any:
    """Read JSON file. Return default if file does not exist."""
    p = Path(path)
    if not p.exists():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def list_files(
    path: str | Path, pattern: str = "*", recursive: bool = False
) -> list[Path]:
    """List files in a directory matching a pattern."""
    p = Path(path)
    if not p.exists():
        return []
    globber = p.rglob if recursive else p.glob
    return sorted([x for x in globber(pattern) if x.is_file()])


def file_stem_sanitized(name: str, max_len: int = 80) -> str:
    """
    Sanitize arbitrary text for safe filename usage.
    Keeps letters, numbers, dash, underscore.
    """
    value = name.strip().lower()
    value = re.sub(r"\s+", "_", value)
    value = re.sub(r"[^\w\-]+", "", value, flags=re.UNICODE)
    value = value.strip("._-")
    if not value:
        value = "file"
    return value[:max_len]


def resolve_path(path: str | Path, base_dir: Optional[str | Path] = None) -> Path:
    """Resolve path against base_dir if not absolute."""
    p = Path(path)
    if p.is_absolute():
        return p
    if base_dir is None:
        return p.resolve()
    return (Path(base_dir) / p).resolve()


# ---------------------------
# Time helpers
# ---------------------------


def seconds_to_hhmmss(seconds: float, with_ms: bool = False) -> str:
    """Convert float seconds to HH:MM:SS or HH:MM:SS.mmm."""
    if math.isnan(seconds) or math.isinf(seconds):
        seconds = 0.0
    total_ms = max(0, int(round(seconds * 1000)))
    hh = total_ms // 3_600_000
    rem = total_ms % 3_600_000
    mm = rem // 60_000
    rem %= 60_000
    ss = rem // 1000
    ms = rem % 1000
    if with_ms:
        return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"
    return f"{hh:02d}:{mm:02d}:{ss:02d}"


def hhmmss_to_seconds(value: str) -> float:
    """
    Parse 'HH:MM:SS', 'MM:SS', or with decimals (e.g. '00:01:02.500') into seconds.
    """
    value = value.strip()
    if not value:
        return 0.0

    parts = value.split(":")
    if len(parts) == 3:
        h, m, s = parts
    elif len(parts) == 2:
        h, m, s = "0", parts[0], parts[1]
    elif len(parts) == 1:
        return float(parts[0])
    else:
        raise ValueError(f"Invalid time format: {value}")

    return int(h) * 3600 + int(m) * 60 + float(s)


def clamp(value: float, min_value: float, max_value: float) -> float:
    """Clamp value to [min_value, max_value]."""
    return max(min_value, min(value, max_value))


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    """Return True if time intervals overlap."""
    return max(a_start, b_start) < min(a_end, b_end)


def duration(start: float, end: float) -> float:
    """Safe duration calc."""
    return max(0.0, end - start)


# ---------------------------
# Process / command helpers
# ---------------------------


@dataclass(slots=True)
class CommandResult:
    cmd: list[str]
    returncode: int
    stdout: str
    stderr: str
    elapsed_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_cmd(
    cmd: list[str],
    cwd: Optional[str | Path] = None,
    env: Optional[dict[str, str]] = None,
    check: bool = True,
    timeout: Optional[int] = None,
) -> CommandResult:
    """
    Run a subprocess command and capture stdout/stderr with timing.
    """
    t0 = time.perf_counter()
    LOGGER.debug("Running command: %s", " ".join(cmd))

    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    elapsed = time.perf_counter() - t0

    result = CommandResult(
        cmd=cmd,
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
        elapsed_s=elapsed,
    )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}) after {elapsed:.2f}s: {' '.join(cmd)}\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )
    return result


def which(binary: str) -> Optional[str]:
    """Return path to executable if available, otherwise None. Also checks sys.executable parent dir."""
    import sys
    venv_dir = Path(sys.executable).parent
    venv_binary = venv_dir / binary
    if venv_binary.exists() and os.access(venv_binary, os.X_OK):
        return str(venv_binary)
    return shutil.which(binary)


def require_binaries(binaries: Iterable[str]) -> None:
    """Raise RuntimeError if any binary is missing from PATH."""
    missing = [b for b in binaries if which(b) is None]
    if missing:
        raise RuntimeError(
            "Missing required binaries in PATH: "
            + ", ".join(missing)
            + ". Install them before running the pipeline."
        )


# ---------------------------
# Logging / misc helpers
# ---------------------------


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging once using RichHandler."""
    from rich.logging import RichHandler
    from .console_ui import console

    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=console,
                rich_tracebacks=True,
                show_time=True,
                show_path=False,
                markup=False,
            )
        ],
        force=True,
    )

    # Keep optional Hub file probes available in DEBUG.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.DEBUG if numeric <= logging.DEBUG else max(numeric, logging.WARNING))


def chunks(items: list[Any], size: int) -> Iterator[list[Any]]:
    """Yield list chunks of `size`."""
    if size <= 0:
        raise ValueError("size must be > 0")
    for i in range(0, len(items), size):
        yield items[i : i + size]


def flatten(nested: Iterable[Iterable[Any]]) -> list[Any]:
    """Flatten one nesting level."""
    return [x for group in nested for x in group]


def parallel_convert_davinci(
    input_path: Path,
    output_path: Path,
    num_chunks: Optional[int] = None,
    temp_dir_base: Optional[Path] = None,
) -> None:
    """
    Convert a video to DaVinci Resolve compatibility format (DNxHR + PCM)
    by splitting it into chunks and converting them in parallel.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # 1. Probe duration
    probe_cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=nw=1:nk=1",
        str(input_path),
    ]
    probe = run_cmd(probe_cmd, check=True)
    try:
        duration = float(probe.stdout.strip())
    except (ValueError, TypeError):
        raise RuntimeError(f"Could not determine duration of {input_path}")

    if duration <= 0:
        raise RuntimeError(f"Video duration is zero: {input_path}")

    # 2. Determine chunks
    if num_chunks is None:
        num_chunks = os.cpu_count() or 4
    
    # Don"t chunk very short videos
    if duration < 10:
        num_chunks = 1
    elif duration < 60:
        num_chunks = min(num_chunks, 2)
    
    chunk_duration = duration / num_chunks
    
    # 3. Prepare temp directory
    if temp_dir_base is None:
        temp_dir_base = input_path.parent / "tmp"
    
    work_dir = temp_dir_base / f"davinci_conv_{int(time.time())}"
    work_dir.mkdir(parents=True, exist_ok=True)
    
    LOGGER.info(
        "Starting parallel DaVinci conversion: %s -> %s (%d chunks, %.2fs each)",
        input_path.name,
        output_path.name,
        num_chunks,
        chunk_duration,
    )
    input_size_gb = input_path.stat().st_size / (1024**3)
    print(f"INFO: Arquivo original: {input_size_gb:.2f} GB", flush=True)
    print(f"INFO: Video dividido em {num_chunks} partes de aproximadamente {chunk_duration:.2f}s cada.", flush=True)
    # DNxHR HQ is roughly 1GB per minute at 1080p60
    est_size_gb = (duration / 60.0) * 1.0 
    print(f"INFO: Tamanho estimado do arquivo final (DNxHR): ~{est_size_gb:.2f} GB", flush=True)

    chunk_files: List[Path] = []
    completed_chunks = 0
    import threading
    progress_lock = threading.Lock()
    
    def _convert_chunk(idx: int) -> Path:
        nonlocal completed_chunks
        start = idx * chunk_duration
        # Last chunk gets the rest to avoid rounding issues
        is_last = idx == num_chunks - 1
        
        chunk_out = work_dir / f"chunk_{idx:03d}.mov"
        
        # Base command
        # Use fast seek (-ss before -i) for speed
        cmd = ["ffmpeg", "-y", "-ss", f"{start:.3f}"]
        if not is_last:
            cmd += ["-t", f"{chunk_duration:.3f}"]
            
        cmd += [
            "-i", str(input_path),
            "-c:v", "dnxhd",
            "-profile:v", "dnxhr_hq",
            "-pix_fmt", "yuv422p",
            "-c:a", "pcm_s16le",
            str(chunk_out)
        ]
        
        # We don"t log all ffmpeg output to keep progress clean
        run_cmd(cmd, check=True)
        
        with progress_lock:
            completed_chunks += 1
            percentage = (completed_chunks / num_chunks) * 100
            # Print to stdout directly so ui/app.py can capture it immediately
            print(f"PROGRESS: {percentage:.1f}% ({completed_chunks}/{num_chunks} chunks completed)", flush=True)
            LOGGER.info("Progress: %.1f%% (%d/%d chunks)", percentage, completed_chunks, num_chunks)
            
        return chunk_out

    # 4. Execute parallel conversion
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=num_chunks) as pool:
            chunk_files = list(pool.map(_convert_chunk, range(num_chunks)))
        
        # 5. Concatenate
        print("PROGRESS: 95.0% (Merging chunks...)", flush=True)
        LOGGER.info("Merging chunks...")
        concat_list_path = work_dir / "chunks.txt"
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for cf in chunk_files:
                f.write(f"file '{cf.name}'\n")
        
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list_path),
            "-c", "copy",
            str(output_path)
        ]
        run_cmd(concat_cmd, check=True)
        output_size_gb = output_path.stat().st_size / (1024**3)
        print(f"PROGRESS: 100.0% (Finalizado! Tamanho: {output_size_gb:.2f} GB)", flush=True)
        LOGGER.info("Parallel conversion finished successfully.")
        
    finally:
        # 6. Cleanup
        if work_dir.exists():
            import shutil
            LOGGER.info("Preserved conversion intermediates: %s", work_dir)


def extract_audio(video_path: str | Path, audio_path: str | Path) -> None:
    """Extracts 16kHz mono audio from video file for Whisper transcription."""
    target = Path(audio_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        str(target),
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
