// library.js - Obsługa zakładki "Biblioteka"

const libraryModule = (() => {
  async function refresh() {
    const container = document.getElementById('library-container');
    container.innerHTML = '<div class="queue-empty">Trwa ładowanie biblioteki...</div>';
    try {
      const resp = await fetch('/api/library');
      const data = await resp.json();
      if (!data.success) throw new Error(data.error);
      const items = data.library || [];
      if (!items.length) {
        container.innerHTML = '<div class="queue-empty">Brak ukończonych nagrań</div>';
        return;
      }
      container.innerHTML = '<div class="library-grid">' + items.map(renderCard).join('') + '</div>';
    } catch (e) {
      container.innerHTML = `<div class="queue-empty text-error">Błąd: ${escapeHtml(e.message)}</div>`;
    }
  }

  function renderCard(item) {
    const files = item.output_files || [];
    const audioPlayers = files.map(f => `
      <div class="audio-player-wrapper" style="background: rgba(255, 255, 255, 0.05); border: 1px solid var(--border); padding: 8px; border-radius: 6px;">
        <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:6px;font-weight:600;">📄 ${f.split('/').pop()}</div>
        <audio controls src="${f}" style="width: 100%; height: 35px; border-radius: 4px; outline: none;"></audio>
      </div>
    `).join('');

    let summaryText = files.length > 1 ? `Rozwiń ${files.length} fragmenty/rozdziały` : "Odtwórz / Pobierz nagranie";
    const audioContent = `
      <details class="chapters-details" style="margin-top: 10px; border: 1px solid var(--border); border-radius: 6px; padding: 5px; background: var(--bg-hover);">
        <summary style="font-weight: 600; font-size: 0.85rem; cursor: pointer; padding: 5px; outline: none; user-select: none; display: list-item; list-style-position: inside;">
          ${summaryText}
        </summary>
        <div style="margin-top: 10px; display: flex; flex-direction: column; gap: 8px; background: var(--bg-primary); padding: 8px; border-radius: 4px;">
          ${audioPlayers}
        </div>
      </details>
    `;

    return `
      <div class="library-card fade-in">
        <div class="library-card-title">
          🎧 <input type="text" value="${escapeHtml(item.title || 'Brak nazwy projektu')}"
            onblur="libraryModule.updateTitle('${item.job_id}', this.value)"
            onkeydown="if(event.key==='Enter')this.blur()"
            title="Kliknij aby edytować nazwę projektu">
        </div>
        <div class="library-card-meta" style="display:flex; align-items:center; gap:8px;">
          <div style="font-size:1.1rem;">📅</div>
          <div style="display:flex; flex-direction:column;">
            ${formatDate(item.completed_at || item.created_at)}
          </div>
          ${files.length > 1 ? `<div style="margin-left:auto; font-size:0.8rem; background:var(--bg-hover); padding:2px 6px; border-radius:4px;">📚 Kolekcja</div>` : ''}
        </div>
        ${audioContent}
        <div class="library-card-actions mt-2">
          <button class="btn btn-primary btn-sm" onclick="libraryModule.download('${item.job_id}')">⬇️ Pobierz</button>
          <button class="btn btn-danger btn-sm" onclick="libraryModule.deleteJob('${item.job_id}')">🗑 Usuń</button>
        </div>
      </div>
    `;
  }

  async function updateTitle(jobId, newTitle) {
    if (!newTitle.trim()) return;
    await fetch(`/api/library/${jobId}/title`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: newTitle })
    });
  }

  async function download(jobId) {
    window.location.href = `/api/library/${jobId}/download`;
  }

  async function deleteJob(jobId) {
    if (!confirm('Usunąć to nagranie i wszystkie pliki?')) return;
    await fetch(`/api/jobs/${jobId}/delete`, { method: 'DELETE' });
    showToast('Nagranie usunięte', 'success');
    refresh();
  }

  return { refresh, updateTitle, download, deleteJob };
})();
