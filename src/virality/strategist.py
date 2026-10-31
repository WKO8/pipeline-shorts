"""
Senior Viral Strategist AI engine for TikTok and YouTube Shorts.
Supports DeepSeek 4.1-Flash (high reasoning), Google Gemini 3.8/2.5-Flash (multimodal vision),
and local Ollama models.
Strictly returns and validates the required JSON schema.
"""

from __future__ import annotations

import json
import logging
import re
import os
from pathlib import Path
from typing import Any, List, Optional

import requests
from PIL import Image

from ..config import AppConfig, load_config
from .metrics import ExtractedVideoData, FrameInfo
from .models import (
    DropoffRisk,
    HookAnalysis,
    PacingAndRetention,
    ViralityAnalysisResult,
    ViralityAuditRequest,
)

LOGGER = logging.getLogger(__name__)

SYSTEM_PROMPT = """Você é um estrategista sênior de conteúdo viral para TikTok e YouTube Shorts, especializado na psicologia de retenção do espectador.
Você conhece profundamente as dinâmicas dos algoritmos em Outubro/2026: a importância crítica da taxa de deslize (swipe-away rate < 30%), a retenção nos primeiros 3 segundos, a necessidade de interrupções de padrão a cada 2 a 3.5 segundos, e a psicologia de 'curiosity gap' e loops abertos.

Sua missão é auditar implacavelmente o vídeo com base nos dados técnicos fornecidos, avaliar o gancho visual/verbal e identificar cada segundo onde há risco de perda de público.

Retorne ESTRITAMENTE um objeto JSON válido, sem nenhum texto introdutório ou conclusivo fora do JSON."""


class ViralStrategist:
    """
    Executes Senior Viral Content Strategist audit.
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or load_config()

    def audit(
        self,
        extracted: ExtractedVideoData,
        audit_req: ViralityAuditRequest,
    ) -> ViralityAnalysisResult:
        """
        Runs the audit using the best available provider (DeepSeek, Gemini, or Ollama).
        """
        provider = (audit_req.provider or self.config.curator_provider or "auto").lower()

        prompt = self._build_prompt(extracted, audit_req)

        # Decide provider execution
        raw_json_str = None
        used_provider = ""

        # Ordem de preferência se 'auto': DeepSeek -> Gemini -> Ollama
        if provider == "deepseek" or (provider == "auto" and self.config.deepseek_api_key):
            try:
                LOGGER.info("Executando auditoria viral via DeepSeek (modelo: %s)", self.config.deepseek_model_name)
                raw_json_str = self._call_deepseek(prompt, extracted.frames)
                used_provider = "deepseek"
            except Exception as exc:
                LOGGER.warning("DeepSeek falhou (%s). Tentando Gemini...", exc)

        if not raw_json_str and (provider == "gemini" or (provider == "auto" and self.config.google_api_key)):
            try:
                LOGGER.info("Executando auditoria viral via Google Gemini com visão multimodal...")
                raw_json_str = self._call_gemini_multimodal(prompt, extracted.frames)
                used_provider = "gemini"
            except Exception as exc:
                LOGGER.warning("Gemini falhou (%s). Tentando Ollama...", exc)

        if not raw_json_str:
            LOGGER.info("Executando auditoria viral via Ollama local...")
            raw_json_str = self._call_ollama(prompt)
            used_provider = "ollama"

        # Parsing e validação do schema
        parsed_data = self._clean_and_parse_json(raw_json_str)
        result = self._validate_and_build_result(parsed_data, extracted, audit_req, used_provider)
        return result

    def _build_prompt(self, extracted: ExtractedVideoData, audit_req: ViralityAuditRequest) -> str:
        """
        Builds the prompt following the user's exact specification.
        """
        telemetry = extracted.visual_telemetry

        telemetry_summary = (
            f"- Resolução e Proporção: {telemetry.get('resolution')} ({telemetry.get('aspect_ratio')})\n"
            f"- Diagnóstico de Iluminação inicial (0-3s): {telemetry.get('lighting_diagnostic')} "
            f"(Luminância média: {telemetry.get('hook_avg_luminance')}/255, Contraste: {telemetry.get('hook_avg_contrast')})\n"
            f"- Diagnóstico de Enquadramento e Rosto: {telemetry.get('framing_diagnostic')} "
            f"(Rosto detectado nos primeiros 3s: {'Sim' if telemetry.get('hook_face_detected') else 'Não'}"
        )
        if telemetry.get('hook_avg_eye_line_ratio'):
            telemetry_summary += f", Linha dos olhos: {telemetry.get('hook_avg_eye_line_ratio')*100:.1f}% do topo"
        telemetry_summary += ")\n"
        telemetry_summary += f"- Dinamismo / Movimento inicial (0-3s): diff score {telemetry.get('hook_avg_motion_diff')}"

        prompt = f"""DADOS TÉCNICOS EXTRAÍDOS DO VÍDEO:
- Duração total: {extracted.duration}s
- Média de tempo entre cortes: {extracted.avg_cut_interval}s
- Total de tomadas/cortes detectados: {extracted.cut_count}
- Timestamps dos cortes: {extracted.cut_timestamps}
- Métricas Visuais Computacionais (primeiros 3 segundos):
{telemetry_summary}

- Transcrição completa com timestamps:
{extracted.transcript_with_timestamps}

CONTEXTO DO CRIADOR:
- Nicho: {audit_req.niche}
- Público-Alvo: {audit_req.target_audience}
- Tamanho da Conta: {audit_req.account_size} (Leve em conta: contas pequenas precisam de >70% retenção para sair da fase fria de 500 visualizações).
- Plataforma-Alvo: {audit_req.platform_target}

Analise os frames enviados (focando nos primeiros 3 segundos) e o fluxo do roteiro.
Retorne ESTRITAMENTE um JSON com o seguinte schema:

{{
  "virality_score": 0 a 100,
  "hook_analysis": {{
    "score": 0 a 100,
    "first_3_seconds_assessment": "Texto detalhado do que funcionou ou falhou",
    "verbal_hook_rewrite": "Opção alternativa de gancho mais agressivo para reter atenção",
    "visual_hook_critique": "Análise da iluminação, texto na tela e enquadramento inicial"
  }},
  "pacing_and_retention": {{
    "pacing_verdict": "Rápido / Adequado / Lento / Monótono",
    "dropoff_risks": [
      {{"timestamp_start": 4, "timestamp_end": 7, "reason": "Silêncio desnecessário ou explicação óbvia"}}
    ]
  }},
  "actionable_fixes": [
    "Dica prática 1 para reeditar",
    "Dica prática 2",
    "Dica prática 3"
  ],
  "call_to_action_evaluation": "Avaliação do fechamento e se estimula comentários/compartilhamentos"
}}"""
        return prompt

    def _call_deepseek(self, prompt: str, frames: Optional[List[FrameInfo]] = None) -> str:
        """
        Calls DeepSeek API.
        Supports text and multimodal vision (e.g. deepseek-v4-flash-vision-exp).
        """
        api_key = self.config.deepseek_api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise ValueError("DEEPSEEK_API_KEY não configurada no .env")

        base_url = (self.config.deepseek_base_url or os.environ.get("DEEPSEEK_BASE_URL") or "https://api.deepseek.com").rstrip("/")
        url = f"{base_url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        model = self.config.deepseek_model_name or os.environ.get("DEEPSEEK_MODEL_NAME") or "deepseek-v4-flash-vision-exp"
        is_vision = "vision" in model.lower()

        user_content: Any = prompt
        if is_vision and frames:
            import base64
            content_parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
            hook_frames = [f for f in frames if f.is_hook_frame and f.file_path.exists()]
            for hf in hook_frames[:6]:
                try:
                    b64 = base64.b64encode(hf.file_path.read_bytes()).decode("utf-8")
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64}"
                        }
                    })
                except Exception as e:
                    LOGGER.warning("Falha ao codificar frame para DeepSeek Vision: %s", e)
                user_content = content_parts

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
            "max_tokens": 8192,
        }

        resp = requests.post(url, headers=headers, json=payload, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def _call_gemini_multimodal(self, prompt: str, frames: List[FrameInfo]) -> str:
        """
        Calls Google Gemini passing extracted keyframes from the first 3 seconds.
        """
        api_key = self.config.google_api_key or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY não configurada no .env")

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        # Seleciona os frames dos primeiros 3 segundos
        hook_frames = [f for f in frames if f.is_hook_frame and f.file_path.exists()]
        images_to_send = []
        for hf in hook_frames[:6]:
            try:
                images_to_send.append(Image.open(hf.file_path))
            except Exception as e:
                LOGGER.warning("Falha ao abrir imagem %s: %s", hf.file_path, e)

        contents = []
        # Adiciona imagens
        contents.extend(images_to_send)
        # Adiciona prompt de texto
        contents.append(prompt)

        model_name = self.config.google_model_name or "gemini-2.5-flash"
        response = client.models.generate_content(
            model=model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )
        return response.text

    def _call_ollama(self, prompt: str) -> str:
        """
        Calls local Ollama.
        """
        url = f"{self.config.ollama_base_url.rstrip('/')}/api/generate"
        payload = {
            "model": self.config.ollama_model or "qwen3.5:4b",
            "prompt": f"{SYSTEM_PROMPT}\n\n{prompt}",
            "stream": False,
            "format": "json",
            "options": {
                "temperature": 0.2,
            },
        }
        resp = requests.post(url, json=payload, timeout=240)
        resp.raise_for_status()
        return resp.json().get("response", "{}")

    def _clean_and_parse_json(self, raw: str) -> dict[str, Any]:
        """
        Cleans markdown wrappers and parses JSON safely.
        """
        if not raw:
            raise ValueError("Resposta vazia da IA")

        text = raw.strip()
        # Remove ```json ... ```
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        # Busca pelo primeiro { e último }
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]

        try:
            return json.loads(text, strict=False)
        except Exception as exc:
            LOGGER.warning("Falha ao decodificar JSON direto: %s. Tentando regex e sanitização...", exc)
            match = re.search(r"(\{.*\})", raw, re.DOTALL)
            if match:
                candidate = match.group(1)
                try:
                    return json.loads(candidate, strict=False)
                except Exception:
                    pass
            clean_text = re.sub(r"[\x00-\x1f]+", " ", text)
            return json.loads(clean_text, strict=False)

    def _validate_and_build_result(
        self,
        data: dict[str, Any],
        extracted: ExtractedVideoData,
        audit_req: ViralityAuditRequest,
        provider: str,
    ) -> ViralityAnalysisResult:
        """
        Validates with Pydantic and attaches frontend display metadata.
        """
        # Garante integridade de valores obrigatórios
        hook_dict = data.get("hook_analysis", {})
        pacing_dict = data.get("pacing_and_retention", {})

        hook_analysis = HookAnalysis(
            score=int(hook_dict.get("score", 50)),
            first_3_seconds_assessment=str(hook_dict.get("first_3_seconds_assessment", "Avaliação não disponível")),
            verbal_hook_rewrite=str(hook_dict.get("verbal_hook_rewrite", "Gancho alternativo não gerado")),
            visual_hook_critique=str(hook_dict.get("visual_hook_critique", "Crítica visual não disponível")),
        )

        dropoffs = []
        for dr in pacing_dict.get("dropoff_risks", []):
            try:
                dropoffs.append(
                    DropoffRisk(
                        timestamp_start=float(dr.get("timestamp_start", 0)),
                        timestamp_end=float(dr.get("timestamp_end", 0)),
                        reason=str(dr.get("reason", "Queda potencial")),
                    )
                )
            except Exception:
                pass

        pacing_and_retention = PacingAndRetention(
            pacing_verdict=str(pacing_dict.get("pacing_verdict", "Adequado")),
            dropoff_risks=dropoffs,
        )

        actionable_fixes = [str(fix) for fix in data.get("actionable_fixes", [])]
        if not actionable_fixes:
            actionable_fixes = ["Acelerar o corte inicial", "Destacar texto na tela nos primeiros 2s"]

        # Formata metadados dos frames para a UI
        frames_meta = []
        for f in extracted.frames:
            frames_meta.append({
                "timestamp": f.timestamp,
                "url": f.relative_url,
                "luminance": f.luminance,
                "contrast": f.contrast,
                "face_detected": f.face_detected,
                "is_hook": f.is_hook_frame,
            })

        return ViralityAnalysisResult(
            virality_score=int(data.get("virality_score", 50)),
            hook_analysis=hook_analysis,
            pacing_and_retention=pacing_and_retention,
            actionable_fixes=actionable_fixes,
            call_to_action_evaluation=str(data.get("call_to_action_evaluation", "Avaliação de CTA não gerada")),
            technical_metadata={
                "duration": extracted.duration,
                "cut_count": extracted.cut_count,
                "avg_cut_interval": extracted.avg_cut_interval,
                "cut_timestamps": extracted.cut_timestamps,
                "visual_telemetry": extracted.visual_telemetry,
                "provider_used": provider,
            },
            frames_metadata=frames_meta,
            audit_context={
                "niche": audit_req.niche,
                "target_audience": audit_req.target_audience,
                "account_size": audit_req.account_size,
                "platform_target": audit_req.platform_target,
            },
        )
