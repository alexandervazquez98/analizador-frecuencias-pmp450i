/**
 * Tower Scan Automation - Frontend JavaScript
 * Maneja la interfaz web, comunicación con API y visualización de datos
 */

// Estado de la aplicación
const appState = {
    currentScanId: null,
    pollInterval: null,
    scanResults: null,
    chartInstance: null,
    lastLogCount: 0
};

// Referencias a elementos DOM
let elements = {};

// Modals
let spectrumModal = null;
let textRecommendationsModal = null;

// ==================== INICIALIZACIÓN ====================

document.addEventListener('DOMContentLoaded', () => {

    // Show Users tab only for admins
    if (window.userRole === 'admin') {
        const usersTabItem = document.getElementById('usersTabItem');
        if (usersTabItem) usersTabItem.style.display = '';
    }

    // Inicializar referencias DOM
    elements = {
        // Inputs
        snmpCommunity: document.getElementById('snmpCommunity'),
        apIPs: document.getElementById('apIPs'),
        smIPs: document.getElementById('smIPs'),
        apFileUpload: document.getElementById('apFileUpload'),
        smFileUpload: document.getElementById('smFileUpload'),
        targetRxLevel: document.getElementById('targetRxLevel'),
        channelWidth: document.getElementById('channelWidth'),

        // Buttons
        startScanBtn: document.getElementById('startScanBtn'),
        clearBtn: document.getElementById('clearBtn'),
        exportResultsBtn: document.getElementById('exportResultsBtn'),
        globalSpectrumBtn: document.getElementById('globalSpectrumBtn'),
        newScanBtn: document.getElementById('newScanBtn'),

        // Auth / Audit
        ticketId: document.getElementById('ticketId'),

        // Dashboard
        welcomeMessage: document.getElementById('welcomeMessage'),
        spectrumChartCanvas: document.getElementById('spectrumChart'),

        // Import Modal
        openImportBtn: document.getElementById('openImportModalBtn'),
        importModal: document.getElementById('importModal'),
        closeImportBtn: document.getElementById('closeImportModal'),
        networkSelect: document.getElementById('networkSelect'),
        towerSelect: document.getElementById('towerSelect'),
        apSelect: document.getElementById('apSelect'),
        confirmImportBtn: document.getElementById('confirmImportBtn'),
        stepLoading: document.getElementById('stepLoading'),
        stepSelection: document.getElementById('stepSelection'),
        smPreviewBox: document.getElementById('smPreviewBox'),
        smListPreview: document.getElementById('smListPreview'),
        smCountBadge: document.getElementById('smCountBadge'),

        // Panels
        statusPanel: document.getElementById('statusPanel'),
        resultsPanel: document.getElementById('resultsPanel'),
        welcomePanel: document.getElementById('welcomePanel'),

        // Status
        statusBadge: document.getElementById('statusBadge'),
        scanIdDisplay: document.getElementById('scanIdDisplay'),
        progressFill: document.getElementById('progressFill'),
        progressText: document.getElementById('progressText'),
        logOutput: document.getElementById('logOutput'),
        detailedLogToggle: document.getElementById('detailedLogToggle'),

        // Results
        resultsSummary: document.getElementById('resultsSummary'),
        frequencyRecommendations: document.getElementById('frequencyRecommendations'),
        spectrumViewerPlaceholder: document.getElementById('spectrumViewerPlaceholder'),

        // Recent Scans
        recentScans: document.getElementById('recentScans')
    };

    // Inicializar Modales Bootstrap
    try {
        const specModalEl = document.getElementById('spectrumModal');
        if (specModalEl) spectrumModal = new bootstrap.Modal(specModalEl);

        const recModalEl = document.getElementById('textRecommendationsModal');
        if (recModalEl) textRecommendationsModal = new bootstrap.Modal(recModalEl);
    } catch (e) {
        console.warn("Bootstrap modals not initialized (might be missing in HTML yet)", e);
    }

    // Configurar event listeners
    setupEventListeners();

    // Cargar configuración desde .env (vía /api/config) y luego historial
    loadConfigDefaults();
    loadRecentScans();
});

// ==================== CARGA DE CONFIGURACIÓN (.env) ====================

async function loadConfigDefaults() {
    /**
     * Carga los defaults de configuración desde el servidor (/api/config)
     * que a su vez los lee del archivo .env.
     * Esto elimina todos los valores hardcodeados en el frontend.
     */
    try {
        const response = await authFetch('/api/config');
        if (!response) return; // Redirected to login
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const config = await response.json();

        // Poblar campos del formulario con los defaults del .env
        if (elements.snmpCommunity && config.snmp_communities) {
            elements.snmpCommunity.value = config.snmp_communities;
        }

        if (elements.targetRxLevel && config.target_rx_level !== undefined) {
            elements.targetRxLevel.value = config.target_rx_level;
        }

        if (elements.channelWidth && config.channel_width !== undefined) {
            elements.channelWidth.value = String(config.channel_width);
        }

        console.log('[Config] Defaults cargados desde .env:', config);
    } catch (error) {
        console.warn('[Config] No se pudo cargar /api/config, usando fallbacks:', error.message);
        // Fallbacks de emergencia si el servidor no responde
        if (elements.snmpCommunity && !elements.snmpCommunity.value) {
            elements.snmpCommunity.value = 'Canopy';
        }
        if (elements.targetRxLevel && !elements.targetRxLevel.value) {
            elements.targetRxLevel.value = '-52';
        }
        if (elements.channelWidth && !elements.channelWidth.value) {
            elements.channelWidth.value = '20';
        }
    }
}

// ==================== EVENT LISTENERS ====================

function setupEventListeners() {
    if (elements.startScanBtn) elements.startScanBtn.addEventListener('click', startScan);
    if (elements.clearBtn) elements.clearBtn.addEventListener('click', clearForm);
    if (elements.exportResultsBtn) elements.exportResultsBtn.addEventListener('click', exportResults);
    if (elements.globalSpectrumBtn) elements.globalSpectrumBtn.addEventListener('click', openGlobalSpectrumViewer);
    if (elements.newScanBtn) elements.newScanBtn.addEventListener('click', resetInterface);

    // Ticket ID: enable/disable scan button based on valid ticket
    if (elements.ticketId) {
        elements.ticketId.addEventListener('input', () => {
            const val = parseInt(elements.ticketId.value);
            if (elements.startScanBtn) {
                elements.startScanBtn.disabled = !val || val <= 0;
            }
        });
    }

    // File Uploads
    setupFileUpload(elements.apFileUpload, elements.apIPs, 'APs');
    setupFileUpload(elements.smFileUpload, elements.smIPs, 'SMs');

    // Import Modal Events
    if (elements.openImportBtn) elements.openImportBtn.addEventListener('click', openImportModal);
    if (elements.closeImportBtn) elements.closeImportBtn.addEventListener('click', () => {
        if (elements.importModal) elements.importModal.style.display = 'none';
    });

    // Wizard Events
    if (elements.networkSelect) elements.networkSelect.addEventListener('change', handleNetworkChange);
    if (elements.towerSelect) elements.towerSelect.addEventListener('change', handleTowerChange);
    if (elements.apSelect) elements.apSelect.addEventListener('change', handleApChange);
    if (elements.confirmImportBtn) elements.confirmImportBtn.addEventListener('click', confirmImport);

    // Close modals on outside click
    window.onclick = function (event) {
        // Assuming recommendationsModal and workOrderModal are defined elsewhere or will be added
        // if (event.target == elements.recommendationsModal) {
        //     elements.recommendationsModal.style.display = "none";
        // }
        // if (event.target == elements.workOrderModal) {
        //      elements.workOrderModal.style.display = "none";
        // }
        if (elements.importModal && event.target == elements.importModal) {
            elements.importModal.style.display = "none";
        }
    }
}

function setupFileUpload(fileInput, targetTextarea, type) {
    if (!fileInput) return;

    fileInput.addEventListener('change', async (e) => {
        const file = e.target.files[0];
        if (!file) return;

        try {
            const content = await readTextFile(file);
            const ips = parseIPList(content);
            if (ips.length > 0) {
                const current = targetTextarea.value.trim();
                targetTextarea.value = current ? current + '\n' + ips.join('\n') : ips.join('\n');
                alert(`Se cargaron ${ips.length} IPs de ${type} correctamente.`);
            } else {
                alert('No se encontraron IPs válidas en el archivo.');
            }
            fileInput.value = ''; // Reset
        } catch (error) {
            alert(`Error leyendo archivo: ${error.message}`);
        }
    });
}

// ==================== LÓGICA PRINCIPAL ====================

/**
 * Wrapper for fetch that handles 401 (unauthorized) by redirecting to /login.
 */
async function authFetch(url, options = {}) {
    const response = await fetch(url, options);
    if (response.status === 401) {
        window.location.href = '/login';
        return null;
    }
    return response;
}

async function startScan() {
    const apIPs = parseIPList(elements.apIPs.value);
    const smIPs = parseIPList(elements.smIPs.value);

    if (apIPs.length === 0) {
        alert('Debe ingresar al menos una IP de Access Point.');
        return;
    }

    // Validate ticket_id
    const ticketId = elements.ticketId ? parseInt(elements.ticketId.value) : null;
    if (!ticketId || ticketId <= 0) {
        alert('Debe ingresar un Ticket ID valido (numero entero positivo).');
        return;
    }

    const channelWidth = parseInt(elements.channelWidth.value);
    const scanData = {
        ap_ips: apIPs,
        sm_ips: smIPs,
        ticket_id: ticketId,
        snmp_community: elements.snmpCommunity.value || '',
        config: {
            target_rx_level: parseFloat(elements.targetRxLevel.value),
            channel_width: channelWidth
        }
    };

    // UI Updates
    if (elements.welcomePanel) elements.welcomePanel.style.display = 'none';
    if (elements.resultsPanel) elements.resultsPanel.style.display = 'none';
    if (elements.statusPanel) elements.statusPanel.style.display = 'block';

    elements.startScanBtn.disabled = true;
    elements.startScanBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span> Iniciando...';

    elements.logOutput.innerHTML = '';
    updateProgress(0, 'Iniciando...');
    addLogEntry(`Iniciando escaneo (Ancho: ${channelWidth} MHz)`, 'info');

    try {
        const response = await authFetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(scanData)
        });

        if (!response) return; // Redirected to login
        if (!response.ok) throw new Error((await response.json()).error || 'Error al iniciar');

        const result = await response.json();
        appState.currentScanId = result.scan_id;
        if (elements.scanIdDisplay) elements.scanIdDisplay.textContent = result.scan_id;

        addLogEntry(`Scan ID: ${result.scan_id}`, 'success');
        addLogEntry(`Objetivo: ${result.ap_count} APs, ${result.sm_count || 0} SMs`, 'info');

        addLogEntry(`Objetivo: ${result.ap_count} APs, ${result.sm_count || 0} SMs`, 'info');

        appState.lastLogCount = 0; // Reset log counter
        startPolling();

    } catch (error) {
        console.error(error);
        addLogEntry(`Error fatal: ${error.message}`, 'error');
        elements.startScanBtn.disabled = false;
        elements.startScanBtn.textContent = 'Reintentar';
    }
}

function startPolling() {
    if (appState.pollInterval) clearInterval(appState.pollInterval);
    appState.pollInterval = setInterval(checkStatus, 2000);
}

async function checkStatus() {
    if (!appState.currentScanId) return;

    try {
        const res = await authFetch(`/api/status/${appState.currentScanId}`);
        if (!res) return; // Redirected to login
        const status = await res.json();

        updateProgress(status.progress);
        updateStatusBadge(status.status);

        // Procesar nuevos logs del backend
        if (status.logs && Array.isArray(status.logs)) {
            if (status.logs.length > appState.lastLogCount) {
                const newLogs = status.logs.slice(appState.lastLogCount);
                newLogs.forEach(log => {
                    // Usar el tipo que viene del backend o default a info
                    addLogEntry(log.msg, log.type || 'info');
                });
                appState.lastLogCount = status.logs.length;
            }
        }

        if (status.status === 'completed') {
            clearInterval(appState.pollInterval);
            addLogEntry('Escaneo finalizado correctamente.', 'success');
            displayResults(status.results);
        } else if (status.status === 'failed') {
            clearInterval(appState.pollInterval);
            addLogEntry(`Falló el escaneo: ${status.error}`, 'error');
            elements.startScanBtn.disabled = false;
            elements.startScanBtn.innerHTML = '<i class="bi bi-broadcast"></i> Iniciar Tower Scan';
        }
    } catch (e) {
        console.error('Polling error:', e);
    }
}

// ==================== VISUALIZACIÓN DE RESULTADOS ====================

function displayResults(results) {
    appState.scanResults = results;

    // UI Switch
    elements.statusPanel.style.display = 'none';
    elements.resultsPanel.style.display = 'block';
    elements.startScanBtn.disabled = false;
    elements.startScanBtn.innerHTML = '<i class="bi bi-broadcast"></i> Iniciar Tower Scan';

    // Summary
    const apCount = results.completed_aps || 0;
    const smCount = results.completed_sms || 0;

    // Configurar Global Spectrum Button si hay APs
    if (elements.globalSpectrumBtn) {
        if (apCount > 0 && results.analysis_results) {
            elements.globalSpectrumBtn.style.display = 'inline-block';
        } else {
            elements.globalSpectrumBtn.style.display = 'none';
        }
    }
    const mode = results.analysis_mode === 'AP_SM_CROSS' ? 'Análisis Cruzado AP-SM' : 'Análisis de AP Indivudual';

    if (elements.resultsSummary) {
        elements.resultsSummary.innerHTML = `
            <div class="row text-center">
                <div class="col-md-4">
                    <div class="p-3 border rounded bg-dark text-light">
                        <small class="text-muted">MODO</small>
                        <h5 class="fw-bold text-info">${mode}</h5>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="p-3 border rounded bg-dark text-light">
                        <small class="text-muted">DISPOSITIVOS</small>
                        <h5 class="fw-bold text-success">${apCount} APs ${smCount > 0 ? `+ ${smCount} SMs` : ''}</h5>
                    </div>
                </div>
                <div class="col-md-4">
                    <div class="p-3 border rounded bg-dark text-light">
                        <small class="text-muted">FECHA</small>
                        <h5 class="fw-bold">${new Date(results.timestamp).toLocaleTimeString()}</h5>
                    </div>
                </div>
            </div>
        `;
    }

    // Recommendations & List
    let containerHTML = '';

    for (const [ip, analysis] of Object.entries(results.analysis_results)) {
        if (analysis.error) {
            containerHTML += renderErrorCard(ip, analysis.error);
            continue;
        }

        containerHTML += renderAPCard(ip, analysis);
    }

    if (elements.frequencyRecommendations) elements.frequencyRecommendations.innerHTML = containerHTML;

    // Render Installation Sheet
    renderInstallationSheet(results);

    // Render JSON in Technical Details

    // Render JSON in Technical Details
    const detailsPane = document.getElementById('details-pane');
    if (detailsPane) {
        detailsPane.innerHTML = `
            <div class="alert alert-info py-2"><i class="bi bi-code-slash"></i> JSON Original del Resultado</div>
            <pre class="bg-dark text-light p-3 rounded border border-secondary" style="max-height: 500px; overflow: auto; font-size: 0.85rem;"><code>${JSON.stringify(results, null, 2)}</code></pre>
        `;
    }

    // Configurar botones de "Ver Espectro"
    // Usamos setTimeout para asegurar que el DOM se haya actualizado
    setTimeout(() => {
        const buttons = document.querySelectorAll('.view-spectrum-btn');
        buttons.forEach(btn => {
            btn.onclick = (e) => {
                e.preventDefault(); // Evitar comportamientos raros
                const ip = btn.getAttribute('data-ip');
                console.log("Abriendo espectro para IP:", ip);
                openSpectrumViewer(ip);
            };
        });
    }, 100);
}

function renderInstallationSheet(results) {
    const container = document.getElementById('installationSheetContent');
    if (!container) return;

    // 1. Calcular Requirement System
    const smCount = results.completed_sms || results.sm_count || 0;

    // Calcular requerimiento de capacidad:
    // Estimación: 5 Mbps por cámara/SM es un estándar seguro para CCTV HD/4K (H.265)
    // No sumamos buffer extra por AP, el usuario quiere cálculo puro por SMs.
    const requiredThroughput = Math.max(5, smCount * 5); // Al menos 5 Mbps si hay 1 SM

    // 2. Obtener pool de frecuencias (Top 50)
    // Extraer de results.combined_ranking (si existe) o de results.analysis_results (AP only)
    let freqPool = [];

    if (results.combined_ranking) {
        freqPool = results.combined_ranking;
    } else {
        // Fallback para analisis solo AP
        for (const [ip, analysis] of Object.entries(results.analysis_results)) {
            // Este caso es complejo porque analyzer no devuelve ranking raw aqui, 
            // pero para esta iteración asumimos que si es cross analysis tenemos combined_ranking
            if (analysis.combined_ranking) freqPool = analysis.combined_ranking;
        }
    }

    // Si aun esta vacio (caso AP Only legacy?), intentamos construir algo
    if (!freqPool || freqPool.length === 0) {
        container.innerHTML = '<div class="alert alert-warning">No hay datos suficientes para generar la ficha de instalación (Falta Ranking).</div>';
        return;
    }



    // Determinar mejor ancho de banda recomendado (Buscando el menor Ancho que cumpla)
    // Estrategia: Buscar en todo el pool (no solo top 15) candidatos VIABLES que cumplan con el requerimiento.
    // Luego ordenarlos por Ancho de Banda (ASC) y luego por Score (DESC).
    let recommendedBW = "N/A";

    // Filtrar Top 50 (User requested 50)
    const topCandidates = freqPool.slice(0, 50);

    // Determinar mejor ancho de banda recomendado (Buscando el menor Ancho que cumpla)
    // Estrategia: Buscar en todo el pool (no solo top 15) candidatos VIABLES que cumplan con el requerimiento.
    // Luego ordenarlos por Ancho de Banda (ASC) y luego por Score (DESC).
    // let recommendedBW = "N/A"; // This line was duplicated, removed.

    // Filtramos candidatos viables con suficiente throughput
    const validCandidates = freqPool.filter(c =>
        c.Estado === 'Viable' &&
        (c['Throughput Est. (Mbps)'] || 0) >= requiredThroughput
    );

    // Si hay candidatos válidos, buscamos el óptimo
    if (validCandidates.length > 0) {
        // Ordenar primero por Ancho (ASCII sort works for 10, 20... wait, need numeric sort)
        // Ascendente en ancho, Descendente en Score
        validCandidates.sort((a, b) => {
            const wa = a['Ancho (MHz)'] || 0;
            const wb = b['Ancho (MHz)'] || 0;
            if (wa !== wb) return wa - wb; // Menor ancho primero

            // A igualdad de ancho, mejor score
            return (b['Score Final'] || 0) - (a['Score Final'] || 0);
        });

        const best = validCandidates[0];
        const cap = best['Throughput Est. (Mbps)'] || 0;
        const width = best['Ancho (MHz)'] || 0;

        recommendedBW = `<span class="text-success fw-bold">${width} MHz</span> <small>(Soporta ${cap} Mbps > ${requiredThroughput} Mbps req.)</small>`;
    } else {
        // Fallback: Si NINGUNO cumple, mostramos el que más se acerca (mayor throughput)
        const bestFallback = freqPool.slice().sort((a, b) => (b['Throughput Est. (Mbps)'] || 0) - (a['Throughput Est. (Mbps)'] || 0))[0];
        if (bestFallback) {
            const cap = bestFallback['Throughput Est. (Mbps)'] || 0;
            const width = bestFallback['Ancho (MHz)'] || 0;
            recommendedBW = `<span class="text-danger fw-bold">${width} MHz</span> <small>(Max Disp: ${cap} Mbps < ${requiredThroughput} Mbps req.)</small>`;
        }
    }

    // 3. Renderizar vista
    let poolRows = topCandidates.map(f => {
        const throughput = f['Throughput Est. (Mbps)'] || 0;
        const isViable = f.Estado === 'Viable' && throughput >= requiredThroughput;
        const rowClass = isViable ? 'table-success' : '';
        const snr = f['SNR Estimado (dB)'] || 0;

        return `
            <tr class="${rowClass}">
                <td><strong>${f['Frecuencia (MHz)']}</strong></td>
                <td>${f['Ancho (MHz)']} MHz</td>
                <td>${throughput} Mbps</td>
                <td class="${throughput >= requiredThroughput ? 'text-success' : 'text-danger'} fw-bold">
                    ${throughput >= requiredThroughput ? 'CUMPLE' : 'INSUFICIENTE'}
                </td>
                <td>${snr} dB</td>
                <td><span class="badge bg-${f.Estado === 'Viable' ? 'success' : 'danger'}">${f.Estado}</span></td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <div class="row g-4 mb-4">
            <!-- Params Card -->
            <div class="col-md-6">
                <div class="card bg-dark text-light border-light h-100">
                    <div class="card-header border-light"><i class="bi bi-sliders"></i> Parámetros de Configuración</div>
                    <div class="card-body">
                        <ul class="list-group list-group-flush bg-dark text-light">
                             <li class="list-group-item bg-dark text-light d-flex justify-content-between">
                                <span>Contention Slots:</span> <span class="fw-bold text-info">4 (Autoset)</span>
                            </li>
                            <li class="list-group-item bg-dark text-light d-flex justify-content-between">
                                <span>Frame Period:</span> <span class="fw-bold text-info">2.5 ms (CCTV Priority)</span>
                            </li>
                             <li class="list-group-item bg-dark text-light d-flex justify-content-between">
                                <span>Max Range:</span> <span class="fw-bold text-warning">7 Km o igualar todos los APs a la misma distancia</span>
                            </li>
                             <li class="list-group-item bg-dark text-light d-flex justify-content-between">
                                <span>Downlink Data:</span> <span class="fw-bold">15% (85% Uplink)</span>
                            </li>
                        </ul>
                    </div>
                </div>
            </div>
            
             <!-- Capacity Card -->
            <div class="col-md-6">
                <div class="card bg-dark text-light border-light h-100">
                    <div class="card-header border-light"><i class="bi bi-speedometer2"></i> Análisis de Capacidad</div>
                    <div class="card-body text-center">
                        <h6 class="text-muted">Requerimiento Calculado (${smCount} SMs x 5Mbps)</h6>
                        <h2 class="display-6 text-warning mb-3">${requiredThroughput} Mbps</h2>
                        <hr class="border-secondary">
                        <h6 class="text-muted">Ancho de Canal Recomendado</h6>
                        <h4>${recommendedBW}</h4>
                    </div>
                </div>
            </div>
        </div>

        <!-- Frequency Pool Table -->
        <div class="card bg-dark text-light border-light">
            <div class="card-header border-light bg-secondary text-white">
                <i class="bi bi-collection-fill"></i> Pool de Frecuencias Candidatas (Top 50) - Prioridad Menor Ancho
            </div>
            <div class="table-responsive">
                <table class="table table-dark table-hover table-sm mb-0 text-center align-middle">
                    <thead>
                        <tr>
                            <th>Freq (MHz)</th>
                            <th>Ancho</th>
                            <th>Capacidad Est.</th>
                            <th>Status Req.</th>
                            <th>SNR Est.</th>
                            <th>Viabilidad RF</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${poolRows}
                    </tbody>
                </table>
            </div>
            <div class="card-footer border-secondary text-muted small">
                * Capacidad estimada teórica basada en SNR y Modulación. Realizar prueba de link test.
            </div>
        </div>
    `;
}
function renderAPCard(ip, analysis) {
    const isCross = analysis.mode === 'AP_SM_CROSS';
    let bestFreqInfo = '';
    let qualityBadge = '';

    // Determinar mejor frecuencia y calidad
    if (isCross && analysis.best_combined_frequency) {
        const best = analysis.best_combined_frequency;
        const color = best.is_viable ? 'success' : 'danger';
        qualityBadge = `<span class="badge bg-${color}">${best.is_viable ? 'VIABLE' : 'NO VIABLE'}</span>`;

        bestFreqInfo = `
            <div class="alert alert-${color} mb-2">
                <strong><i class="bi bi-star-fill"></i> Mejor Frecuencia: ${best.frequency} MHz</strong><br>
                <small>Score Combinado: ${best.combined_score} | Ruido Promedio SMs: ${best.sm_avg_noise.toFixed(1)} dBm</small>
            </div>
        `;
    } else if (analysis.best_frequency) {
        const best = analysis.best_frequency;
        // Mapear calidad de texto a colores bootstap
        const qColorMap = {
            'EXCELENTE': 'success', 'BUENO': 'primary', 'ACEPTABLE': 'info',
            'MARGINAL': 'warning', 'CRÍTICO': 'danger'
        };
        const qColor = qColorMap[best.quality_level] || 'secondary';
        qualityBadge = `<span class="badge bg-${qColor}">${best.quality_level || 'N/A'}</span>`;

        bestFreqInfo = `
            <div class="alert alert-${qColor} mb-2 text-dark">
                <strong><i class="bi bi-star-fill"></i> Mejor Frecuencia: ${best['Frecuencia Central (MHz)']} MHz</strong><br>
                <small>Score: ${best['Puntaje Final']} | SNR Est: ${best['SNR Estimado (dB)']} dB</small>
            </div>
        `;
    } else {
        bestFreqInfo = '<div class="alert alert-warning">No se encontraron frecuencias válidas.</div>';
    }

    // Botón de Espectro (solo si hay datos)
    // spectrum_data es un objeto {ap: [], sms: {}}, no un array, no tiene .length
    const hasSpectrumData = analysis.spectrum_data && (analysis.spectrum_data.ap || analysis.spectrum_data.sms);
    // Usamos onclick inline o data attributes, voy a usar data attributes para el listener global
    const spectrumBtn = hasSpectrumData
        ? `<button type="button" class="btn btn-outline-info btn-sm view-spectrum-btn" data-ip="${ip}"><i class="bi bi-graph-up"></i> Ver Espectro</button>`
        : '<span class="text-muted small">Sin datos de espectro</span>';

    return `
        <div class="card mb-3 border-secondary bg-dark text-light">
            <div class="card-header d-flex justify-content-between align-items-center">
                <h5 class="mb-0"><i class="bi bi-router"></i> AP ${ip}</h5>
                <div>${qualityBadge}</div>
            </div>
            <div class="card-body">
                ${bestFreqInfo}
                <div class="d-flex justify-content-between align-items-center mt-3">
                    <small class="text-muted"><i class="bi bi-database"></i> ${analysis.spectrum_points || 0} puntos analizados</small>
                    ${spectrumBtn}
                </div>
            </div>
        </div>
    `;
}

function renderErrorCard(ip, error) {
    return `
        <div class="card mb-3 border-danger bg-dark">
            <div class="card-body text-danger">
                <h5 class="card-title"><i class="bi bi-x-circle"></i> AP ${ip} - Error</h5>
                <p class="card-text">${error}</p>
            </div>
        </div>
    `;
}

// ==================== VISOR DE ESPECTRO (CHART.JS) ====================

const CHART_COLORS = [
    '#FF5722', '#E91E63', '#9C27B0', '#673AB7', '#3F51B5', // Cálidos/Vibrantes
    '#00BCD4', '#009688', '#4CAF50', '#8BC34A', '#CDDC39'  // Fríos/Natura
];

function openSpectrumViewer(ip) {
    console.log("Abriendo visualizador para", ip);

    if (!appState.currentScanId) {
        // Intentar recuperar ID del resultado si no está en estado global
        if (appState.scanResults && appState.scanResults.scan_id) {
            appState.currentScanId = appState.scanResults.scan_id;
        } else {
            alert("No se puede identificar el ID del escaneo. Por favor inicie un nuevo escaneo.");
            return;
        }
    }

    const url = `/spectrum/${appState.currentScanId}/${ip}`;
    window.open(url, '_blank', 'width=1200,height=800');
}

function openGlobalSpectrumViewer() {
    if (!appState.scanResults || !appState.scanResults.analysis_results) {
        alert("No hay resultados de análisis disponibles.");
        return;
    }

    // Buscar la primer IP de AP disponible
    const ips = Object.keys(appState.scanResults.analysis_results);
    if (ips.length === 0) {
        alert("No se encontraron APs en los resultados.");
        return;
    }

    // Abrir el primero (o podría implementarse un selector si se desea)
    openSpectrumViewer(ips[0]);
}

// ==================== UTILS ====================

function updateProgress(percent, text) {
    if (elements.progressFill) elements.progressFill.style.width = `${percent}%`;
    if (elements.progressText) elements.progressText.textContent = `${percent}%`;
    if (text && elements.statusBadge) elements.statusBadge.textContent = text;
}

function updateStatusBadge(status) {
    if (!elements.statusBadge) return;
    const map = {
        'initializing': 'Inicializando',
        'scanning': 'Escaneando',
        'analyzing': 'Analizando',
        'completed': 'Completado',
        'failed': 'Error'
    };
    elements.statusBadge.textContent = map[status] || status;
}

function addLogEntry(msg, type = 'info', detailed = false) {
    if (!elements.logOutput) return;
    if (detailed && elements.detailedLogToggle && !elements.detailedLogToggle.checked) return;

    const div = document.createElement('div');
    const color = type === 'error' ? 'text-danger' :
        type === 'success' ? 'text-success' :
            type === 'warning' ? 'text-warning' : 'text-light';
    div.className = `${color} mb-1`;
    div.innerHTML = `<small class="text-muted">[${new Date().toLocaleTimeString()}]</small> ${msg}`;
    elements.logOutput.appendChild(div);
    elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
}

function resetInterface() {
    elements.resultsPanel.style.display = 'none';
    elements.statusPanel.style.display = 'none';
    elements.welcomePanel.style.display = 'block';
    clearForm();
}

function clearForm() {
    elements.apIPs.value = '';
    elements.smIPs.value = '';
    if (elements.ticketId) elements.ticketId.value = '';
    if (elements.startScanBtn) elements.startScanBtn.disabled = true;
    elements.logOutput.innerHTML = '';
}

function parseIPList(text) {
    if (!text) return [];

    return text.split(/[\n,]+/)
        .map(t => t.split('#')[0].trim()) // Eliminar comentarios y espacios
        .filter(t => t.length > 0) // Quitar líneas vacías
        .filter(t =>
            // Regex básico de IP
            /^(\d{1,3}\.){3}\d{1,3}$/.test(t)
        );
}

function readTextFile(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = e => resolve(e.target.result);
        reader.onerror = e => reject(e);
        reader.readAsText(file);
    });
}

// Simulador de Progreso ELIMINADO - Ahora usamos logs reales del backend

async function loadRecentScans() {
    if (!elements.recentScans) return;

    try {
        const response = await authFetch('/api/scans');
        if (!response) return; // Redirected to login
        if (!response.ok) throw new Error(`HTTP ${response.status}`);

        const data = await response.json();
        const scans = data.scans || [];

        if (scans.length === 0) {
            elements.recentScans.innerHTML = '<div class="text-center text-muted p-2">Sin escaneos recientes</div>';
            return;
        }

        elements.recentScans.innerHTML = scans.map(scan => {
            const date = scan.created_at ? new Date(scan.created_at).toLocaleString() : 'N/A';
            const statusColors = {
                'completed': 'success',
                'scanning': 'primary',
                'analyzing': 'info',
                'failed': 'danger',
                'started': 'warning'
            };
            const badgeColor = statusColors[scan.status] || 'secondary';
            const statusLabel = scan.status || 'unknown';
            const isClickable = scan.status === 'completed';

            return `
                <a href="#" class="list-group-item list-group-item-action bg-dark text-light border-secondary py-1 px-2 ${isClickable ? 'recent-scan-entry' : ''}"
                   data-scan-id="${scan.scan_id}" ${!isClickable ? 'style="pointer-events:none;opacity:0.6;"' : ''}>
                    <div class="d-flex justify-content-between align-items-center">
                        <small class="text-truncate me-2" style="max-width: 140px;" title="${scan.scan_id}">${scan.scan_id.substring(0, 8)}...</small>
                        <span class="badge bg-${badgeColor}" style="font-size:0.65rem;">${statusLabel}</span>
                    </div>
                    <div class="d-flex justify-content-between">
                        <small class="text-muted" style="font-size:0.65rem;">${date}</small>
                        <small class="text-muted" style="font-size:0.65rem;">${scan.ap_count || 0} APs</small>
                    </div>
                </a>
            `;
        }).join('');

        // Add click handlers to load completed scan results
        elements.recentScans.querySelectorAll('.recent-scan-entry').forEach(entry => {
            entry.addEventListener('click', async (e) => {
                e.preventDefault();
                const scanId = entry.getAttribute('data-scan-id');
                if (!scanId) return;

                try {
                    const res = await authFetch(`/api/status/${scanId}`);
                    if (!res) return; // Redirected to login
                    const status = await res.json();

                    if (status.status === 'completed' && status.results) {
                        appState.currentScanId = scanId;
                        if (elements.welcomePanel) elements.welcomePanel.style.display = 'none';
                        if (elements.statusPanel) elements.statusPanel.style.display = 'none';
                        displayResults(status.results);
                    }
                } catch (err) {
                    console.error('Error loading scan:', err);
                }
            });
        });

    } catch (error) {
        console.warn('[RecentScans] No se pudo cargar historial:', error.message);
        elements.recentScans.innerHTML = '<div class="text-center text-muted p-2">Error cargando historial</div>';
    }
}

function exportResults() {
    if (!appState.scanResults) return;
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(appState.scanResults, null, 2));
    const downloadAnchorNode = document.createElement('a');
    downloadAnchorNode.setAttribute("href", dataStr);
    downloadAnchorNode.setAttribute("download", "scan_results_" + new Date().toISOString() + ".json");
    document.body.appendChild(downloadAnchorNode);
    downloadAnchorNode.click();
    downloadAnchorNode.remove();
}

// ==================== CNMAESTRO IMPORT WIZARD ====================

let cnMaestroData = null;

async function openImportModal() {
    if (!elements.importModal) return;
    elements.importModal.style.display = 'block';

    // Reset view
    if (elements.stepLoading) elements.stepLoading.style.display = 'block';
    if (elements.stepSelection) elements.stepSelection.style.display = 'none';

    try {
        const response = await authFetch('/api/cnmaestro/inventory');
        if (!response) return; // Redirected to login
        const data = await response.json();

        if (data.error) {
            alert('Error cargando inventario: ' + data.error);
            elements.importModal.style.display = 'none';
            return;
        }

        cnMaestroData = data;
        populateNetworks();

        if (elements.stepLoading) elements.stepLoading.style.display = 'none';
        if (elements.stepSelection) elements.stepSelection.style.display = 'block';

    } catch (e) {
        alert('Error conectando con el servidor: ' + e);
        elements.importModal.style.display = 'none';
    }
}

function populateNetworks() {
    if (!elements.networkSelect) return;
    const networks = Object.keys(cnMaestroData).sort();
    elements.networkSelect.innerHTML = '<option value="">-- Seleccionar --</option>';
    networks.forEach(net => {
        const option = document.createElement('option');
        option.value = net;
        option.textContent = net;
        elements.networkSelect.appendChild(option);
    });

    resetSelect(elements.towerSelect);
    resetSelect(elements.apSelect);
}

function handleNetworkChange() {
    const net = elements.networkSelect.value;
    if (!net) {
        resetSelect(elements.towerSelect);
        return;
    }

    const towers = Object.keys(cnMaestroData[net]).sort();
    elements.towerSelect.innerHTML = '<option value="">-- Seleccionar Torre --</option>';
    towers.forEach(t => {
        const option = document.createElement('option');
        option.value = t;
        option.textContent = t;
        elements.towerSelect.appendChild(option);
    });

    elements.towerSelect.disabled = false;
    resetSelect(elements.apSelect);
}

function handleTowerChange() {
    const net = elements.networkSelect.value;
    const tower = elements.towerSelect.value;
    if (!tower) {
        resetSelect(elements.apSelect);
        return;
    }

    const towerData = cnMaestroData[net][tower];
    const aps = towerData.aps || [];

    elements.apSelect.innerHTML = '<option value="">-- Seleccionar AP --</option>';
    aps.forEach((ap, index) => {
        const option = document.createElement('option');
        option.value = index; // Store index to retrieve obj later
        option.textContent = `${ap.name} (${ap.ip})`;
        elements.apSelect.appendChild(option);
    });

    elements.apSelect.disabled = false;
    elements.confirmImportBtn.disabled = true;
    elements.smPreviewBox.style.display = 'none';
}

function handleApChange() {
    const net = elements.networkSelect.value;
    const tower = elements.towerSelect.value;
    const apIndex = elements.apSelect.value;

    if (apIndex === "") {
        elements.confirmImportBtn.disabled = true;
        elements.smPreviewBox.style.display = 'none';
        return;
    }

    // Logic: Use correct SM list linked to this AP
    const towerData = cnMaestroData[net][tower];
    const selectedAp = towerData.aps[apIndex];
    const linkedSms = selectedAp.sms || [];

    // Display
    elements.smListPreview.innerHTML = linkedSms.length > 0 ? '' : '<em>Sin SMs conectados (o enlace desconocido)</em>';
    linkedSms.forEach(sm => {
        const div = document.createElement('div');
        div.textContent = `• ${sm.name} (${sm.ip})`;
        elements.smListPreview.appendChild(div);
    });

    elements.smCountBadge.textContent = linkedSms.length;
    elements.smPreviewBox.style.display = 'block';

    // Enable import even if 0 SMs (maybe user just wants AP)
    elements.confirmImportBtn.disabled = false;
}

function confirmImport() {
    const net = elements.networkSelect.value;
    const tower = elements.towerSelect.value;
    const apIndex = elements.apSelect.value;

    const towerData = cnMaestroData[net][tower];
    const selectedAp = towerData.aps[apIndex];
    const sms = selectedAp.sms || [];

    // Populate Inputs
    elements.apIPs.value = selectedAp.ip + " # " + selectedAp.name;

    // SMs IPs
    const smIps = sms.map(sm => sm.ip).filter(ip => ip);
    elements.smIPs.value = smIps.join('\n');

    elements.importModal.style.display = 'none';
}

function resetSelect(sel) {
    if (!sel) return;
    sel.innerHTML = '<option value="">-- Seleccionar --</option>';
    sel.disabled = true;
    if (elements.confirmImportBtn) elements.confirmImportBtn.disabled = true;
    if (elements.smPreviewBox) elements.smPreviewBox.style.display = 'none';
}

// ==================== SHARED PANEL UTILITIES ====================
// These utilities are used by towers.js, users.js and history.js

/**
 * Shows a dismissible alert inside a panel's alert container.
 * Auto-dismisses after 5 seconds.
 * @param {string} containerId - The ID of the alert host element.
 * @param {string} message
 * @param {'info'|'success'|'warning'|'danger'} type
 */
function showPanelAlert(containerId, message, type = 'info') {
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = `
        <div class="alert alert-${type} alert-dismissible py-2 small" role="alert">
            ${escapeHtml(message)}
            <button type="button" class="btn-close btn-sm" onclick="this.parentElement.remove()"></button>
        </div>
    `;
    container.style.display = '';
    setTimeout(() => {
        if (container.firstChild) container.firstChild.remove();
    }, 5000);
}

/**
 * Escapes HTML special characters to prevent XSS.
 * @param {*} str
 * @returns {string}
 */
function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

/**
 * Escapes a string for safe use in HTML attribute values
 * (single/double quotes in onclick handlers, etc.).
 * @param {*} str
 * @returns {string}
 */
function escapeAttr(str) {
    if (str === null || str === undefined) return '';
    return String(str)
        .replace(/\\/g, '\\\\')
        .replace(/'/g, "\\'")
        .replace(/"/g, '&quot;');
}

// Note: Torres, Usuarios and Historial logic has been extracted to:
//   static/js/towers.js
//   static/js/users.js
//   static/js/history.js
// Those modules depend on authFetch, showPanelAlert, escapeHtml, escapeAttr defined above.

