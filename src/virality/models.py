"""
Data models and schemas for Virality Analysis.
Strict JSON schema compliance as requested by the Senior Viral Strategist specification.
"""

from __future__ import annotations

from typing import Any, List, Optional
from pydantic import BaseModel, Field


class DropoffRisk(BaseModel):
    timestamp_start: float = Field(..., description="Início do trecho de risco em segundos")
    timestamp_end: float = Field(..., description="Fim do trecho de risco em segundos")
    reason: str = Field(..., description="Motivo do risco de queda (ex: silêncio, explicação óbvia)")


class HookAnalysis(BaseModel):
    score: int = Field(..., ge=0, le=100, description="Nota do gancho de 0 a 100")
    first_3_seconds_assessment: str = Field(..., description="Texto detalhado do que funcionou ou falhou nos 3 primeiros segundos")
    verbal_hook_rewrite: str = Field(..., description="Opção alternativa de gancho verbal mais agressivo para reter atenção")
    visual_hook_critique: str = Field(..., description="Análise da iluminação, texto na tela e enquadramento inicial")


class PacingAndRetention(BaseModel):
    pacing_verdict: str = Field(..., description="Veredito de ritmo: Rápido / Adequado / Lento / Monótono")
    dropoff_risks: List[DropoffRisk] = Field(default_factory=list, description="Lista de trechos com risco de abandono")


class ViralityAnalysisResult(BaseModel):
    """
    Strict Schema required by the user:
    {
      "virality_score": 0 a 100,
      "hook_analysis": { ... },
      "pacing_and_retention": { ... },
      "actionable_fixes": [ ... ],
      "call_to_action_evaluation": "..."
    }
    """
    virality_score: int = Field(..., ge=0, le=100, description="Score geral de viralidade de 0 a 100")
    hook_analysis: HookAnalysis
    pacing_and_retention: PacingAndRetention
    actionable_fixes: List[str] = Field(default_factory=list, description="Dicas práticas para reeditar")
    call_to_action_evaluation: str = Field(..., description="Avaliação do fechamento e estímulo a comentários/compartilhamentos")

    # Metadados adicionais para exibição rica na UI (não quebram a compatibilidade com o schema raiz)
    technical_metadata: Optional[dict[str, Any]] = None
    frames_metadata: Optional[List[dict[str, Any]]] = None
    audit_context: Optional[dict[str, Any]] = None


class ViralityAuditRequest(BaseModel):
    video_source: str = Field(..., description="Caminho local para o arquivo de vídeo ou URL do YouTube/TikTok")
    niche: str = Field(default="Podcast / Cortes", description="Nicho do conteúdo")
    target_audience: str = Field(default="Geral", description="Público-alvo esperado")
    account_size: str = Field(default="pequena", description="Tamanho da conta: pequena (<10k), media (10k-100k), grande (>100k)")
    platform_target: str = Field(default="ambos", description="Plataforma alvo: tiktok, youtube_shorts, ambos")
    provider: Optional[str] = Field(default=None, description="Provedor de IA preferencial (deepseek, gemini, ollama)")


class ChannelVideo(BaseModel):
    id: str
    title: str
    url: str
    thumbnail: str
    duration_seconds: Optional[float] = 0.0
    views_count: Optional[int] = 0
    likes_count: Optional[int] = 0
    comments_count: Optional[int] = 0
    shares_count: Optional[int] = 0
    published_at: Optional[str] = ""
    platform: str = "youtube"  # 'youtube' ou 'tiktok'
