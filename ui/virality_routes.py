"""
Flask routes for Virality Analysis.
Manages video upload, async audits, frame serving, and YouTube/TikTok channel queries.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request, send_from_directory
from werkzeug.exceptions import BadRequest

from src.config import AppConfig, load_config
from src.utils import new_run_dir
from src.virality.engine import AUDITS_DIR, ViralityEngine
from src.virality.models import ViralityAuditRequest
from src.virality.platforms import TikTokPlatform, YouTubePlatform

LOGGER = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}

# Gerenciamento de tarefas em background
_audit_progress: dict[str, dict[str, Any]] = {}
_progress_lock = threading.Lock()


def register_virality(app, backend):
    bp = Blueprint("virality", __name__, url_prefix="/api/virality")
    root = backend.PROJECT_ROOT
    uploads = (backend.DATA_DIR / "virality_uploads").resolve()
    uploads.mkdir(parents=True, exist_ok=True)
    audits_dir = (backend.DATA_DIR / "virality_audits").resolve()
    audits_dir.mkdir(parents=True, exist_ok=True)
    engine = ViralityEngine(load_config(), audits_dir)

    @bp.post("/upload")
    def upload_video():
        """Upload a finished edited video file for virality auditing."""
        request.max_content_length = 2 * 1024**3  # 2 GiB
        file = request.files.get("video")
        if not file or Path(file.filename or "").suffix.lower() not in VIDEO_EXTENSIONS:
            return jsonify(error="Envie um arquivo válido (MP4, MOV, MKV ou WebM)."), 400

        run_dir = new_run_dir(uploads).resolve()
        file_path = run_dir / ("video" + Path(file.filename).suffix.lower())
        file.save(file_path)
        return jsonify(ok=True, video_path=str(file_path), filename=file.filename)

    @bp.post("/audit")
    def start_audit():
        """Starts a virality audit asynchronously."""
        data = request.get_json(force=True) or {}
        video_source = data.get("video_source", "").strip()
        if not video_source:
            return jsonify(error="Fonte de vídeo não informada."), 400

        audit_req = ViralityAuditRequest(
            video_source=video_source,
            niche=data.get("niche", "Podcast / Cortes"),
            target_audience=data.get("target_audience", "Geral"),
            account_size=data.get("account_size", "pequena"),
            platform_target=data.get("platform_target", "ambos"),
            provider=data.get("provider", None),
        )

        run_folder = new_run_dir(audits_dir).resolve()
        run_id = run_folder.name

        with _progress_lock:
            _audit_progress[run_id] = {
                "status": "in_progress",
                "step": "iniciando",
                "message": "Iniciando processo de auditoria...",
                "result": None,
                "error": None,
            }

        def worker():
            def on_progress(step: str, msg: str):
                with _progress_lock:
                    if run_id in _audit_progress:
                        _audit_progress[run_id]["step"] = step
                        _audit_progress[run_id]["message"] = msg

            try:
                # Instancia engine para a run com configuracoes do .env
                sub_engine = ViralityEngine(load_config(), audits_dir)
                # Executa garantindo reutilizacao da pasta criada
                _, res = sub_engine.run_audit(audit_req, progress_callback=on_progress, run_folder=run_folder)
                with _progress_lock:
                    _audit_progress[run_id]["status"] = "done"
                    _audit_progress[run_id]["message"] = "Auditoria concluída!"
                    _audit_progress[run_id]["result"] = res.model_dump()
            except Exception as exc:
                LOGGER.exception("Erro na auditoria %s", run_id)
                with _progress_lock:
                    _audit_progress[run_id]["status"] = "error"
                    _audit_progress[run_id]["error"] = str(exc)

        threading.Thread(target=worker, daemon=True).start()
        return jsonify(ok=True, run_id=run_id)

    @bp.get("/status/<run_id>")
    def get_status(run_id: str):
        """Returns progress or stored result for a run."""
        with _progress_lock:
            state = _audit_progress.get(run_id)

        if state:
            return jsonify(state)

        # Se não estiver na memória, busca no disco
        saved = engine.get_audit_result(run_id)
        if saved:
            return jsonify({
                "status": "done",
                "step": "done",
                "message": "Auditoria carregada do histórico.",
                "result": saved,
                "error": None,
            })

        return jsonify(error="Auditoria não encontrada"), 404

    @bp.get("/frames/<run_id>/<filename>")
    def get_frame(run_id: str, filename: str):
        """Serves sampled frame images."""
        frames_dir = (audits_dir / run_id / "frames").resolve()
        if not frames_dir.exists():
            for r in audits_dir.glob(f"*{run_id}*"):
                alt_dir = (r / "frames").resolve()
                if (alt_dir / filename).exists():
                    return send_from_directory(alt_dir, filename)
            return jsonify(error="Frames não encontrados"), 404
        return send_from_directory(frames_dir, filename)

    @bp.get("/video/<run_id>")
    def get_video(run_id: str):
        """Serves video file if present in the run folder."""
        run_folder = (audits_dir / run_id).resolve()
        if not run_folder.exists():
            for r in audits_dir.glob(f"*{run_id}*"):
                if r.is_dir():
                    run_folder = r.resolve()
                    break

        if run_folder.exists():
            for ext in (".mp4", ".mov", ".webm", ".mkv"):
                candidate = run_folder / f"audit_video{ext}"
                if candidate.exists():
                    return send_from_directory(run_folder, candidate.name)

            # Se for um upload salvo
            req_file = run_folder / "request.json"
            if req_file.exists():
                try:
                    src = json.loads(req_file.read_text())["video_source"]
                    p = Path(src).resolve()
                    if p.exists():
                        return send_from_directory(p.parent.resolve(), p.name)
                except Exception:
                    pass

        return jsonify(error="Vídeo não disponível para stream"), 404

    @bp.get("/recent")
    def get_recent():
        """Lists recent audits."""
        return jsonify(engine.list_recent_audits())

    # --- YouTube Endpoints ---
    @bp.get("/youtube/videos")
    def get_youtube_videos():
        channel_id = request.args.get("channel_id")
        yt = YouTubePlatform(load_config())
        videos = yt.list_videos(channel_id=channel_id, max_results=12)
        return jsonify([v.model_dump() for v in videos])

    # --- TikTok Endpoints ---
    @bp.get("/tiktok/videos")
    def get_tiktok_videos():
        profile = request.args.get("profile", "")
        if not profile:
            return jsonify(error="Informe a URL ou @username do TikTok."), 400
        tt = TikTokPlatform(load_config())
        videos = tt.list_profile_videos_via_ytdlp(profile, max_results=10)
        return jsonify([v.model_dump() for v in videos])

    app.register_blueprint(bp)
    LOGGER.info("Blueprint /api/virality registrado com sucesso.")
