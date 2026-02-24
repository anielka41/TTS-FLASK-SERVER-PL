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
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;max-width:900px">

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
      </div>

      <div style="margin-top:16px;display:flex;gap:12px">
        <button class="btn btn-primary btn-lg" onclick="settingsModule.save()">💾 Zapisz ustawienia</button>
        <button class="btn btn-secondary" onclick="settingsModule.load()">↩ Odśwież</button>
      </div>
      <div id="settings-msg" style="margin-top:8px;font-size:0.875rem"></div>
    `;
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
      chatterbox_mtl_local_default_language: get('s-language').value,
      chatterbox_mtl_local_device: get('s-device').value,
      chatterbox_mtl_local_default_prompt: get('s-default-prompt').value,
      chatterbox_mtl_local_chunk_size: parseInt(get('s-chunk-size').value),
      chatterbox_mtl_local_temperature: parseFloat(get('s-temperature').value),
      chatterbox_mtl_local_top_p: parseFloat(get('s-top-p').value),
      chatterbox_mtl_local_repetition_penalty: parseFloat(get('s-rep-penalty').value),
      chatterbox_mtl_local_cfg_weight: parseFloat(get('s-cfg-weight').value),
      chatterbox_mtl_local_exaggeration: parseFloat(get('s-exaggeration').value),
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
        if (msg) { msg.textContent = '✓ Ustawienia zapisane'; msg.style.color = 'var(--success)'; }
      } else throw new Error(data.error);
    } catch (e) {
      showToast('Błąd zapisu: ' + e.message, 'error');
    }
  }

  return { load, save };
})();
