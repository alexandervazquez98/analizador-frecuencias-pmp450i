/**
 * static/js/history.js — Módulo de Historial de Escaneos y Verificaciones
 *
 * Maneja el panel de historial: listado de scans, detalle de escaneo,
 * formulario de verificación de configuración y listado de verificaciones.
 *
 * Depende de: authFetch, showPanelAlert, escapeHtml, escapeAttr (app.js)
 */

// Track the currently selected scan for the verification form
let _selectedScanId = null;

// ==================== API CALLS ====================

/**
 * Fetches scan history from GET /api/scans.
 * @returns {Promise<Array>}
 */
async function loadScanHistory() {
    const response = await authFetch('/api/scans');
    if (!response) return [];
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    return data.scans || [];
}

/**
 * Fetches full scan status + results from GET /api/status/<scan_id>.
 * @param {string} scanId
 * @returns {Promise<Object|null>}
 */
async function fetchScanStatus(scanId) {
    const res = await authFetch(`/api/status/${encodeURIComponent(scanId)}`);
    if (!res) return null;
    return await res.json();
}

/**
 * Submits a new config verification to POST /api/config-verifications.
 * @param {Object} data
 * @returns {Promise<Response|null>}
 */
async function submitVerification(data) {
    return await authFetch('/api/config-verifications', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
    });
}

/**
 * Fetches verifications for a specific scan from GET /api/scans/<id>/verifications.
 * @param {string} scanId
 * @returns {Promise<Array>}
 */
async function loadVerifications(scanId) {
    const response = await authFetch(`/api/scans/${encodeURIComponent(scanId)}/verifications`);
    if (!response || !response.ok) return [];
    const data = await response.json();
    return data.verifications || [];
}

// ==================== RENDER HISTORIAL ====================

/**
 * Renders the history panel — fetches and displays the scans table.
 */
async function renderHistoryPanel() {
    const container = document.getElementById('historyTableContainer');
    if (!container) return;

    hideScanDetail();
    container.innerHTML = `
        <div class="text-center text-muted p-3">
            <div class="spinner-border spinner-border-sm text-secondary me-2"></div>Cargando...
        </div>`;

    try {
        const scans = await loadScanHistory();

        if (scans.length === 0) {
            container.innerHTML = `
                <div class="alert alert-secondary text-center">
                    <i class="bi bi-clock-history"></i> No hay escaneos en el historial.
                </div>`;
            return;
        }

        const statusColors = {
            completed: 'success',
            scanning: 'primary',
            analyzing: 'info',
            failed: 'danger',
            started: 'warning'
        };

        const rows = scans.map(scan => {
            const date = scan.created_at ? new Date(scan.created_at).toLocaleString() : 'N/A';
            const badgeColor = statusColors[scan.status] || 'secondary';
            const shortId = scan.scan_id ? scan.scan_id.substring(0, 12) + '...' : 'N/A';
            const isCompleted = scan.status === 'completed';
            const ticketDisplay = scan.ticket_id ? `#${scan.ticket_id}` : '—';

            return `
                <tr class="${isCompleted ? 'scan-row-clickable' : 'opacity-75'}"
                    style="${isCompleted ? 'cursor:pointer;' : ''}"
                    ${isCompleted ? `onclick="openScanDetail('${escapeAttr(scan.scan_id)}')"` : ''}>
                    <td class="font-monospace small align-middle" title="${escapeHtml(scan.scan_id || '')}">${escapeHtml(shortId)}</td>
                    <td class="small align-middle">${date}</td>
                    <td class="align-middle"><span class="badge bg-${badgeColor}">${scan.status || 'unknown'}</span></td>
                    <td class="text-center align-middle">${scan.ap_count || 0}</td>
                    <td class="align-middle">${escapeHtml(scan.username || 'unknown')}</td>
                    <td class="text-center align-middle text-info small">${escapeHtml(ticketDisplay)}</td>
                    <td class="align-middle">
                        ${isCompleted
                            ? `<button class="btn btn-outline-info btn-sm"
                                onclick="event.stopPropagation(); openScanDetail('${escapeAttr(scan.scan_id)}')">
                                <i class="bi bi-eye"></i> Ver
                               </button>`
                            : '<span class="text-muted small">—</span>'}
                    </td>
                </tr>
            `;
        }).join('');

        container.innerHTML = `
            <div class="table-responsive">
                <table class="table table-dark table-hover table-sm align-middle mb-0">
                    <thead class="table-secondary text-dark">
                        <tr>
                            <th>Scan ID</th>
                            <th>Fecha</th>
                            <th>Estado</th>
                            <th class="text-center">APs</th>
                            <th>Usuario</th>
                            <th class="text-center">Ticket</th>
                            <th>Acción</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
            <div class="mt-2 text-muted small text-end">${scans.length} escaneo(s) registrado(s)</div>
        `;
    } catch (err) {
        container.innerHTML = `
            <div class="alert alert-danger">
                <i class="bi bi-exclamation-triangle"></i> Error cargando historial: ${escapeHtml(err.message)}
            </div>`;
    }
}

// ==================== DETALLE DE ESCANEO ====================

/**
 * Opens the scan detail panel and loads scan info + verifications.
 * Also auto-fills the verification form with scan data.
 * @param {string} scanId
 */
async function openScanDetail(scanId) {
    _selectedScanId = scanId;
    const panel = document.getElementById('scanDetailPanel');
    const idEl = document.getElementById('scanDetailId');
    const contentEl = document.getElementById('scanDetailContent');
    if (!panel || !idEl || !contentEl) return;

    if (idEl) idEl.textContent = scanId.substring(0, 16) + '...';

    // Clear the verification form
    document.getElementById('verifFieldApIp').value = '';
    document.getElementById('verifFieldRecommendedFreq').value = '';
    document.getElementById('verifFieldAppliedFreq').value = '';
    document.getElementById('verifFieldChannelWidth').value = '';
    document.getElementById('verifFieldTowerId').value = '';
    document.getElementById('verifFieldNotes').value = '';

    contentEl.innerHTML = `<div class="spinner-border spinner-border-sm text-info me-2"></div> Cargando detalles...`;
    panel.style.display = '';
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    try {
        const status = await fetchScanStatus(scanId);
        if (!status) return;

        const apCount = status.results ? (status.results.completed_aps || 0) : 0;
        const smCount = status.results ? (status.results.completed_sms || 0) : 0;
        const timestamp = status.results ? status.results.timestamp : null;
        const dateStr = timestamp ? new Date(timestamp).toLocaleString() : 'N/A';

        // Auto-fill recommended freq and AP IP from best frequency
        if (status.results && status.results.analysis_results) {
            const firstAp = Object.values(status.results.analysis_results)[0];
            if (firstAp && firstAp.best_frequency) {
                const freq = firstAp.best_frequency['Frecuencia Central (MHz)'];
                if (freq) document.getElementById('verifFieldRecommendedFreq').value = freq;
            } else if (firstAp && firstAp.best_combined_frequency) {
                const freq = firstAp.best_combined_frequency.frequency;
                if (freq) document.getElementById('verifFieldRecommendedFreq').value = freq;
            }
            const firstIp = Object.keys(status.results.analysis_results)[0];
            if (firstIp) document.getElementById('verifFieldApIp').value = firstIp;
        }

        contentEl.innerHTML = `
            <div class="row g-2">
                <div class="col-auto">
                    <span class="badge bg-secondary">Estado: ${escapeHtml(status.status || 'N/A')}</span>
                </div>
                <div class="col-auto">
                    <span class="badge bg-info text-dark">APs: ${apCount}</span>
                </div>
                <div class="col-auto">
                    <span class="badge bg-primary">SMs: ${smCount}</span>
                </div>
                <div class="col-auto">
                    <span class="text-muted small">${dateStr}</span>
                </div>
            </div>
        `;
    } catch (err) {
        contentEl.innerHTML = `<span class="text-danger">Error cargando detalles: ${escapeHtml(err.message)}</span>`;
    }

    await refreshScanVerifications(scanId);
    await renderApplyHistorySection(scanId);
}

/**
 * Hides the scan detail panel and clears selection.
 */
function hideScanDetail() {
    const panel = document.getElementById('scanDetailPanel');
    if (panel) panel.style.display = 'none';
    _selectedScanId = null;
}

// ==================== VERIFICACIONES ====================

/**
 * Loads and renders existing verifications for a scan.
 * @param {string} scanId
 */
async function refreshScanVerifications(scanId) {
    const container = document.getElementById('scanVerificationsContainer');
    if (!container) return;

    try {
        const verifications = await loadVerifications(scanId);

        if (verifications.length === 0) {
            container.innerHTML = `
                <p class="text-muted small">
                    <i class="bi bi-info-circle"></i> Sin verificaciones registradas para este escaneo.
                </p>`;
            return;
        }

        const rows = verifications.map(v => `
            <tr>
                <td class="small">${escapeHtml(v.ap_ip || '—')}</td>
                <td>${v.recommended_freq || '—'}</td>
                <td>${v.applied_freq || '—'}</td>
                <td>${v.channel_width || '—'}</td>
                <td class="text-muted small">${v.notes ? escapeHtml(v.notes) : '—'}</td>
                <td class="text-muted small">${v.created_at ? new Date(v.created_at).toLocaleString() : 'N/A'}</td>
            </tr>
        `).join('');

        container.innerHTML = `
            <h6 class="text-info mt-2"><i class="bi bi-list-check"></i> Verificaciones anteriores (${verifications.length})</h6>
            <div class="table-responsive">
                <table class="table table-dark table-sm align-middle mb-0">
                    <thead>
                        <tr>
                            <th>AP IP</th><th>Rec. (MHz)</th><th>Aplicada (MHz)</th>
                            <th>Ancho</th><th>Notas</th><th>Fecha</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>
        `;
    } catch (err) {
        container.innerHTML = `<p class="text-danger small">Error cargando verificaciones: ${escapeHtml(err.message)}</p>`;
    }
}

/**
 * Validates and submits the verification form for the selected scan.
 */
async function submitVerificationForm() {
    if (!_selectedScanId) {
        showPanelAlert('historyAlert', 'No hay escaneo seleccionado.', 'warning');
        return;
    }

    const recommendedFreq = parseInt(document.getElementById('verifFieldRecommendedFreq').value);
    if (!recommendedFreq || recommendedFreq <= 0) {
        showPanelAlert('historyAlert', 'La Frecuencia Recomendada es obligatoria.', 'warning');
        return;
    }

    const appliedFreqVal = document.getElementById('verifFieldAppliedFreq').value;
    const channelWidthVal = document.getElementById('verifFieldChannelWidth').value;

    const data = {
        scan_id: _selectedScanId,
        recommended_freq: recommendedFreq,
        ap_ip: document.getElementById('verifFieldApIp').value.trim() || null,
        applied_freq: appliedFreqVal ? parseInt(appliedFreqVal) : null,
        channel_width: channelWidthVal ? parseInt(channelWidthVal) : null,
        tower_id: document.getElementById('verifFieldTowerId').value.trim() || null,
        notes: document.getElementById('verifFieldNotes').value.trim() || null
    };

    const response = await submitVerification(data);
    if (!response) return;

    const result = await response.json();
    if (!response.ok) {
        showPanelAlert('historyAlert', result.error || 'Error al registrar verificación.', 'danger');
        return;
    }

    showPanelAlert('historyAlert', 'Verificaci\u00f3n registrada correctamente.', 'success');
    await refreshScanVerifications(_selectedScanId);
}

// ==================== HISTORIAL DE APPLIES (Tarea 4.5) ====================

/**
 * Fetches apply history from GET /api/apply-history/<tower_id>.
 * @param {string} towerId
 * @returns {Promise<Array>}
 */
async function loadApplyHistory(towerId) {
    const res = await authFetch(`/api/apply-history/${encodeURIComponent(towerId)}`);
    if (!res || !res.ok) return [];
    const data = await res.json();
    return data.applies || [];
}

/**
 * Tarea 4.5: Renderiza la seccion de historial de applies para un scan.
 * @param {string} scanId
 */
async function renderApplyHistorySection(scanId) {
    let container = document.getElementById('applyHistoryContainer');
    if (!container) {
        const panel = document.getElementById('scanDetailPanel');
        if (!panel) return;
        container = document.createElement('div');
        container.id = 'applyHistoryContainer';
        container.style.marginTop = '1rem';
        panel.appendChild(container);
    }

    container.innerHTML = `<div class="text-muted small"><span class="spinner-border spinner-border-sm me-1"></span>Cargando historial de aplicaciones...</div>`;

    try {
        const statusRes = await authFetch(`/api/status/${encodeURIComponent(scanId)}`);
        const statusData = statusRes && statusRes.ok ? await statusRes.json() : null;

        let towerId = null;
        if (statusData && statusData.results) {
            const cfg = statusData.results.config;
            if (cfg && cfg.tower_id) towerId = cfg.tower_id;
            if (!towerId && statusData.results.analysis_results) {
                towerId = Object.keys(statusData.results.analysis_results)[0] || null;
            }
        }

        if (!towerId) {
            container.innerHTML = `<p class="text-muted small"><i class="bi bi-info-circle"></i> Sin tower_id \u2014 no hay historial de applies.</p>`;
            return;
        }

        const applies = await loadApplyHistory(towerId);
        if (applies.length === 0) {
            container.innerHTML = `
                <h6 class="text-warning mt-2"><i class="bi bi-lightning-charge"></i> Historial de Aplicaciones</h6>
                <p class="text-muted small"><i class="bi bi-info-circle"></i> Sin aplicaciones registradas para <code>${escapeHtml(towerId)}</code>.</p>`;
            return;
        }

        const stateColor = { completed: 'success', failed: 'danger', sms_applied: 'warning', pending: 'secondary' };
        const rows = applies.map(a => {
            const freqMhz = a.freq_khz ? (a.freq_khz / 1000).toFixed(1) : '\u2014';
            const prevMhz = a.prev_freq_khz ? (a.prev_freq_khz / 1000).toFixed(1) : '\u2014';
            const date = a.created_at ? new Date(a.created_at).toLocaleString() : 'N/A';
            const badgeColor = stateColor[a.state] || 'secondary';
            return `
                <tr>
                    <td class="small">${date}</td>
                    <td><strong>${escapeHtml(String(freqMhz))} MHz</strong></td>
                    <td class="text-muted">${escapeHtml(String(prevMhz))} MHz</td>
                    <td><span class="badge bg-${badgeColor}">${escapeHtml(a.state || '\u2014')}</span></td>
                    <td class="small">${escapeHtml(a.applied_by_username || '\u2014')}</td>
                    <td class="text-danger small">${a.error ? escapeHtml(a.error.substring(0, 60)) : '\u2014'}</td>
                </tr>`;
        }).join('');

        container.innerHTML = `
            <h6 class="text-warning mt-2"><i class="bi bi-lightning-charge"></i> Historial de Aplicaciones (${applies.length})</h6>
            <div class="table-responsive">
                <table class="table table-dark table-sm align-middle mb-0" style="font-size:.82rem;">
                    <thead>
                        <tr>
                            <th>Fecha</th><th>Frec. Aplicada</th><th>Frec. Anterior</th>
                            <th>Estado</th><th>Usuario</th><th>Error</th>
                        </tr>
                    </thead>
                    <tbody>${rows}</tbody>
                </table>
            </div>`;
    } catch (err) {
        container.innerHTML = `<p class="text-danger small">Error cargando historial: ${escapeHtml(err.message)}</p>`;
    }
}
