// main.js - Główna logika zakładki "Generuj"

let currentVoices = [];
let currentSpeakers = [];
let voiceAssignments = {};

// ===== Tab Management =====
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  loadVoicesForAssignments();
  setupFormatToggle();
  setupDocUpload();
  startStatusPolling();
  startSystemStatusPolling();
  loadModelInfo();
  loadDictionaryBadge();
  setupLiveStats();
});

// ===== Model Selection =====
async function loadModelInfo() {
  try {
    const resp = await fetch("/api/model-info");
    const data = await resp.json();
    if (data.success) {
      const sel = document.getElementById("model-select");
      const statusEl = document.getElementById("model-status-text");
      const type = data.type || "";
      // Map loaded model type to selector value
      if (type === "multilingual") sel.value = "chatterbox-multilingual";
      else if (type === "turbo") sel.value = "chatterbox-turbo";
      else if (type === "original") sel.value = "chatterbox";
      if (statusEl) {
        statusEl.textContent = `${data.class_name || "?"} (${data.device || "?"})`;
        statusEl.style.color = data.loaded ? "var(--success)" : "var(--error)";
      }
    }
  } catch (e) {
    console.error("loadModelInfo:", e);
  }
}

async function changeModel() {
  const sel = document.getElementById("model-select");
  const btn = document.getElementById("model-reload-btn");
  const statusEl = document.getElementById("model-status-text");
  const newModel = sel.value;

  if (
    !confirm(
      `Czy na pewno chcesz zmienić model na: ${sel.options[sel.selectedIndex].text}?\n\nTo zwolni VRAM i przeładuje model.`,
    )
  ) {
    return;
  }

  btn.disabled = true;
  btn.textContent = "⏳ Ładowanie...";
  if (statusEl) {
    statusEl.textContent = "Przeładowywanie...";
    statusEl.style.color = "var(--warning)";
  }

  try {
    // First update config with new model selector
    const saveResp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model_repo_id: newModel }),
    });

    // Then trigger model reload
    const resp = await fetch("/api/restart-server", { method: "POST" });
    const data = await resp.json();
    if (data.success) {
      showToast(data.message || "Model przeładowany", "success");
      loadModelInfo();
    } else {
      showToast(data.error || "Błąd przeładowania modelu", "error");
    }
  } catch (e) {
    showToast("Błąd komunikacji z serwerem", "error");
    console.error("changeModel:", e);
  } finally {
    btn.disabled = false;
    btn.textContent = "🔄 Przeładuj model";
    loadModelInfo();
  }
}

function initTabs() {
  const btns = document.querySelectorAll(".tab-btn");
  btns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.dataset.tab;
      btns.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document
        .querySelectorAll(".tab-pane")
        .forEach((p) => p.classList.remove("active"));
      const pane = document.getElementById(`tab-${tabId}`);
      if (pane) pane.classList.add("active");

      // Lazy load tab content
      if (tabId === "queue") queueModule.refresh();
      if (tabId === "library") libraryModule.refresh();
      if (tabId === "voices") voiceManager.refresh();
      if (tabId === "dictionary") loadDictionary();
      if (tabId === "settings") settingsModule.load();
      if (tabId === "logs") logsModule.loadLogs();
    });
  });
}

// ===== Live Stats (client-side, no API call) =====
let _liveStatsTimer = null;
function setupLiveStats() {
  const ta = document.getElementById("text-input");
  ta.addEventListener("input", () => {
    clearTimeout(_liveStatsTimer);
    _liveStatsTimer = setTimeout(updateLiveStats, 400);
  });
  // Run once on load
  updateLiveStats();
}

function updateLiveStats() {
  const text = document.getElementById("text-input").value;
  const words = text.trim() ? text.trim().split(/\s+/).length : 0;
  document.getElementById("stat-words").textContent = words;

  // Estimate duration: ~150 words per minute for TTS
  const dur = (words / 150) * 60;
  document.getElementById("stat-duration").textContent =
    dur >= 60
      ? `${Math.floor(dur / 60)}m ${Math.round(dur % 60)}s`
      : `${Math.round(dur)}s`;

  // Count speakers from tags like [narrator], [alice], etc.
  const speakerTags = text.match(/\[([^\]/]+)\]/g) || [];
  const speakers = new Set();
  speakerTags.forEach((tag) => {
    const name = tag.slice(1, -1).toLowerCase();
    // Exclude known inline tags
    if (
      !["sigh", "laugh", "cough", "pause", "/narrator", "/default"].includes(
        name,
      ) &&
      !name.startsWith("/")
    ) {
      speakers.add(name);
    }
  });
  document.getElementById("stat-speakers").textContent =
    speakers.size || (words > 0 ? 1 : 0);

  // Count chapters
  const chapterCount = (text.match(/\{ROZDZIAL\}/g) || []).length;
  const chapContainer = document.getElementById("stat-chapters-container");
  if (chapterCount > 0) {
    chapContainer.style.display = "flex";
    document.getElementById("stat-chapters").textContent = chapterCount + 1;
  } else {
    chapContainer.style.display = "none";
  }
}

// ===== Text Analysis =====
async function analyzeText() {
  const text = document.getElementById("text-input").value.trim();
  if (!text) {
    showToast("Wpisz tekst przed analizą", "warning");
    return;
  }

  const btn = document.getElementById("analyze-btn");
  btn.disabled = true;
  btn.textContent = "⏳ Analiza...";

  try {
    const resp = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.error);

    // Update stats
    document.getElementById("stats-bar").style.display = "flex";
    document.getElementById("stat-speakers").textContent =
      data.speaker_count || 1;
    document.getElementById("stat-chunks").textContent = data.total_chunks || 0;
    document.getElementById("stat-words").textContent = data.word_count || 0;
    const dur = data.estimated_duration || 0;
    document.getElementById("stat-duration").textContent =
      dur >= 60
        ? `${Math.floor(dur / 60)}m ${Math.round(dur % 60)}s`
        : `${Math.round(dur)}s`;

    if (data.chapter_count > 0) {
      document.getElementById("stat-chapters-container").style.display = "flex";
      document.getElementById("stat-chapters").textContent = data.chapter_count;
    }

    // Update speaker assignments
    currentSpeakers =
      data.speakers && data.speakers.length > 0 ? data.speakers : ["default"];
    renderVoiceAssignments(currentSpeakers);

    showToast(
      `Analiza: ${data.total_chunks} fragmentów, ${data.speaker_count} mówców`,
      "success",
    );
  } catch (e) {
    showToast("Błąd analizy: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "🔍 Analizuj";
  }
}

function renderVoiceAssignments(speakers) {
  const card = document.getElementById("voice-assignments-card");
  const tbody = document.getElementById("voice-assignments-body");
  card.style.display = "block";
  tbody.innerHTML = "";

  speakers.forEach((speaker) => {
    const current = voiceAssignments[speaker] || {};
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="speaker-tag">🎭 ${speaker}</span></td>
      <td>
        <select class="voice-select" data-speaker="${speaker}" onchange="updateVoiceAssignment('${speaker}', 'voice', this.value)" style="min-width:180px">
          <option value="">— Brak (domyślny) —</option>
          ${currentVoices.map((v) => `<option value="${v.file_name}" ${current.audio_prompt_path === v.file_name ? "selected" : ""}>${v.name} (${v.duration_seconds}s)</option>`).join("")}
        </select>
      </td>
      <td>
        <div class="fx-controls">
          <label>Pitch</label>
          <input type="number" step="0.5" min="-12" max="12" value="${current.fx?.pitch || 0}"
            title="Semitony"
            onchange="updateVoiceAssignmentFX('${speaker}', 'pitch', this.value)" style="width:60px">
          <label>Speed</label>
          <input type="number" step="0.1" min="0.5" max="2.0" value="${current.fx?.speed || 1.0}"
            onchange="updateVoiceAssignmentFX('${speaker}', 'speed', this.value)" style="width:60px">
        </div>
      </td>
      <td>
        <button class="btn btn-secondary btn-sm" onclick="previewVoice('${speaker}')">▶ Test</button>
      </td>
    `;
    tbody.appendChild(tr);
  });
}

function updateVoiceAssignment(speaker, field, value) {
  if (!voiceAssignments[speaker]) voiceAssignments[speaker] = {};
  if (field === "voice") {
    voiceAssignments[speaker].audio_prompt_path = value;
    voiceAssignments[speaker].voice = value;
  } else {
    voiceAssignments[speaker][field] = value;
  }
}

function updateVoiceAssignmentFX(speaker, fxField, value) {
  if (!voiceAssignments[speaker]) voiceAssignments[speaker] = {};
  if (!voiceAssignments[speaker].fx)
    voiceAssignments[speaker].fx = { enabled: true };
  voiceAssignments[speaker].fx.enabled = true;
  voiceAssignments[speaker].fx[fxField] = parseFloat(value);
}

async function previewVoice(speaker) {
  const assignment = voiceAssignments[speaker] || {};
  const voice = assignment.audio_prompt_path;
  if (!voice) {
    showToast("Wybierz najpierw prompt głosowy", "warning");
    return;
  }

  showToast("Generowanie podglądu...", "info");
  try {
    const resp = await fetch("/api/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        voice,
        lang_code: "pl",
        text: "Witaj, to jest próbka głosu.",
        fx: assignment.fx,
      }),
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.error);

    // Play base64 audio
    const audio = new Audio("data:audio/wav;base64," + data.audio_base64);
    audio.play();
    showToast(`Podgląd: ${data.duration}s`, "success");
  } catch (e) {
    showToast("Błąd podglądu: " + e.message, "error");
  }
}

// ===== Generate =====
async function generateAudio() {
  const text = document.getElementById("text-input").value.trim();
  const projectTitle = document.getElementById("project-title").value.trim();
  // Check if chapters exist
  const chaptersRepeater = document.getElementById("chapters-repeater");
  const hasChapters =
    chaptersRepeater && chaptersRepeater.style.display !== "none";

  if (!text && !hasChapters) {
    showToast("Wpisz tekst do wygenerowania", "warning");
    return;
  }

  const btn = document.getElementById("generate-btn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span> Generowanie...';

  const output_format = document.getElementById("opt-format").value;
  const output_bitrate_kbps = parseInt(
    document.getElementById("opt-bitrate").value,
  );

  // Build final voice assignments
  const finalAssignments = {};
  currentSpeakers.forEach((speaker) => {
    finalAssignments[speaker] = voiceAssignments[speaker] || {};
    if (!finalAssignments[speaker].lang_code)
      finalAssignments[speaker].lang_code = "pl";
  });

  // Collect chapters from repeater if available
  let chapters = [];
  if (hasChapters) {
    const items = document.querySelectorAll("#chapters-list .chapter-item");
    items.forEach((item) => {
      const textarea = item.querySelector("textarea");
      if (textarea && textarea.value.trim()) {
        chapters.push(textarea.value.trim());
      }
    });
  }

  try {
    const body = {
      text: hasChapters ? "" : text,
      title: projectTitle,
      voice_assignments: finalAssignments,
      output_format,
      output_bitrate_kbps,
      tts_engine: "chatterbox_mtl_local",
    };
    if (chapters.length > 0) {
      body.chapters = chapters;
    }

    const resp = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!data.success) throw new Error(data.error);

    showToast(
      `Job ${data.job_id.slice(0, 8)}... dodany do kolejki (pozycja: ${data.queue_position || 1})`,
      "success",
    );

    // Switch to queue tab
    setTimeout(() => {
      document.querySelector('[data-tab="queue"]').click();
    }, 800);
  } catch (e) {
    showToast("Błąd generowania: " + e.message, "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "🚀 Generuj audio";
  }
}

// ===== Voices for assignments =====
async function loadVoicesForAssignments() {
  try {
    const resp = await fetch("/api/chatterbox-voices");
    const data = await resp.json();
    if (data.success) currentVoices = data.voices || [];
  } catch (e) {
    console.error("Błąd ładowania głosów:", e);
  }
}

// ===== Tag insertion =====
function insertTag(speakerName) {
  const ta = document.getElementById("text-input");
  const start = ta.selectionStart;
  const end = ta.selectionEnd;
  const selected = ta.value.substring(start, end);
  const tag = selected
    ? `[${speakerName}]${selected}[/${speakerName}]`
    : `[${speakerName}][/${speakerName}]`;
  ta.value = ta.value.substring(0, start) + tag + ta.value.substring(end);
  const newPos = selected ? start + tag.length : start + speakerName.length + 2;
  ta.selectionStart = ta.selectionEnd = newPos;
  ta.focus();
}

function insertInlineTag(tag) {
  const ta = document.getElementById("text-input");
  const pos = ta.selectionStart;
  ta.value = ta.value.substring(0, pos) + tag + ta.value.substring(pos);
  ta.selectionStart = ta.selectionEnd = pos + tag.length;
  ta.focus();
}

function insertCustomTag() {
  const name = prompt("Nazwa mówcy (np. alice, narrator, bohater):");
  if (name && /^[a-zA-Z0-9_-]+$/.test(name.trim())) {
    insertTag(name.trim().toLowerCase());
  } else if (name) {
    showToast("Nieprawidłowa nazwa - tylko litery, cyfry, _ i -", "warning");
  }
}

function clearText() {
  if (
    document.getElementById("text-input").value &&
    confirm("Wyczyścić tekst?")
  ) {
    document.getElementById("text-input").value = "";
    document.getElementById("stats-bar").style.display = "none";
    document.getElementById("voice-assignments-card").style.display = "none";
  }
}

function insertRozdzial() {
  const ta = document.getElementById("text-input");
  const start = ta.selectionStart;
  const end = ta.selectionEnd;
  const tag = "{ROZDZIAL}";
  ta.value = ta.value.substring(0, start) + tag + ta.value.substring(end);
  ta.selectionStart = ta.selectionEnd = start + tag.length;
  ta.focus();
}

// ===== UI Toggles =====
function setupFormatToggle() {
  document.getElementById("opt-format").addEventListener("change", function () {
    document.getElementById("bitrate-group").style.display =
      this.value === "mp3" ? "block" : "none";
  });
}

// ===== Doc Upload =====
function setupDocUpload() {
  const input = document.getElementById("doc-upload-input");
  input.addEventListener("change", async () => {
    if (!input.files[0]) return;
    const formData = new FormData();
    formData.append("file", input.files[0]);
    showToast("Ładowanie dokumentu...", "info");
    try {
      const resp = await fetch("/api/upload-document", {
        method: "POST",
        body: formData,
      });
      const data = await resp.json();
      if (!data.success) throw new Error(data.error);
      document.getElementById("text-input").value = data.text;
      showToast(`Załadowano: ${data.filename}`, "success");
    } catch (e) {
      showToast("Błąd ładowania: " + e.message, "error");
    }
    input.value = "";
  });
}

// ===== Chapter Import =====
let currentChapters = [];

function importChapters() {
  const text = document.getElementById("text-input").value.trim();
  if (!text) {
    showToast("Wpisz tekst z tagami {ROZDZIAL} przed importem", "warning");
    return;
  }

  const parts = text
    .split("{ROZDZIAL}")
    .map((p) => p.trim())
    .filter((p) => p.length > 0);
  if (parts.length <= 1) {
    showToast(
      "Nie znaleziono tagów {ROZDZIAL}. Dodaj {ROZDZIAL} między rozdziałami.",
      "warning",
    );
    return;
  }

  currentChapters = parts;
  renderChapters();
  showToast(`Zaimportowano ${parts.length} rozdziałów`, "success");
}

function renderChapters() {
  const repeater = document.getElementById("chapters-repeater");
  const list = document.getElementById("chapters-list");
  const badge = document.getElementById("chapters-count-badge");

  if (currentChapters.length === 0) {
    repeater.style.display = "none";
    return;
  }

  repeater.style.display = "block";
  badge.textContent = currentChapters.length;

  list.innerHTML = currentChapters
    .map((ch, i) => {
      const preview = ch.substring(0, 80).replace(/\n/g, " ");
      return `
      <div class="chapter-item" style="padding:10px; margin-bottom:8px; background:var(--bg-primary); border-radius:8px; border:1px solid var(--border)">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px">
          <strong style="font-size:0.85rem">📖 Rozdział ${i + 1}</strong>
          <button class="btn btn-danger btn-sm" onclick="removeChapter(${i})" style="padding:2px 8px">🗑</button>
        </div>
        <textarea style="width:100%; min-height:60px; font-size:0.82rem; resize:vertical">${escapeHtml(ch)}</textarea>
      </div>
    `;
    })
    .join("");
}

function removeChapter(index) {
  currentChapters.splice(index, 1);
  renderChapters();
  showToast("Rozdział usunięty", "info");
}

function clearChapters() {
  if (!confirm("Wyczyścić wszystkie rozdziały?")) return;
  currentChapters = [];
  renderChapters();
  showToast("Rozdziały wyczyszczone", "info");
}

// ===== Text Converter (through Dictionary) =====
async function convertText() {
  const input = document.getElementById("converter-input-text").value;
  if (!input.trim()) {
    showToast("Wklej tekst do przetworzenia", "warning");
    return;
  }

  try {
    const resp = await fetch("/api/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: input }),
    });
    const data = await resp.json();
    if (data.success) {
      document.getElementById("converter-output-text").value = data.text;
      showToast("Tekst przetworzony przez słownik", "success");
    } else {
      showToast("Błąd: " + (data.error || "nieznany"), "error");
    }
  } catch (e) {
    showToast("Błąd komunikacji: " + e.message, "error");
  }
}

// ===== Dictionary =====
async function loadDictionary() {
  const container = document.getElementById("dictionary-list");
  try {
    const resp = await fetch("/api/dictionary");
    const data = await resp.json();
    if (!data.success) return;

    const badge = document.getElementById("dict-badge");
    if (data.count > 0) {
      badge.textContent = data.count;
      badge.style.display = "inline-flex";
    } else {
      badge.style.display = "none";
    }

    if (!data.entries.length) {
      container.innerHTML = '<div class="queue-empty">Słownik jest pusty</div>';
      return;
    }

    container.innerHTML = `
      <table class="voice-list-table">
        <thead><tr><th>Słowo/fraza</th><th>Zamiennik</th><th>Akcje</th></tr></thead>
        <tbody>
          ${data.entries
            .map(
              (e) => `
            <tr class="dict-row" data-word="${escapeHtml(e.word.toLowerCase())}" data-replacement="${escapeHtml(e.replacement.toLowerCase())}">
              <td><code style="color:var(--accent-light)">${escapeHtml(e.word)}</code></td>
              <td>${escapeHtml(e.replacement)}</td>
              <td><button class="btn btn-danger btn-sm" onclick="dictionaryDelete(this.closest('tr').dataset.origWord)"
                    data-orig-word="${escapeHtml(e.word)}">🗑</button></td>
            </tr>
          `,
            )
            .join("")}
        </tbody>
      </table>
    `;
    // Store original word in data attribute for safe deletion
    document.querySelectorAll("#dictionary-list .btn-danger").forEach((btn) => {
      btn.setAttribute("data-orig-word", btn.getAttribute("data-orig-word"));
    });
  } catch (e) {
    container.innerHTML =
      '<div class="queue-empty text-error">Błąd ładowania słownika</div>';
  }
}

function filterDictionary(query) {
  const q = query.toLowerCase().trim();
  document.querySelectorAll("#dictionary-list .dict-row").forEach((row) => {
    const word = row.dataset.word || "";
    const repl = row.dataset.replacement || "";
    row.style.display =
      !q || word.includes(q) || repl.includes(q) ? "" : "none";
  });
}

async function dictionaryAdd() {
  const word = document.getElementById("dict-word").value.trim();
  const replacement = document.getElementById("dict-replacement").value.trim();
  if (!word) {
    showToast("Wpisz słowo", "warning");
    return;
  }
  try {
    await fetch("/api/dictionary", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ word, replacement }),
    });
    document.getElementById("dict-word").value = "";
    document.getElementById("dict-replacement").value = "";
    loadDictionary();
    showToast("Dodano do słownika", "success");
  } catch (e) {
    showToast("Błąd: " + e.message, "error");
  }
}

async function dictionaryDelete(word) {
  if (!word) return;
  if (!confirm(`Usunąć wpis "${word}"?`)) return;
  try {
    await fetch(`/api/dictionary/${encodeURIComponent(word)}`, {
      method: "DELETE",
    });
    loadDictionary();
    showToast("Wpis usunięty", "success");
  } catch (e) {
    showToast("Błąd usuwania: " + e.message, "error");
  }
}

async function dictionaryClear() {
  if (!confirm("Wyczyścić cały słownik?")) return;
  await fetch("/api/dictionary", { method: "DELETE" });
  loadDictionary();
  showToast("Słownik wyczyszczony", "success");
}

async function dictionaryExport() {
  const resp = await fetch("/api/dictionary");
  const data = await resp.json();
  const obj = {};
  (data.entries || []).forEach((e) => (obj[e.word] = e.replacement));
  const blob = new Blob([JSON.stringify(obj, null, 2)], {
    type: "application/json",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "slownik.json";
  a.click();
}

function dictionaryImport() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = ".json";
  input.onchange = async () => {
    const text = await input.files[0].text();
    try {
      const obj = JSON.parse(text);
      await fetch("/api/dictionary/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(obj),
      });
      loadDictionary();
      showToast("Słownik zaimportowany", "success");
    } catch (e) {
      showToast("Błąd importu: " + e.message, "error");
    }
  };
  input.click();
}

// ===== Status polling =====
function startStatusPolling() {
  setInterval(async () => {
    try {
      const resp = await fetch("/api/jobs");
      const data = await resp.json();
      const active = (data.jobs || []).filter(
        (j) => j.status === "queued" || j.status === "processing",
      );
      const count = active.length;
      const badge = document.getElementById("queue-badge");
      const queueCount = document.getElementById("queue-count");
      const queueN = document.getElementById("queue-n");
      if (count > 0) {
        badge.textContent = count;
        badge.style.display = "inline-flex";
        queueCount.style.display = "inline";
        queueN.textContent = count;
      } else {
        badge.style.display = "none";
        queueCount.style.display = "none";
      }

      // Update status dot
      const dot = document.getElementById("status-dot");
      const statusText = document.getElementById("status-text");
      const processing = (data.jobs || []).find(
        (j) => j.status === "processing",
      );
      if (processing) {
        let progress = processing.progress || 0;
        const tch = processing.total_chapters || 1;
        const cc = processing.completed_chapters || 0;
        let runningFraction = 0;

        if (processing.chapter_states && processing.chapter_states.length > 0) {
          processing.chapter_states.forEach((state) => {
            if (state.status === "processing" && state.total_chunks > 0) {
              runningFraction += state.current_chunk / state.total_chunks;
            }
          });
        }
        progress = Math.min(
          99,
          Math.floor(((cc + runningFraction) / tch) * 100),
        );

        dot.className = "status-dot busy";
        statusText.innerHTML = `Generowanie: <div class="progress-container" style="display:inline-block; width:100px; vertical-align:middle; margin:0 8px;"><div class="progress-bar" style="width:${progress}%"></div></div> ${progress}%`;
      } else if (count > 0) {
        dot.className = "status-dot busy";
        statusText.textContent = "W kolejce";
      } else {
        dot.className = "status-dot";
        statusText.textContent = "Gotowy";
      }
    } catch (e) {
      /* ignore */
    }
  }, 3000);
}

// ===== System Status polling =====
function startSystemStatusPolling() {
  const fetchStatus = async () => {
    try {
      const resp = await fetch("/api/system-status");
      const data = await resp.json();
      if (data.success && data.status) {
        const rEl = document.getElementById("sys-redis");
        const sEl = document.getElementById("sys-supervisor");
        const wEl = document.getElementById("sys-workers");
        const vEl = document.getElementById("sys-vram");

        if (data.status.redis) {
          rEl.innerHTML = '<span class="sys-dot sys-on">●</span> Redis: OK';
        } else {
          rEl.innerHTML = '<span class="sys-dot sys-off">●</span> Redis: Błąd';
        }

        if (data.status.supervisor) {
          sEl.innerHTML =
            '<span class="sys-dot sys-on">●</span> Supervisor: OK';
        } else {
          sEl.innerHTML =
            '<span class="sys-dot sys-off">●</span> Supervisor: Błąd';
        }

        wEl.innerHTML = `🛠 Workery: ${data.status.workers}`;

        if (data.status.vram_total > 0 && vEl) {
          const totalGB = (
            data.status.vram_total /
            (1024 * 1024 * 1024)
          ).toFixed(1);
          const freeGB = (data.status.vram_free / (1024 * 1024 * 1024)).toFixed(
            1,
          );
          const usedGB = (totalGB - freeGB).toFixed(1);
          vEl.innerHTML = `💾 VRAM: ${usedGB}GB / ${totalGB}GB`;
          vEl.style.display = "block";
        }
      }
    } catch (e) {
      /* ignore */
    }
  };

  fetchStatus(); // fetch immediately
  setInterval(fetchStatus, 5000);
}

// ===== Toast notifications =====
function showToast(msg, type = "info", duration = 3500) {
  const container = document.getElementById("toast-container");
  const toast = document.createElement("div");
  const icons = { success: "✓", error: "✕", warning: "⚠", info: "ℹ" };
  toast.className = `toast ${type}`;
  toast.innerHTML = `<span>${icons[type] || "ℹ"}</span><span>${escapeHtml(msg)}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.animation = "toast-out 0.3s ease forwards";
    setTimeout(() => toast.remove(), 300);
  }, duration);
  toast.addEventListener("click", () => toast.remove());
}

// ===== Helpers =====
function escapeHtml(str) {
  const d = document.createElement("div");
  d.appendChild(document.createTextNode(String(str)));
  return d.innerHTML;
}

function formatDate(iso) {
  if (!iso) return "-";
  return new Date(iso).toLocaleString("pl-PL");
}

// ===== Dictionary badge on load =====
async function loadDictionaryBadge() {
  try {
    const resp = await fetch("/api/dictionary");
    const data = await resp.json();
    if (data.success) {
      const badge = document.getElementById("dict-badge");
      if (data.count > 0) {
        badge.textContent = data.count;
        badge.style.display = "inline-flex";
      } else {
        badge.style.display = "none";
      }
    }
  } catch (e) {
    /* ignore */
  }
}
