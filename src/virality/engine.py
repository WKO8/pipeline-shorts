"""
Virality Engine: Orchestrates end-to-end audit process.
Connects metrics extractor, platform fetchers, strategist AI, and run storage.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from ..config import AppConfig, load_config
from ..utils import new_run_dir
from .metrics import VideoMetricsExtractor
from .models import ViralityAnalysisResult, ViralityAuditRequest
from .platforms import download_video_for_audit
from .strategist import ViralStrategist

LOGGER = logging.getLogger(__name__)

AUDITS_DIR = Path("data/virality_audits").resolve()


class ViralityEngine:
    """
    Unified entry point for conducting virality audits.
    """

    def __init__(self, config: Optional[AppConfig] = None, audits_dir: Optional[Path] = None):
        self.config = config or load_config()
        self.audits_dir = (audits_dir or AUDITS_DIR).resolve()
        self.audits_dir.mkdir(parents=True, exist_ok=True)
        self.extractor = VideoMetricsExtractor(self.config)
        self.strategist = ViralStrategist(self.config)

    def run_audit(
        self,
        audit_req: ViralityAuditRequest,
        progress_callback: Optional[callable] = None,
        run_folder: Optional[Path] = None,
    ) -> tuple[str, ViralityAnalysisResult]:
        """
        Executes a complete audit run. Returns (run_id, ViralityAnalysisResult).
        """
        if run_folder is None:
            run_folder = new_run_dir(self.audits_dir).resolve()
        else:
            run_folder = Path(run_folder).resolve()
            run_folder.mkdir(parents=True, exist_ok=True)

        run_id = run_folder.name
        frames_dir = (run_folder / "frames").resolve()

        LOGGER.info("[%s] Iniciando Auditoria Viral para: %s", run_id, audit_req.video_source)
        if progress_callback:
            progress_callback("preparando", "Preparando ambiente e vídeo...")

        # 1. Obter arquivo de vídeo
        video_source = audit_req.video_source.strip()
        if video_source.startswith("http://") or video_source.startswith("https://"):
            if progress_callback:
                progress_callback("downloading", "Baixando vídeo da plataforma (YouTube / TikTok)...")
            video_path = download_video_for_audit(video_source, run_folder)
        else:
            video_path = Path(video_source).resolve()
            if not video_path.exists():
                raise FileNotFoundError(f"Vídeo de entrada não encontrado: {video_path}")
            # Garante presença de audit_video na pasta da run para streaming imediato
            dest_video = run_folder / f"audit_video{video_path.suffix.lower()}"
            if video_path != dest_video and not dest_video.exists():
                import shutil
                try:
                    shutil.copy2(video_path, dest_video)
                except Exception:
                    pass

        # Salva a requisição
        req_file = run_folder / "request.json"
        req_file.write_text(audit_req.model_dump_json(indent=2), encoding="utf-8")

        # 2. Extração técnica (Áudio + Whisper + Cortes + Frames + OpenCV)
        if progress_callback:
            progress_callback("extracting", "Extraindo métricas audiovisuais, cortes de cena e frames 0-3s...")
        extracted_data = self.extractor.extract_all(
            video_path=video_path,
            output_frames_dir=frames_dir,
        )

        # 3. Auditoria pelo Estrategista de IA
        if progress_callback:
            progress_callback("analyzing", "Estrategista Sênior de Conteúdo Viral analisando retenção e ganchos...")
        result = self.strategist.audit(
            extracted=extracted_data,
            audit_req=audit_req,
        )

        # 4. Salva relatório completo
        report_file = run_folder / "audit_report.json"
        report_file.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        if progress_callback:
            progress_callback("done", "Auditoria concluída com sucesso!")

        LOGGER.info("[%s] Auditoria concluída. Virality Score: %d/100", run_id, result.virality_score)
        return run_id, result

    def get_audit_result(self, run_id: str) -> Optional[dict[str, Any]]:
        """
        Retrieves stored audit result for a given run_id.
        """
        report_file = self.audits_dir / run_id / "audit_report.json"
        if not report_file.exists():
            return None
        return json.loads(report_file.read_text(encoding="utf-8"))

    def list_recent_audits(self, limit: int = 20) -> list[dict[str, Any]]:
        """
        Lists recent audits with basic summary for dashboard view.
        """
        results = []
        if not self.audits_dir.exists():
            return []

        runs = sorted(self.audits_dir.glob("run-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for r in runs[:limit]:
            report_file = r / "audit_report.json"
            req_file = r / "request.json"
            if report_file.exists():
                try:
                    rep = json.loads(report_file.read_text(encoding="utf-8"))
                    req = json.loads(req_file.read_text(encoding="utf-8")) if req_file.exists() else {}
                    results.append({
                        "run_id": r.name,
                        "created_at": r.stat().st_mtime,
                        "virality_score": rep.get("virality_score"),
                        "hook_score": rep.get("hook_analysis", {}).get("score"),
                        "pacing": rep.get("pacing_and_retention", {}).get("pacing_verdict"),
                        "video_source": req.get("video_source", ""),
                        "niche": req.get("niche", "Geral"),
                    })
                except Exception:
                    continue
        return results
