/**
 * Pipeline Shorts — Virality Auditor Client Script
 * Handles uploads, asynchronous audit polling, interactive retention timeline,
 * hook studio frame scrubbing, and channel imports.
 */

(function () {
  'use strict';

  let currentRunId = null;
  let currentAuditResult = null;
  let uploadedVideoPath = null;
  let pollInterval = null;

  // DOM Elements
  const form = document.getElementById('virality-form');
  const sourceTabs = document.querySelectorAll('#virality-source-tabs .seg-btn');
  const fileInput = document.getElementById('virality-file-input');
  const fileInfo = document.getElementById('virality-file-info');
  const urlInput = document.getElementById('virality-url-input');
  const channelPlatform = document.getElementById('virality-channel-platform');
  const channelQuery = document.getElementById('virality-channel-query');
  const btnFetchChannels = document.getElementById('btn-fetch-channel-videos');
  const channelVideosGrid = document.getElementById('virality-channel-videos-grid');

  const progressCard = document.getElementById('virality-progress-card');
  const progressTitle = document.getElementById('virality-progress-title');
  const progressDesc = document.getElementById('virality-progress-desc');
  const progressBar = document.getElementById('virality-progress-bar');

  const resultsContainer = document.getElementById('virality-results-container');
  const videoPreview = document.getElementById('virality-video-preview');

  // Modal elements
  const btnOpenModal = document.getElementById('btn-virality-channels-modal');
  const btnCloseModal = document.getElementById('btn-close-credentials-modal');
  const btnDoneModal = document.getElementById('btn-done-credentials-modal');
  const modal = document.getElementById('virality-credentials-modal');

  // Initialize
  document.addEventListener('DOMContentLoaded', init);

  function init() {
    setupSourceTabs();
    setupFileUpload();
    setupChannelFetch();
    setupFormSubmit();
    setupCopyButton();
    setupModal();
    setupActionButtons();
  }

  // 1. Source Tab Switching
  function setupSourceTabs() {
    sourceTabs.forEach(btn => {
      btn.addEventListener('click', () => {
        sourceTabs.forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const tab = btn.dataset.srcTab;
        document.getElementById('vtab-upload').style.display = tab === 'upload' ? 'block' : 'none';
        document.getElementById('vtab-url').style.display = tab === 'url' ? 'block' : 'none';
        document.getElementById('vtab-channels').style.display = tab === 'channels' ? 'block' : 'none';
      });
    });
  }

  // 2. File Upload Handling
  function setupFileUpload() {
    fileInput.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;

      fileInfo.style.display = 'block';
      fileInfo.innerHTML = `⏳ Enviando <strong>${file.name}</strong> (${(file.size / (1024*1024)).toFixed(1)} MB)...`;

      const fd = new FormData();
      fd.append('video', file);

      try {
        const res = await fetch('/api/virality/upload', {
          method: 'POST',
          body: fd,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Erro no upload.');

        uploadedVideoPath = data.video_path;
        fileInfo.innerHTML = `✅ Pronto: <strong>${file.name}</strong> carregado com sucesso.`;
        fileInfo.style.color = '#10b981';
      } catch (err) {
        fileInfo.innerHTML = `❌ Erro: ${err.message}`;
        fileInfo.style.color = '#ef4444';
        uploadedVideoPath = null;
      }
    });
  }

  // 3. Channels Fetching
  function setupChannelFetch() {
    btnFetchChannels.addEventListener('click', async () => {
      const platform = channelPlatform.value;
      const query = channelQuery.value.trim();

      channelVideosGrid.innerHTML = '<small style="color:var(--text-muted);">Buscando vídeos...</small>';

      try {
        let url = '';
        if (platform === 'youtube') {
          url = `/api/virality/youtube/videos${query ? '?channel_id=' + encodeURIComponent(query) : ''}`;
        } else {
          if (!query) throw new Error('Informe o @username ou link do TikTok');
          url = `/api/virality/tiktok/videos?profile=${encodeURIComponent(query)}`;
        }

        const res = await fetch(url);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Falha ao buscar vídeos.');

        if (!data || data.length === 0) {
          channelVideosGrid.innerHTML = '<small style="color:var(--text-muted);">Nenhum vídeo encontrado ou credenciais não configuradas.</small>';
          return;
        }

        channelVideosGrid.innerHTML = '';
        data.forEach(v => {
          const card = document.createElement('div');
          card.style.cssText = 'background:rgba(255,255,255,0.05); border:1px solid #333; border-radius:8px; padding:8px; cursor:pointer; transition:border 0.2s;';
          card.innerHTML = `
            <img src="${v.thumbnail || ''}" style="width:100%; height:110px; object-fit:cover; border-radius:4px; background:#000;">
            <div style="font-size:12px; font-weight:600; margin-top:6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">${v.title}</div>
            <div style="font-size:11px; color:var(--text-muted); margin-top:2px;">👁️ ${(v.views_count || 0).toLocaleString()} • ❤️ ${(v.likes_count || 0).toLocaleString()}</div>
            <button type="button" class="btn btn-secondary btn-sm" style="width:100%; margin-top:6px; font-size:11px;">Selecionar</button>
          `;
          card.addEventListener('click', () => {
            urlInput.value = v.url;
            // Altera para a aba URL
            document.querySelector('[data-src-tab="url"]').click();
            showToast(`Vídeo "${v.title.slice(0, 30)}..." selecionado para auditoria.`);
          });
          channelVideosGrid.appendChild(card);
        });
      } catch (err) {
        channelVideosGrid.innerHTML = `<small style="color:#ef4444;">Erro: ${err.message}</small>`;
      }
    });
  }

  // 4. Form Submission & Audit Launch
  function setupFormSubmit() {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const activeTab = document.querySelector('#virality-source-tabs .seg-btn.active').dataset.srcTab;
      let videoSource = '';

      if (activeTab === 'upload') {
        if (!uploadedVideoPath) {
          alert('Por favor, selecione e envie um vídeo antes de iniciar a auditoria.');
          return;
        }
        videoSource = uploadedVideoPath;
      } else {
        videoSource = urlInput.value.trim();
        if (!videoSource) {
          alert('Informe o link do vídeo para auditoria.');
          return;
        }
      }

      const payload = {
        video_source: videoSource,
        niche: document.getElementById('virality-niche').value,
        target_audience: document.getElementById('virality-audience').value,
        account_size: document.getElementById('virality-account-size').value,
        provider: document.getElementById('virality-provider').value,
      };

      // Inicia progresso
      resultsContainer.style.display = 'none';
      progressCard.style.display = 'block';
      progressTitle.textContent = 'Iniciando Auditoria...';
      progressDesc.textContent = 'Enviando parâmetros para o Estrategista Sênior...';
      progressBar.style.width = '15%';

      try {
        const res = await fetch('/api/virality/audit', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Erro ao iniciar auditoria.');

        currentRunId = data.run_id;
        startPolling(currentRunId);
      } catch (err) {
        progressCard.style.display = 'none';
        alert('Erro ao iniciar auditoria: ' + err.message);
      }
    });
  }

  // 5. Polling de Progresso
  function startPolling(runId) {
    if (pollInterval) clearInterval(pollInterval);

    pollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/virality/status/${runId}`);
        const state = await res.json();

        if (state.status === 'in_progress') {
          progressDesc.textContent = state.message || 'Analisando vídeo...';
          const step = state.step || '';
          if (step === 'downloading') progressBar.style.width = '25%';
          else if (step === 'extracting') progressBar.style.width = '55%';
          else if (step === 'analyzing') progressBar.style.width = '85%';
        } else if (state.status === 'done') {
          clearInterval(pollInterval);
          progressBar.style.width = '100%';
          progressTitle.textContent = 'Concluído!';
          progressDesc.textContent = 'Renderizando diagnóstico completo...';
          setTimeout(() => {
            progressCard.style.display = 'none';
            renderAuditResult(state.result);
          }, 600);
        } else if (state.status === 'error') {
          clearInterval(pollInterval);
          progressCard.style.display = 'none';
          alert('Erro durante a auditoria: ' + (state.error || 'Falha no processamento'));
        }
      } catch (err) {
        console.error('Erro no polling:', err);
      }
    }, 1800);
  }

  // 6. Render Results Dashboard
  function renderAuditResult(result) {
    if (!result) return;
    currentAuditResult = result;

    const score = result.virality_score || 0;
    const hook = result.hook_analysis || {};
    const hookScore = hook.score || 0;
    const pacing = result.pacing_and_retention || {};

    // Scores & Circles
    const scoreVal = document.getElementById('res-virality-score-val');
    const scoreCircle = document.getElementById('res-virality-score-circle');
    scoreVal.textContent = score;

    let scoreColor = '#ef4444';
    let verdictText = 'Alta Taxa de Swipe Prevista';
    let summaryText = 'O vídeo apresenta ganchos fracos ou lulls de retenção que levarão o algoritmo a interromper a distribuição após o primeiro teste.';

    if (score >= 75) {
      scoreColor = '#10b981';
      verdictText = 'Alto Potencial de Viralidade';
      summaryText = 'Excelente retenção esperada! Abertura magnética, pacing dinâmico e forte incentivo de conclusão alinhado aos algoritmos de 2026.';
    } else if (score >= 50) {
      scoreColor = '#f59e0b';
      verdictText = 'Potencial Moderado (Necessita Ajustes)';
      summaryText = 'O conteúdo tem valor, mas corre sério risco de perder até 50% dos espectadores nos pontos críticos apontados abaixo antes do fim.';
    }

    scoreCircle.style.borderColor = scoreColor;
    document.getElementById('res-virality-verdict').textContent = verdictText;
    document.getElementById('res-virality-verdict').style.color = scoreColor;
    document.getElementById('res-virality-summary').textContent = summaryText;

    // Hook Score Circle
    const hookScoreVal = document.getElementById('res-hook-score-val');
    const hookScoreCircle = document.getElementById('res-hook-score-circle');
    hookScoreVal.textContent = hookScore;
    hookScoreCircle.style.borderColor = hookScore >= 75 ? '#10b981' : hookScore >= 50 ? '#f59e0b' : '#ef4444';

    // Hook Studio Texts
    document.getElementById('res-hook-assessment').textContent = hook.first_3_seconds_assessment || 'Sem avaliação disponível.';
    document.getElementById('res-hook-visual-critique').textContent = hook.visual_hook_critique || 'Sem crítica visual.';
    document.getElementById('res-hook-rewrite').textContent = `"${hook.verbal_hook_rewrite || ''}"`;

    // Hook Frames Strip
    const framesStrip = document.getElementById('res-hook-frames-strip');
    framesStrip.innerHTML = '';
    const framesMeta = result.frames_metadata || [];
    framesMeta.filter(f => f.is_hook).forEach(f => {
      const item = document.createElement('div');
      item.style.cssText = 'flex-shrink:0; text-align:center; position:relative;';
      item.innerHTML = `
        <img src="${f.url}" style="width:130px; height:200px; object-fit:cover; border-radius:6px; border:1px solid #444; background:#000;">
        <span style="position:absolute; bottom:6px; left:6px; background:rgba(0,0,0,0.75); color:#fff; font-size:10px; font-weight:700; padding:2px 5px; border-radius:3px;">
          ${f.timestamp.toFixed(1)}s
        </span>
        ${f.face_detected ? '<span style="position:absolute; top:6px; right:6px; background:rgba(16,185,129,0.85); color:#fff; font-size:9px; padding:1px 4px; border-radius:3px;">Rosto</span>' : ''}
      `;
      framesStrip.appendChild(item);
    });

    // Pacing & Metrics
    const tech = result.technical_metadata || {};
    document.getElementById('res-pacing-verdict').textContent = pacing.pacing_verdict || 'Adequado';
    document.getElementById('res-cut-interval').textContent = `${tech.avg_cut_interval || 0}s`;
    document.getElementById('res-cut-count').textContent = tech.cut_count || 0;

    // Video Player Preview
    if (currentRunId) {
      videoPreview.src = `/api/virality/video/${currentRunId}`;
    }

    // Drop-off Risks List
    const dropoffList = document.getElementById('res-dropoff-list');
    dropoffList.innerHTML = '';
    const risks = pacing.dropoff_risks || [];
    if (risks.length === 0) {
      dropoffList.innerHTML = '<div style="color:#10b981; font-size:14px;">✅ Nenhum trecho crítico de queda imediata detectado no fluxo do roteiro!</div>';
    } else {
      risks.forEach((r, idx) => {
        const item = document.createElement('div');
        item.style.cssText = 'background:rgba(239, 68, 68, 0.08); border-left:4px solid #ef4444; border-radius:6px; padding:10px 14px; cursor:pointer; display:flex; justify-content:space-between; align-items:center; transition:background 0.2s;';
        item.innerHTML = `
          <div>
            <div style="font-weight:700; color:#f87171; font-size:13px;">Risco #${idx+1}: [${r.timestamp_start}s - ${r.timestamp_end}s]</div>
            <div style="font-size:13px; color:#e2e8f0; margin-top:2px;">${r.reason}</div>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" style="font-size:11px; flex-shrink:0;">▶ Pular no Vídeo</button>
        `;
        item.addEventListener('click', () => {
          if (videoPreview) {
            videoPreview.currentTime = r.timestamp_start;
            videoPreview.play();
          }
        });
        dropoffList.appendChild(item);
      });
    }

    // Actionable Fixes Checklist
    const fixesList = document.getElementById('res-actionable-fixes');
    fixesList.innerHTML = '';
    const fixes = result.actionable_fixes || [];
    fixes.forEach((fix, idx) => {
      const label = document.createElement('label');
      label.style.cssText = 'display:flex; align-items:flex-start; gap:10px; cursor:pointer; font-size:14px; line-height:1.4; color:#e2e8f0;';
      label.innerHTML = `
        <input type="checkbox" style="margin-top:3px; accent-color:#6366f1;">
        <span>${fix}</span>
      `;
      fixesList.appendChild(label);
    });

    // Call to Action
    document.getElementById('res-cta-eval').textContent = result.call_to_action_evaluation || 'Sem avaliação de CTA.';

    resultsContainer.style.display = 'block';
    resultsContainer.scrollIntoView({ behavior: 'smooth' });
  }

  // 7. Copy Hook Rewrite
  function setupCopyButton() {
    const btn = document.getElementById('btn-copy-hook-rewrite');
    btn.addEventListener('click', () => {
      const text = document.getElementById('res-hook-rewrite').textContent;
      navigator.clipboard.writeText(text).then(() => {
        showToast('Gancho verbal copiado para a área de transferência!');
      });
    });
  }

  // 8. Modal Handlers
  function setupModal() {
    btnOpenModal.addEventListener('click', () => { modal.style.display = 'flex'; });
    btnCloseModal.addEventListener('click', () => { modal.style.display = 'none'; });
    btnDoneModal.addEventListener('click', () => { modal.style.display = 'none'; });
    modal.addEventListener('click', (e) => {
      if (e.target === modal) modal.style.display = 'none';
    });
  }

  // 9. Action Buttons (Export JSON & New Audit)
  function setupActionButtons() {
    document.getElementById('btn-export-audit-json').addEventListener('click', () => {
      if (!currentAuditResult) return;
      const blob = new Blob([JSON.stringify(currentAuditResult, null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `virality-audit-${currentRunId || 'report'}.json`;
      a.click();
    });

    document.getElementById('btn-new-audit').addEventListener('click', () => {
      resultsContainer.style.display = 'none';
      form.scrollIntoView({ behavior: 'smooth' });
    });
  }

  function showToast(msg) {
    const t = document.getElementById('toast');
    if (t) {
      t.textContent = msg;
      t.style.display = 'block';
      setTimeout(() => { t.style.display = 'none'; }, 3000);
    }
  }

})();
