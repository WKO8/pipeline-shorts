// Pipeline Shorts UI — Frontend Logic

// -----------------------------------------------------------------------------
// State & Initialization
// -----------------------------------------------------------------------------
let _sse = null;
let _isRunning = false;
window._currentSub = null;

document.addEventListener('DOMContentLoaded', () => {
  initNavigation();
  initColorPickers();
  initRangeSliders();
  initCaptionPreview();
  initSubtitleMode();
  initSilenceToggle();
  initAudioDownload();
  initSourceMode();
  loadConverterFileLists();
  const upToggle = document.getElementById("run-upscale");
  const upOptions = document.getElementById("run-upscale-options");
  if (upToggle && upOptions) {
    upToggle.addEventListener("change", () => {
      upOptions.style.display = upToggle.checked ? "grid" : "none";
    });
  }


  // Bind actions
  document.getElementById('btn-run-pipeline').addEventListener('click', runPipelineAction);
  document.getElementById('btn-run-rerender').addEventListener('click', runRerenderAction);
  document.getElementById('btn-save-env').addEventListener('click', saveEnvAction);
  document.getElementById('btn-console-close').addEventListener('click', closeConsole);
  document.getElementById('btn-console-clear').addEventListener('click', clearConsole);
  document.getElementById('btn-console-copy').addEventListener('click', copyConsole);
  document.getElementById('btn-console-cancel').addEventListener('click', cancelPipeline);
  document.getElementById('btn-load-sub').addEventListener('click', loadSubAction);
  document.getElementById('btn-save-sub').addEventListener('click', saveSubtitle);
  document.getElementById('btn-overlay-fix').addEventListener('click', runOverlayFixAction);
  document.getElementById('btn-save-captions').addEventListener('click', saveCaptionsAction);
  document.getElementById('btn-run-upscale').addEventListener('click', runFaceOnlyUpscaleAction);
  document.getElementById('btn-refresh-exp').addEventListener('click', refreshData);
  document.getElementById('btn-adl-download').addEventListener('click', downloadAudioAction);
  document.getElementById('btn-adl-fetch-info').addEventListener('click', fetchVideoInfoAction);
  document.getElementById('btn-refresh-downloads').addEventListener('click', loadDownloadsList);
  document.getElementById('btn-run-converter').addEventListener('click', runConverterAction);
  document.getElementById('btn-conv-refresh-files').addEventListener('click', loadConverterFileLists);
  document.getElementById('conv-video-select').addEventListener('change', () => {
    document.getElementById('conv-file-path').value = document.getElementById('conv-video-select').value;
  });
  document.getElementById('conv-audio-select').addEventListener('change', () => {
    document.getElementById('conv-file-path').value = document.getElementById('conv-audio-select').value;
  });
  document.getElementById('adl-url').addEventListener('keydown', e => {
    if (e.key === 'Enter') fetchVideoInfoAction();
  });
  
  // Initial data fetch
  refreshData();
  fetchEnv();
  checkStatus();
});

// -----------------------------------------------------------------------------
// Source Mode (URL vs Local Video)
// -----------------------------------------------------------------------------
function initSourceMode() {
  const group = document.getElementById('run-source-mode-group');
  if (!group) return;
  group.querySelectorAll('.seg-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      group.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      const mode = btn.dataset.srcmode;
      document.getElementById('run-src-url-section').style.display = mode === 'url' ? '' : 'none';
      document.getElementById('run-src-local-section').style.display = mode === 'local' ? '' : 'none';
      updateDownloadOriginalToggle(mode);
      if (mode === 'local') loadDownloadsList();
    });
  });
  updateDownloadOriginalToggle(getSourceMode());
}

function updateDownloadOriginalToggle(mode) {
  const row = document.getElementById('run-download-original-row');
  const checkbox = document.getElementById('run-download-original');
  if (!row || !checkbox) return;
  const isUrlMode = mode === 'url';
  row.style.display = isUrlMode ? '' : 'none';
  if (!isUrlMode) checkbox.checked = false;
}

function getSourceMode() {
  const active = document.querySelector('#run-source-mode-group .seg-btn.active');
  return active ? active.dataset.srcmode : 'url';
}

async function loadDownloadsList() {
  const listEl = document.getElementById('run-downloads-list');
  if (!listEl) return;
  listEl.innerHTML = '<div class="empty-state" style="padding:24px;">⏳ Carregando...</div>';

  try {
    const res = await fetch('/api/downloads');
    const items = await res.json();

    if (items.length === 0) {
      listEl.innerHTML = '<div class="empty-state" style="padding:24px;">Nenhum vídeo encontrado em data/downloads/</div>';
      return;
    }

    listEl.innerHTML = '';
    items.forEach(item => {
      const sizeMb = (item.size / 1024 / 1024).toFixed(0);
      const hasAudio = !!item.audio_path;
      const audioLabel = hasAudio ? '🔊 Áudio OK' : '⚠️ Sem áudio';
      const audioColor = hasAudio ? 'var(--accent)' : '#ff9800';

      const div = document.createElement('div');
      div.className = 'explorer-item dl-library-item';
      div.dataset.videoPath = item.abs;
      div.dataset.audioPath = item.audio_path || '';
      div.dataset.stem = item.stem;
      div.dataset.name = item.name;
      div.dataset.size = item.size;
      div.innerHTML = `
        <div class="ei-icon" style="font-size:20px;">🎞️</div>
        <div style="flex:1; min-width:0;">
          <div class="ei-name" style="font-size:12px; font-weight:600;" title="${escHtml(item.abs)}">${escHtml(item.name)}</div>
          <div style="font-size:10px; color:var(--text3); margin-top:2px;">${sizeMb} MB</div>
        </div>
        <div style="font-size:10px; font-weight:700; color:${audioColor}; white-space:nowrap;">${audioLabel}</div>
      `;
      div.addEventListener('click', () => selectLocalVideo(item));
      listEl.appendChild(div);
    });
  } catch (err) {
    listEl.innerHTML = `<div class="empty-state" style="padding:24px; color:var(--err);">Erro: ${escHtml(err.message)}</div>`;
  }
}

function selectLocalVideo(item) {
  // Remove previous selection highlight
  document.querySelectorAll('.dl-library-item').forEach(el => el.classList.remove('selected'));

  // Highlight selected row
  const row = document.querySelector(`.dl-library-item[data-stem="${CSS.escape(item.stem)}"]`);
  if (row) row.classList.add('selected');

  // Fill hidden inputs
  document.getElementById('run-local-video-path').value = item.abs;
  document.getElementById('run-local-audio-path').value = item.audio_path || '';
  document.getElementById('run-local-stem').value = item.stem;

  // Auto-fill slug (only if still empty)
  const slugEl = document.getElementById('run-slug-local');
  if (slugEl && !slugEl.value.trim()) slugEl.value = item.stem;

  // Show selected info badge
  const infoEl = document.getElementById('run-local-selected-info');
  const nameEl = document.getElementById('run-local-selected-name');
  const metaEl = document.getElementById('run-local-selected-meta');
  const badgeEl = document.getElementById('run-local-audio-badge');
  const warnEl = document.getElementById('run-local-no-audio-warn');

  if (infoEl) {
    nameEl.textContent = item.name;
    const sizeMb = (item.size / 1024 / 1024).toFixed(1);
    metaEl.textContent = `${sizeMb} MB · ${item.abs}`;

    const hasAudio = !!item.audio_path;
    badgeEl.textContent = hasAudio ? '🔊 Áudio pronto' : '⚠️ Sem áudio extraído';
    badgeEl.style.background = hasAudio ? 'rgba(0,220,130,0.15)' : 'rgba(255,152,0,0.15)';
    badgeEl.style.color = hasAudio ? 'var(--accent)' : '#ff9800';
    badgeEl.style.border = hasAudio ? '1px solid var(--accent)' : '1px solid #ff9800';

    infoEl.style.display = 'flex';
    if (warnEl) warnEl.style.display = hasAudio ? 'none' : 'block';
  }
}

// -----------------------------------------------------------------------------
// Subtitle Mode Segmented Buttons (Re-render panel)
// -----------------------------------------------------------------------------
function initSubtitleMode() {
  const group = document.getElementById('rr-sub-mode-group');
  if (!group) return;
  group.querySelectorAll('.seg-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      group.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
    });
  });
}

function getSubtitleMode() {
  const active = document.querySelector('#rr-sub-mode-group .seg-btn.active');
  return active ? active.dataset.mode : 'burnin';
}

function initSilenceToggle() {
  const chk = document.getElementById('rr-jumps');
  const info = document.getElementById('rr-silence-info');
  if (!chk || !info) return;
  chk.addEventListener('change', () => {
    info.style.display = chk.checked ? 'block' : 'none';
  });
}

// -----------------------------------------------------------------------------
// Audio Download
// -----------------------------------------------------------------------------
async function loadConverterFileLists() {
  const videoSel = document.getElementById('conv-video-select');
  const audioSel = document.getElementById('conv-audio-select');
  if (!videoSel || !audioSel) return;

  videoSel.innerHTML = '<option value=>Carregando vídeos...</option>';
  audioSel.innerHTML = '<option value=>Carregando áudios...</option>';

  try {
    const [resClips, resDl] = await Promise.all([
      fetch('/api/clips'),
      fetch('/api/downloads')
    ]).catch(() => [null, null]);

    const videos = [];
    if (resClips && resClips.ok) {
       const clips = await resClips.json();
       clips.forEach(c => videos.push({name: '[Clip] ' + c.name, path: c.path}));
    }
    if (resDl && resDl.ok) {
       const dls = await resDl.json();
       dls.forEach(d => videos.push({name: '[Download] ' + d.name, path: 'data/downloads/' + d.name}));
    }

    videoSel.innerHTML = '<option value=>-- Selecione um vídeo --</option>';
    videos.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.path;
      opt.textContent = v.name;
      videoSel.appendChild(opt);
    });

    // Improved audio list
    const resAudio = await fetch('/api/explorer/audio').catch(() => null);
    audioSel.innerHTML = '<option value=>-- Selecione um áudio --</option>';
    if (resAudio && resAudio.ok) {
       const audios = await resAudio.json();
       audios.forEach(a => {
          const opt = document.createElement('option');
          opt.value = a.path;
          opt.textContent = a.name;
          audioSel.appendChild(opt);
       });
    }

  } catch (err) {
    console.error('Error loading converter lists:', err);
  }
}

async function runConverterAction() {
  const inputPath = document.getElementById('conv-file-path').value.trim();
  const format = document.getElementById('conv-format').value;
  const outputName = document.getElementById('conv-output-name').value.trim();

  if (!inputPath) {
    showToast('Selecione ou cole o caminho de um arquivo', 'err');
    return;
  }

  if (_isRunning) {
    showToast('Um processo já está em execução', 'err');
    openConsole();
    return;
  }

  openConsole();
  clearConsole();
  setRunningState(true);

  try {
    const res = await fetch('/api/convert', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        input_path: inputPath,
        format: format,
        output_name: outputName,
        davinci: document.getElementById('conv-davinci').checked
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Erro na conversão');

    startSSE();
  } catch (err) {
    setRunningState(false);
    appendLog({ type: 'error', text: 'Erro: ' + err.message });
  }
}

function initAudioDownload() {
  // Update dest path display based on format
  const fmt = document.getElementById('adl-format');
  if (fmt) fmt.addEventListener('change', () => {
    updateAudioDestPath();
  });
  updateAudioDestPath();
}

function updateAudioDestPath() {
  const fmt = (document.getElementById('adl-format')?.value || 'mp3');
  const slug = document.getElementById('adl-slug')?.value.trim() || '<slug>';
  const el = document.getElementById('adl-dest-path');
  if (el) el.textContent = `data/audio/${slug}.${fmt}`;
}

async function fetchVideoInfoAction() {
  const url = document.getElementById('adl-url').value.trim();
  if (!url) { showToast('Cole uma URL do YouTube primeiro', 'err'); return; }
  
  const btn = document.getElementById('btn-adl-fetch-info');
  btn.disabled = true;
  btn.textContent = '⏳ Buscando...';

  try {
    const res = await fetch('/api/audio-info', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Erro desconhecido');

    // Fill slug if empty
    const slugEl = document.getElementById('adl-slug');
    if (slugEl && !slugEl.value.trim() && data.slug) slugEl.value = data.slug;
    updateAudioDestPath();

    // Show info card
    const card = document.getElementById('adl-info-card');
    document.getElementById('adl-info-title').textContent = data.title || url;
    const dur = data.duration ? formatDuration(data.duration) : '?';
    document.getElementById('adl-info-meta').textContent =
      `${data.uploader || 'Canal desconhecido'} · ${dur}`;
    document.getElementById('adl-info-badge').textContent = dur;
    card.style.display = 'flex';

    showToast('Informações carregadas ✓', 'ok');
  } catch (err) {
    showToast('Falha ao buscar informações: ' + err.message, 'err');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔍 Verificar';
  }
}

function formatDuration(seconds) {
  const s = Math.floor(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`;
  return `${m}:${String(sec).padStart(2,'0')}`;
}

async function downloadAudioAction() {
  const url = document.getElementById('adl-url').value.trim();
  if (!url) { showToast('Cole uma URL do YouTube primeiro', 'err'); return; }
  
  const fmt    = document.getElementById('adl-format').value;
  const quality= document.getElementById('adl-quality').value;
  const slug   = document.getElementById('adl-slug').value.trim();
  const norm   = document.getElementById('adl-normalize').checked;
  const mono   = document.getElementById('adl-mono').checked;

  openConsole();
  clearConsole();
  setRunningState(true);

  try {
    const res = await fetch('/api/download-audio', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, format: fmt, quality, slug, normalize: norm, mono })
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || 'Erro desconhecido');

    startSSEAudio(data.result_path, data.title);
  } catch (err) {
    setRunningState(false);
    appendLog({ type: 'error', text: 'Erro ao iniciar download: ' + err.message });
  }
}

function startSSEAudio(resultPath, title) {
  if (_sse) _sse.close();
  _sse = new EventSource('/api/stream');
  
  _sse.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'ping') return;
    appendLog(msg);
    
    if (msg.type === 'done') {
      _sse.close();
      setRunningState(false);
      if (msg.code === 0) {
        showToast('Áudio baixado com sucesso ✓', 'ok');
        showDownloadedFile(resultPath, title);
      } else {
        showToast('Download falhou (código ' + msg.code + ')', 'err');
      }
    }
  };
  _sse.onerror = () => { _sse.close(); setRunningState(false); };
}

function showDownloadedFile(filePath, title) {
  if (!filePath) return;
  const card = document.getElementById('adl-results-card');
  const list = document.getElementById('adl-results-list');
  const name = filePath.split('/').pop();
  const item = document.createElement('div');
  item.className = 'explorer-item';
  item.innerHTML = `
    <div class="ei-icon">🎵</div>
    <div class="ei-name" title="${escHtml(filePath)}">${escHtml(name)}</div>
    <div class="ei-size" style="color:var(--accent);font-weight:600;">✓ Pronto</div>
  `;
  list.prepend(item);
  card.style.display = 'block';
}

// -----------------------------------------------------------------------------
// UI Utilities
// -----------------------------------------------------------------------------
function escHtml(unsafe) {
  if (!unsafe) return '';
  return unsafe
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function showToast(msg, type = '') {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'show ' + type;
  setTimeout(() => { t.className = ''; }, 2800);
}

// -----------------------------------------------------------------------------
// Navigation
// -----------------------------------------------------------------------------
function initNavigation() {
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', () => {
      const panel = item.dataset.panel;
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
      document.getElementById('panel-' + panel).classList.add('active');
      item.classList.add('active');
      
      const text = item.textContent.replace(item.querySelector('.nav-icon')?.textContent || '', '').trim();
      document.getElementById('topbar-title').textContent = text;

      if (panel === 'vertical' || panel === 'captions') {
        updatePreview();
      }
    });
  });
}

function initColorPickers() {
  const pairs = [
    ['cap-color-picker', 'cap-color-hex'],
    ['cap-activecolor-picker', 'cap-activecolor-hex'],
    ['cap-strokecolor-picker', 'cap-strokecolor-hex'],
    ['cap-hookcolor-picker', 'cap-hookcolor-hex'],
    ['cap-kwa-picker', 'cap-kwa-hex'],
    ['cap-kwb-picker', 'cap-kwb-hex']
  ];
  
  pairs.forEach(([pickerId, hexId]) => {
    const picker = document.getElementById(pickerId);
    const hex = document.getElementById(hexId);
    if (!picker || !hex) return;
    
    picker.addEventListener('input', () => { 
      hex.value = picker.value.toUpperCase(); 
      updatePreview(); 
    });
    
    hex.addEventListener('input', () => {
      let val = hex.value.trim();
      if (!val.startsWith('#')) val = '#' + val;
      if (/^#[0-9A-Fa-f]{6}$/.test(val)) {
        picker.value = val;
        updatePreview();
      }
    });
  });
}

function initRangeSliders() {
  document.querySelectorAll('input[type="range"]').forEach(range => {
    const valDisp = document.getElementById(range.id + '-val');
    if (valDisp) {
      range.addEventListener('input', () => {
        valDisp.textContent = range.value;
      });
    }
  });
}

// -----------------------------------------------------------------------------
// Caption Preview
// -----------------------------------------------------------------------------
function initCaptionPreview() {
  const inputs = document.querySelectorAll('#panel-captions input, #panel-captions select');
  inputs.forEach(el => {
    el.addEventListener('input', updatePreview);
    el.addEventListener('change', updatePreview);
  });
}

function getCaptionStyleObject() {
  const capSizeEl = document.getElementById('cap-size');
  const capUpperEl = document.getElementById('cap-upper');
  return {
    font: document.getElementById('cap-font')?.value || 'Montserrat',
    size: parseInt(capSizeEl?.value || '120', 10),
    color: document.getElementById('cap-color-hex')?.value || '#FFFFFF',
    activeColor: document.getElementById('cap-activecolor-hex')?.value || '#FFD400',
    strokeWidth: parseFloat(document.getElementById('cap-strokew')?.value || '3'),
    strokeColor: document.getElementById('cap-strokecolor-hex')?.value || '#000000',
    isUpper: capUpperEl?.checked ?? false,
    glow: parseFloat(document.getElementById('cap-glowblur')?.value || '1.2'),
    shadow: parseFloat(document.getElementById('cap-shadow')?.value || '0.8'),
    hookScale: parseInt(document.getElementById('cap-hookscale')?.value || '155', 10),
    hookColor: document.getElementById('cap-hookcolor-hex')?.value || '#FFD700',
    maxWords: parseInt(document.getElementById('cap-maxwords')?.value || '2', 10),
    maxChars: parseInt(document.getElementById('cap-maxchars')?.value || '20', 10),
    position: document.getElementById('cap-pos')?.value || 'bottom',
  };
}
window.getCaptionStyleObject = getCaptionStyleObject;

function updatePreview() {
  const text = document.getElementById('preview-text');
  const active = document.getElementById('preview-active');
  if(!text || !active) return;
  
  const st = getCaptionStyleObject();
  
  text.style.fontFamily = st.font;
  text.style.fontSize = st.size + 'px';
  text.style.color = st.color;
  text.style.paintOrder = 'stroke fill';
  active.style.fontFamily = st.font;
  active.style.paintOrder = 'stroke fill';
  
  if (st.strokeWidth > 0) {
    // Com paint-order: stroke fill, o preenchimento cobre o interior do traço,
    // garantindo contorno 100% externo, sem invadir o meio das letras.
    const strokePx = (st.strokeWidth * 2) + 'px';
    text.style.webkitTextStroke = `${strokePx} ${st.strokeColor}`;
    active.style.webkitTextStroke = `${strokePx} ${st.strokeColor}`;
  } else {
    text.style.webkitTextStroke = '0px transparent';
    active.style.webkitTextStroke = '0px transparent';
  }
  text.style.textTransform = st.isUpper ? 'uppercase' : 'none';
  active.style.textTransform = st.isUpper ? 'uppercase' : 'none';
  
  active.style.color = st.activeColor;
  text.style.textShadow = `${st.shadow}px ${st.shadow}px 2px rgba(0,0,0,0.8)`;
  active.style.textShadow = `${st.shadow}px ${st.shadow}px 2px rgba(0,0,0,0.8), 0 0 ${st.glow*5}px ${st.activeColor}`;

  // Keep vertical tab controls synchronized if present
  const vertSize = document.getElementById('vertical-font-size');
  const vertSizeDisp = document.getElementById('vertical-font-size-display');
  const vertUpper = document.getElementById('vertical-uppercase');
  const vertWords = document.getElementById('vertical-max-words');
  if (vertSize && document.activeElement !== vertSize) {
    vertSize.value = st.size;
  }
  if (vertSizeDisp) vertSizeDisp.textContent = st.size + 'px';
  if (vertUpper && document.activeElement !== vertUpper) {
    vertUpper.checked = Boolean(st.isUpper);
  }
  if (vertWords && document.activeElement !== vertWords) {
    vertWords.value = String(st.maxWords);
  }

  if (typeof window.updateVerticalCaptionPreview === 'function') {
    window.updateVerticalCaptionPreview();
  }
}

// -----------------------------------------------------------------------------
// Data Fetching (ENV, Clips, Subs)
// -----------------------------------------------------------------------------
async function refreshData() {
  try {
    const [resClips, resSubs] = await Promise.all([
      fetch('/api/clips'), fetch('/api/subtitles')
    ]);
    const clips = await resClips.json();
    const subs = await resSubs.json();
    
    // Populate dropdowns
    populateSelect('rr-clip', clips, 'abs', 'name', 'Nenhum clipe encontrado');
    populateSelect('up-clip', clips, 'abs', 'name', 'Nenhum clipe encontrado');
    populateSelect('rr-subs', subs, 'abs', 'name', 'Nenhuma legenda encontrada');
    populateSelect('sub-file-select', subs, 'abs', 'name', 'Nenhuma legenda encontrada');
    
    // Populate Explorer
    renderExplorerList('exp-clips-list', clips, '🎞️', (c) => {
      document.getElementById('rr-clip').value = c.abs;
      document.querySelector('[data-panel="rerender"]').click();
    });
    renderExplorerList('exp-subs-list', subs, '💬', (s) => {
      document.getElementById('sub-file-select').value = s.abs;
      document.querySelector('[data-panel="subtitles"]').click();
    });
    
  } catch (err) {
    console.error("Failed to load data:", err);
  }
}

function populateSelect(id, items, valKey, textKey, emptyText) {
  const sel = document.getElementById(id);
  if(!sel) return;
  sel.innerHTML = items.length === 0 ? `<option value="">${emptyText}</option>` : '';
  items.forEach(it => {
    const opt = document.createElement('option');
    opt.value = it[valKey];
    opt.textContent = it[textKey];
    sel.appendChild(opt);
  });
}

function renderExplorerList(id, items, icon, onClick) {
  const list = document.getElementById(id);
  if(!list) return;
  if(items.length === 0) {
    list.innerHTML = `<div class="empty-state">Vazio</div>`;
    return;
  }
  list.innerHTML = '';
  items.forEach(it => {
    const div = document.createElement('div');
    div.className = 'explorer-item';
    let sizeStr = '';
    if (it.size) sizeStr = (it.size / 1024 / 1024).toFixed(1) + ' MB';
    
    div.innerHTML = `
      <div class="ei-icon">${icon}</div>
      <div class="ei-name">${escHtml(it.name)}</div>
      <div class="ei-size">${sizeStr}</div>
    `;
    div.addEventListener('click', () => onClick(it));
    list.appendChild(div);
  });
}

async function fetchEnv() {
  try {
    const res = await fetch('/api/env');
    const data = await res.json();
    if(data.content) {
      document.getElementById('env-textarea').value = data.content;
      parseEnvToCaptionsForm(data.content);
    }
  } catch (err) {
    console.error("Failed to load env:", err);
  }
}

// -----------------------------------------------------------------------------
// Subtitle Editor
// -----------------------------------------------------------------------------
async function loadSubAction() {
  const absPath = document.getElementById('sub-file-select').value;
  if(!absPath) { showToast('Nenhum arquivo selecionado', 'err'); return; }
  
  try {
    const res = await fetch('/api/subtitle', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({abs: absPath})
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);
    
    window._currentSub = data;
    renderSubTable(data.events);
    document.getElementById('sub-editor-wrap').style.display = 'block';
  } catch(err) {
    showToast('Erro ao carregar legenda: ' + err.message, 'err');
  }
}

function renderSubTable(events) {
  const tbody = document.querySelector('#sub-table tbody');
  tbody.innerHTML = '';
  events.forEach(ev => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td class="sub-idx">${ev.idx + 1}</td>
      <td class="sub-time">${escHtml(ev.start)}</td>
      <td class="sub-time">${escHtml(ev.end)}</td>
      <td><input class="sub-text-inp" data-idx="${ev.idx}" value="${escHtml(ev.text)}"></td>
    `;
    tbody.appendChild(tr);
  });
}

async function saveSubtitle() {
  if(!window._currentSub) return;
  const events = Array.from(document.querySelectorAll('.sub-text-inp')).map(inp => ({
    idx: parseInt(inp.dataset.idx),
    text: inp.value
  }));
  
  try {
    const res = await fetch('/api/subtitle/save', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({abs: window._currentSub.path, events, raw: window._currentSub.raw})
    });
    const data = await res.json();
    if(data.error) throw new Error(data.error);
    showToast('Legendas salvas com sucesso ✓', 'ok');
  } catch(err) {
    showToast('Erro ao salvar: ' + err.message, 'err');
  }
}

async function runOverlayFixAction() {
  if(!window._currentSub) return;
  const args = ['--overlay-text-fix', '--subtitles', window._currentSub.path];
  runPipeline(args);
}

// -----------------------------------------------------------------------------
// Pipeline Execution & SSE Console
// -----------------------------------------------------------------------------
function buildRunArgs() {
  const args = [];
  const mode = getSourceMode();

  if (mode === 'local') {
    // ---- Local video mode: use --skip-download + --video-path + --audio-path
    const videoPath = document.getElementById('run-local-video-path').value.trim();
    const audioPath = document.getElementById('run-local-audio-path').value.trim();
    const stem      = document.getElementById('run-local-stem').value.trim();

    if (!videoPath) {
      showToast('Selecione um vídeo da biblioteca primeiro', 'err');
      return null;
    }

    args.push('--skip-download', '--video-path', videoPath);

    if (audioPath) {
      args.push('--audio-path', audioPath);
    } else {
      // Pipeline will extract audio from the video; we still need to pass audio-path
      // pointing to a location. Use the expected wav path (pipeline will create it).
      const expectedAudio = videoPath.replace(/\.(mp4|mov)$/i, '.wav')
        .replace('/downloads/', '/audio/');
      args.push('--audio-path', expectedAudio);
    }

    // Slug: prefer explicit slug, fall back to stem
    const slugLocal = document.getElementById('run-slug-local').value.trim();
    args.push('--slug', slugLocal || stem);

    const momentLocal = document.getElementById('run-moment-local').value.trim();
    if (momentLocal) args.push('--specific-moment', momentLocal);

  } else {
    // ---- URL mode: classic behavior
    const url = document.getElementById('run-url').value.trim();
    if (url) args.push('--url', url);

    const slug = document.getElementById('run-slug').value.trim();
    if (slug) args.push('--slug', slug);

    const moment = document.getElementById('run-moment').value.trim();
    if (moment) args.push('--specific-moment', moment);
  }

  // Shared options (both modes)
  const lang = document.getElementById('run-lang').value;
  if (lang) args.push('--language', lang);

  const profile = document.getElementById('run-profile').value;
  if (profile) args.push('--profile', profile);

  const curator = document.getElementById('run-curator').value;
  if (curator === 'jev') {
    args.push('--curator', 'jev');
    args.push('--jev-curate');
  } else if (curator) {
    args.push('--curator', curator);
  }

  const reasoningEffort = document.getElementById('run-reasoning-effort')?.value;
  if (reasoningEffort) args.push('--reasoning-effort', reasoningEffort);

  const maxclips = document.getElementById('run-maxclips').value.trim();
  if (maxclips) args.push('--max-clips', maxclips);

  const minsec = document.getElementById('run-minsec').value.trim();
  if (minsec) args.push('--clip-min-seconds', minsec);

  const maxsec = document.getElementById('run-maxsec').value.trim();
  if (maxsec) args.push('--clip-max-seconds', maxsec);

  const audiofmt = document.getElementById('run-audiofmt').value;
  if (audiofmt && audiofmt !== 'wav') args.push('--audio-format', audiofmt);

  const videoext = document.getElementById('run-videoext').value;
  if (videoext && videoext !== 'mp4') args.push('--output-ext', videoext);

  if (document.getElementById('run-dryrun').checked) args.push('--dry-run');
  if (document.getElementById('run-verbose').checked) args.push('--verbose');
  if (document.getElementById('run-sepsub').checked) args.push('--separate-subtitles');
  if (document.getElementById('run-enrich-effects').checked) args.push('--enrich-clip');
  if (document.getElementById('run-cinematic').checked) args.push('--cinematic-zoom');
  if (document.getElementById('run-davinci').checked) args.push('--davinci');
  if (document.getElementById('run-http-range')?.checked) args.push('--http-range');
  if (document.getElementById('run-jev-triage')?.checked) args.push('--jev-triage');
  // Upscaling
  if (document.getElementById("run-upscale").checked) {
    args.push("--upscale");
    const scale = document.getElementById("run-upscale-scale").value;
    if (scale) args.push("--upscale-scale", scale);
    const face = document.getElementById("run-upscale-face").value;
    if (face) args.push("--upscale-face-model", face);
    const fidelity = document.getElementById("run-upscale-fidelity").value.trim();
    if (fidelity) args.push("--upscale-codeformer-fidelity", fidelity);
  }

  if (mode === 'url' && document.getElementById('run-download-original').checked) {
    args.push('--download-original-video');
  }

  return args;
}

function buildRerenderArgs() {
  const args = ['--rerender'];
  
  const clipe = document.getElementById('rr-clip').value;
  if(clipe) args.push('--clipe', clipe);
  
  const subs = document.getElementById('rr-subs').value;
  if(subs) args.push('--subtitles', subs);
  
  // Subtitle output mode
  const mode = getSubtitleMode();
  if (mode === 'burnin') {
    // default — no extra flag needed (pipeline burns-in by default)
    // ensure separate-subtitles is NOT set
  } else if (mode === 'overlay') {
    args.push('--separate-subtitles');
  } else if (mode === 'both') {
    args.push('--separate-subtitles', '--also-burn-in');
  }
  
  if (document.getElementById('rr-retrack').checked) args.push('--retrack');
  if (document.getElementById('rr-fullframe').checked) args.push('--rerender-full-frame');
  if (document.getElementById('rr-jumps').checked) args.push('--rerender-allow-jump-cuts');
  if (document.getElementById('rr-cinematic').checked) args.push('--cinematic-zoom');
  if (document.getElementById('rr-davinci').checked) args.push('--davinci');
  
  const duodis = document.getElementById('rr-duodis').value.trim();
  if(duodis) args.push('--duo-disable-windows', duodis);
  
  const singledis = document.getElementById('rr-singledis').value.trim();
  if(singledis) args.push('--single-disable-windows', singledis);
  
  const focwin = document.getElementById('rr-focwin').value.trim();
  if(focwin) args.push('--focus-fix-windows', focwin);

  const videoext = document.getElementById('rr-videoext').value;
  if (videoext && videoext !== 'mp4') args.push('--output-ext', videoext);
  
  return args;
}

function runPipelineAction() {
  const args = buildRunArgs();
  if (args === null) return; // validation failed (e.g., no local video selected)
  runPipeline(args);
}

function runRerenderAction() {
  const args = buildRerenderArgs();
  runPipeline(args);
}

function runFaceOnlyUpscaleAction() {
  const clip = document.getElementById('up-clip').value;
  if(!clip) { showToast('Selecione um clipe', 'err'); return; }
  
  const args = ['--upscale-face-only-codeformer', '--clipe', clip];
  
  // also get fidelity from the panel
  const fid = document.getElementById('up-fidelity').value;
  if(fid) args.push('--upscale-codeformer-fidelity', fid);
  
  runPipeline(args);
}

async function runPipeline(args) {
  if (_isRunning) {
    showToast('Um processo já está em execução', 'err');
    openConsole();
    return;
  }
  
  openConsole();
  clearConsole();
  setRunningState(true);
  
  try {
    const res = await fetch('/api/run', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({args})
    });
    const data = await res.json();
    
    if (!res.ok) {
      throw new Error(data.error || 'Erro desconhecido');
    }
    
    startSSE();
  } catch(err) {
    setRunningState(false);
    appendLog({type: 'error', text: 'Erro ao iniciar: ' + err.message});
  }
}

function startSSE() {
  if (_sse) _sse.close();
  _sse = new EventSource('/api/stream');
  
  _sse.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === 'ping') return;
    
    appendLog(msg);
    
    if (msg.type === 'done') {
      _sse.close();
      setRunningState(false);
      refreshData();
      document.dispatchEvent(new CustomEvent('pipeline-done', {detail: msg}));
    }
  };
  
  _sse.onerror = () => {
    _sse.close();
    setRunningState(false);
  };
}

async function cancelPipeline() {
  if(!_isRunning) return;
  try {
    await fetch('/api/cancel', {method: 'POST'});
    showToast('Sinal de cancelamento enviado', 'ok');
  } catch(e) {}
}

async function checkStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    if(data.running && !_isRunning) {
      setRunningState(true);
      openConsole();
      startSSE();
    } else if(!data.running && _isRunning) {
      setRunningState(false);
    }
  } catch(e) {}
}

function setRunningState(running) {
  _isRunning = running;
  
  const gDot = document.getElementById('global-status-dot');
  const gText = document.getElementById('global-status-text');
  const cPulse = document.getElementById('console-pulse');
  const btnCancel = document.getElementById('btn-console-cancel');
  
  if (running) {
    gDot.className = 'status-dot running';
    gText.textContent = 'Rodando...';
    cPulse.className = 'status-dot running';
    btnCancel.style.display = 'inline-block';
  } else {
    gDot.className = 'status-dot done';
    gText.textContent = 'Pronto';
    cPulse.className = 'status-dot done';
    btnCancel.style.display = 'none';
  }
}

// -----------------------------------------------------------------------------
// Console UI
// -----------------------------------------------------------------------------
function openConsole() {
  document.getElementById('console-panel').classList.add('open');
}
function closeConsole() {
  document.getElementById('console-panel').classList.remove('open');
}
function clearConsole() {
  document.getElementById('console-body').innerHTML = '';
}
function copyConsole() {
  const text = document.getElementById('console-body').innerText;
  navigator.clipboard.writeText(text).then(() => showToast('Copiado para área de transferência', 'ok'));
}

function appendLog(msg) {
  const div = document.createElement('div');
  div.className = 'log-line log-' + msg.type;
  if(msg.type === 'info' && msg.text.startsWith('$')) div.className = 'log-line log-cmd';
  div.textContent = msg.text;
  
  const body = document.getElementById('console-body');
  body.appendChild(div);
  
  // Auto scroll
  body.scrollTop = body.scrollHeight;
}

// -----------------------------------------------------------------------------
// ENV Parsing & Saving
// -----------------------------------------------------------------------------
async function saveEnvAction() {
  const content = document.getElementById('env-textarea').value;
  try {
    const res = await fetch('/api/env', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({content})
    });
    if(!res.ok) throw new Error('Falha ao salvar');
    showToast('.env salvo com sucesso', 'ok');
    parseEnvToCaptionsForm(content);
  } catch(err) {
    showToast(err.message, 'err');
  }
}

function parseEnvToCaptionsForm(content) {
  const envMap = {};
  content.split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const m = trimmed.match(/^([A-Z0-9_]+)=(.*)$/);
    if(m) envMap[m[1]] = m[2].trim();
  });
  
  console.log("Parsed ENV Map:", envMap);

  const setV = (id, key, def) => {
    const el = document.getElementById(id);
    if(!el) return;
    let val = envMap[key];
    if (val === undefined || val === '') val = def;
    
    if (id.includes('hex')) {
        if (val && !val.startsWith('#') && /^[0-9A-Fa-f]{6}$/.test(val)) val = '#' + val;
        val = (val || '#FFFFFF').toUpperCase();
        
        // Also update the linked color picker
        const pickerId = id.replace('-hex', '-picker');
        const picker = document.getElementById(pickerId);
        if (picker) picker.value = val;
    }
    el.value = val;
  };
  
  const setC = (id, key, def) => {
    const el = document.getElementById(id);
    if(!el) return;
    let val = envMap[key] !== undefined ? envMap[key] : def;
    val = String(val).toLowerCase() === 'true' || String(val) === '1';
    el.checked = val;
  };
  
  setV('cap-font', 'CAPTIONS_FONT', 'Montserrat');
  setV('cap-size', 'CAPTIONS_FONT_SIZE', '120');
  setV('cap-strokew', 'CAPTIONS_STROKE_WIDTH', '3');
  setV('cap-pos', 'CAPTIONS_POSITION', 'center');
  setV('cap-hookscale', 'CAPTIONS_HOOK_FIRST_SCALE', '155');
  setV('cap-maxwords', 'CAPTIONS_SHORT_PHRASE_MAX_WORDS', '2');
  setV('cap-maxchars', 'CAPTIONS_MAX_CHARS', '20');
  setV('cap-shadow', 'CAPTIONS_SHADOW', '0.8');
  setV('cap-glowblur', 'CAPTIONS_KEYWORD_GLOW_BLUR', '1.2');
  
  setV('cap-color-hex', 'CAPTIONS_COLOR', '#FFD700');
  setV('cap-activecolor-hex', 'CAPTIONS_ACTIVE_WORD_COLOR', '#FFAA00');
  setV('cap-strokecolor-hex', 'CAPTIONS_STROKE_COLOR', '#DAA520');
  setV('cap-hookcolor-hex', 'CAPTIONS_HOOK_FIRST_COLOR', '#FFD700');
  setV('cap-kwa-hex', 'CAPTIONS_KEYWORD_COLOR_A', '#FFD700');
  setV('cap-kwb-hex', 'CAPTIONS_KEYWORD_COLOR_B', '#FFAA00');
  
  setC('cap-upper', 'CAPTIONS_FORCE_UPPERCASE', 'false');
  setC('cap-burnin', 'CAPTIONS_BURN_IN_VIDEO', 'true');
  setC('cap-overlay', 'CAPTIONS_EXPORT_OVERLAY_VIDEO', 'false');
  
  setV('up-enable', 'UPSCALING_ENABLED', 'false');
  setV('up-scale', 'UPSCALING_SCALE', '2');
  setV('up-facemodel', 'UPSCALING_FACE_MODEL', 'gfpgan');
  setV('up-fidelity', 'UPSCALING_CODEFORMER_FIDELITY', '0.75');
  
  // Trigger input events to update ranges (labels)
  document.querySelectorAll('#panel-captions input[type="range"]').forEach(el => {
    el.dispatchEvent(new Event('input'));
  });
  
  updatePreview();
}

async function saveCaptionsAction() {
  // Read current textarea
  let content = document.getElementById('env-textarea').value;
  
  const updates = {
    'CAPTIONS_FONT': document.getElementById('cap-font').value,
    'CAPTIONS_FONT_SIZE': document.getElementById('cap-size').value,
    'CAPTIONS_POSITION': document.getElementById('cap-pos').value,
    'CAPTIONS_STROKE_WIDTH': document.getElementById('cap-strokew').value,
    'CAPTIONS_HOOK_FIRST_SCALE': document.getElementById('cap-hookscale').value,
    'CAPTIONS_KEYWORD_GLOW_BLUR': document.getElementById('cap-glowblur').value,
    'CAPTIONS_SHORT_PHRASE_MAX_WORDS': document.getElementById('cap-maxwords').value,
    'CAPTIONS_MAX_CHARS': document.getElementById('cap-maxchars').value,
    'CAPTIONS_SHADOW': document.getElementById('cap-shadow').value,
    'CAPTIONS_COLOR': document.getElementById('cap-color-hex').value,
    'CAPTIONS_ACTIVE_WORD_COLOR': document.getElementById('cap-activecolor-hex').value,
    'CAPTIONS_STROKE_COLOR': document.getElementById('cap-strokecolor-hex').value,
    'CAPTIONS_HOOK_FIRST_COLOR': document.getElementById('cap-hookcolor-hex').value,
    'CAPTIONS_KEYWORD_COLOR_A': document.getElementById('cap-kwa-hex').value,
    'CAPTIONS_KEYWORD_COLOR_B': document.getElementById('cap-kwb-hex').value,
    'CAPTIONS_FORCE_UPPERCASE': document.getElementById('cap-upper').checked ? 'true' : 'false',
    'CAPTIONS_BURN_IN_VIDEO': document.getElementById('cap-burnin').checked ? 'true' : 'false',
    'CAPTIONS_EXPORT_OVERLAY_VIDEO': document.getElementById('cap-overlay').checked ? 'true' : 'false',
    'UPSCALING_ENABLED': document.getElementById('up-enable').value,
    'UPSCALING_SCALE': document.getElementById('up-scale').value,
    'UPSCALING_FACE_MODEL': document.getElementById('up-facemodel').value,
    'UPSCALING_CODEFORMER_FIDELITY': document.getElementById('up-fidelity').value

  };
  
  // Modify content string
  let lines = content.split('\n');
  const seenKeys = new Set();
  for(let i=0; i<lines.length; i++) {
    const m = lines[i].match(/^([A-Z0-9_]+)=(.*)$/);
    if(m && updates[m[1]] !== undefined) {
      lines[i] = m[1] + '=' + updates[m[1]];
      seenKeys.add(m[1]);
    }
  }
  for (const [k, v] of Object.entries(updates)) {
    if (!seenKeys.has(k)) {
      lines.push(`${k}=${v}`);
    }
  }
  
  document.getElementById('env-textarea').value = lines.join('\n');
  await saveEnvAction();
}
