"""
Pipeline Shorts — Flask UI Backend
Serves the web interface and proxies commands to main.py via subprocess.
"""

from __future__ import annotations

import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
UI_DIR = Path(__file__).parent.resolve()
ENV_FILE = PROJECT_ROOT / ".env"
DATA_DIR = PROJECT_ROOT / "data"
CLIPS_DIR = DATA_DIR / "clips"
SUBTITLES_DIR = DATA_DIR / "subtitles"
_venv_python = PROJECT_ROOT / ".venv" / "bin" / "python"
PYTHON_BIN = _venv_python if _venv_python.exists() else Path(sys.executable)

app = Flask(__name__, static_folder=str(UI_DIR), static_url_path="")


# ---------------------------------------------------------------------------
# Security & Edge Hardening
# ---------------------------------------------------------------------------
@app.before_request
def block_sensitive_paths():
    """Bloqueio imediato (404) para requisições a *.map, *.env*, *.git*, docker-compose*."""
    path = request.path.lower()
    if re.search(r'(^|/)(\.env|\.git|docker-compose)|\.map$', path):
        abort(404)


@app.after_request
def inject_security_headers(response: Response) -> Response:
    """Injeta cabeçalhos de segurança essenciais e remove cabeçalhos informativos."""
    response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: blob: https:; "
        "media-src 'self' blob: data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    # Remove Server e X-Powered-By
    response.headers.pop("Server", None)
    response.headers.pop("X-Powered-By", None)
    return response

# ---------------------------------------------------------------------------
# Process state (one process at a time)
# ---------------------------------------------------------------------------
_proc: subprocess.Popen | None = None
_log_queue: queue.Queue = queue.Queue()
_proc_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(UI_DIR, "index.html")


@app.route("/api/health", methods=["GET"])
def health_check():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# ENV API
# ---------------------------------------------------------------------------
@app.route("/api/env", methods=["GET"])
def get_env():
    try:
        content = ENV_FILE.read_text(encoding="utf-8") if ENV_FILE.exists() else ""
        return jsonify({"content": content, "path": str(ENV_FILE)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/env", methods=["POST"])
def save_env():
    try:
        data = request.get_json(force=True)
        content = data.get("content", "")
        ENV_FILE.write_text(content, encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Downloads Library
# ---------------------------------------------------------------------------
DOWNLOADS_DIR = DATA_DIR / "downloads"
AUDIO_DIR = DATA_DIR / "audio"


@app.route("/api/downloads")
def list_downloads():
    """List all .mp4 files in data/downloads/ for the local video library."""
    items = []
    if DOWNLOADS_DIR.exists():
        for p in sorted(sorted(list(DOWNLOADS_DIR.glob("*.mp4")) + list(DOWNLOADS_DIR.glob("*.mov"))), key=lambda x: x.stat().st_mtime, reverse=True):
            stem = p.stem
            stat = p.stat()
            # Look for a matching extracted audio file (wav preferred, then mp3)
            audio_path = ""
            for ext in ("wav", "mp3"):
                candidate = AUDIO_DIR / f"{stem}.{ext}"
                if candidate.exists():
                    audio_path = str(candidate)
                    break
            items.append({
                "name": p.name,
                "stem": stem,
                "abs": str(p),
                "audio_path": audio_path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            })
    return jsonify(items)


# ---------------------------------------------------------------------------
# Clips & Subtitles
# ---------------------------------------------------------------------------
@app.route("/api/clips")
def list_clips():
    clips = []
    if CLIPS_DIR.exists():
        for p in sorted(sorted(list(CLIPS_DIR.rglob("*.mp4")) + list(CLIPS_DIR.rglob("*.mov")))):
            # Ignore silent video-only stems and modular/ folder files
            if p.name.endswith("_video.mp4") or "modular" in p.parts or ".history" in p.parts:
                continue
            clips.append({
                "name": p.name,
                "path": str(p.relative_to(PROJECT_ROOT)),
                "abs": str(p),
                "size": p.stat().st_size,
                "mtime": p.stat().st_mtime,
            })
    return jsonify(clips)



@app.route("/api/subtitles")
def list_subtitles():
    subs = []
    for search_root in [SUBTITLES_DIR, DATA_DIR / "tmp"]:
        if search_root.exists():
            for p in sorted(list(search_root.rglob("*.ass")) + list(search_root.rglob("*.srt"))):
                subs.append({
                    "name": p.name,
                    "path": str(p.relative_to(PROJECT_ROOT)),
                    "abs": str(p),
                    "mtime": p.stat().st_mtime,
                })
    return jsonify(subs)


@app.route("/api/subtitle", methods=["POST"])
def get_subtitle():
    """Parse an .ass file and return events as JSON."""
    data = request.get_json(force=True)
    abs_path = data.get("abs", "")
    if not abs_path:
        return jsonify({"error": "No path"}), 400
    p = Path(abs_path)
    if not p.exists():
        return jsonify({"error": "File not found"}), 404
    try:
        raw = p.read_text(encoding="utf-8")
        events = _parse_ass_events(raw)
        return jsonify({"events": events, "raw": raw, "path": str(p)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/subtitle/save", methods=["POST"])
def save_subtitle():
    """Save edited ASS events back to file."""
    data = request.get_json(force=True)
    abs_path = data.get("abs", "")
    events = data.get("events", [])
    raw = data.get("raw", "")

    if not abs_path:
        return jsonify({"error": "No path"}), 400

    p = Path(abs_path)
    if not p.exists():
        return jsonify({"error": "File not found"}), 404

    try:
        if raw:
            # Rebuild: replace only Dialogue lines text field
            rebuilt = _rebuild_ass(raw, events)
            p.write_text(rebuilt, encoding="utf-8")
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------
@app.route("/api/run", methods=["POST"])
def run_pipeline():
    global _proc
    data = request.get_json(force=True)
    args_list = data.get("args", [])

    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return jsonify({"error": "A process is already running. Cancel it first."}), 409

        if "--yes" not in args_list and "-y" not in args_list:
            args_list = list(args_list) + ["--yes"]
        cmd = [str(PYTHON_BIN), str(PROJECT_ROOT / "main.py")] + args_list
        _log_queue.queue.clear()
        _log_queue.put({"type": "info", "text": f"$ {' '.join(cmd)}\n"})

        try:
            # Build environment for child process: inherit current env
            # but override with values from the project's .env file so
            # changes saved via the UI are respected by the pipeline.
            env_for_proc = {**os.environ, "PYTHONUNBUFFERED": "1"}
            if ENV_FILE.exists():
                for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
                    m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
                    if m:
                        env_for_proc[m.group(1)] = m.group(2).strip()

            _proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(PROJECT_ROOT),
                env=env_for_proc,
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        def _reader():
            global _proc
            for line in _proc.stdout:
                level = _classify_line(line)
                _log_queue.put({"type": level, "text": line})
            rc = _proc.wait()
            _log_queue.put({"type": "done", "text": f"\n[Process exited with code {rc}]\n", "code": rc})

        threading.Thread(target=_reader, daemon=True).start()

    return jsonify({"ok": True, "pid": _proc.pid})


@app.route("/api/stream")
def stream_logs():
    def generate():
        while True:
            try:
                msg = _log_queue.get(timeout=30)
                yield f"data: {json.dumps(msg)}\n\n"
                if msg.get("type") == "done":
                    break
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/cancel", methods=["POST"])
def cancel():
    global _proc
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            try:
                os.killpg(os.getpgid(_proc.pid), signal.SIGTERM)
            except Exception:
                _proc.terminate()
            return jsonify({"ok": True})
    return jsonify({"ok": False, "reason": "No running process"})


@app.route("/api/status")
def status():
    global _proc
    if _proc is None:
        return jsonify({"running": False})
    rc = _proc.poll()
    return jsonify({"running": rc is None, "returncode": rc, "pid": _proc.pid})


# ---------------------------------------------------------------------------
# YouTube Audio Download — shared helpers
# ---------------------------------------------------------------------------
def _load_ytdlp_env() -> dict[str, str]:
    """Parse .env and return key→value mapping (stripped, no comments)."""
    env: dict[str, str] = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
            if m:
                env[m.group(1)] = m.group(2).strip()
    return env


def _yt_cookie_args() -> list[str]:
    """Return yt-dlp cookie flags from .env if configured."""
    env = _load_ytdlp_env()
    if env.get("YTDLP_COOKIES_FROM_BROWSER"):
        return ["--cookies-from-browser", env["YTDLP_COOKIES_FROM_BROWSER"]]
    if env.get("YTDLP_COOKIES_FILE"):
        return ["--cookies", env["YTDLP_COOKIES_FILE"]]
    return []


def _yt_extractor_args() -> list[str]:
    """Return --extractor-args flag from .env if configured."""
    val = _load_ytdlp_env().get("YTDLP_EXTRACTOR_ARGS", "").strip()
    return ["--extractor-args", f"youtube:{val}"] if val else []


def _yt_js_runtime_args() -> list[str]:
    """
    Return --js-runtimes flag based on YTDLP_JS_RUNTIME in .env.
    Mirrors the logic in src/downloader.py so the same node binary is used.
    """
    import shutil
    runtime = _load_ytdlp_env().get("YTDLP_JS_RUNTIME", "").strip()
    if not runtime:
        return []
    # Auto-detect NVM node when value is bare "node"
    if runtime.lower() == "node" and ":" not in runtime:
        node_path = shutil.which("node")
        if not node_path:
            home = Path.home()
            for nvm_node in sorted(
                (home / ".nvm" / "versions" / "node").glob("*/bin/node"),
                reverse=True,
            ):
                if nvm_node.exists():
                    node_path = str(nvm_node)
                    break
        if node_path:
            runtime = f"node:{node_path}"
    return ["--js-runtimes", runtime] if runtime else []


def _slugify(text: str, max_len: int = 60) -> str:
    """Simple ASCII slug from a title string."""
    import unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len] or "audio"


@app.route("/api/audio-info", methods=["POST"])
def audio_info():
    """Fetch metadata for a YouTube URL without downloading media."""
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL obrigatória"}), 400

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--dump-single-json",
        "--no-playlist",
        *_yt_cookie_args(),
        *_yt_js_runtime_args(),
        *_yt_extractor_args(),
        url,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            err_lines = result.stderr.strip().splitlines()
            err_msg = err_lines[-1] if err_lines else "yt-dlp falhou"
            return jsonify({"error": err_msg}), 500
        info = json.loads(result.stdout)
        return jsonify({
            "title": info.get("title", ""),
            "uploader": info.get("uploader", ""),
            "duration": info.get("duration"),
            "slug": _slugify(info.get("title", "audio")),
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Timeout ao buscar metadados"}), 504
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/explorer/audio")
def list_audio_folder():
    """List files in data/audio/ for the converter."""
    items = []
    if AUDIO_DIR.exists():
        for p in sorted(AUDIO_DIR.glob("*")):
            if p.suffix.lower() in [".mp3", ".wav", ".ogg", ".flac", ".m4a"]:
                items.append({
                    "name": p.name,
                    "path": str(p.relative_to(PROJECT_ROOT)),
                })
    return jsonify(items)


@app.route("/api/convert", methods=["POST"])
def convert_media():
    """Convert video/audio using ffmpeg, streaming logs."""
    global _proc
    data = request.get_json(force=True)
    input_path = data.get("input_path", "").strip()
    out_format = data.get("format", "mp4").lower()
    out_name = data.get("output_name", "").strip()

    if not input_path:
        return jsonify({"error": "Caminho de entrada obrigatório"}), 400

    davinci = data.get("davinci", False)

    p_in = PROJECT_ROOT / input_path
    if not p_in.exists():
        p_in = Path(input_path) # try absolute
    if not p_in.exists():
        return jsonify({"error": f"Arquivo não encontrado: {input_path}"}), 404

    # Determine output path
    if out_name:
        if "." not in out_name:
            out_name = f"{out_name}.{out_format}"
        p_out = p_in.parent / out_name
    else:
        p_out = p_in.with_suffix(f".{out_format}")

    if p_in == p_out:
        p_out = p_in.parent / f"{p_in.stem}_converted.{out_format}"

    if davinci:
        cmd = [str(PYTHON_BIN), str(PROJECT_ROOT / "main.py"), "--convert-davinci", "--input", str(p_in), "--output", str(p_out)]
    elif out_format == "mp4":
        cmd = ["ffmpeg", "-y", "-i", str(p_in), "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-c:a", "aac", str(p_out)]
    elif out_format == "mov":
        cmd = ["ffmpeg", "-y", "-i", str(p_in), "-c:v", "libx264", "-c:a", "aac", str(p_out)]
    elif out_format == "webm":
        cmd = ["ffmpeg", "-y", "-i", str(p_in), "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0", "-c:a", "libopus", str(p_out)]
    elif out_format == "mp3":
        cmd = ["ffmpeg", "-y", "-i", str(p_in), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(p_out)]
    elif out_format == "wav":
        cmd = ["ffmpeg", "-y", "-i", str(p_in), "-vn", "-acodec", "pcm_s16le", str(p_out)]
    else:
        cmd = ["ffmpeg", "-y", "-i", str(p_in), str(p_out)]

    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return jsonify({"error": "Um processo já está em execução."}), 409

        _log_queue.queue.clear()
        _log_queue.put({"type": "info", "text": f"🔄 Convertendo: {p_in.name} -> {out_format}\n"})
        _log_queue.put({"type": "info", "text": f"$ {' '.join(cmd)}\n"})

        try:
            _proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        def _reader():
            global _proc
            for line in _proc.stdout:
                level = _classify_line(line)
                _log_queue.put({"type": level, "text": line})
            rc = _proc.wait()
            _log_queue.put({
                "type": "done",
                "text": f"\n[Conversão finalizada com código {rc}]\n","code": rc,
            })

        threading.Thread(target=_reader, daemon=True).start()

    return jsonify({"ok": True, "pid": _proc.pid, "output": str(p_out)})



@app.route("/api/download-audio", methods=["POST"])
def download_audio():
    """Download audio-only from YouTube, convert with ffmpeg, stream logs via SSE."""
    global _proc
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL obrigatória"}), 400

    fmt       = data.get("format", "mp3").lower()
    quality   = str(data.get("quality", "192"))
    slug      = (data.get("slug") or "").strip()
    normalize = bool(data.get("normalize", False))
    mono      = bool(data.get("mono", True))

    AUDIO_OUT_DIR = DATA_DIR / "audio"
    AUDIO_OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not slug:
        slug = f"audio_{int(time.time())}"

    output_path = AUDIO_OUT_DIR / f"{slug}.{fmt}"

    # Map format → yt-dlp postprocessor flags
    fmt_map = {
        "mp3": ["--extract-audio", "--audio-format", "mp3", "--audio-quality", quality + "k"],
        "wav": ["--extract-audio", "--audio-format", "wav"],
        "ogg": ["--extract-audio", "--audio-format", "vorbis"],
        "flac": ["--extract-audio", "--audio-format", "flac"],
        "m4a": ["--extract-audio", "--audio-format", "m4a"],
    }
    postproc_args = fmt_map.get(fmt, fmt_map["mp3"])

    tmp_template = str(AUDIO_OUT_DIR / f"{slug}.%(ext)s")

    # Broad format selector: prefer webm/opus or m4a/aac natively, then any audio,
    # finally fall back to any best stream (yt-dlp will extract audio from it).
    # This avoids "format not available" with clients that expose limited formats.
    AUDIO_FORMAT_SEL = (
        "bestaudio[ext=webm]/bestaudio[ext=m4a]/bestaudio[ext=opus]/"
        "bestaudio/best[ext=mp4]/best"
    )

    cmd = [
        "yt-dlp",
        "--no-warnings",
        "--no-playlist",
        "--force-overwrites",
        "-f", AUDIO_FORMAT_SEL,
        "-o", tmp_template,
        *postproc_args,
        *_yt_cookie_args(),
        *_yt_js_runtime_args(),
        *_yt_extractor_args(),
        url,
    ]

    needs_ffmpeg_post = mono or normalize

    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return jsonify({"error": "Um processo já está em execução."}), 409

        _log_queue.queue.clear()
        _log_queue.put({"type": "info", "text": f"🎵 Baixando áudio: {url}\n"})
        _log_queue.put({"type": "info", "text": f"$ {' '.join(cmd)}\n"})

        try:
            _proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(PROJECT_ROOT),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

        def _reader():
            global _proc
            for line in _proc.stdout:
                level = _classify_line(line)
                _log_queue.put({"type": level, "text": line})
            rc = _proc.wait()

            # Optional mono / loudnorm post-processing via ffmpeg
            if rc == 0 and needs_ffmpeg_post:
                candidates = sorted(AUDIO_OUT_DIR.glob(f"{slug}.*"))
                if candidates:
                    src = candidates[-1]
                    pp_out = AUDIO_OUT_DIR / f"{slug}_pp{src.suffix}"
                    ff_cmd = ["ffmpeg", "-y", "-i", str(src)]
                    if mono:
                        ff_cmd += ["-ac", "1"]
                    if normalize:
                        ff_cmd += ["-af", "loudnorm"]
                    ff_cmd.append(str(pp_out))
                    _log_queue.put({"type": "info", "text": f"\n🔧 Pós-processamento: {' '.join(ff_cmd)}\n"})
                    pp = subprocess.run(ff_cmd, capture_output=True, text=True)
                    if pp.returncode == 0:
                        # Preserve source and keep the processed file beside it.
                        _log_queue.put({"type": "info", "text": f"Arquivo processado: {pp_out}\n"})
                        _log_queue.put({"type": "info", "text": "✓ Pós-processamento concluído\n"})
                    else:
                        _log_queue.put({"type": "error", "text": pp.stderr or "ffmpeg falhou\n"})

            _log_queue.put({
                "type": "done",
                "text": f"\n[Process exited with code {rc}]\n",
                "code": rc,
            })

        threading.Thread(target=_reader, daemon=True).start()

    return jsonify({"ok": True, "pid": _proc.pid, "result_path": str(output_path), "title": slug})


# ---------------------------------------------------------------------------
# ASS parser / rebuilder helpers
# ---------------------------------------------------------------------------
_DIALOGUE_RE = re.compile(
    r"^(Dialogue:\s*\d+,)"           # prefix
    r"(\d+:\d{2}:\d{2}\.\d{2}),"    # start
    r"(\d+:\d{2}:\d{2}\.\d{2}),"    # end
    r"([^,]*),"                       # style
    r"([^,]*),"                       # name
    r"([^,]*),"                       # marginL
    r"([^,]*),"                       # marginR
    r"([^,]*),"                       # marginV
    r"([^,]*),"                       # effect
    r"(.*)$",                         # text
    re.MULTILINE,
)


def _parse_ass_events(raw: str) -> list[dict]:
    events = []
    idx = 0
    for m in _DIALOGUE_RE.finditer(raw):
        events.append({
            "idx": idx,
            "start": m.group(2),
            "end": m.group(3),
            "style": m.group(4).strip(),
            "text": m.group(10),
        })
        idx += 1
    return events


def _rebuild_ass(raw: str, events: list[dict]) -> str:
    """Replace Dialogue text fields in raw ASS with edited versions."""
    event_map = {e["idx"]: e["text"] for e in events}
    idx = 0
    result = []
    last_end = 0

    for m in _DIALOGUE_RE.finditer(raw):
        result.append(raw[last_end:m.start(10)])
        new_text = event_map.get(idx, m.group(10))
        result.append(new_text)
        last_end = m.end(10)
        idx += 1

    result.append(raw[last_end:])
    return "".join(result)


def _classify_line(line: str) -> str:
    """Assign a log level (type) based on ffmpeg/yt-dlp output patterns."""
    l = line.lower()
    if "progress:" in l:
        return "progress"
    if "error" in l or "failed" in l:
        return "error"
    if "warning" in l:
        return "warning"
    if "frame=" in l or "size=" in l or "time=" in l:
        return "progress"
    return "info"


sys.path.insert(0, str(PROJECT_ROOT))
from ui.vertical_routes import register_vertical
register_vertical(app, sys.modules[__name__])
from ui.virality_routes import register_virality
register_virality(app, sys.modules[__name__])

from ui.vsf_produce_routes import register_vsf_produce
register_vsf_produce(app, sys.modules[__name__])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "7860"))
    print(f"🎬 Pipeline Shorts UI → http://{host}:{port}")
    print(f"   Project: {PROJECT_ROOT}")
    app.run(host=host, port=port, debug=False, threaded=True)
