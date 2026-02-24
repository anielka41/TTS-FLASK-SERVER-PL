// queue.js - Obsługa zakładki "Kolejka"

const queueModule = (() => {
  let pollInterval = null;

  function startPolling() {
    if (pollInterval) return;
    pollInterval = setInterval(refresh, 4000);
  }

  function stopPolling() {
    if (pollInterval) {
      clearInterval(pollInterval);
      pollInterval = null;
    }
  }

  async function refresh() {
    const container = document.getElementById('queue-container');
    try {
      const resp = await fetch('/api/jobs');
      const data = await resp.json();
      if (!data.success) return;

      const jobs = data.jobs || [];
      // Show all jobs (not just active) sorted by created_at desc
      jobs.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));

      if (!jobs.length) {
        container.innerHTML = '<div class="queue-empty">Brak zadań w kolejce</div>';
        return;
      }

      container.innerHTML = `
        <div style="overflow-x:auto">
          <table class="job-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Tytuł</th>
                <th>Status</th>
                <th>Postęp</th>
                <th>Utworzono</th>
                <th>Akcje</th>
              </tr>
            </thead>
            <tbody>
              ${jobs.map(j => renderJobRow(j)).join('')}
            </tbody>
          </table>
        </div>
      `;
    } catch (e) {
      container.innerHTML = '<div class="queue-empty text-error">Błąd ładowania kolejki</div>';
    }
  }

  function renderJobRow(job) {
    const statusLabels = {
      queued: 'W kolejce', processing: 'Przetwarzanie',
      completed: 'Ukończono', failed: 'Błąd',
      paused: 'Wstrzymano', cancelled: 'Anulowano'
    };
    const status = job.status || 'unknown';
    const progress = job.progress || 0;
    const shortId = (job.job_id || '').slice(0, 8);
    const title = escapeHtml(job.title || 'Bez tytułu');
    const created = formatDate(job.created_at);

    // Chunk/chapter progress info
    const chunkInfo = (status === 'processing' || status === 'paused')
      ? (() => {
        const cc = job.current_chunk || 0;
        const tc = job.total_chunks || 0;
        const cch = job.current_chapter || 0;
        const tch = job.total_chapters || 0;
        let info = '';
        if (tc > 0) info += `Chunk ${cc}/${tc}`;
        if (tch > 1) info += ` · Rozdział ${cch}/${tch}`;
        return info ? `<div style="font-size:0.72rem;color:var(--text-muted);margin-top:2px">${info}</div>` : '';
      })()
      : '';

    let progressCol = '';
    if (status === 'processing') {
      progressCol = `
        <div style="min-width:140px">
          <div style="display:flex;align-items:center;gap:8px">
            <div class="progress-container" style="flex:1">
              <div class="progress-bar" style="width:${progress}%"></div>
            </div>
            <span style="font-size:0.75rem;color:var(--text-muted);white-space:nowrap">${progress}%</span>
          </div>
          ${chunkInfo}
        </div>
      `;
    } else if (status === 'paused') {
      progressCol = `
        <div style="min-width:140px">
          <span style="color:var(--warning);font-size:0.8rem">⏸ ${progress}%</span>
          ${chunkInfo}
        </div>
      `;
    } else if (status === 'completed') {
      // Show output files as download links
      const files = job.output_files || [];
      if (files.length > 0) {
        const links = files.map((f, i) => {
          const fname = f.split('/').pop();
          return `<a href="${f}" download style="color:var(--accent-light);font-size:0.75rem;text-decoration:none" title="Pobierz ${fname}">📥 ${fname}</a>`;
        }).join(' ');
        progressCol = `<div>✓ <span style="color:var(--success);font-size:0.8rem">100%</span><br>${links}</div>`;
      } else {
        progressCol = '<span style="color:var(--success);font-size:0.8rem">✓ 100%</span>';
      }
    } else {
      progressCol = '<span style="color:var(--text-muted)">—</span>';
    }

    let actions = '';
    if (status === 'processing') {
      actions = `<button class="btn btn-warning btn-sm" onclick="queueModule.pauseJob('${job.job_id}')">⏸ Wstrzymaj</button>`;
    } else if (status === 'paused') {
      actions = `<button class="btn btn-success btn-sm" onclick="queueModule.resumeJob('${job.job_id}')">▶ Wznów</button>`;
    } else if (status === 'queued') {
      actions = `<button class="btn btn-danger btn-sm" onclick="queueModule.cancelJob('${job.job_id}')">✕ Anuluj</button>`;
    }

    actions += ` <button class="btn btn-secondary btn-sm" onclick="queueModule.deleteJob('${job.job_id}')" title="Usuń">🗑</button>`;

    const errorMsg = job.error ? `<div style="color:var(--error);font-size:0.75rem;max-width:200px;overflow:hidden;text-overflow:ellipsis" title="${escapeHtml(job.error)}">⚠ ${escapeHtml(job.error.slice(0, 60))}</div>` : '';

    return `
      <tr class="${status === 'processing' ? 'generating-animation' : ''}">
        <td><code style="font-size:0.75rem;color:var(--text-muted)">${shortId}…</code></td>
        <td>
          <div style="font-weight:500;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${title}</div>
          ${errorMsg}
        </td>
        <td><span class="status-badge ${status}">${statusLabels[status] || status}</span></td>
        <td>${progressCol}</td>
        <td style="font-size:0.8rem;color:var(--text-muted);white-space:nowrap">${created}</td>
        <td><div style="display:flex;gap:4px;flex-wrap:wrap">${actions}</div></td>
      </tr>
    `;
  }

  async function pauseJob(jobId) {
    try {
      const resp = await fetch(`/api/jobs/${jobId}/pause`, { method: 'POST' });
      const data = await resp.json();
      if (data.success) { showToast('Job wstrzymany', 'success'); refresh(); }
      else showToast(data.error, 'error');
    } catch (e) { showToast('Błąd: ' + e.message, 'error'); }
  }

  async function resumeJob(jobId) {
    try {
      const resp = await fetch(`/api/jobs/${jobId}/resume`, { method: 'POST' });
      const data = await resp.json();
      if (data.success) { showToast('Job wznowiony', 'success'); refresh(); }
      else showToast(data.error, 'error');
    } catch (e) { showToast('Błąd: ' + e.message, 'error'); }
  }

  async function cancelJob(jobId) {
    stopPolling();
    if (!confirm('Anulować to zadanie?')) { startPolling(); return; }
    try {
      await fetch(`/api/jobs/${jobId}/cancel`, { method: 'POST' });
      showToast('Anulowanie zlecone', 'warning');
      refresh();
    } catch (e) { showToast('Błąd: ' + e.message, 'error'); }
    startPolling();
  }

  async function deleteJob(jobId) {
    stopPolling();
    if (!confirm('Usunąć to zadanie i wszystkie pliki audio?')) { startPolling(); return; }
    try {
      await fetch(`/api/jobs/${jobId}/delete`, { method: 'DELETE' });
      showToast('Job usunięty', 'success');
      refresh();
    } catch (e) { showToast('Błąd: ' + e.message, 'error'); }
    startPolling();
  }

  // Uruchom auto-odświeżanie
  startPolling();

  return { refresh, startPolling, stopPolling, pauseJob, resumeJob, cancelJob, deleteJob };
})();
