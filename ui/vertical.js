// The vertical tab shares the existing console, status and cancel controls.
async function verticalResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  if (!contentType.includes('application/json')) {
    if (response.status === 404 || response.status === 405) {
      throw new Error('O servidor não carregou as rotas de Vídeos Verticais. Reinicie a UI com ./run_ui.sh e recarregue a página.');
    }
    throw new Error(`O servidor retornou uma resposta inesperada (HTTP ${response.status}). Confira o log da UI.`);
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Falha na requisição (HTTP ${response.status}).`);
  return data;
}

document.addEventListener('DOMContentLoaded', () => {
  const el = name => document.getElementById('vertical-' + name);
  let pending = null;
  let currentMode = 'subtitles_only'; // 'subtitles_only' | 'remix'
  let currentBaseUploadId = null;
  let currentPreviewTimestamp = 3.0;

  // ---------------------------------------------------------------------------
  // Mode Selector (Subtitles Only vs Remix)
  // ---------------------------------------------------------------------------
  const modeSubsonly = document.getElementById('vertical-mode-subsonly');
  const modeRemix = document.getElementById('vertical-mode-remix');
  const remixCard = document.getElementById('vertical-remix-card');
  const runBtn = document.getElementById('vertical-run');

  function setMode(mode) {
    currentMode = mode;
    if (mode === 'subtitles_only') {
      modeSubsonly?.classList.add('active');
      modeRemix?.classList.remove('active');
      if (remixCard) remixCard.style.display = 'none';
      if (runBtn) runBtn.innerHTML = '🔥 Somente Adicionar Legendas';
    } else {
      modeRemix?.classList.add('active');
      modeSubsonly?.classList.remove('active');
      if (remixCard) remixCard.style.display = 'block';
      if (runBtn) runBtn.innerHTML = '🚀 Renderizar Remix Vertical';
    }
  }

  modeSubsonly?.addEventListener('click', () => setMode('subtitles_only'));
  modeRemix?.addEventListener('click', () => setMode('remix'));
  setMode('subtitles_only');

  // ---------------------------------------------------------------------------
  // Base Video Selector (Upload vs URL)
  // ---------------------------------------------------------------------------
  const baseTypeSelect = document.getElementById('vertical-base-type');
  const baseUploadSection = document.getElementById('vertical-base-upload-section');
  const baseUrlSection = document.getElementById('vertical-base-url-section');
  const baseFileInput = document.getElementById('vertical-base-file');
  const baseUploadStatus = document.getElementById('vertical-base-upload-status');
  const baseUrlInput = document.getElementById('vertical-url');

  function updateBaseSourceVisibility() {
    const isUpload = (baseTypeSelect?.value === 'upload');
    if (baseUploadSection) baseUploadSection.style.display = isUpload ? 'block' : 'none';
    if (baseUrlSection) baseUrlSection.style.display = isUpload ? 'none' : 'block';
  }

  baseTypeSelect?.addEventListener('change', () => {
    updateBaseSourceVisibility();
    const isUpload = (baseTypeSelect.value === 'upload');
    if (isUpload && currentBaseUploadId) {
      loadFramePreview(currentBaseUploadId, currentPreviewTimestamp);
    } else if (!isUpload && baseUrlInput?.value.trim()) {
      loadFramePreview(baseUrlInput.value.trim(), currentPreviewTimestamp);
    }
  });
  updateBaseSourceVisibility();

  async function uploadBaseFile(file) {
    if (!file) return;
    if (baseUploadStatus) baseUploadStatus.textContent = 'Enviando vídeo para gerar preview...';
    try {
      const form = new FormData();
      form.append('video', file);
      const response = await fetch('/api/vertical/upload', { method: 'POST', body: form });
      const data = await verticalResponse(response);
      currentBaseUploadId = data.id;
      if (baseUploadStatus) {
        baseUploadStatus.innerHTML = `✓ Vídeo pronto: <strong>${file.name}</strong>`;
        baseUploadStatus.style.color = 'var(--text-accent, #10b981)';
      }
      currentPreviewTimestamp = 3.0;
      await loadFramePreview(currentBaseUploadId, currentPreviewTimestamp);
    } catch (err) {
      if (baseUploadStatus) {
        baseUploadStatus.textContent = `Erro no upload: ${err.message}`;
        baseUploadStatus.style.color = '#ef4444';
      }
    }
  }

  baseFileInput?.addEventListener('change', () => {
    const file = baseFileInput.files?.[0];
    if (file) uploadBaseFile(file);
  });

  baseUrlInput?.addEventListener('blur', () => {
    const url = baseUrlInput.value.trim();
    if (url.startsWith('http')) {
      currentPreviewTimestamp = 3.0;
      loadFramePreview(url, currentPreviewTimestamp);
    }
  });

  // ---------------------------------------------------------------------------
  // Frame Preview Loader
  // ---------------------------------------------------------------------------
  async function loadFramePreview(source, timestamp = null) {
    if (!source) return;
    const mockupImg = document.getElementById('vertical-mockup-frame');
    if (!mockupImg) return;

    try {
      let endpoint = `/api/vertical/frame_preview?source=${encodeURIComponent(source)}`;
      if (timestamp !== null) {
        endpoint += `&timestamp=${timestamp}`;
      }
      const response = await fetch(endpoint);
      if (response.ok) {
        const blob = await response.blob();
        mockupImg.src = URL.createObjectURL(blob);
      }
    } catch (e) {
      console.warn('Erro ao carregar preview do frame:', e);
    }
  }

  const btnRefreshFrame = document.getElementById('btn-refresh-frame');
  btnRefreshFrame?.addEventListener('click', () => {
    currentPreviewTimestamp += 3.0;
    const isUpload = (baseTypeSelect?.value === 'upload');
    const activeSource = isUpload ? currentBaseUploadId : baseUrlInput?.value.trim();
    if (activeSource) {
      loadFramePreview(activeSource, currentPreviewTimestamp);
    } else {
      alert('Selecione ou envie um vídeo primeiro para visualizar os frames.');
    }
  });

  // ---------------------------------------------------------------------------
  // Height Slider & Presets & Safe Zone Guide
  // ---------------------------------------------------------------------------
  const sliderY = document.getElementById('vertical-y');
  const displayY = document.getElementById('vertical-y-display');
  const valueY = document.getElementById('vertical-y-value');
  const captionWrap = document.getElementById('vertical-mockup-caption-wrap');
  const safeCheck = document.getElementById('vertical-safe');
  const safeGuide = document.getElementById('vertical-safe-guide');
  const linkGotoStyle = document.getElementById('link-goto-style');

  // Direct vertical caption styling controls
  const sliderFontSize = document.getElementById('vertical-font-size');
  const displayFontSize = document.getElementById('vertical-font-size-display');
  const checkboxUppercase = document.getElementById('vertical-uppercase');
  const selectMaxWords = document.getElementById('vertical-max-words');

  function updateCaptionY(yVal) {
    if (displayY) displayY.textContent = yVal + '%';
    if (valueY) valueY.value = yVal;
    if (captionWrap) captionWrap.style.top = yVal + '%';
  }

  sliderY?.addEventListener('input', () => {
    updateCaptionY(sliderY.value);
  });

  document.querySelectorAll('.height-preset-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const y = btn.getAttribute('data-y');
      if (y && sliderY) {
        sliderY.value = y;
        updateCaptionY(y);
      }
    });
  });

  safeCheck?.addEventListener('change', () => {
    if (safeGuide) safeGuide.style.display = safeCheck.checked ? 'block' : 'none';
  });

  linkGotoStyle?.addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelector('[data-panel="captions"]')?.click();
  });

  // Vertical font size slider
  sliderFontSize?.addEventListener('input', () => {
    if (displayFontSize) displayFontSize.textContent = sliderFontSize.value + 'px';
    const capSize = document.getElementById('cap-size');
    const capSizeVal = document.getElementById('cap-size-val');
    if (capSize) capSize.value = sliderFontSize.value;
    if (capSizeVal) capSizeVal.textContent = sliderFontSize.value;
    if (typeof window.updateVerticalCaptionPreview === 'function') {
      window.updateVerticalCaptionPreview();
    }
  });

  // Vertical uppercase toggle
  checkboxUppercase?.addEventListener('change', () => {
    const capUpper = document.getElementById('cap-upper');
    if (capUpper) capUpper.checked = checkboxUppercase.checked;
    if (typeof window.updateVerticalCaptionPreview === 'function') {
      window.updateVerticalCaptionPreview();
    }
  });

  // Vertical max words dropdown
  selectMaxWords?.addEventListener('change', () => {
    const capMaxWords = document.getElementById('cap-maxwords');
    const capMaxWordsVal = document.getElementById('cap-maxwords-val');
    if (capMaxWords) capMaxWords.value = selectMaxWords.value;
    if (capMaxWordsVal) capMaxWordsVal.textContent = selectMaxWords.value;
    if (typeof window.updateVerticalCaptionPreview === 'function') {
      window.updateVerticalCaptionPreview();
    }
  });

  // Secondary volume slider
  const volEl = el('volume');
  if (volEl) {
    volEl.addEventListener('input', () => {
      const volValEl = el('volume-value');
      if (volValEl) volValEl.value = volEl.value;
    });
  }

  // ---------------------------------------------------------------------------
  // Real-Time Caption Style Preview Sync
  // ---------------------------------------------------------------------------
  window.updateVerticalCaptionPreview = function() {
    const textEl = document.getElementById('vertical-mockup-text');
    const activeEl = document.getElementById('vertical-mockup-active');
    const summaryEl = document.getElementById('vertical-style-summary');
    if (!textEl || !activeEl) return;

    if (typeof window.getCaptionStyleObject !== 'function') return;
    const st = window.getCaptionStyleObject();

    // Sync input controls if present
    if (sliderFontSize && document.activeElement !== sliderFontSize) {
      sliderFontSize.value = st.size;
    }
    if (displayFontSize) {
      displayFontSize.textContent = st.size + 'px';
    }
    if (checkboxUppercase && document.activeElement !== checkboxUppercase) {
      checkboxUppercase.checked = Boolean(st.isUpper);
    }
    if (selectMaxWords && document.activeElement !== selectMaxWords) {
      selectMaxWords.value = String(st.maxWords || 2);
    }

    // The phone mockup width is 270px (full HD 1080x1920 scaled down by 4x)
    const scale = 270 / 1080;
    const scaledSize = Math.max(10, Math.round(st.size * scale));

    textEl.style.fontFamily = st.font;
    textEl.style.fontSize = scaledSize + 'px';
    textEl.style.color = st.color;
    textEl.style.paintOrder = 'stroke fill';
    textEl.style.textTransform = st.isUpper ? 'uppercase' : 'none';

    activeEl.style.fontFamily = st.font;
    activeEl.style.fontSize = scaledSize + 'px';
    activeEl.style.color = st.activeColor;
    activeEl.style.paintOrder = 'stroke fill';
    activeEl.style.textTransform = st.isUpper ? 'uppercase' : 'none';

    if (st.strokeWidth > 0) {
      // With paint-order: stroke fill, doubling the stroke ensures 100% exterior stroke
      const strokePx = Math.max(1, (st.strokeWidth * 2 * scale)).toFixed(1) + 'px';
      textEl.style.webkitTextStroke = `${strokePx} ${st.strokeColor}`;
      activeEl.style.webkitTextStroke = `${strokePx} ${st.strokeColor}`;
    } else {
      textEl.style.webkitTextStroke = '0px transparent';
      activeEl.style.webkitTextStroke = '0px transparent';
    }

    const shadowPx = Math.max(0.5, (st.shadow * scale)).toFixed(1);
    textEl.style.textShadow = `${shadowPx}px ${shadowPx}px 2px rgba(0,0,0,0.8)`;
    activeEl.style.textShadow = `${shadowPx}px ${shadowPx}px 2px rgba(0,0,0,0.8), 0 0 ${(st.glow * 4 * scale).toFixed(1)}px ${st.activeColor}`;

    if (summaryEl) {
      summaryEl.innerHTML = `<strong>Fonte:</strong> ${st.font} (${st.size}px) · <strong>UPPERCASE:</strong> ${st.isUpper ? 'Sim' : 'Não'} · <strong>Palavras/Linha:</strong> ${st.maxWords || 2}<br><strong>Cores:</strong> <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${st.color};vertical-align:middle;border:1px solid #555;"></span> Base · <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${st.activeColor};vertical-align:middle;border:1px solid #555;"></span> Destaque · <strong>Contorno:</strong> ${st.strokeWidth}px (${st.strokeColor})`;
    }
  };

  // Initial call to sync styles
  if (typeof window.updateVerticalCaptionPreview === 'function') {
    window.updateVerticalCaptionPreview();
  }

  // ---------------------------------------------------------------------------
  // Secondary Video Dropdown Logic (for Remix Mode)
  // ---------------------------------------------------------------------------
  async function sources() {
    const sourceEl = el('source');
    if (!sourceEl) return;
    const mode = sourceEl.value;
    const presetField = el('preset-field');
    const uploadField = el('upload-field');
    const secondaryField = el('secondary-field');
    const layoutEl = el('layout');

    if (presetField) presetField.hidden = (mode !== 'preset');
    if (uploadField) uploadField.hidden = (mode !== 'upload');
    if (secondaryField) secondaryField.hidden = (mode !== 'url');
    if (layoutEl) {
      layoutEl.disabled = (mode === 'none');
      if (mode === 'none') layoutEl.value = 'full_screen';
    }

    if (mode === 'preset') {
      try {
        const response = await fetch('/api/vertical/presets');
        const items = await verticalResponse(response);
        const presetEl = el('preset');
        if (presetEl) {
          presetEl.replaceChildren(...items.map(item => new Option(item.name, item.id)));
          if (!items.length) presetEl.add(new Option('Galeria vazia — adicione arquivos em assets/vertical/', ''));
        }
      } catch (error) {
        if (el('message')) el('message').textContent = error.message;
      }
    }
  }

  el('source')?.addEventListener('change', sources);
  sources();

  // ---------------------------------------------------------------------------
  // Form Submission
  // ---------------------------------------------------------------------------
  el('form')?.addEventListener('submit', async event => {
    event.preventDefault();
    if (typeof _isRunning !== 'undefined' && _isRunning) {
      if (typeof openConsole === 'function') openConsole();
      return;
    }

    const runButton = el('run');
    if (runButton) runButton.disabled = true;
    if (el('result')) el('result').hidden = true;
    if (el('message')) el('message').textContent = 'Preparando…';

    try {
      // 1. Determine base video
      let base = null;
      const baseType = baseTypeSelect?.value || 'upload';
      if (baseType === 'upload') {
        if (!currentBaseUploadId) {
          const file = baseFileInput?.files?.[0];
          if (!file) throw new Error('Selecione um arquivo de vídeo base.');
          await uploadBaseFile(file);
        }
        base = currentBaseUploadId;
      } else {
        base = baseUrlInput?.value.trim();
        if (!base) throw new Error('Informe a URL do vídeo base.');
      }

      // 2. Determine secondary video
      let secondaryMode = 'none';
      let secondary = null;
      if (currentMode === 'remix') {
        secondaryMode = el('source')?.value || 'none';
        secondary = secondaryMode === 'url' ? el('secondary')?.value : el('preset')?.value;
        if (secondaryMode === 'upload') {
          const uploadFileInput = el('upload');
          if (!uploadFileInput?.files[0]) throw new Error('Selecione um vídeo complementar.');
          const form = new FormData();
          form.append('video', uploadFileInput.files[0]);
          if (el('message')) el('message').textContent = 'Enviando vídeo complementar…';
          const response = await fetch('/api/vertical/upload', { method: 'POST', body: form });
          secondary = (await verticalResponse(response)).id;
        }
      }

      // 3. Assemble options
      const st = (typeof window.getCaptionStyleObject === 'function') ? window.getCaptionStyleObject() : {};
      const options = {
        subtitles_only: (currentMode === 'subtitles_only'),
        layout: (currentMode === 'subtitles_only') ? 'full_screen' : (el('layout')?.value || 'full_screen'),
        fps: Number(el('fps')?.value || 30),
        invert: el('invert')?.checked || false,
        secondary_volume: Number(el('volume')?.value || 0) / 100,
        remove_silence: el('silence')?.checked || false,
        captions: el('captions')?.checked ?? true,
        caption_y: Number(sliderY?.value || 65),
        safe_zones: el('safe')?.checked ?? true,
        // Caption customization options
        caption_font_size: Number(st.size) || 120,
        caption_force_uppercase: Boolean(st.isUpper),
        caption_font: st.font || 'Montserrat',
        caption_color: st.color || '#FFFFFF',
        caption_active_color: st.activeColor || '#FFD400',
        caption_stroke_color: st.strokeColor || '#000000',
        caption_stroke_width: (st.strokeWidth !== undefined) ? Number(st.strokeWidth) : 3.0,
        caption_shadow: (st.shadow !== undefined) ? Number(st.shadow) : 0.8,
        caption_glow_blur: (st.glow !== undefined) ? Number(st.glow) : 1.2,
        caption_hook_scale: Number(st.hookScale) || 155,
        caption_hook_color: st.hookColor || '#FFD700',
        caption_max_words: Number(st.maxWords) || 2,
        caption_max_chars: Number(st.maxChars) || 20,
        caption_position: st.position || 'bottom'
      };

      const response = await fetch('/api/vertical/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base: base,
          secondary_mode: secondaryMode,
          secondary: secondary,
          options: options
        })
      });

      const data = await verticalResponse(response);
      pending = data.output;
      if (el('message')) el('message').textContent = 'Processando. Acompanhe no console; você pode cancelar a execução.';
      if (typeof openConsole === 'function') openConsole();
      if (typeof clearConsole === 'function') clearConsole();
      if (typeof setRunningState === 'function') setRunningState(true);
      if (typeof startSSE === 'function') startSSE();
    } catch (error) {
      if (el('message')) el('message').textContent = error.message;
    } finally {
      if (runButton) runButton.disabled = false;
    }
  });

  // ---------------------------------------------------------------------------
  // Pipeline Done Event Listener
  // ---------------------------------------------------------------------------
  document.addEventListener('pipeline-done', async event => {
    if (!pending) return;
    const output = pending;
    pending = null;
    if (event.detail.code !== 0) {
      if (el('message')) el('message').textContent = 'Processamento interrompido. Veja os detalhes no console.';
      return;
    }
    if (el('preview')) el('preview').src = output;
    if (el('download')) el('download').href = output;
    if (el('command')) el('command').href = output.replace('vertical.mp4', 'vertical.command.txt');
    for (const ext of ['ass', 'srt']) {
      const url = output.replace('vertical.mp4', 'captions.' + ext);
      const linkEl = el(ext);
      if (linkEl) {
        linkEl.href = url;
        linkEl.hidden = !(await fetch(url, { method: 'HEAD' })).ok;
      }
    }
    if (el('result')) el('result').hidden = false;
    if (el('message')) el('message').textContent = 'Vídeo concluído.';
  });
});

