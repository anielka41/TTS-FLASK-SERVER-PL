// voice-manager.js - Obsługa zakładki "Dostępne głosy"

const voiceManager = (() => {
    let selectedFile = null;

    async function refresh() {
        const container = document.getElementById('voices-list-container');
        container.innerHTML = '<div class="queue-empty">Trwa ładowanie...</div>';
        try {
            const resp = await fetch('/api/chatterbox-voices');
            const data = await resp.json();
            if (!data.success) throw new Error(data.error);
            const voices = data.voices || [];
            if (!voices.length) {
                container.innerHTML = `
          <div class="queue-empty">
            Brak promptów głosowych.<br>
            <button class="btn btn-primary btn-sm mt-2" onclick="voiceManager.openUploadModal()">➕ Dodaj pierwszy prompt</button>
          </div>
        `;
                return;
            }
            container.innerHTML = `
        <div style="overflow-x:auto">
          <table class="voice-list-table">
            <thead>
              <tr>
                <th>Nazwa</th>
                <th>Plik</th>
                <th>Język</th>
                <th>Płeć</th>
                <th>Długość</th>
                <th>Status</th>
                <th>Akcje</th>
              </tr>
            </thead>
            <tbody>
              ${voices.map(v => renderVoiceRow(v)).join('')}
            </tbody>
          </table>
        </div>
      `;
            const predefinedContainer = document.getElementById('predefined-voices-list-container');
            if (predefinedContainer) {
                const predVoices = data.predefined_voices || [];
                if (!predVoices.length) {
                    predefinedContainer.innerHTML = '<div class="queue-empty">Brak domyślnych głosów.</div>';
                } else {
                    predefinedContainer.innerHTML = `
            <div style="overflow-x:auto">
              <table class="voice-list-table">
                <thead>
                  <tr>
                    <th>Nazwa</th>
                    <th>Plik</th>
                    <th>Język</th>
                    <th>Płeć</th>
                    <th>Długość</th>
                    <th>Status</th>
                    <th>Akcje</th>
                  </tr>
                </thead>
                <tbody>
                  ${predVoices.map(v => renderVoiceRow(v, true)).join('')}
                </tbody>
              </table>
            </div>
          `;
                }
            }

            // Refresh voice list for assignments
            loadVoicesForAssignments && loadVoicesForAssignments();
        } catch (e) {
            container.innerHTML = `<div class="queue-empty text-error">Błąd: ${escapeHtml(e.message)}</div>`;
        }
    }

    function renderVoiceRow(v, isPredefined = false) {
        const genderLabel = { male: '♂ Mężczyzna', female: '♀ Kobieta', unknown: '— Nieznane' };
        const dur = v.duration_seconds ? `${v.duration_seconds}s` : '?';
        const valid = v.is_valid_prompt
            ? '<span class="valid-badge">✓ Ważny</span>'
            : '<span class="invalid-badge">✗ Za krótki</span>';

        let actionsHtml = `<button class="btn btn-secondary btn-sm" onclick="voiceManager.previewVoice('${v.id}')" title="Podgląd głosu">▶</button>
            <button class="btn btn-secondary btn-sm" onclick="voiceManager.editVoice('${v.id}','${escapeHtml(v.name)}','${v.gender}','${v.language}','${escapeHtml(v.description || '')}')" title="Edytuj">✏️</button>`;
        if (!isPredefined) {
            actionsHtml += `
            <button class="btn btn-danger btn-sm" onclick="voiceManager.deleteVoice('${v.id}','${escapeHtml(v.name)}')" title="Usuń">🗑</button>
            `;
        }

        return `
      <tr>
        <td><strong>${escapeHtml(v.name)}</strong>
          ${v.description ? `<div style="font-size:0.75rem;color:var(--text-muted)">${escapeHtml(v.description)}</div>` : ''}
        </td>
        <td><code style="font-size:0.75rem;color:var(--text-muted)">${escapeHtml(v.file_name)}</code></td>
        <td>${escapeHtml(v.language || 'pl')}</td>
        <td>${genderLabel[v.gender] || '—'}</td>
        <td style="white-space:nowrap">${dur}</td>
        <td>${valid}</td>
        <td>
          <div style="display:flex;gap:4px;flex-wrap:wrap">
            ${actionsHtml}
          </div>
        </td>
      </tr>
    `;
    }

    async function previewVoice(voiceId) {
        showToast('Generowanie podglądu...', 'info');
        try {
            const resp = await fetch(`/api/chatterbox-voices/${voiceId}/preview`);
            const data = await resp.json();
            if (!data.success) throw new Error(data.error);
            const audio = new Audio('data:audio/wav;base64,' + data.audio_base64);
            audio.play();
            showToast(`Podgląd: ${data.duration}s`, 'success');
        } catch (e) {
            showToast('Błąd podglądu: ' + e.message, 'error');
        }
    }

    async function deleteVoice(voiceId, name) {
        if (!confirm(`Usunąć prompt "${name}"? Spowoduje to trwałe usunięcie pliku.`)) return;
        try {
            const resp = await fetch(`/api/chatterbox-voices/${voiceId}`, { method: 'DELETE' });
            const data = await resp.json();
            if (data.success) { showToast('Prompt usunięty', 'success'); refresh(); }
            else throw new Error(data.error);
        } catch (e) {
            showToast('Błąd usuwania: ' + e.message, 'error');
        }
    }

    function editVoice(voiceId, name, gender, language, description) {
        document.getElementById('edit-voice-id-input').value = voiceId;
        document.getElementById('edit-voice-name-input').value = name;
        document.getElementById('edit-voice-gender-input').value = gender || 'unknown';
        document.getElementById('edit-voice-language-input').value = language || 'pl';
        document.getElementById('edit-voice-description-input').value = description || '';
        document.getElementById('edit-voice-modal').style.display = 'flex';
    }

    function closeEditModal() {
        document.getElementById('edit-voice-modal').style.display = 'none';
        document.getElementById('edit-voice-id-input').value = '';
    }

    async function submitEditVoice() {
        const voiceId = document.getElementById('edit-voice-id-input').value;
        const newName = document.getElementById('edit-voice-name-input').value.trim();
        const newGender = document.getElementById('edit-voice-gender-input').value;
        const newLanguage = document.getElementById('edit-voice-language-input').value;
        const newDescription = document.getElementById('edit-voice-description-input').value.trim();

        if (!newName) {
            showToast('Nazwa głosu pole jest wymagane!', 'warning');
            return;
        }

        const btn = document.getElementById('save-voice-btn');
        btn.disabled = true;
        btn.innerHTML = 'Zapisywanie...';

        try {
            const resp = await fetch(`/api/chatterbox-voices/${voiceId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    name: newName, 
                    gender: newGender, 
                    language: newLanguage, 
                    description: newDescription 
                })
            });
            const data = await resp.json();
            if(!data.success) throw new Error(data.error || "Wystąpił nieznany błąd podczas zapisywania");
            showToast('Zaktualizowano metadane pomyślnie!', 'success');
            closeEditModal();
            refresh();
        } catch (e) {
            showToast('Błąd edycji: ' + e.message, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '💾 Zapisz zmiany';
        }
    }

    function openUploadModal() {
        selectedFile = null;
        document.getElementById('voice-file-input').value = '';
        document.getElementById('voice-name-input').value = '';
        document.getElementById('voice-drop-info').style.display = 'none';
        document.getElementById('upload-voice-modal').classList.add('open');
        setupDropZone();
    }

    function closeUploadModal() {
        document.getElementById('upload-voice-modal').classList.remove('open');
        selectedFile = null;
    }

    function setupDropZone() {
        const zone = document.getElementById('voice-drop-zone');
        const input = document.getElementById('voice-file-input');

        zone.ondragover = e => { e.preventDefault(); zone.classList.add('dragover'); };
        zone.ondragleave = () => zone.classList.remove('dragover');
        zone.ondrop = e => {
            e.preventDefault();
            zone.classList.remove('dragover');
            if (e.dataTransfer.files[0]) setSelectedFile(e.dataTransfer.files[0]);
        };

        input.onchange = () => {
            if (input.files[0]) setSelectedFile(input.files[0]);
        };
    }

    function setSelectedFile(file) {
        selectedFile = file;
        const info = document.getElementById('voice-drop-info');
        info.style.display = 'block';
        info.innerHTML = `📁 <strong>${escapeHtml(file.name)}</strong> (${(file.size / 1024).toFixed(0)} KB)<br>
      <span style="color:var(--text-muted);font-size:0.8rem">Czas trwania będzie sprawdzony po przesłaniu (wymagane min. 5s)</span>`;
        // Auto-fill name from filename
        const nameInput = document.getElementById('voice-name-input');
        if (!nameInput.value) {
            nameInput.value = file.name.replace(/\.[^.]+$/, '').replace(/[-_]/g, ' ');
        }
    }

    async function uploadVoice() {
        if (!selectedFile) { showToast('Wybierz plik audio', 'warning'); return; }
        const name = document.getElementById('voice-name-input').value.trim();
        if (!name) { showToast('Wpisz nazwę głosu', 'warning'); return; }

        const formData = new FormData();
        formData.append('file', selectedFile);
        formData.append('name', name);
        formData.append('gender', document.getElementById('voice-gender-input').value);
        formData.append('language', document.getElementById('voice-language-input').value);
        formData.append('description', document.getElementById('voice-description-input').value);

        const btn = document.getElementById('upload-voice-btn');
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Przesyłanie...';

        try {
            const resp = await fetch('/api/chatterbox-voices', { method: 'POST', body: formData });
            const data = await resp.json();
            if (!data.success) throw new Error(data.error);
            closeUploadModal();
            showToast(`Prompt "${name}" dodany!`, 'success');
            refresh();
        } catch (e) {
            showToast('Błąd przesyłania: ' + e.message, 'error');
        } finally {
            btn.disabled = false;
            btn.innerHTML = '📤 Wgraj prompt';
        }
    }

    // Close modal on outside click
    document.getElementById('upload-voice-modal').addEventListener('click', function (e) {
        if (e.target === this) closeUploadModal();
    });

    return { refresh, previewVoice, deleteVoice, editVoice, openUploadModal, closeUploadModal, uploadVoice, closeEditModal, submitEditVoice };
})();
