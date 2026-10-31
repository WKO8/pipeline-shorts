"""
Unit tests for Virality Analysis sub-project:
- Strict Schema Validation (Pydantic models)
- Video Metrics and Cut Calculations
- Senior Viral Strategist JSON Parser and Validator
- Flask API Endpoints
"""

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from src.models import Transcript, TranscriptSegment
from src.virality.models import (
    DropoffRisk,
    HookAnalysis,
    PacingAndRetention,
    ViralityAnalysisResult,
    ViralityAuditRequest,
)
from src.virality.metrics import ExtractedVideoData, VideoMetricsExtractor
from src.virality.strategist import ViralStrategist


def test_strict_schema_valid():
    """Verifies that a compliant dictionary parses successfully into ViralityAnalysisResult."""
    data = {
        "virality_score": 88,
        "hook_analysis": {
            "score": 92,
            "first_3_seconds_assessment": "Excelente padrão de quebra inicial com pergunta instigante.",
            "verbal_hook_rewrite": "Se você ainda faz isso ao editar, pare imediatamente.",
            "visual_hook_critique": "Iluminação frontal limpa e legenda amarela em destaque."
        },
        "pacing_and_retention": {
            "pacing_verdict": "Rápido",
            "dropoff_risks": [
                {
                    "timestamp_start": 4.5,
                    "timestamp_end": 7.0,
                    "reason": "Explicação redundante sobre o contexto inicial."
                }
            ]
        },
        "actionable_fixes": [
            "Cortar 1.5s de silêncio aos 5s",
            "Aumentar o zoom no momento do gancho",
            "Adicionar efeito sonoro de whoosh na troca de cena"
        ],
        "call_to_action_evaluation": "Final com loop infinito perfeito que liga a última palavra à primeira."
    }

    res = ViralityAnalysisResult(**data)
    assert res.virality_score == 88
    assert res.hook_analysis.score == 92
    assert len(res.pacing_and_retention.dropoff_risks) == 1
    assert res.pacing_and_retention.dropoff_risks[0].timestamp_start == 4.5
    assert len(res.actionable_fixes) == 3
    assert "loop" in res.call_to_action_evaluation


def test_strict_schema_invalid():
    """Verifies that invalid or out-of-range data raises ValidationError."""
    with pytest.raises(ValidationError):
        # virality_score fora do intervalo [0, 100]
        ViralityAnalysisResult(
            virality_score=150,
            hook_analysis=HookAnalysis(
                score=90,
                first_3_seconds_assessment="ok",
                verbal_hook_rewrite="ok",
                visual_hook_critique="ok"
            ),
            pacing_and_retention=PacingAndRetention(
                pacing_verdict="Rápido",
                dropoff_risks=[]
            ),
            actionable_fixes=[],
            call_to_action_evaluation="ok"
        )


def test_format_transcript_with_timestamps():
    """Verifies formatting of Whisper Transcript into timestamped lines."""
    extractor = VideoMetricsExtractor()
    t = Transcript(
        language="pt",
        segments=[
            TranscriptSegment(id=0, start=0.0, end=2.5, text="O segredo que ninguém te conta"),
            TranscriptSegment(id=1, start=2.5, end=5.8, text="sobre retenção nos vídeos curtos."),
        ]
    )
    formatted = extractor._format_transcript_with_timestamps(t)
    assert "[0.00s - 2.50s] O segredo que ninguém te conta" in formatted
    assert "[2.50s - 5.80s] sobre retenção nos vídeos curtos." in formatted


def test_clean_and_parse_json_with_markdown_fences():
    """Verifies that strategist cleanly extracts JSON wrapped in markdown code blocks."""
    strategist = ViralStrategist()
    wrapped_json = """Aqui está a análise solicitada:
```json
{
  "virality_score": 75,
  "hook_analysis": {
    "score": 80,
    "first_3_seconds_assessment": "Gancho forte",
    "verbal_hook_rewrite": "Você está perdendo visualizações por causa disso",
    "visual_hook_critique": "Enquadramento centralizado correto"
  },
  "pacing_and_retention": {
    "pacing_verdict": "Adequado",
    "dropoff_risks": []
  },
  "actionable_fixes": ["Acelerar corte"],
  "call_to_action_evaluation": "Estimula comentários"
}
```
Espero que ajude!"""

    parsed = strategist._clean_and_parse_json(wrapped_json)
    assert parsed["virality_score"] == 75
    assert parsed["hook_analysis"]["score"] == 80


def test_flask_api_routes(monkeypatch):
    """Tests the Flask API endpoints for virality routes."""
    import sys
    from ui.app import app

    client = app.test_client()

    # Test status for non-existent run
    res = client.get("/api/virality/status/run-99999999")
    assert res.status_code == 404

    # Test upload with invalid file extension
    import io
    data = {"video": (io.BytesIO(b"dummy"), "invalid.exe")}
    res = client.post("/api/virality/upload", data=data, content_type="multipart/form-data")
    assert res.status_code == 400
    assert "Envie um arquivo válido" in res.get_json()["error"]

    # Test list recent audits
    res = client.get("/api/virality/recent")
    assert res.status_code == 200
    assert isinstance(res.get_json(), list)


def test_security_headers_and_edge_blocking():
    """Valida hardening de cabeçalhos HTTP e bloqueio de arquivos sensíveis na borda."""
    from ui.app import app

    client = app.test_client()

    # Validação de bloqueio 404 imediato para arquivos sensíveis
    for blocked_path in ["/.env", "/.env.local", "/.git", "/.git/config", "/app.js.map", "/docker-compose.yml"]:
        res = client.get(blocked_path)
        assert res.status_code == 404, f"Esperado 404 para {blocked_path}, obteve {res.status_code}"

    # Validação de cabeçalhos de segurança em resposta normal
    res = client.get("/")
    assert res.status_code == 200
    assert res.headers.get("Strict-Transport-Security") == "max-age=63072000; includeSubDomains; preload"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in res.headers.get("Permissions-Policy", "")
    assert res.headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    assert res.headers.get("Cross-Origin-Resource-Policy") == "same-origin"
    assert "default-src 'self'" in res.headers.get("Content-Security-Policy", "")
    assert "Server" not in res.headers
    assert "X-Powered-By" not in res.headers

    # Validação do endpoint de health
    health_res = client.get("/api/health")
    assert health_res.status_code == 200
    assert health_res.get_json() == {"status": "ok"}
