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
      <div class="audio-player-wrapper">
        <div style="font-size:0.75rem;color:var(--text-muted);margin-bottom:4px">📄 ${f.split('/').pop()}</div>
        <audio controls src="${f}"></audio>
      </div>
    `).join('');

    return `
      <div class="library-card fade-in">
        <div class="library-card-title">
          🎧 <input type="text" value="${escapeHtml(item.title || 'Brak nazwy projektu')}"
            onblur="libraryModule.updateTitle('${item.job_id}', this.value)"
            onkeydown="if(event.key==='Enter')this.blur()"
            title="Kliknij aby edytować nazwę projektu">
        </div>
        <div class="library-card-meta">
          📅 ${formatDate(item.completed_at || item.created_at)}
          ${files.length > 1 ? ` · 📚 ${files.length} plików` : ''}
        </div>
        ${audioPlayers}
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
