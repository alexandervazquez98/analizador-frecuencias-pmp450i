/**
 * static/js/towers.js — Módulo de Gestión de Torres
 *
 * Maneja el panel completo de torres: CRUD, formulario inline,
 * confirmación de borrado.
 *
 * Depende de: authFetch, showPanelAlert, escapeHtml, escapeAttr (app.js)
 */

// ==================== API CALLS ====================

/**
 * Fetches all towers from GET /api/towers.
 * @returns {Promise<Array>}
 */
async function loadTowers() {
    const response = await authFetch('/api/towers');
    if (!response) return [];
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    return Array.isArray(data) ? data : (data.towers || []);
}

/**
 * Creates a new tower via POST /api/towers.
 * @param {{ tower_id: string, name: string, location?: string, notes?: string }} data
 * @returns {Promise<Response|null>}
 */
async function createTower(data) {
    return await authFetch('/api/towers', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}

/**
 * Updates a tower via PUT /api/towers/<tower_id>.
 * @param {string} towerId
 * @param {{ name?: string, location?: string, notes?: string }} data
 * @returns {Promise<Response|null>}
 */
async function updateTower(towerId, data) {
    return await authFetch(`/api/towers/${encodeURIComponent(towerId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}

/**
 * Deletes a tower via DELETE /api/towers/<tower_id>.
 * @param {string} towerId
 * @returns {Promise<Response|null>}
 */
async function deleteTower(towerId) {
    return await authFetch(`/api/towers/${encodeURIComponent(towerId)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' }
    });
}

// ==================== RENDER ====================

/**
 * Renders the towers panel — fetches and displays the table.
 */
async function renderTowersPanel() {
    const container = document.getElementById('towersTableContainer');
    if (!container) return;
    container.innerHTML = `
        <div class="text-center text-muted p-3">
            <div class="spinner-border spinner-border-sm text-primary me-2"></div>Cargando...
        </div>`;

    try {
        const towers = await loadTowers();
        if (towers.length === 0) {
            container.innerHTML = `
                <div class="alert alert-secondary text-center">
                    <i class="bi bi-building"></i> No hay torres registradas. Crea la primera con "Nueva Torre".
                </div>`;
            return;
        }

        const isAdmin = window.userRole === 'admin';
        const rows = towers.map(t => {
            const createdAt = t.created_at ? new Date(t.created_at).toLocaleString() : 'N/A';
            return `
                <tr>
                    <td class="font-monospace small align-middle">${escapeHtml(t.tower_id)}</td>
                    <td class="align-middle">${escapeHtml(t.name)}</td>
                    <td class="align-middle">${escapeHtml(t.location || '—')}</td>
                    <td class="text-muted small align-middle">${escapeHtml(t.notes || '—')}</td>
                    <td class="text-muted small align-middle">${createdAt}</td>
                    <td class="align-middle">
                        <button class="btn btn-outline-info btn-sm me-1"
                            onclick="editTowerForm('${escapeAttr(t.tower_id)}','${escapeAttr(t.name)}','${escapeAttr(t.location || '')}','${escapeAttr(t.notes || '')}')">
                            <i class="bi bi-pencil"></i> Editar
                        </button>
                        ${isAdmin ? `
                        <button class="btn btn-outline-danger btn-sm"
                            onclick="confirmDeleteTower('${escapeAttr(t.tower_id)}')">
                            <i class="bi bi-trash"></i>
                        </button>` : ''}
                    </td>
                </tr>
            `;
        }).join('');

        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-dark table-hover table-sm align-middle mb-0">
                    <thead class="table-secondary text-dark">
                        <tr>
                            <th>Tower ID</th>
                            <th>Nombre</th>
                            <th>Ubicación</th>
                            <th>Notas</th>
                            <th>Creada</th>
                            <th>Acciones</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div class="mt-2 text-muted small text-end">${towers.length} torre(s) registrada(s)</div>
        `;
    } catch (err) {
        container.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle"></i> Error cargando torres: ${escapeHtml(err.message)}
            </div>`;
    }
}

// ==================== FORMULARIO INLINE ====================

/**
 * Shows the create/edit inline form.
 * @param {'create'|'edit'} mode
 */
function showTowerForm(mode = 'create') {
    const panel = document.getElementById('towerFormPanel');
    const modeInput = document.getElementById('towerFormMode');
    const idField = document.getElementById('towerFieldId');
    const titleEl = document.getElementById('towerFormTitle');
    if (!panel) return;

    if (mode === 'create') {
        if (modeInput) modeInput.value = 'create';
        if (titleEl) titleEl.innerHTML = '<i class="bi bi-plus-circle"></i> Nueva Torre';
        if (idField) { idField.value = ''; idField.disabled = false; }
        document.getElementById('towerFieldName').value = '';
        document.getElementById('towerFieldLocation').value = '';
        document.getElementById('towerFieldNotes').value = '';
        document.getElementById('towerFormEditId').value = '';
    }

    panel.style.display = '';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

/**
 * Hides the inline tower form.
 */
function hideTowerForm() {
    const panel = document.getElementById('towerFormPanel');
    if (panel) panel.style.display = 'none';
}

/**
 * Populates and shows the form in edit mode for a given tower.
 * @param {string} towerId
 * @param {string} name
 * @param {string} location
 * @param {string} notes
 */
function editTowerForm(towerId, name, location, notes) {
    showTowerForm('edit');
    document.getElementById('towerFormMode').value = 'edit';
    document.getElementById('towerFormTitle').innerHTML = '<i class="bi bi-pencil"></i> Editar Torre';
    const idField = document.getElementById('towerFieldId');
    if (idField) { idField.value = towerId; idField.disabled = true; }
    document.getElementById('towerFormEditId').value = towerId;
    document.getElementById('towerFieldName').value = name;
    document.getElementById('towerFieldLocation').value = location;
    document.getElementById('towerFieldNotes').value = notes;
}

/**
 * Validates and submits the tower form (create or edit).
 */
async function submitTowerForm() {
    const mode = document.getElementById('towerFormMode').value;
    const name = document.getElementById('towerFieldName').value.trim();
    const location = document.getElementById('towerFieldLocation').value.trim();
    const notes = document.getElementById('towerFieldNotes').value.trim();

    if (!name) {
        showPanelAlert('towersAlert', 'El campo Nombre es obligatorio.', 'warning');
        return;
    }

    let response;
    if (mode === 'create') {
        const towerId = document.getElementById('towerFieldId').value.trim();
        if (!towerId) {
            showPanelAlert('towersAlert', 'El Tower ID es obligatorio.', 'warning');
            return;
        }
        response = await createTower({ tower_id: towerId, name, location: location || null, notes: notes || null });
    } else {
        const towerId = document.getElementById('towerFormEditId').value;
        response = await updateTower(towerId, { name, location: location || null, notes: notes || null });
    }

    if (!response) return;

    const result = await response.json();
    if (!response.ok) {
        showPanelAlert('towersAlert', result.error || 'Error al guardar la torre.', 'danger');
        return;
    }

    hideTowerForm();
    showPanelAlert('towersAlert', mode === 'create' ? 'Torre creada correctamente.' : 'Torre actualizada correctamente.', 'success');
    renderTowersPanel();
}

/**
 * Confirms and deletes a tower.
 * @param {string} towerId
 */
async function confirmDeleteTower(towerId) {
    if (!confirm(`¿Eliminar la torre "${towerId}"?\nEsta acción no se puede deshacer.`)) return;

    const response = await deleteTower(towerId);
    if (!response) return;

    const result = await response.json();
    if (!response.ok) {
        showPanelAlert('towersAlert', result.error || 'Error al eliminar.', 'danger');
        return;
    }
    showPanelAlert('towersAlert', `Torre "${towerId}" eliminada.`, 'success');
    renderTowersPanel();
}
