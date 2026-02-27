// settings.js - Obsługa zakładki "Ustawienia"

const settingsModule = (() => {
  let currentSettings = {};
  let currentVoicesOption = [];

  async function load() {
    const container = document.getElementById('settings-container');
    container.innerHTML = '<div class="queue-empty">Trwa ładowanie...</div>';
    try {
      const [settingsResp, voicesResp] = await Promise.all([
        fetch('/api/settings'),
        fetch('/api/chatterbox-voices')
      ]);
      const data = await settingsResp.json();
      if (!data.success) throw new Error(data.error);
      currentSettings = data.settings || {};

      const vData = await voicesResp.json();
      if (vData.success) currentVoicesOption = vData.voices || [];

      render(currentSettings);
    } catch (e) {
      container.innerHTML = `<div class="queue-empty text-error">Błąd: ${escapeHtml(e.message)}</div>`;
    }
  }

  function render(s) {
    const container = document.getElementById('settings-container');
    container.innerHTML = `
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:20px;max-width:100%">

        <!-- General Settings -->
        <div class="card">
          <div class="settings-section-title">⚙️ Ogólne</div>

          <div class="form-group">
            <label>Domyślny format wyjściowy</label>
            <select id="s-output-format">
              <option value="mp3" ${s.output_format === 'mp3' ? 'selected' : ''}>MP3</option>
              <option value="wav" ${s.output_format === 'wav' ? 'selected' : ''}>WAV</option>
              <option value="ogg" ${s.output_format === 'ogg' ? 'selected' : ''}>OGG</option>
            </select>
          </div>

          <div class="form-group">
            <label>Domyślny bitrate MP3 (kbps)</label>
            <select id="s-bitrate">
              ${[64, 96, 128, 160, 192, 224, 256].map(b => `<option value="${b}" ${s.output_bitrate_kbps == b ? 'selected' : ''}>${b} kbps</option>`).join('')}
            </select>
          </div>

          <div class="form-group">
            <label>Crossfade (sekundy)</label>
            <input type="number" id="s-crossfade" step="0.05" min="0" max="2" value="${s.crossfade_duration ?? 0.1}">
          </div>

          <div class="form-group">
            <label>Cisza na początku (ms)</label>
            <input type="number" id="s-intro-silence" min="0" max="5000" value="${s.intro_silence_ms ?? 0}">
          </div>

          <div class="form-group">
            <label>Cisza między fragmentami (ms)</label>
            <input type="number" id="s-inter-silence" min="0" max="5000" value="${s.inter_chunk_silence_ms ?? 0}">
          </div>

          <div class="form-group">
            <div class="toggle-group">
              <label class="toggle">
                <input type="checkbox" id="s-group-speakers" ${s.group_chunks_by_speaker ? 'checked' : ''}>
                <span class="toggle-slider"></span>
              </label>
              <span>Grupuj fragmenty według mówcy</span>
            </div>
          </div>

          <div class="form-group">
            <div class="toggle-group">
              <label class="toggle">
                <input type="checkbox" id="s-cleanup-vram" ${s.cleanup_vram_after_job ? 'checked' : ''}>
                <span class="toggle-slider"></span>
              </label>
              <span>Zwalniaj VRAM po jobie</span>
            </div>
          </div>
          
          <div class="form-group">
            <label>Liczba procesów generacji (workery): <span id="s-workers-val">${s.num_workers ?? 1}</span></label>
            <input type="range" id="s-num-workers" min="1" max="8" step="1" value="${s.num_workers ?? 1}"
              oninput="document.getElementById('s-workers-val').textContent=this.value">
          </div>
        </div>

        <!-- Chatterbox Settings -->
        <div class="card">
          <div class="settings-section-title">🤖 Chatterbox Multilingual</div>

          <div class="form-group">
            <label>Domyślny język</label>
            <select id="s-language">
              <option value="pl" ${s.chatterbox_mtl_local_default_language === 'pl' ? 'selected' : ''}>Polski (pl)</option>
              <option value="en" ${s.chatterbox_mtl_local_default_language === 'en' ? 'selected' : ''}>Angielski (en)</option>
              <option value="de" ${s.chatterbox_mtl_local_default_language === 'de' ? 'selected' : ''}>Niemiecki (de)</option>
              <option value="fr" ${s.chatterbox_mtl_local_default_language === 'fr' ? 'selected' : ''}>Francuski (fr)</option>
              <option value="ru" ${s.chatterbox_mtl_local_default_language === 'ru' ? 'selected' : ''}>Rosyjski (ru)</option>
            </select>
          </div>

          <div class="form-group">
            <label>Urządzenie</label>
            <select id="s-device">
              <option value="auto" ${s.chatterbox_mtl_local_device === 'auto' ? 'selected' : ''}>Auto (GPU jeśli dostępne)</option>
              <option value="cuda" ${s.chatterbox_mtl_local_device === 'cuda' ? 'selected' : ''}>CUDA (GPU)</option>
              <option value="cpu" ${s.chatterbox_mtl_local_device === 'cpu' ? 'selected' : ''}>CPU</option>
            </select>
          </div>

          <div class="form-group">
            <label>Domyślny głos (Prompt)</label>
            <select id="s-default-prompt">
              <option value="">— Brak (użyj systemowego domyślnego) —</option>
              ${currentVoicesOption.map(v => `<option value="${v.file_name}" ${s.chatterbox_mtl_local_default_prompt === v.file_name ? 'selected' : ''}>${v.name}</option>`).join('')}
            </select>
          </div>

          <div class="form-group">
            <label>Rozmiar fragmentu (znaki): <span id="s-chunk-val">${s.chatterbox_mtl_local_chunk_size ?? 450}</span></label>
            <input type="range" id="s-chunk-size" min="100" max="800" step="50" value="${s.chatterbox_mtl_local_chunk_size ?? 450}"
              oninput="document.getElementById('s-chunk-val').textContent=this.value">
          </div>

          <div class="form-group">
            <label>Temperatura: <span id="s-temp-val">${s.chatterbox_mtl_local_temperature ?? 0.8}</span></label>
            <input type="range" id="s-temperature" min="0.1" max="1.5" step="0.05" value="${s.chatterbox_mtl_local_temperature ?? 0.8}"
              oninput="document.getElementById('s-temp-val').textContent=parseFloat(this.value).toFixed(2)">
          </div>

          <div class="form-group">
            <label>Prędkość mówienia (Speed Factor): <span id="s-speed-val">${s.chatterbox_mtl_local_speed_factor ?? 1.0}</span></label>
            <input type="range" id="s-speed-factor" min="0.5" max="2.0" step="0.05" value="${s.chatterbox_mtl_local_speed_factor ?? 1.0}"
              oninput="document.getElementById('s-speed-val').textContent=parseFloat(this.value).toFixed(2)">
          </div>

          <div class="form-group">
            <label>Przerwa między zdaniami (ms): <span id="s-pause-val">${s.chatterbox_mtl_local_sentence_pause_ms ?? 500}</span></label>
            <input type="range" id="s-sentence-pause" min="0" max="2000" step="50" value="${s.chatterbox_mtl_local_sentence_pause_ms ?? 500}"
              oninput="document.getElementById('s-pause-val').textContent=parseInt(this.value)">
          </div>

          <div class="form-group">
            <label>Top-P: <span id="s-topp-val">${s.chatterbox_mtl_local_top_p ?? 0.95}</span></label>
            <input type="range" id="s-top-p" min="0.1" max="1.0" step="0.05" value="${s.chatterbox_mtl_local_top_p ?? 0.95}"
              oninput="document.getElementById('s-topp-val').textContent=parseFloat(this.value).toFixed(2)">
          </div>

          <div class="form-group">
            <label>Kara za powtarzanie: <span id="s-rep-val">${s.chatterbox_mtl_local_repetition_penalty ?? 1.2}</span></label>
            <input type="range" id="s-rep-penalty" min="1.0" max="2.0" step="0.05" value="${s.chatterbox_mtl_local_repetition_penalty ?? 1.2}"
              oninput="document.getElementById('s-rep-val').textContent=parseFloat(this.value).toFixed(2)">
          </div>

          <div class="form-group">
            <label>Waga CFG: <span id="s-cfg-val">${s.chatterbox_mtl_local_cfg_weight ?? 0.0}</span></label>
            <input type="range" id="s-cfg-weight" min="0.0" max="1.0" step="0.05" value="${s.chatterbox_mtl_local_cfg_weight ?? 0.0}"
              oninput="document.getElementById('s-cfg-val').textContent=parseFloat(this.value).toFixed(2)">
          </div>

          <div class="form-group">
            <label>Wyolbrzymienie: <span id="s-exag-val">${s.chatterbox_mtl_local_exaggeration ?? 0.0}</span></label>
            <input type="range" id="s-exaggeration" min="0.0" max="1.0" step="0.05" value="${s.chatterbox_mtl_local_exaggeration ?? 0.0}"
              oninput="document.getElementById('s-exag-val').textContent=parseFloat(this.value).toFixed(2)">
          </div>

          <div class="form-group">
            <div class="toggle-group">
              <label class="toggle">
                <input type="checkbox" id="s-norm-loudness" ${s.chatterbox_mtl_local_norm_loudness !== false ? 'checked' : ''}>
                <span class="toggle-slider"></span>
              </label>
              <span>Normalizacja głośności (wyjście)</span>
            </div>
          </div>

          <div class="form-group">
            <div class="toggle-group">
              <label class="toggle">
                <input type="checkbox" id="s-prompt-norm" ${s.chatterbox_mtl_local_prompt_norm_loudness !== false ? 'checked' : ''}>
                <span class="toggle-slider"></span>
              </label>
              <span>Normalizacja głośności promptu</span>
            </div>
          </div>
        </div>

        <!-- Quality and Artifacts Settings -->
        <!-- Quality and Artifacts Settings -->
        <div class="card">
          <div class="settings-section-title">✨ Jakość / Artefakty (Pipeline)</div>

          <div class="form-group">
            <div class="toggle-group">
              <label class="toggle">
                <input type="checkbox" id="s-artifacts-enabled" ${s.artifacts?.enabled ? 'checked' : ''} onchange="settingsModule.toggleArtifacts()">
                <span class="toggle-slider"></span>
              </label>
              <span>Włącz redukcję artefaktów i panel dostrajania (Tryb Tuning)</span>
            </div>
          </div>

          <div id="artifacts-options" style="display: ${s.artifacts?.enabled ? 'block' : 'none'}; padding-left: 20px; border-left: 2px solid var(--border);">
            
            <div class="settings-section-title" style="font-size: 0.9em; margin-top: 10px;">Denoising (RNNoise)</div>
            
            <div class="form-group">
              <div class="toggle-group">
                <label class="toggle">
                  <input type="checkbox" id="s-artifacts-denoise" ${s.artifacts?.denoise_enabled ? 'checked' : ''}>
                  <span class="toggle-slider"></span>
                </label>
                <span>Włącz odszumianie (RNNoise)</span>
              </div>
              <div style="font-size:0.75rem; color:var(--text-muted); margin-top:4px;">Zalecane powolne dobieranie siły, gdyż może powodować metaliczny artefakt na wysokich tonach.</div>
            </div>

            <div class="form-group">
              <label>Siła odszumiania (Mieszanie z oryginałem): <span id="s-denoise-str-val">${s.artifacts?.denoise_strength ?? 0.5}</span></label>
              <input type="range" id="s-artifacts-denoise-str" min="0.1" max="1.0" step="0.1" value="${s.artifacts?.denoise_strength ?? 0.5}"
                oninput="document.getElementById('s-denoise-str-val').textContent=parseFloat(this.value).toFixed(1)">
            </div>


            <div class="settings-section-title" style="font-size: 0.9em; margin-top: 15px;">Post-processing (Auto-Editor)</div>

              <div class="form-group">
                <div class="toggle-group">
                  <label class="toggle">
                    <input type="checkbox" id="s-artifacts-autoeditor" ${s.artifacts?.autoeditor_enabled ? 'checked' : ''}>
                    <span class="toggle-slider"></span>
                  </label>
                  <span>Auto-Editor (cięcie ciszy/stutterów)</span>
                </div>
                <div style="font-size:0.75rem; color:var(--text-muted); margin-top:4px;">Automatycznie wycina długie pauzy i stuttery; wyższy margines = mniej agresywne cięcia.</div>
              </div>

              <div class="form-group">
                <label>Próg ciszy (Auto-Editor): <span id="s-ae-thresh-val">${s.artifacts?.autoeditor_threshold ?? 4.0}</span>%</label>
                <input type="range" id="s-artifacts-ae-thresh" min="0.1" max="10.0" step="0.1" value="${s.artifacts?.autoeditor_threshold ?? 4.0}"
                  oninput="document.getElementById('s-ae-thresh-val').textContent=parseFloat(this.value).toFixed(1)">
                <div style="font-size:0.75rem; color:var(--text-muted); margin-top:4px;">np. 4.0% - wyższa wartość utnie więcej dźwięku.</div>
              </div>

              <div class="form-group">
                <label>Margines (Auto-Editor) [s]: <span id="s-ae-marg-val">${s.artifacts?.autoeditor_margin ?? 0.2}</span></label>
                <input type="range" id="s-artifacts-ae-margin" min="0.0" max="1.0" step="0.05" value="${s.artifacts?.autoeditor_margin ?? 0.2}"
                  oninput="document.getElementById('s-ae-marg-val').textContent=parseFloat(this.value).toFixed(2)">
              </div>

            <div class="settings-section-title" style="font-size: 0.9em; margin-top: 20px;">Whisper walidacja</div>
            
            <div class="form-group">
              <div class="toggle-group">
                <label class="toggle">
                  <input type="checkbox" id="s-whisper-enabled" ${s.whisper?.enabled ? 'checked' : ''} onchange="settingsModule.toggleWhisper()">
                  <span class="toggle-slider"></span>
                </label>
                <span>Włącz walidację Whisper</span>
              </div>
              <div style="font-size:0.75rem; color:var(--text-muted); margin-top:4px;">Waliduje, czy model powiedział to, co w tekście; spowalnia generację.</div>
            </div>

            <div id="whisper-options" style="display: ${s.whisper?.enabled ? 'block' : 'none'}; padding-left: 20px;">
              <div class="form-group">
                <label>Backend Whispera</label>
                <select id="s-whisper-backend" onchange="settingsModule.updateWhisperModels()">
                  <option value="faster-whisper" ${s.whisper?.backend === 'faster-whisper' ? 'selected' : ''}>Faster-Whisper (SYSTRAN)</option>
                  <option value="whisper" ${s.whisper?.backend === 'whisper' ? 'selected' : ''}>OpenAI Whisper</option>
                </select>
              </div>

              <div class="form-group">
                <label>Model Whispera</label>
                <select id="s-whisper-model" data-selected="${s.whisper?.model_name || 'small'}">
                  <!-- Pula jest dynamiczna. Metoda updateWhisperModels() ją podmienia. -->
                </select>
                <div id="whisper-model-desc" style="font-size:0.75rem; color:var(--text-muted); margin-top:4px;"></div>
              </div>

              <div class="form-group">
                <label>Język Whisper (domyślnie)</label>
                <input type="text" id="s-whisper-lang" value="${s.whisper?.language || 'pl'}" placeholder="np. pl, en">
              </div>
            </div>

          </div>
        </div>

      </div>

      <div style="margin-top:16px;display:flex;gap:12px">
        <button class="btn btn-primary btn-lg" onclick="settingsModule.save()">💾 Zapisz ustawienia</button>
        <button class="btn btn-secondary" onclick="settingsModule.load()">↩ Odśwież</button>
      </div>
      <div id="settings-msg" style="margin-top:8px;font-size:0.875rem"></div>
    `;

    // Zaktualizuj modele whisper po załadowaniu html'a
    if(s.artifacts?.enabled && s.whisper?.enabled) {
        updateWhisperModels();
    }
  }

  function toggleArtifacts() {
    const isChecked = document.getElementById('s-artifacts-enabled').checked;
    document.getElementById('artifacts-options').style.display = isChecked ? 'block' : 'none';
  }



  function toggleWhisper() {
    const isChecked = document.getElementById('s-whisper-enabled').checked;
    document.getElementById('whisper-options').style.display = isChecked ? 'block' : 'none';
    if(isChecked) {
      updateWhisperModels();
    }
  }

  function updateWhisperModels() {
    const backend = document.getElementById('s-whisper-backend').value;
    const modelSelect = document.getElementById('s-whisper-model');
    const desc = document.getElementById('whisper-model-desc');
    const selected = modelSelect.getAttribute('data-selected') || 'small';
    
    let options = [];
    if (backend === 'whisper') {
      options = ['tiny', 'base', 'small', 'medium', 'large-v3'];
    } else {
      options = ['tiny', 'small', 'medium', 'large-v2'];
    }

    modelSelect.innerHTML = options.map(opt => 
      `<option value="${opt}" ${opt === selected ? 'selected' : ''}>${opt}</option>`
    ).join('');

    // Update tooltip
    modelSelect.onchange = () => {
      const val = modelSelect.value;
      if (val === 'tiny') desc.textContent = 'Najszybszy, najmniej dokładny.';
      else if (val.includes('large')) desc.textContent = 'Najlepsza jakość, największy VRAM/CPU.';
      else desc.textContent = 'Dobry kompromis szybkości i jakości.';
      modelSelect.setAttribute('data-selected', val);
    };
    modelSelect.onchange(); // Fire raz na start
  }

  async function save() {
    const get = id => document.getElementById(id);
    const payload = {
      output_format: get('s-output-format').value,
      output_bitrate_kbps: parseInt(get('s-bitrate').value),
      crossfade_duration: parseFloat(get('s-crossfade').value),
      intro_silence_ms: parseInt(get('s-intro-silence').value),
      inter_chunk_silence_ms: parseInt(get('s-inter-silence').value),
      group_chunks_by_speaker: get('s-group-speakers').checked,
      cleanup_vram_after_job: get('s-cleanup-vram').checked,
      num_workers: parseInt(get('s-num-workers').value),
      // NEW PARAMS:
      artifacts: {
        enabled: get('s-artifacts-enabled').checked,
        denoise_enabled: get('s-artifacts-denoise')?.checked || false,
        denoise_strength: parseFloat(get('s-artifacts-denoise-str')?.value || 0.5),
        autoeditor_enabled: get('s-artifacts-autoeditor')?.checked || false,
        autoeditor_threshold: parseFloat(get('s-artifacts-ae-thresh')?.value || 0.04),
        autoeditor_margin: parseFloat(get('s-artifacts-ae-margin')?.value || 0.2),
      },
      whisper: {
        enabled: get('s-whisper-enabled')?.checked || false,
        backend: get('s-whisper-backend')?.value || 'faster-whisper',
        model_name: get('s-whisper-model')?.value || 'small',
        language: get('s-whisper-lang')?.value || 'pl'
      },
      chatterbox_mtl_local_default_language: get('s-language').value,
      chatterbox_mtl_local_device: get('s-device').value,
      chatterbox_mtl_local_default_prompt: get('s-default-prompt').value,
      chatterbox_mtl_local_chunk_size: parseInt(get('s-chunk-size').value),
      chatterbox_mtl_local_temperature: parseFloat(get('s-temperature').value),
      chatterbox_mtl_local_top_p: parseFloat(get('s-top-p').value),
      chatterbox_mtl_local_repetition_penalty: parseFloat(get('s-rep-penalty').value),
      chatterbox_mtl_local_cfg_weight: parseFloat(get('s-cfg-weight').value),
      chatterbox_mtl_local_exaggeration: parseFloat(get('s-exaggeration').value),
      chatterbox_mtl_local_speed_factor: parseFloat(get('s-speed-factor').value),
      chatterbox_mtl_local_sentence_pause_ms: parseInt(get('s-sentence-pause').value),
      chatterbox_mtl_local_norm_loudness: get('s-norm-loudness').checked,
      chatterbox_mtl_local_prompt_norm_loudness: get('s-prompt-norm').checked,
    };

    try {
      const resp = await fetch('/api/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await resp.json();
      if (data.success) {
        showToast('Ustawienia zapisane!', 'success');
        const msg = document.getElementById('settings-msg');
        if (msg) { msg.textContent = '✓ Ustawienia zapisane. Zmiany ilości workerów wymagają restartu.'; msg.style.color = 'var(--success)'; }
      } else throw new Error(data.error);
    } catch (e) {
      showToast('Błąd zapisu: ' + e.message, 'error');
    }
  }

  return { load, save, toggleArtifacts, toggleWhisper, updateWhisperModels };
})();
