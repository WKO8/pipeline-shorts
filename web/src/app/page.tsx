'use client';

import React, { useState, useEffect, useRef } from 'react';
import {
  Sparkles,
  Flame,
  Clock,
  Scissors,
  CheckCircle2,
  AlertTriangle,
  Copy,
  Check,
  Upload,
  Link as LinkIcon,
  Video,
  Key,
  ExternalLink,
  ChevronRight,
  TrendingUp,
  BarChart3,
  HelpCircle,
  Eye,
  Heart,
  MessageSquare,
  Share2
} from 'lucide-react';

interface DropoffRisk {
  timestamp_start: number;
  timestamp_end: number;
  reason: string;
}

interface HookAnalysis {
  score: number;
  first_3_seconds_assessment: string;
  verbal_hook_rewrite: string;
  visual_hook_critique: string;
}

interface PacingAndRetention {
  pacing_verdict: string;
  dropoff_risks: DropoffRisk[];
}

interface FrameMeta {
  timestamp: number;
  url: string;
  luminance: number;
  contrast: number;
  face_detected: boolean;
  is_hook: boolean;
}

interface AuditResult {
  virality_score: number;
  hook_analysis: HookAnalysis;
  pacing_and_retention: PacingAndRetention;
  actionable_fixes: string[];
  call_to_action_evaluation: string;
  technical_metadata?: {
    duration: number;
    cut_count: number;
    avg_cut_interval: number;
    cut_timestamps: number[];
    visual_telemetry?: any;
    provider_used?: string;
  };
  frames_metadata?: FrameMeta[];
  audit_context?: {
    niche: string;
    target_audience: string;
    account_size: string;
    platform_target: string;
  };
}

interface ChannelVideoItem {
  id: string;
  title: string;
  url: string;
  thumbnail: string;
  views_count: number;
  likes_count: number;
  comments_count: number;
  platform: string;
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<'audit' | 'channels' | 'settings'>('audit');
  const [sourceMode, setSourceMode] = useState<'upload' | 'url' | 'channel'>('upload');

  // Audit form state
  const [videoFile, setVideoFile] = useState<File | null>(null);
  const [uploadedPath, setUploadedPath] = useState<string | null>(null);
  const [videoUrl, setVideoUrl] = useState('');
  const [niche, setNiche] = useState('Podcast / Cortes');
  const [audience, setAudience] = useState('Jovens e Adultos (18-35)');
  const [accountSize, setAccountSize] = useState('pequena');
  const [provider, setProvider] = useState('auto');

  // Progress & Result
  const [isAuditing, setIsAuditing] = useState(false);
  const [progressStep, setProgressStep] = useState('iniciando');
  const [progressMsg, setProgressMsg] = useState('Preparando...');
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [auditResult, setAuditResult] = useState<AuditResult | null>(null);
  const [copiedHook, setCopiedHook] = useState(false);

  // Channels state
  const [channelPlatform, setChannelPlatform] = useState<'youtube' | 'tiktok'>('youtube');
  const [channelQuery, setChannelQuery] = useState('');
  const [channelVideos, setChannelVideos] = useState<ChannelVideoItem[]>([]);
  const [loadingVideos, setLoadingVideos] = useState(false);

  // Modal
  const [showApiModal, setShowApiModal] = useState(false);

  const videoRef = useRef<HTMLVideoElement | null>(null);

  // Handle File Upload
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setVideoFile(file);

    const fd = new FormData();
    fd.append('video', file);

    try {
      const res = await fetch('/api/virality/upload', {
        method: 'POST',
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Erro no upload.');
      setUploadedPath(data.video_path);
    } catch (err: any) {
      alert('Falha no upload: ' + err.message);
      setVideoFile(null);
      setUploadedPath(null);
    }
  };

  // Launch Audit
  const handleStartAudit = async (e: React.FormEvent) => {
    e.preventDefault();

    let source = '';
    if (sourceMode === 'upload') {
      if (!uploadedPath) {
        alert('Selecione e envie o vídeo primeiro.');
        return;
      }
      source = uploadedPath;
    } else {
      if (!videoUrl) {
        alert('Informe a URL do vídeo.');
        return;
      }
      source = videoUrl;
    }

    setIsAuditing(true);
    setAuditResult(null);
    setProgressStep('iniciando');
    setProgressMsg('Enviando dados para o Estrategista Sênior de Conteúdo Viral...');

    try {
      const res = await fetch('/api/virality/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          video_source: source,
          niche,
          target_audience: audience,
          account_size: accountSize,
          provider,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Falha ao iniciar auditoria');

      const runId = data.run_id;
      setCurrentRunId(runId);
      pollStatus(runId);
    } catch (err: any) {
      setIsAuditing(false);
      alert('Erro: ' + err.message);
    }
  };

  const pollStatus = (runId: string) => {
    const timer = setInterval(async () => {
      try {
        const res = await fetch(`/api/virality/status/${runId}`);
        const data = await res.json();

        if (data.status === 'in_progress') {
          setProgressStep(data.step || 'processando');
          setProgressMsg(data.message || 'Analisando vídeo...');
        } else if (data.status === 'done') {
          clearInterval(timer);
          setIsAuditing(false);
          setAuditResult(data.result);
        } else if (data.status === 'error') {
          clearInterval(timer);
          setIsAuditing(false);
          alert('Erro na auditoria: ' + (data.error || 'Erro desconhecido'));
        }
      } catch (err) {
        console.error(err);
      }
    }, 1800);
  };

  // Fetch Channels Videos
  const fetchChannelVideos = async () => {
    setLoadingVideos(true);
    try {
      let url = '';
      if (channelPlatform === 'youtube') {
        url = `/api/virality/youtube/videos${channelQuery ? '?channel_id=' + encodeURIComponent(channelQuery) : ''}`;
      } else {
        if (!channelQuery) {
          alert('Informe o @username do TikTok');
          setLoadingVideos(false);
          return;
        }
        url = `/api/virality/tiktok/videos?profile=${encodeURIComponent(channelQuery)}`;
      }

      const res = await fetch(url);
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Erro ao carregar vídeos');
      setChannelVideos(data || []);
    } catch (err: any) {
      alert('Erro: ' + err.message);
    } finally {
      setLoadingVideos(false);
    }
  };

  const copyHookToClipboard = () => {
    if (!auditResult?.hook_analysis?.verbal_hook_rewrite) return;
    navigator.clipboard.writeText(auditResult.hook_analysis.verbal_hook_rewrite);
    setCopiedHook(true);
    setTimeout(() => setCopiedHook(false), 2500);
  };

  const seekVideo = (time: number) => {
    if (videoRef.current) {
      videoRef.current.currentTime = time;
      videoRef.current.play();
    }
  };

  return (
    <div className="min-h-screen bg-[#09090b] text-[#f4f4f5] flex flex-col">
      {/* Top Header */}
      <header className="border-b border-[#27272a] bg-[#121215]/80 backdrop-blur sticky top-0 z-40 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-gradient-to-tr from-indigo-500 to-purple-600 flex items-center justify-center shadow-lg shadow-indigo-500/20">
            <Flame className="w-6 h-6 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="font-bold text-lg tracking-tight">Pipeline Shorts</h1>
              <span className="text-[11px] font-semibold uppercase px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
                GoViral AI Engine
              </span>
            </div>
            <p className="text-xs text-zinc-400">shorts.wkodev.com • Estrategista de Retenção &amp; Viralidade</p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <nav className="flex items-center gap-1 bg-[#18181b] p-1 rounded-lg border border-[#27272a]">
            <button
              onClick={() => setActiveTab('audit')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${
                activeTab === 'audit' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-white'
              }`}
            >
              Auditoria de Vídeo
            </button>
            <button
              onClick={() => setActiveTab('channels')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${
                activeTab === 'channels' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-white'
              }`}
            >
              Canais Conectados
            </button>
            <button
              onClick={() => setActiveTab('settings')}
              className={`px-3 py-1.5 rounded-md text-xs font-medium transition ${
                activeTab === 'settings' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-white'
              }`}
            >
              Configurações
            </button>
          </nav>

          <button
            onClick={() => setShowApiModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-[#18181b] border border-[#27272a] text-zinc-300 hover:text-white hover:border-zinc-500 transition"
          >
            <Key className="w-3.5 h-3.5 text-indigo-400" />
            Conectar TikTok &amp; YouTube
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
        {/* TAB 1: AUDIT */}
        {activeTab === 'audit' && (
          <div className="space-y-6">
            {/* Input Card */}
            <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 shadow-xl">
              <div className="mb-6">
                <h2 className="text-xl font-bold flex items-center gap-2 text-white">
                  <Sparkles className="w-5 h-5 text-indigo-400" />
                  Nova Auditoria de Viralidade
                </h2>
                <p className="text-sm text-zinc-400 mt-1">
                  Avalie seu vídeo com base nos algoritmos de Outubro/2026: taxa de swipe, psicologia dos primeiros 3 segundos,
                  ritmo de cortes e gatilhos de engajamento.
                </p>
              </div>

              <form onSubmit={handleStartAudit} className="space-y-6">
                {/* Source Mode selector */}
                <div>
                  <label className="text-xs font-semibold uppercase text-zinc-400 tracking-wider block mb-2">
                    Origem do Vídeo:
                  </label>
                  <div className="grid grid-cols-3 gap-2 bg-[#121215] p-1 rounded-lg border border-[#27272a] max-w-md">
                    <button
                      type="button"
                      onClick={() => setSourceMode('upload')}
                      className={`flex items-center justify-center gap-2 py-2 rounded-md text-xs font-medium transition ${
                        sourceMode === 'upload' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-white'
                      }`}
                    >
                      <Upload className="w-3.5 h-3.5" /> Enviar Arquivo
                    </button>
                    <button
                      type="button"
                      onClick={() => setSourceMode('url')}
                      className={`flex items-center justify-center gap-2 py-2 rounded-md text-xs font-medium transition ${
                        sourceMode === 'url' ? 'bg-indigo-600 text-white' : 'text-zinc-400 hover:text-white'
                      }`}
                    >
                      <LinkIcon className="w-3.5 h-3.5" /> Link (Shorts/TikTok)
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setActiveTab('channels');
                      }}
                      className="flex items-center justify-center gap-2 py-2 rounded-md text-xs font-medium text-zinc-400 hover:text-white transition"
                    >
                      <Video className="w-3.5 h-3.5" /> Do Canal
                    </button>
                  </div>
                </div>

                {/* Input Fields */}
                {sourceMode === 'upload' ? (
                  <div className="border-2 border-dashed border-[#27272a] hover:border-indigo-500/50 rounded-xl p-8 text-center transition bg-[#121215]/50">
                    <input
                      type="file"
                      id="file-upload"
                      accept=".mp4,.mov,.mkv,.webm,.m4v"
                      onChange={handleFileUpload}
                      className="hidden"
                    />
                    <label htmlFor="file-upload" className="cursor-pointer block space-y-3">
                      <div className="w-12 h-12 rounded-full bg-indigo-500/10 text-indigo-400 flex items-center justify-center mx-auto">
                        <Upload className="w-6 h-6" />
                      </div>
                      <div>
                        <span className="font-semibold text-sm text-indigo-400 hover:underline">
                          Clique para selecionar
                        </span>{' '}
                        <span className="text-sm text-zinc-400">ou arraste o vídeo pronto (.mp4, .mov até 2GB)</span>
                      </div>
                      {videoFile && (
                        <p className="text-xs text-emerald-400 font-medium">
                          ✓ {videoFile.name} ({(videoFile.size / (1024 * 1024)).toFixed(1)} MB carregado)
                        </p>
                      )}
                    </label>
                  </div>
                ) : (
                  <div>
                    <label className="text-xs font-medium text-zinc-300 block mb-1">URL Pública do Vídeo:</label>
                    <input
                      type="url"
                      value={videoUrl}
                      onChange={(e) => setVideoUrl(e.target.value)}
                      placeholder="https://www.youtube.com/shorts/... ou https://www.tiktok.com/@user/video/..."
                      className="w-full bg-[#121215] border border-[#27272a] rounded-lg px-4 py-2.5 text-sm text-white focus:outline-none focus:border-indigo-500"
                    />
                  </div>
                )}

                {/* Strategic Context Settings */}
                <div className="grid grid-cols-1 md:grid-cols-4 gap-4 pt-2 border-t border-[#27272a]">
                  <div>
                    <label className="text-xs font-medium text-zinc-400 block mb-1">Nicho do Conteúdo</label>
                    <select
                      value={niche}
                      onChange={(e) => setNiche(e.target.value)}
                      className="w-full bg-[#121215] border border-[#27272a] rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"
                    >
                      <option value="Podcast / Cortes">Podcast / Cortes de Entrevista</option>
                      <option value="Educação / Tech">Educação / Dicas / Tecnologia</option>
                      <option value="Finanças / Negócios">Finanças / Negócios / Riqueza</option>
                      <option value="Humor / Entretenimento">Humor / Memes / Entretenimento</option>
                      <option value="Histórias / Curiosidades">Histórias / Curiosidades / Fatos</option>
                      <option value="Fitness / Saúde">Fitness / Estilo de Vida</option>
                      <option value="Vlog / Cotidiano">Vlog / Vida Pessoal</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-xs font-medium text-zinc-400 block mb-1">Público-Alvo</label>
                    <input
                      type="text"
                      value={audience}
                      onChange={(e) => setAudience(e.target.value)}
                      className="w-full bg-[#121215] border border-[#27272a] rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"
                    />
                  </div>

                  <div>
                    <label className="text-xs font-medium text-zinc-400 block mb-1">Tamanho da Conta</label>
                    <select
                      value={accountSize}
                      onChange={(e) => setAccountSize(e.target.value)}
                      className="w-full bg-[#121215] border border-[#27272a] rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"
                    >
                      <option value="pequena">Pequena (&lt; 10k) • Passar do batch frio</option>
                      <option value="media">Média (10k a 100k) • Destravar FYP</option>
                      <option value="grande">Grande (&gt; 100k) • Escala massiva</option>
                    </select>
                  </div>

                  <div>
                    <label className="text-xs font-medium text-zinc-400 block mb-1">Provedor IA</label>
                    <select
                      value={provider}
                      onChange={(e) => setProvider(e.target.value)}
                      className="w-full bg-[#121215] border border-[#27272a] rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"
                    >
                      <option value="auto">Auto (DeepSeek Vision / Gemini)</option>
                      <option value="deepseek">DeepSeek v4-Flash-Vision (Multimodal + Raciocínio)</option>
                      <option value="gemini">Gemini 3.8 Flash (Visão Multimodal)</option>
                      <option value="ollama">Ollama Local (Sem Custo)</option>
                    </select>
                  </div>
                </div>

                <div className="flex justify-end pt-2">
                  <button
                    type="submit"
                    disabled={isAuditing}
                    className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-medium text-sm transition shadow-lg shadow-indigo-600/30 disabled:opacity-50"
                  >
                    <Flame className="w-4 h-4" />
                    {isAuditing ? 'Analisando...' : 'Iniciar Auditoria com Estrategista IA'}
                  </button>
                </div>
              </form>
            </div>

            {/* In-Progress Card */}
            {isAuditing && (
              <div className="bg-[#18181b] border border-indigo-500/30 rounded-xl p-8 shadow-2xl space-y-4">
                <div className="flex items-center gap-3">
                  <div className="w-3 h-3 rounded-full bg-indigo-500 animate-ping" />
                  <h3 className="font-semibold text-lg text-white">Estrategista Sênior em Ação...</h3>
                </div>
                <p className="text-sm text-zinc-400">{progressMsg}</p>
                <div className="w-full h-2 bg-[#27272a] rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-indigo-500 to-purple-500 transition-all duration-500"
                    style={{
                      width:
                        progressStep === 'downloading'
                          ? '25%'
                          : progressStep === 'extracting'
                          ? '60%'
                          : progressStep === 'analyzing'
                          ? '85%'
                          : '15%',
                    }}
                  />
                </div>
              </div>
            )}

            {/* RESULTS DASHBOARD */}
            {auditResult && (
              <div className="space-y-6 animate-in fade-in duration-300">
                {/* Score Banner */}
                <div className="bg-gradient-to-r from-indigo-950/40 via-[#18181b] to-[#18181b] border border-indigo-500/20 rounded-xl p-6 shadow-xl flex flex-wrap items-center justify-between gap-6">
                  <div className="space-y-2 max-w-2xl">
                    <span className="text-xs font-bold uppercase tracking-wider text-indigo-400">
                      Diagnóstico Geral de Retenção
                    </span>
                    <h2
                      className={`text-2xl font-black ${
                        auditResult.virality_score >= 75
                          ? 'text-emerald-400'
                          : auditResult.virality_score >= 50
                          ? 'text-amber-400'
                          : 'text-red-400'
                      }`}
                    >
                      {auditResult.virality_score >= 75
                        ? '🔥 Alto Potencial de Viralidade'
                        : auditResult.virality_score >= 50
                        ? '⚠️ Potencial Moderado (Requer Ajustes)'
                        : '🛑 Alta Taxa de Swipe Prevista'}
                    </h2>
                    <p className="text-sm text-zinc-300 leading-relaxed">
                      {auditResult.virality_score >= 75
                        ? 'Excelente alinhamento com os algoritmos de 2026. Abertura afiada e ritmo ágil para superar a barreira dos 70% de retenção.'
                        : auditResult.virality_score >= 50
                        ? 'O conteúdo tem pontos fortes, mas existem gargalos nos primeiros segundos ou pausas que correm sério risco de derrubar a retenção.'
                        : 'O gancho inicial falha em abrir um loop de curiosidade instantâneo, resultando em alta probabilidade de o espectador pular o vídeo antes do 3º segundo.'}
                    </p>
                  </div>

                  <div className="flex items-center gap-6">
                    {/* Virality Score */}
                    <div className="text-center">
                      <div
                        className={`w-24 h-24 rounded-full border-4 flex flex-col items-center justify-center bg-black/40 shadow-xl ${
                          auditResult.virality_score >= 75
                            ? 'border-emerald-500 text-emerald-400'
                            : auditResult.virality_score >= 50
                            ? 'border-amber-500 text-amber-400'
                            : 'border-red-500 text-red-400'
                        }`}
                      >
                        <span className="text-3xl font-black">{auditResult.virality_score}</span>
                        <span className="text-[10px] text-zinc-400">/ 100</span>
                      </div>
                      <span className="text-[11px] font-bold text-zinc-400 block mt-2 uppercase">
                        Virality Score
                      </span>
                    </div>

                    {/* Hook Score */}
                    <div className="text-center">
                      <div
                        className={`w-20 h-20 rounded-full border-4 flex flex-col items-center justify-center bg-black/40 shadow-xl ${
                          auditResult.hook_analysis.score >= 75
                            ? 'border-emerald-500 text-emerald-400'
                            : auditResult.hook_analysis.score >= 50
                            ? 'border-amber-500 text-amber-400'
                            : 'border-red-500 text-red-400'
                        }`}
                      >
                        <span className="text-2xl font-black">{auditResult.hook_analysis.score}</span>
                        <span className="text-[10px] text-zinc-400">/ 100</span>
                      </div>
                      <span className="text-[11px] font-bold text-zinc-400 block mt-2 uppercase">
                        Hook (0-3s)
                      </span>
                    </div>
                  </div>
                </div>

                {/* Hook Studio: 0 to 3 Seconds */}
                <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 shadow-xl space-y-6">
                  <div>
                    <h3 className="text-lg font-bold flex items-center gap-2 text-white">
                      <Clock className="w-5 h-5 text-indigo-400" />
                      Hook Studio: Autópsia dos Primeiros 3 Segundos
                    </h3>
                    <p className="text-xs text-zinc-400 mt-1">
                      Em 2026, 60% do público decide continuar ou pular nos primeiros 1.5 a 3.0 segundos.
                    </p>
                  </div>

                  {/* Frames Strip */}
                  {auditResult.frames_metadata && auditResult.frames_metadata.length > 0 && (
                    <div>
                      <span className="text-xs font-semibold uppercase text-zinc-400 tracking-wider block mb-2">
                        Frames Analisados (Abertura 0s a 3s):
                      </span>
                      <div className="flex gap-3 overflow-x-auto pb-2">
                        {auditResult.frames_metadata
                          .filter((f) => f.is_hook)
                          .map((f, i) => (
                            <div key={i} className="flex-shrink-0 relative group">
                              <img
                                src={f.url}
                                alt={`Frame ${f.timestamp}s`}
                                className="w-32 h-48 object-cover rounded-lg border border-[#27272a] bg-black"
                              />
                              <span className="absolute bottom-1.5 left-1.5 bg-black/80 px-1.5 py-0.5 rounded text-[10px] font-bold text-white">
                                {f.timestamp.toFixed(1)}s
                              </span>
                              {f.face_detected && (
                                <span className="absolute top-1.5 right-1.5 bg-emerald-500/80 px-1.5 py-0.5 rounded text-[9px] font-bold text-white">
                                  Rosto
                                </span>
                              )}
                            </div>
                          ))}
                      </div>
                    </div>
                  )}

                  {/* Text Diagnostics & Aggressive Rewrite */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    <div className="space-y-4">
                      <div>
                        <h4 className="text-xs font-semibold uppercase text-zinc-400 tracking-wider mb-1.5">
                          Avaliação do Gancho Falado:
                        </h4>
                        <div className="bg-[#121215] border-l-2 border-indigo-500 p-4 rounded-r-lg text-sm text-zinc-200 leading-relaxed">
                          {auditResult.hook_analysis.first_3_seconds_assessment}
                        </div>
                      </div>

                      <div>
                        <h4 className="text-xs font-semibold uppercase text-zinc-400 tracking-wider mb-1.5">
                          Crítica Visual &amp; Iluminação:
                        </h4>
                        <div className="bg-[#121215] border-l-2 border-cyan-500 p-4 rounded-r-lg text-sm text-zinc-200 leading-relaxed">
                          {auditResult.hook_analysis.visual_hook_critique}
                        </div>
                      </div>
                    </div>

                    <div>
                      <div className="bg-gradient-to-br from-red-950/20 to-amber-950/20 border border-red-500/30 rounded-xl p-5 space-y-3">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-bold uppercase tracking-wider text-red-400 flex items-center gap-1.5">
                            <Flame className="w-4 h-4" /> Reescrita Agressiva do Gancho
                          </span>
                          <button
                            type="button"
                            onClick={copyHookToClipboard}
                            className="flex items-center gap-1 px-2.5 py-1 rounded bg-[#18181b] border border-[#27272a] text-xs text-zinc-200 hover:text-white transition"
                          >
                            {copiedHook ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                            {copiedHook ? 'Copiado' : 'Copiar'}
                          </button>
                        </div>
                        <p className="text-base font-semibold text-amber-200 italic leading-snug">
                          "{auditResult.hook_analysis.verbal_hook_rewrite}"
                        </p>
                        <p className="text-xs text-zinc-400 pt-2 border-t border-red-500/10">
                          💡 <strong>Recomendação do Estrategista:</strong> Grave ou sobreponha esta frase exata nos primeiros 2 segundos para cortar o tempo de decisão do espectador e aumentar a retenção inicial.
                        </p>
                      </div>
                    </div>
                  </div>
                </div>

                {/* Pacing, Drop-off Risks & Video Player */}
                <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 shadow-xl space-y-6">
                  <div>
                    <h3 className="text-lg font-bold flex items-center gap-2 text-white">
                      <Scissors className="w-5 h-5 text-indigo-400" />
                      Ritmo (Pacing) &amp; Mapa de Riscos de Abandono
                    </h3>
                  </div>

                  {/* Pacing badges */}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div className="bg-[#121215] border border-[#27272a] p-4 rounded-xl">
                      <span className="text-xs text-zinc-400 block mb-1">Veredito de Ritmo:</span>
                      <span className="text-lg font-bold text-sky-400">
                        {auditResult.pacing_and_retention.pacing_verdict}
                      </span>
                    </div>
                    <div className="bg-[#121215] border border-[#27272a] p-4 rounded-xl">
                      <span className="text-xs text-zinc-400 block mb-1">Média entre Cortes:</span>
                      <span className="text-lg font-bold text-amber-400">
                        {auditResult.technical_metadata?.avg_cut_interval || 0}s
                      </span>
                      <span className="text-[11px] text-zinc-500 ml-2">(Benchmark ideal: 2.0s a 3.5s)</span>
                    </div>
                    <div className="bg-[#121215] border border-[#27272a] p-4 rounded-xl">
                      <span className="text-xs text-zinc-400 block mb-1">Total de Tomadas / Cortes:</span>
                      <span className="text-lg font-bold text-purple-400">
                        {auditResult.technical_metadata?.cut_count || 0}
                      </span>
                    </div>
                  </div>

                  {/* Video & Drop-off List */}
                  <div className="grid grid-cols-1 md:grid-cols-3 gap-6 items-start">
                    <div className="md:col-span-1">
                      <video
                        ref={videoRef}
                        controls
                        src={currentRunId ? `/api/virality/video/${currentRunId}` : undefined}
                        className="w-full rounded-xl bg-black shadow-lg aspect-[9/16] object-cover"
                      />
                    </div>

                    <div className="md:col-span-2 space-y-3">
                      <h4 className="text-xs font-semibold uppercase text-zinc-400 tracking-wider">
                        Pontos Críticos de Risco (Clique para pular no vídeo):
                      </h4>
                      {auditResult.pacing_and_retention.dropoff_risks.length === 0 ? (
                        <p className="text-sm text-emerald-400 bg-emerald-500/10 p-4 rounded-lg border border-emerald-500/20">
                          ✓ Nenhum ponto de risco de abandono detectado no fluxo do roteiro!
                        </p>
                      ) : (
                        auditResult.pacing_and_retention.dropoff_risks.map((risk, idx) => (
                          <div
                            key={idx}
                            onClick={() => seekVideo(risk.timestamp_start)}
                            className="bg-red-500/5 hover:bg-red-500/10 border-l-4 border-red-500 p-3.5 rounded-r-lg cursor-pointer transition flex items-center justify-between group"
                          >
                            <div>
                              <div className="text-xs font-bold text-red-400">
                                Trecho: {risk.timestamp_start.toFixed(1)}s até {risk.timestamp_end.toFixed(1)}s
                              </div>
                              <div className="text-sm text-zinc-200 mt-0.5">{risk.reason}</div>
                            </div>
                            <span className="text-xs text-indigo-400 group-hover:translate-x-1 transition flex items-center gap-1">
                              Ir ao segundo <ChevronRight className="w-3.5 h-3.5" />
                            </span>
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>

                {/* Actionable Fixes & CTA */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* Fixes checklist */}
                  <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 shadow-xl space-y-4">
                    <h3 className="text-base font-bold flex items-center gap-2 text-white">
                      <CheckCircle2 className="w-5 h-5 text-indigo-400" />
                      Checklist Prático para Reeditar
                    </h3>
                    <div className="space-y-2.5">
                      {auditResult.actionable_fixes.map((fix, idx) => (
                        <label key={idx} className="flex items-start gap-3 cursor-pointer group">
                          <input
                            type="checkbox"
                            className="mt-1 w-4 h-4 rounded border-zinc-700 bg-zinc-800 text-indigo-600 focus:ring-0"
                          />
                          <span className="text-sm text-zinc-300 group-hover:text-white transition leading-snug">
                            {fix}
                          </span>
                        </label>
                      ))}
                    </div>
                  </div>

                  {/* Call to Action */}
                  <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 shadow-xl space-y-4">
                    <h3 className="text-base font-bold flex items-center gap-2 text-white">
                      <TrendingUp className="w-5 h-5 text-indigo-400" />
                      Avaliação de Fechamento &amp; Call to Action
                    </h3>
                    <div className="bg-[#121215] border-l-2 border-emerald-500 p-4 rounded-r-lg text-sm text-zinc-200 leading-relaxed">
                      {auditResult.call_to_action_evaluation}
                    </div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* TAB 2: CHANNELS */}
        {activeTab === 'channels' && (
          <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 shadow-xl space-y-6">
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <Video className="w-5 h-5 text-indigo-400" />
                  Canais Conectados (YouTube &amp; TikTok)
                </h2>
                <p className="text-xs text-zinc-400 mt-1">
                  Selecione qualquer vídeo já postado para rodar a auditoria de viralidade com 1 clique.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <select
                  value={channelPlatform}
                  onChange={(e: any) => setChannelPlatform(e.target.value)}
                  className="bg-[#121215] border border-[#27272a] rounded-lg px-3 py-2 text-xs text-white"
                >
                  <option value="youtube">YouTube Shorts</option>
                  <option value="tiktok">TikTok</option>
                </select>
                <input
                  type="text"
                  value={channelQuery}
                  onChange={(e) => setChannelQuery(e.target.value)}
                  placeholder={channelPlatform === 'youtube' ? 'Canal ID (opcional)' : '@username do TikTok'}
                  className="bg-[#121215] border border-[#27272a] rounded-lg px-3 py-2 text-xs text-white w-52"
                />
                <button
                  onClick={fetchChannelVideos}
                  disabled={loadingVideos}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium transition"
                >
                  {loadingVideos ? 'Carregando...' : 'Carregar Vídeos'}
                </button>
              </div>
            </div>

            {/* Video Cards Grid */}
            <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
              {channelVideos.length === 0 ? (
                <div className="col-span-full py-16 text-center text-zinc-500 text-sm">
                  Clique em "Carregar Vídeos" ou configure suas credenciais para listar seus Shorts/TikToks.
                </div>
              ) : (
                channelVideos.map((v) => (
                  <div
                    key={v.id}
                    className="bg-[#121215] border border-[#27272a] rounded-xl overflow-hidden hover:border-indigo-500/50 transition group flex flex-col"
                  >
                    <div className="relative aspect-[9/16] bg-black">
                      <img src={v.thumbnail} alt={v.title} className="w-full h-full object-cover" />
                      <div className="absolute inset-0 bg-black/40 opacity-0 group-hover:opacity-100 transition flex items-center justify-center">
                        <button
                          onClick={() => {
                            setVideoUrl(v.url);
                            setSourceMode('url');
                            setActiveTab('audit');
                          }}
                          className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold rounded-lg shadow-lg"
                        >
                          Auditar este Vídeo
                        </button>
                      </div>
                    </div>
                    <div className="p-3 flex-1 flex flex-col justify-between">
                      <h4 className="text-xs font-semibold text-white line-clamp-2">{v.title}</h4>
                      <div className="flex items-center justify-between text-[11px] text-zinc-400 mt-2">
                        <span className="flex items-center gap-1">
                          <Eye className="w-3 h-3" /> {v.views_count.toLocaleString()}
                        </span>
                        <span className="flex items-center gap-1">
                          <Heart className="w-3 h-3" /> {v.likes_count.toLocaleString()}
                        </span>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}

        {/* TAB 3: SETTINGS */}
        {activeTab === 'settings' && (
          <div className="bg-[#18181b] border border-[#27272a] rounded-xl p-6 shadow-xl space-y-6 max-w-3xl">
            <h2 className="text-xl font-bold text-white flex items-center gap-2">
              <Key className="w-5 h-5 text-indigo-400" />
              Configurações de Credenciais &amp; Provedores
            </h2>

            <div className="space-y-4 text-sm text-zinc-300">
              <div className="bg-[#121215] p-4 rounded-lg border border-[#27272a]">
                <h3 className="font-semibold text-indigo-400 mb-1">DeepSeek 4.1-Flash (Raciocínio)</h3>
                <p className="text-xs text-zinc-400 mb-2">
                  Configurado no arquivo <code>.env</code> do projeto principal:
                </p>
                <code className="text-xs text-zinc-200 block bg-black/50 p-2 rounded">
                  DEEPSEEK_API_KEY=sua_chave
                  <br />
                  DEEPSEEK_MODEL_NAME=deepseek-v4.1-flash
                </code>
              </div>

              <div className="bg-[#121215] p-4 rounded-lg border border-[#27272a]">
                <h3 className="font-semibold text-red-400 mb-1">YouTube Data API v3 &amp; Analytics</h3>
                <code className="text-xs text-zinc-200 block bg-black/50 p-2 rounded">
                  YOUTUBE_API_KEY=sua_api_key_do_google_cloud
                  <br />
                  YOUTUBE_CLIENT_ID=seu_client_id.apps.googleusercontent.com
                </code>
              </div>

              <div className="bg-[#121215] p-4 rounded-lg border border-[#27272a]">
                <h3 className="font-semibold text-cyan-400 mb-1">TikTok for Developers API</h3>
                <code className="text-xs text-zinc-200 block bg-black/50 p-2 rounded">
                  TIKTOK_CLIENT_KEY=seu_client_key
                  <br />
                  TIKTOK_CLIENT_SECRET=seu_client_secret
                </code>
              </div>
            </div>
          </div>
        )}
      </main>

      {/* MODAL: API Setup Guide */}
      {showApiModal && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-[#18181b] border border-[#27272a] rounded-2xl max-w-2xl w-full p-6 space-y-6 shadow-2xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between border-b border-[#27272a] pb-4">
              <h3 className="text-lg font-bold text-white flex items-center gap-2">
                <Key className="w-5 h-5 text-indigo-400" />
                Guia de Conexão: TikTok for Developers
              </h3>
              <button
                onClick={() => setShowApiModal(false)}
                className="text-zinc-400 hover:text-white p-1 text-sm"
              >
                ✕
              </button>
            </div>

            <div className="space-y-4 text-xs text-zinc-300 leading-relaxed">
              <div className="bg-indigo-950/20 border border-indigo-500/20 p-4 rounded-xl">
                <h4 className="font-bold text-indigo-300 text-sm mb-1">
                  Passo a Passo para Obter o TikTok API:
                </h4>
                <ol className="list-decimal pl-4 space-y-2 mt-2">
                  <li>
                    Acesse o portal{' '}
                    <a
                      href="https://developers.tiktok.com/"
                      target="_blank"
                      rel="noreferrer"
                      className="text-indigo-400 underline inline-flex items-center gap-0.5"
                    >
                      TikTok for Developers <ExternalLink className="w-3 h-3" />
                    </a>{' '}
                    e faça login com a conta do seu canal/perfil.
                  </li>
                  <li>Vá em <strong>"Manage apps"</strong> e clique em <strong>"Create an app"</strong>.</li>
                  <li>Dê um nome para seu aplicativo (ex: <em>Pipeline Shorts Analyzer</em>).</li>
                  <li>
                    Na aba <strong>"Products"</strong>, adicione o <strong>Login Kit</strong>.
                  </li>
                  <li>
                    Na seção <strong>"Add Scopes"</strong> (Permissões de Acesso), marque:
                    <ul className="list-disc pl-4 mt-1 space-y-1 text-zinc-400">
                      <li><code>user.info.profile</code>: Acesso ao link público, bio e verificação da conta.</li>
                      <li><code>user.info.stats</code>: Dados estatísticos de likes, contagem de seguidores e vídeos.</li>
                      <li><code>video.list</code>: Leitura dos vídeos públicos postados no canal.</li>
                    </ul>
                  </li>
                  <li>
                    Defina o Redirect URI como:{' '}
                    <code className="bg-black/50 px-1 py-0.5 rounded text-zinc-200">
                      https://shorts.wkodev.com/api/virality/tiktok/callback
                    </code>
                  </li>
                  <li>
                    Copie a <strong>Client Key</strong> e o <strong>Client Secret</strong> gerados e insira no
                    arquivo <code>.env</code>.
                  </li>
                </ol>
              </div>

              <div className="bg-emerald-950/20 border border-emerald-500/20 p-4 rounded-xl">
                <h4 className="font-bold text-emerald-300 text-sm mb-1">
                  💡 Modo Ágil sem Aprovação:
                </h4>
                <p>
                  Você já pode auditar qualquer vídeo público do TikTok agora mesmo! Basta colar a URL direta do vídeo
                  ou usar o campo @username na aba "Canais Conectados" — nosso extrator nativo via yt-dlp puxa os vídeos
                  sem necessidade de esperar os dias de aprovação do app.
                </p>
              </div>
            </div>

            <div className="flex justify-end pt-2">
              <button
                onClick={() => setShowApiModal(false)}
                className="px-5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold"
              >
                Entendi
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
