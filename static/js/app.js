/**
 * Tower Scan Automation - Frontend JavaScript
 * Maneja la interfaz web, comunicaciÃ³n con API y visualizaciÃ³n de datos
 */

// Estado de la aplicaciÃ³n
const appState = {
    currentScanId: null,
    pollInterval: null,
    scanResults: null,
    lastLogCount: 0,
    // Log auto-scroll state (T4 â€” frontend-responsive-ux)
    logUserScrolled: false,  // true si el usuario scrolleÃ³ hacia arriba
    logNewLinesCount: 0,     // lÃ­neas nuevas pendientes de ver
};

// Referencias a elementos DOM
let elements = {};

// ==================== INICIALIZACIÃ“N ====================

document.addEventListener('DOMContentLoaded', () => {

    // Show Users nav item only for admins
    if (window.userRole === 'admin') {
        const navUsers = document.getElementById('nav-users');
        if (navUsers) navUsers.style.display = '';
    }

    // Inicializar referencias DOM
    elements = {
        // Inputs
        snmpCommunity: document.getElementById('snmpCommunity'),
        apIPs: document.getElementById('apIPs'),
        apFileUpload: document.getElementById('apFileUpload'),
        targetRxLevel: document.getElementById('targetRxLevel'),
        channelWidth: document.getElementById('channelWidth'),

        // Buttons
        startScanBtn: document.getElementById('startScanBtn'),
        discoverBtn: document.getElementById('discoverBtn'),
        clearBtn: document.getElementById('clearBtn'),
        exportResultsBtn: document.getElementById('exportResultsBtn'),
        globalSpectrumBtn: document.getElementById('globalSpectrumBtn'),
        newScanBtn: document.getElementById('newScanBtn'),

        // Auth / Audit
        ticketId: document.getElementById('ticketId'),

        // Discovery section (ap-sm-autodiscovery)
        discoverySection: document.getElementById('discoverySection'),
        discoveryCards: document.getElementById('discoveryCards'),
        discoveryCount: document.getElementById('discoveryCount'),

        // Panels
        statusPanel: document.getElementById('statusPanel'),
        resultsPanel: document.getElementById('resultsPanel'),
        emptyState: document.getElementById('emptyState'),

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
        recentScans: document.getElementById('recentScans'),

        // Log badge
        logNewLinesBadge: document.getElementById('logNewLinesBadge'),
    };

    // Configurar event listeners
    setupEventListeners();

    // Cargar configuraciÃ³n desde .env (vÃ­a /api/config) y luego historial
    loadConfigDefaults();
    loadRecentScans();
});

// ==================== CARGA DE CONFIGURACIÃ“N (.env) ====================

async function loadConfigDefaults() {
    /**
     * Carga los defaults de configuraciÃ³n desde el servidor (/api/config)
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

    // Discovery button
    if (elements.discoverBtn) elements.discoverBtn.addEventListener('click', runDiscovery);

    // Log scroll listener â€” detecta si el usuario scrollÃ³ hacia arriba
    if (elements.logOutput) {
        elements.logOutput.addEventListener('scroll', () => {
            if (isLogAtBottom()) {
                // Usuario volviÃ³ al fondo â€” reanudar auto-scroll y ocultar badge
                appState.logUserScrolled = false;
                appState.logNewLinesCount = 0;
                updateLogBadge(0);
            } else {
                // Usuario scrollÃ³ hacia arriba
                appState.logUserScrolled = true;
            }
        });
    }
}

// ==================== LOG HELPERS (T4 â€” frontend-responsive-ux) ====================

/**
 * Devuelve true si el panel de log estÃ¡ scrolleado al fondo (threshold: 10px).
 * O(1) â€” no usa observers.
 */
function isLogAtBottom() {
    if (!elements.logOutput) return true;
    const el = elements.logOutput;
    return el.scrollTop + el.clientHeight >= el.scrollHeight - 10;
}

/**
 * Actualiza la visibilidad y texto del badge #logNewLinesBadge.
 * @param {number} count â€” 0 para ocultar el badge.
 */
function updateLogBadge(count) {
    if (!elements.logNewLinesBadge) return;
    if (count <= 0) {
        elements.logNewLinesBadge.style.display = 'none';
    } else {
        elements.logNewLinesBadge.textContent = `${count} nueva${count > 1 ? 's' : ''}`;
        elements.logNewLinesBadge.style.display = 'inline-block';
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
                showPanelAlert('scanAlert', `Se cargaron ${ips.length} IPs de ${type} correctamente.`, 'success');
            } else {
                showPanelAlert('scanAlert', 'No se encontraron IPs vÃ¡lidas en el archivo.', 'warning');
            }
            fileInput.value = ''; // Reset
        } catch (error) {
            showPanelAlert('scanAlert', `Error leyendo archivo: ${error.message}`, 'danger');
        }
    });
}

// ==================== LÃ“GICA PRINCIPAL ====================

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
    const apIPs = parseIPList(elements.apIPs ? elements.apIPs.value : '');

    if (apIPs.length === 0) {
        showScanAlert('Debe ingresar al menos una IP de Access Point.', 'warning');
        return;
    }

    const ticketId = elements.ticketId ? parseInt(elements.ticketId.value) : null;
    if (!ticketId || ticketId <= 0) {
        showScanAlert('Debe ingresar un Ticket ID vÃ¡lido (nÃºmero entero positivo).', 'warning');
        return;
    }

    const channelWidth = parseInt(elements.channelWidth ? elements.channelWidth.value : '20');
    // SMs are auto-discovered in the backend (Fase 0.5 â€” ap-sm-autodiscovery)
    const scanData = {
        ap_ips: apIPs,
        sm_ips: [],
        ticket_id: ticketId,
        snmp_community: elements.snmpCommunity ? elements.snmpCommunity.value : '',
        config: {
            target_rx_level: parseFloat(elements.targetRxLevel ? elements.targetRxLevel.value : '-52'),
            channel_width: channelWidth
        }
    };

    // UI Updates
    if (elements.emptyState) elements.emptyState.style.display = 'none';
    if (elements.resultsPanel) elements.resultsPanel.style.display = 'none';
    if (elements.statusPanel) elements.statusPanel.style.display = '';

    elements.startScanBtn.disabled = true;
    elements.startScanBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Iniciando...';

    if (elements.logOutput) elements.logOutput.innerHTML = '';
    updateProgress(0, 'Iniciando...');
    setStepperState('initializing');
    addLogEntry(`Iniciando escaneo (Ancho: ${channelWidth} MHz)`, 'info');

    try {
        const response = await authFetch('/api/scan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(scanData)
        });

        if (!response) return;
        if (!response.ok) throw new Error((await response.json()).error || 'Error al iniciar');

        const result = await response.json();
        appState.currentScanId = result.scan_id;
        if (elements.scanIdDisplay) elements.scanIdDisplay.textContent = result.scan_id;

        addLogEntry(`Scan ID: ${result.scan_id}`, 'success');
        addLogEntry(`Objetivo: ${result.ap_count} APs â€” SMs via SNMP auto-discovery`, 'info');

        appState.lastLogCount = 0;
        startPolling();

    } catch (error) {
        console.error(error);
        addLogEntry(`Error fatal: ${error.message}`, 'error');
        elements.startScanBtn.disabled = false;
        elements.startScanBtn.innerHTML = '<i class="bi bi-play-circle-fill"></i> Iniciar AnÃ¡lisis';
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
            addLogEntry(`FallÃ³ el escaneo: ${status.error}`, 'error');
            elements.startScanBtn.disabled = false;
            elements.startScanBtn.innerHTML = '<i class="bi bi-broadcast"></i> Iniciar Tower Scan';
        }
    } catch (e) {
        console.error('Polling error:', e);
    }
}

// ==================== VISUALIZACIÃ“N DE RESULTADOS ====================

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
    const mode = results.analysis_mode === 'AP_SM_CROSS' ? 'AP-SM Cross' : 'AP Only';
    const ts = results.timestamp ? new Date(results.timestamp).toLocaleTimeString() : 'â€”';

    if (elements.resultsSummary) {
        elements.resultsSummary.innerHTML = `
            <div class="rsbar-stat"><div class="rsbar-num">${apCount}</div><div class="rsbar-label">APs</div></div>
            <div class="rsbar-stat"><div class="rsbar-num">${smCount}</div><div class="rsbar-label">SMs</div></div>
            <div class="rsbar-stat" style="min-width:110px;">
                <div class="rsbar-num" style="font-size:0.85rem;color:var(--accent-cyan);">${mode}</div>
                <div class="rsbar-label">Modo</div>
            </div>
            <div class="rsbar-stat" style="min-width:90px;">
                <div class="rsbar-num" style="font-size:0.85rem;">${ts}</div>
                <div class="rsbar-label">Hora</div>
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
    // EstimaciÃ³n: 5 Mbps por cÃ¡mara/SM es un estÃ¡ndar seguro para CCTV HD/4K (H.265)
    // No sumamos buffer extra por AP, el usuario quiere cÃ¡lculo puro por SMs.
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
            // pero para esta iteraciÃ³n asumimos que si es cross analysis tenemos combined_ranking
            if (analysis.combined_ranking) freqPool = analysis.combined_ranking;
        }
    }

    // Si aun esta vacio (caso AP Only legacy?), intentamos construir algo
    if (!freqPool || freqPool.length === 0) {
        container.innerHTML = '<div class="alert alert-warning">No hay datos suficientes para generar la ficha de instalaciÃ³n (Falta Ranking).</div>';
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
    // Soporte dual de keys: AP_ONLY usa 'VÃ¡lido'='SÃ­', AP_SM_CROSS usa 'Estado'='Viable'
    const validCandidates = freqPool.filter(c => {
        const isViable = c.Estado === 'Viable' || c['VÃ¡lido'] === 'SÃ­';
        return isViable && (c['Throughput Est. (Mbps)'] || 0) >= requiredThroughput;
    });

    // Si hay candidatos vÃ¡lidos, buscamos el Ã³ptimo
    if (validCandidates.length > 0) {
        // Ordenar primero por Ancho (ASCII sort works for 10, 20... wait, need numeric sort)
        // Ascendente en ancho, Descendente en Score
        validCandidates.sort((a, b) => {
            // Soporte dual: keys largas (backend AP_ONLY) y cortas (backend AP_SM_CROSS)
            const wa = a['Ancho Banda (MHz)'] ?? a['Ancho (MHz)'] ?? 0;
            const wb = b['Ancho Banda (MHz)'] ?? b['Ancho (MHz)'] ?? 0;
            if (wa !== wb) return wa - wb; // Menor ancho primero

            // A igualdad de ancho, mejor score
            const sa = a['Puntaje Final'] ?? a['Score Final'] ?? 0;
            const sb = b['Puntaje Final'] ?? b['Score Final'] ?? 0;
            return sb - sa;
        });

        const best = validCandidates[0];
        const cap = best['Throughput Est. (Mbps)'] || 0;
        // Soporte dual de keys
        const width = best['Ancho Banda (MHz)'] ?? best['Ancho (MHz)'] ?? 0;

        recommendedBW = `<span class="text-success fw-bold">${width} MHz</span> <small>(Soporta ${cap} Mbps > ${requiredThroughput} Mbps req.)</small>`;
    } else {
        // Fallback: Si NINGUNO cumple, mostramos el que mÃ¡s se acerca (mayor throughput)
        const bestFallback = freqPool.slice().sort((a, b) => (b['Throughput Est. (Mbps)'] || 0) - (a['Throughput Est. (Mbps)'] || 0))[0];
        if (bestFallback) {
            const cap = bestFallback['Throughput Est. (Mbps)'] || 0;
            const width = bestFallback['Ancho Banda (MHz)'] ?? bestFallback['Ancho (MHz)'] ?? 0;
            recommendedBW = `<span class="text-danger fw-bold">${width} MHz</span> <small>(Max Disp: ${cap} Mbps < ${requiredThroughput} Mbps req.)</small>`;
        }
    }

    // 3. Renderizar vista
    let poolRows = topCandidates.map(f => {
        const throughput = f['Throughput Est. (Mbps)'] || 0;
        // Dual-key support: AP_ONLY usa 'VÃ¡lido'='SÃ­', AP_SM_CROSS usa 'Estado'='Viable'
        const estadoLabel = f.Estado ?? (f['VÃ¡lido'] === 'SÃ­' ? 'Viable' : 'No Viable');
        const isViable = estadoLabel === 'Viable' && throughput >= requiredThroughput;
        const rowClass = isViable ? 'table-success' : '';
        const snr = f['SNR Estimado (dB)'] || 0;
        // Dual-key: 'Frecuencia Central (MHz)' (AP_ONLY) vs 'Frecuencia (MHz)' (AP_SM_CROSS)
        const freq = f['Frecuencia Central (MHz)'] ?? f['Frecuencia (MHz)'] ?? 'â€”';
        // Dual-key: 'Ancho Banda (MHz)' (AP_ONLY) vs 'Ancho (MHz)' (AP_SM_CROSS)
        const ancho = f['Ancho Banda (MHz)'] ?? f['Ancho (MHz)'] ?? 'â€”';

        return `
            <tr class="${rowClass}">
                <td><strong>${freq}</strong></td>
                <td>${ancho} MHz</td>
                <td>${throughput} Mbps</td>
                <td class="${throughput >= requiredThroughput ? 'text-success' : 'text-danger'} fw-bold">
                    ${throughput >= requiredThroughput ? 'CUMPLE' : 'INSUFICIENTE'}
                </td>
                <td>${snr} dB</td>
                <td><span class="badge bg-${estadoLabel === 'Viable' ? 'success' : 'danger'}">${estadoLabel}</span></td>
            </tr>
        `;
    }).join('');

    container.innerHTML = `
        <div class="row g-4 mb-4">
            <!-- Params Card -->
            <div class="col-md-6">
                <div class="card bg-dark text-light border-light h-100">
                    <div class="card-header border-light"><i class="bi bi-sliders"></i> ParÃ¡metros de ConfiguraciÃ³n</div>
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
                    <div class="card-header border-light"><i class="bi bi-speedometer2"></i> AnÃ¡lisis de Capacidad</div>
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
                * Capacidad estimada teÃ³rica basada en SNR y ModulaciÃ³n. Realizar prueba de link test.
            </div>
        </div>
    `;
}
function renderAPCard(ip, analysis) {
    const isCross = analysis.mode === 'AP_SM_CROSS';
    let bestFreqMhz = null, bwMhz = null;
    let qualityClass = 'none', qualityLabel = 'N/A';
    let metricScore = 'â€”', metricNoise = 'â€”';
    let metricPoints = analysis.spectrum_points || 0;
    let applyBtn = '';
    const isViewer = (window.userRole === 'viewer');

    if (isCross && analysis.best_combined_frequency) {
        const best = analysis.best_combined_frequency;
        bestFreqMhz = best.frequency;
        bwMhz = best.channel_width || best.bandwidth || 20;
        qualityClass = best.is_viable ? 'excellent' : 'poor';
        qualityLabel = best.is_viable ? 'VIABLE' : 'NO VIABLE';
        metricScore = best.combined_score != null ? Number(best.combined_score).toFixed(2) : 'â€”';
        metricNoise = best.sm_avg_noise != null ? `${Number(best.sm_avg_noise).toFixed(1)} dBm` : 'â€”';
    } else if (!isCross && analysis.best_frequency) {
        const best = analysis.best_frequency;
        bestFreqMhz = best['Frecuencia Central (MHz)'];
        bwMhz = best['Ancho Banda (MHz)'] || 20;
        const qMap = { 'EXCELENTE': 'excellent', 'BUENO': 'good', 'ACEPTABLE': 'fair', 'MARGINAL': 'fair', 'CRÃTICO': 'poor' };
        qualityClass = qMap[best.quality_level] || 'none';
        qualityLabel = best.quality_level || 'N/A';
        metricScore = best['Puntaje Final'] != null ? String(best['Puntaje Final']) : 'â€”';
        metricNoise = best['SNR Estimado (dB)'] != null ? `${best['SNR Estimado (dB)']} dB` : 'â€”';
    }

    if (!isViewer && bestFreqMhz) {
        const scanId = appState.currentScanId || (appState.scanResults && appState.scanResults.scan_id);
        if (scanId) {
            const ranking = analysis.combined_ranking || [];
            const freqs = ranking.map(f => f.frequency || f['Frecuencia Central (MHz)'] || f['Frecuencia (MHz)']).filter(Boolean);
            const freqMin = freqs.length ? Math.min(...freqs) : 3400;
            const freqMax = freqs.length ? Math.max(...freqs) : 6000;
            const recBw = bwMhz || 20;
            const scoreNorm = isCross
                ? (analysis.best_combined_frequency?.combined_score || 0)
                : ((analysis.best_frequency?.['Puntaje Final'] || 0) / 200).toFixed(2);
            const isViable = isCross
                ? (analysis.best_combined_frequency?.is_viable || false)
                : (analysis.best_frequency?.['VÃ¡lido'] === 'SÃ­');
            const rankingJson = escapeAttr(JSON.stringify(ranking.slice(0, 20)));
            applyBtn = `<button type="button" class="btn-glass btn-sm-g"
                id="applyBtn-${ip.replace(/\./g, '-')}"
                onclick="openApplyModal('${escapeAttr(scanId)}','${escapeAttr(ip)}',${bestFreqMhz},${scoreNorm},${isViable},${freqMin},${freqMax},JSON.parse(this.dataset.ranking),${recBw})"
                data-ranking='${rankingJson}'><i class="bi bi-lightning-charge-fill"></i> Aplicar</button>`;
        }
    }

    const hasSpec = analysis.spectrum_data && (analysis.spectrum_data.ap || analysis.spectrum_data.sms);
    const specBtn = hasSpec
        ? `<button type="button" class="btn-glass btn-sm-g view-spectrum-btn" data-ip="${ip}"><i class="bi bi-graph-up"></i> Espectro</button>`
        : '';

    const smDetails = analysis.sm_details || [];
    const smIpsArr = analysis.sm_ips || [];
    let smSection = '';
    if (smDetails.length > 0 || smIpsArr.length > 0) {
        const chips = smDetails.length > 0
            ? smDetails.map(sm => `<div class="arc-sm-chip"><span class="chip-name">${escapeHtml(sm.site_name || sm.ip)}</span><span class="chip-ip">${escapeHtml(sm.ip)}</span></div>`).join('')
            : smIpsArr.map(i => `<div class="arc-sm-chip"><span class="chip-ip">${escapeHtml(i)}</span></div>`).join('');
        smSection = `<div class="arc-sm-list">
            <div class="arc-sm-title"><i class="bi bi-reception-4"></i> SMs registrados (${smDetails.length || smIpsArr.length})</div>
            <div class="arc-sm-chips">${chips}</div></div>`;
    }

    const freqDisplay = bestFreqMhz
        ? `<div class="arc-best-freq"><span class="arc-freq-value">${bestFreqMhz}</span><span class="arc-freq-unit">MHz</span>${bwMhz ? `<span class="arc-freq-bw">/ ${bwMhz} MHz BW</span>` : ''}</div>`
        : `<div class="scan-alert warning" style="margin:8px 0;"><i class="bi bi-exclamation-triangle"></i> Sin frecuencias vÃ¡lidas</div>`;

    return `
    <div class="ap-result-card">
        <div class="arc-header">
            <div class="arc-ip"><i class="bi bi-router"></i> ${escapeHtml(ip)}</div>
            <div class="arc-badges"><span class="quality-badge ${qualityClass}">${qualityLabel}</span></div>
        </div>
        <div class="arc-body">
            ${freqDisplay}
            <div class="arc-metrics">
                <div class="arc-metric"><div class="arc-metric-val">${metricScore}</div><div class="arc-metric-lbl">Score</div></div>
                <div class="arc-metric"><div class="arc-metric-val">${metricNoise}</div><div class="arc-metric-lbl">${isCross ? 'Ruido SMs' : 'SNR Est.'}</div></div>
                <div class="arc-metric"><div class="arc-metric-val">${metricPoints}</div><div class="arc-metric-lbl">Puntos RF</div></div>
            </div>
            ${smSection}
            <div class="arc-actions">${specBtn}${applyBtn}</div>
        </div>
    </div>`;
}

function renderErrorCard(ip, error) {
    return `<div class="ap-result-card" style="border-color:rgba(239,68,68,0.35);">
        <div class="arc-header"><div class="arc-ip" style="color:var(--accent-red);"><i class="bi bi-x-circle"></i> \</div></div>
        <div class="arc-body"><div class="scan-alert error">\</div></div>
    </div>`;
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
    '#FF5722', '#E91E63', '#9C27B0', '#673AB7', '#3F51B5', // CÃ¡lidos/Vibrantes
    '#00BCD4', '#009688', '#4CAF50', '#8BC34A', '#CDDC39'  // FrÃ­os/Natura
];

function openSpectrumViewer(ip) {
    console.log("Abriendo visualizador para", ip);

    if (!appState.currentScanId) {
        if (appState.scanResults && appState.scanResults.scan_id) {
            appState.currentScanId = appState.scanResults.scan_id;
        } else {
            showPanelAlert('scanAlert', 'No se puede identificar el ID del escaneo. Por favor inicie un nuevo escaneo.', 'warning');
            return;
        }
    }

    const url = `/spectrum/${appState.currentScanId}/${ip}`;
    window.open(url, '_blank', 'width=1200,height=800');
}

function openGlobalSpectrumViewer() {
    if (!appState.scanResults || !appState.scanResults.analysis_results) {
        showPanelAlert('scanAlert', 'No hay resultados de anÃ¡lisis disponibles.', 'warning');
        return;
    }

    const ips = Object.keys(appState.scanResults.analysis_results);
    if (ips.length === 0) {
        showPanelAlert('scanAlert', 'No se encontraron APs en los resultados.', 'warning');
        return;
    }

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
        'initializing': 'Inicializando...',
        'discovering': 'Descubriendo SMs...',
        'scanning': 'Escaneando espectro...',
        'analyzing': 'Analizando frecuencias...',
        'completed': 'âœ“ Completado',
        'failed': 'âœ— Error'
    };
    elements.statusBadge.textContent = map[status] || status;
    setStepperState(status);
}

/**
 * Advance the 5-step stepper UI to reflect the current scan status.
 * Steps: validating(0) â†’ discovering(1) â†’ scanning(2) â†’ analyzing(3) â†’ completed(4)
 */
function setStepperState(status) {
    const steps = ['validating', 'discovering', 'scanning', 'analyzing', 'completed'];
    const activeIdx = {
        'initializing': 0, 'discovering': 1, 'scanning': 2,
        'analyzing': 3, 'completed': 4, 'failed': 4
    }[status] ?? 0;

    steps.forEach((step, idx) => {
        const el = document.getElementById(`step-${step}`);
        if (!el) return;
        el.classList.remove('active', 'done');
        if (idx < activeIdx) el.classList.add('done');
        else if (idx === activeIdx) el.classList.add('active');
        // Connector line
        const line = el.nextElementSibling;
        if (line && line.classList.contains('step-line')) {
            if (idx < activeIdx) line.classList.add('done');
            else line.classList.remove('done');
        }
    });
}

function addLogEntry(msg, type = 'info', detailed = false) {
    if (!elements.logOutput) return;
    if (detailed && elements.detailedLogToggle && !elements.detailedLogToggle.checked) return;

    const line = document.createElement('div');
    line.className = `log-line ${type}`;
    line.innerHTML = `<span class="log-ts">[${new Date().toLocaleTimeString()}]</span><span class="log-msg">${msg}</span>`;
    elements.logOutput.appendChild(line);

    // Auto-scroll inteligente
    if (!appState.logUserScrolled) {
        elements.logOutput.scrollTop = elements.logOutput.scrollHeight;
    } else {
        appState.logNewLinesCount++;
        updateLogBadge(appState.logNewLinesCount);
    }
}

function resetInterface() {
    // Stop any active polling
    if (appState.pollInterval) {
        clearInterval(appState.pollInterval);
        appState.pollInterval = null;
    }
    // Reset all application state
    appState.currentScanId = null;
    appState.scanResults = null;
    appState.lastLogCount = 0;
    // Reset log scroll state
    appState.logUserScrolled = false;
    appState.logNewLinesCount = 0;
    updateLogBadge(0);

    // Hide results and status panels, show empty state
    if (elements.resultsPanel) elements.resultsPanel.style.display = 'none';
    if (elements.statusPanel) elements.statusPanel.style.display = 'none';
    if (elements.emptyState) elements.emptyState.style.display = 'flex';

    // Clear form fields
    clearForm();

    // Reset results content
    if (elements.resultsSummary) elements.resultsSummary.innerHTML = '';
    if (elements.frequencyRecommendations) elements.frequencyRecommendations.innerHTML = '';
    if (elements.installationSheetContent) elements.installationSheetContent.innerHTML = '';
}

function clearForm() {
    if (elements.apIPs) elements.apIPs.value = '';
    if (elements.ticketId) elements.ticketId.value = '';
    if (elements.startScanBtn) {
        elements.startScanBtn.disabled = true;
        elements.startScanBtn.innerHTML = '<i class="bi bi-play-circle-fill"></i> Iniciar AnÃ¡lisis';
    }
    if (elements.logOutput) elements.logOutput.innerHTML = '';
    // Reset discovery section
    if (elements.discoverySection) elements.discoverySection.style.display = 'none';
    if (elements.discoveryCards) elements.discoveryCards.innerHTML = '';
    appState.discoveryResult = null;
}

function parseIPList(text) {
    if (!text) return [];

    return text.split(/[\n,]+/)
        .map(t => t.split('#')[0].trim()) // Eliminar comentarios y espacios
        .filter(t => t.length > 0) // Quitar lÃ­neas vacÃ­as
        .filter(t =>
            // Regex bÃ¡sico de IP
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
            const d = scan.created_at ? new Date(scan.created_at).toLocaleString('es', {dateStyle:'short', timeStyle:'short'}) : 'N/A';
            const isClickable = scan.status === 'completed';
            const mode = scan.analysis_mode === 'AP_SM_CROSS' ? 'Cross' : 'AP';
            return `<div class="recent-item ${isClickable ? 'recent-scan-entry' : ''}" data-scan-id="${escapeAttr(scan.scan_id)}" style="${!isClickable ? 'opacity:0.5;cursor:default;' : ''}">
                <span class="ri-id">${scan.scan_id.substring(0, 8)}</span>
                <span class="ri-mode">${mode} Â· ${scan.ap_count || 0} APs</span>
                <span class="ri-date">${d}</span>
            </div>`;
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
                        if (elements.emptyState) elements.emptyState.style.display = 'none';
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

// ==================== SNMP DISCOVERY (ap-sm-autodiscovery) ====================

/**
 * Calls GET /api/discover to pre-scan SMs registered in each AP.
 * Renders discovery cards and enables the scan button on success.
 */
async function runDiscovery() {
    const apIPs = parseIPList(elements.apIPs ? elements.apIPs.value : '');
    if (apIPs.length === 0) {
        showScanAlert('IngresÃ¡ al menos una IP de AP antes de descubrir SMs.', 'warning');
        return;
    }
    if (elements.discoverBtn) {
        elements.discoverBtn.disabled = true;
        elements.discoverBtn.innerHTML = '<i class="bi bi-hourglass-split"></i> Descubriendo...';
    }
    if (elements.startScanBtn) elements.startScanBtn.disabled = true;
    showScanAlert('Consultando APs via SNMP linkTable...', 'info');

    try {
        const community = elements.snmpCommunity ? elements.snmpCommunity.value : '';
        const params = new URLSearchParams();
        apIPs.forEach(ip => params.append('ap_ips', ip));
        if (community) params.append('community', community);

        const res = await authFetch(`/api/discover?${params.toString()}`);
        if (!res) return;
        if (!res.ok) { const e = await res.json(); throw new Error(e.error || `HTTP ${res.status}`); }

        const data = await res.json(); // {ap_ip: [{luid, ip, mac, site_name}]}
        appState.discoveryResult = data;
        renderDiscoveryCards(data);

        const totalSMs = Object.values(data).reduce((acc, sms) => acc + sms.filter(sm => sm.ip).length, 0);
        if (totalSMs > 0) {
            showScanAlert(`Discovery completado â€” ${totalSMs} SM(s) en ${Object.keys(data).length} AP(s).`, 'success');
        } else {
            showScanAlert('Sin SMs activos â€” el anÃ¡lisis correrÃ¡ en modo AP-Only.', 'warning');
        }
        if (elements.startScanBtn) elements.startScanBtn.disabled = false;

    } catch (err) {
        console.error('[Discovery]', err);
        showScanAlert(`Error en discovery: ${err.message}`, 'error');
        if (elements.startScanBtn) elements.startScanBtn.disabled = false;
    } finally {
        if (elements.discoverBtn) {
            elements.discoverBtn.disabled = false;
            elements.discoverBtn.innerHTML = '<i class="bi bi-search"></i> Descubrir SMs';
        }
    }
}

function renderDiscoveryCards(data) {
    if (!elements.discoverySection || !elements.discoveryCards) return;
    const totalSMs = Object.values(data).reduce((acc, sms) => acc + sms.filter(sm => sm.ip).length, 0);
    if (elements.discoveryCount) elements.discoveryCount.textContent = totalSMs;
    elements.discoverySection.style.display = '';
    elements.discoveryCards.innerHTML = Object.entries(data).map(([apIp, sms]) => {
        const active = sms.filter(sm => sm.ip);
        const chips = active.length > 0
            ? active.map(sm => `<div class="sm-chip"><i class="bi bi-reception-4"></i>${escapeHtml(sm.site_name || sm.ip)}${sm.site_name ? `<span style="color:rgba(255,255,255,0.3)">(${escapeHtml(sm.ip)})</span>` : ''}</div>`).join('')
            : '<span style="color:var(--text-muted);font-size:0.73rem;">Sin SMs activos</span>';
        return `<div class="discovery-card${active.length === 0 ? ' error-card' : ''}">
            <div class="dc-header"><span class="dc-ap-ip"><i class="bi bi-router"></i> ${escapeHtml(apIp)}</span><span class="dc-sm-count">${active.length} SM${active.length !== 1 ? 's' : ''}</span></div>
            <div class="dc-sm-list">${chips}</div></div>`;
    }).join('');
}

/** Show a non-Bootstrap alert inside #scanAlert using the new .scan-alert CSS classes. */
function showScanAlert(msg, type = 'info') {
    const el = document.getElementById('scanAlert');
    if (!el) return;
    const icon = { success: 'check-circle', error: 'x-circle', warning: 'exclamation-triangle', info: 'info-circle' }[type] || 'info-circle';
    el.className = `scan-alert ${type}`;
    el.innerHTML = `<i class="bi bi-${icon}"></i> ${escapeHtml(msg)}`;
    el.style.display = '';
    clearTimeout(el._hideTimer);
    el._hideTimer = setTimeout(() => { el.style.display = 'none'; }, 6000);
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

// ==================== APPLY FREQUENCY MODAL (Tarea 4.2 + 4.3) ====================

/**
 * Estado interno del modal de aplicaciÃ³n de frecuencia.
 */
const _applyModal = {
    scanId: null,
    apIp: null,
    freqMhz: null,
    isViable: null,
    score: null,
    ranking: [],       // Top-20 frecuencias del anÃ¡lisis
    freqMin: 3400,
    freqMax: 6000,
    recommendedBw: 20, // Ancho de canal recomendado por el anÃ¡lisis
    submitting: false,
};

/**
 * Inyecta el modal de apply-frequency en el DOM si no existe todavÃ­a.
 */
function _ensureApplyModal() {
    if (document.getElementById('applyFreqModal')) return;

    const modal = document.createElement('div');
    modal.id = 'applyFreqModal';
    modal.style.cssText = 'display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,.7);overflow:auto;';
    modal.innerHTML = `
        <div style="margin:4% auto;max-width:520px;background:#1e1e2e;border:1px solid #444;border-radius:10px;padding:1.5rem;color:#e0e0e0;box-shadow:0 8px 32px #0008;">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
                <h5 style="margin:0;"><i class="bi bi-lightning-charge-fill" style="color:#ffc107;"></i> Aplicar Frecuencia Optima</h5>
                <button onclick="closeApplyModal()" style="background:none;border:none;color:#aaa;font-size:1.4rem;cursor:pointer;">&times;</button>
            </div>
            <div id="applyModalBadges" style="margin-bottom:1rem;display:flex;gap:.5rem;flex-wrap:wrap;"></div>

            <!-- Dropdown: Frecuencia del ranking -->
            <div style="margin-bottom:.75rem;">
                <label for="applySelectFreq" style="font-size:.85rem;color:#aaa;">
                    <i class="bi bi-broadcast" style="color:#ffc107;"></i>
                    Frecuencia (MHz) <span style="color:#555;font-size:.78rem;">Top-20 del anÃ¡lisis</span>
                </label>
                <select id="applySelectFreq"
                    onchange="_onApplyFreqChange(this.value)"
                    style="width:100%;padding:.4rem .7rem;background:#2a2a3e;border:1px solid #555;border-radius:6px;color:#fff;font-size:.95rem;">
                </select>
            </div>

            <!-- Dropdown: Ancho de canal -->
            <div style="margin-bottom:.75rem;">
                <label for="applySelectBw" style="font-size:.85rem;color:#aaa;">
                    <i class="bi bi-arrows-expand"></i>
                    Ancho de Canal <span style="color:#555;font-size:.78rem;">(se aplicarÃ¡ vÃ­a SNMP)</span>
                </label>
                <select id="applySelectBw"
                    style="width:100%;padding:.4rem .7rem;background:#2a2a3e;border:1px solid #555;border-radius:6px;color:#fff;font-size:.95rem;">
                    <option value="5">5 MHz</option>
                    <option value="10">10 MHz</option>
                    <option value="15">15 MHz</option>
                    <option value="20" selected>20 MHz</option>
                    <option value="30">30 MHz</option>
                    <option value="40">40 MHz</option>
                </select>
            </div>

            <!-- Info fija -->
            <div style="margin-bottom:.75rem;padding:.5rem .75rem;background:#16161f;border-radius:6px;font-size:.8rem;color:#888;">
                <i class="bi bi-info-circle"></i>
                Al aplicar tambiÃ©n se configurarÃ¡: <strong style="color:#aaa;">Contention Slots = 4</strong> &amp; <strong style="color:#aaa;">Broadcast Retry = 0</strong>
            </div>

            <!-- Tower ID -->
            <div style="margin-bottom:.75rem;">
                <label for="applyInputTower" style="font-size:.85rem;color:#aaa;">Tower ID <span style="color:#777;">(opcional)</span></label>
                <input type="text" id="applyInputTower" placeholder="Ej: TORRE-01"
                    style="width:100%;padding:.4rem .7rem;background:#2a2a3e;border:1px solid #555;border-radius:6px;color:#fff;font-size:.9rem;">
            </div>
            <div id="applyForceWrapper" style="display:none;margin-bottom:.75rem;">
                <label style="font-size:.85rem;cursor:pointer;">
                    <input type="checkbox" id="applyForceCheck" style="margin-right:.4rem;">
                    <span style="color:#f66;">Forzar apply (ignorar viabilidad)</span>
                    <small style="display:block;color:#888;margin-top:.2rem;">Solo disponible para administradores.</small>
                </label>
            </div>
            <div id="applyInfoBox" style="font-size:.82rem;color:#aaa;margin-bottom:1rem;line-height:1.5;"></div>
            <div id="applyResultArea" style="display:none;margin-bottom:1rem;padding:.75rem;border-radius:6px;font-size:.87rem;"></div>
            <div style="display:flex;justify-content:flex-end;gap:.5rem;">
                <button onclick="closeApplyModal()"
                    style="padding:.4rem 1rem;background:#444;border:none;border-radius:6px;color:#ccc;cursor:pointer;">
                    Cerrar
                </button>
                <button id="applySubmitBtn" onclick="submitApplyFrequency()"
                    style="padding:.4rem 1.2rem;background:#ffc107;border:none;border-radius:6px;color:#000;font-weight:600;cursor:pointer;">
                    <i class="bi bi-lightning-charge-fill"></i> Aplicar
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);
    modal.addEventListener('click', (e) => { if (e.target === modal) closeApplyModal(); });
}

/**
 * Tarea 4.2: Abre el modal de apply-frequency pre-llenado.
 * ranking: array de objetos del combined_ranking (top-20)
 * recommendedBw: ancho de canal recomendado en MHz
 */
function openApplyModal(scanId, apIp, freqMhz, score, isViable, freqMin, freqMax, ranking, recommendedBw) {
    _ensureApplyModal();
    _applyModal.scanId = scanId;
    _applyModal.apIp = apIp;
    _applyModal.freqMhz = freqMhz;
    _applyModal.isViable = isViable;
    _applyModal.score = score;
    _applyModal.submitting = false;
    _applyModal.ranking = ranking || [];
    _applyModal.freqMin = freqMin || 3400;
    _applyModal.freqMax = freqMax || 6000;
    _applyModal.recommendedBw = recommendedBw || 20;

    // â”€â”€ Poblar dropdown de frecuencias (top-20 del ranking) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const freqSelect = document.getElementById('applySelectFreq');
    freqSelect.innerHTML = '';
    const top20 = _applyModal.ranking.slice(0, 20);
    if (top20.length === 0) {
        // Fallback: solo la frecuencia recomendada
        const opt = document.createElement('option');
        opt.value = freqMhz;
        opt.textContent = `${freqMhz} MHz â€” Recomendada`;
        freqSelect.appendChild(opt);
    } else {
        top20.forEach((item, idx) => {
            const fMhz = item.frequency ?? item['Frecuencia (MHz)'] ?? item['Frecuencia Central (MHz)'];
            const score = item.combined_score ?? item['Score Final'] ?? item['Puntaje Final'] ?? item.score ?? '';
            const bw = item.channel_width ?? item['Ancho (MHz)'] ?? item['Ancho Banda (MHz)'] ?? '';

            const isRec = fMhz == freqMhz;
            const label = isRec
                ? `â˜… ${fMhz} MHz â€” ${bw ? bw + 'MHz BW | ' : ''}Score: ${score} (Recomendada)`
                : `${String(idx + 1).padStart(2, '0')}. ${fMhz} MHz â€” ${bw ? bw + 'MHz BW | ' : ''}Score: ${score}`;
            const opt = document.createElement('option');
            opt.value = fMhz;
            if (isRec) opt.selected = true;
            opt.textContent = label;
            freqSelect.appendChild(opt);
        });
    }

    // â”€â”€ Pre-seleccionar ancho de canal recomendado â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const bwSelect = document.getElementById('applySelectBw');
    const validBws = [5, 10, 15, 20, 30, 40];
    const bwToSelect = validBws.includes(Number(_applyModal.recommendedBw)) ? _applyModal.recommendedBw : 20;
    Array.from(bwSelect.options).forEach(o => { o.selected = Number(o.value) === Number(bwToSelect); });

    document.getElementById('applyInputTower').value = '';
    const forceCheck = document.getElementById('applyForceCheck');
    if (forceCheck) forceCheck.checked = false;

    const viableColor = isViable ? '#198754' : '#dc3545';
    const viableLabel = isViable ? 'VIABLE' : 'NO VIABLE';
    document.getElementById('applyModalBadges').innerHTML = `
        <span style="padding:.2rem .6rem;background:#2a2a3e;border:1px solid #555;border-radius:4px;font-size:.78rem;">
            <i class="bi bi-router"></i> ${escapeHtml(apIp)}
        </span>
        <span style="padding:.2rem .6rem;background:${viableColor}22;border:1px solid ${viableColor};border-radius:4px;font-size:.78rem;color:${viableColor};">
            ${viableLabel}
        </span>
        <span style="padding:.2rem .6rem;background:#2a2a3e;border:1px solid #555;border-radius:4px;font-size:.78rem;">
            Score: <strong>${Number(score).toFixed(2)}</strong>
        </span>`;

    _onApplyFreqChange(freqMhz);

    const forceWrapper = document.getElementById('applyForceWrapper');
    if (forceWrapper) forceWrapper.style.display = (window.userRole === 'admin') ? '' : 'none';

    const resultArea = document.getElementById('applyResultArea');
    resultArea.style.display = 'none';
    resultArea.textContent = '';

    const submitBtn = document.getElementById('applySubmitBtn');
    if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> Aplicar'; }

    document.getElementById('applyFreqModal').style.display = 'block';
}

/**
 * Actualiza el infoBox cuando el operador cambia la frecuencia seleccionada.
 */
function _onApplyFreqChange(freqVal) {
    const fMhz = parseFloat(freqVal);
    const isViable = _applyModal.isViable;
    document.getElementById('applyInfoBox').innerHTML = isViable
        ? `<i class="bi bi-check-circle-fill" style="color:#198754;"></i> Frecuencia <strong>${fMhz} MHz</strong> viable. Se aplicara primero a los SMs y luego al AP.`
        : `<i class="bi bi-exclamation-triangle-fill" style="color:#dc3545;"></i> Frecuencia <strong>${fMhz} MHz</strong> <strong>no viable</strong>. Requiere forzar (solo admin).`;
}

function closeApplyModal() {
    const modal = document.getElementById('applyFreqModal');
    if (modal) modal.style.display = 'none';
    _applyModal.submitting = false;
}

/**
 * Tarea 4.3: EnvÃ­a POST /api/apply-frequency y muestra resultado inline.
 */
async function submitApplyFrequency() {
    if (_applyModal.submitting) return;

    // Leer frecuencia del dropdown
    const freqMhz = parseFloat(document.getElementById('applySelectFreq').value);
    if (!freqMhz || isNaN(freqMhz)) {
        showApplyResult('danger', 'Selecciona una frecuencia valida.');
        return;
    }

    // Leer ancho de canal del dropdown
    const channelWidthMhz = parseInt(document.getElementById('applySelectBw').value, 10);

    // tower_id es opcional â€” si el usuario no lo llena se envÃ­a null
    const towerId = (document.getElementById('applyInputTower').value || '').trim() || null;
    const forceCheck = document.getElementById('applyForceCheck');
    const force = !!(forceCheck && forceCheck.checked);

    _applyModal.submitting = true;
    const submitBtn = document.getElementById('applySubmitBtn');
    if (submitBtn) { submitBtn.disabled = true; submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span> Aplicando...'; }
    showApplyResult('info', '<span class="spinner-border spinner-border-sm me-2"></span> Enviando comandos SNMP...');

    try {
        const res = await authFetch('/api/apply-frequency', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scan_id: _applyModal.scanId,
                freq_mhz: freqMhz,
                channel_width_mhz: channelWidthMhz,
                tower_id: towerId,
                force,
            }),
        });
        if (!res) { _applyModal.submitting = false; return; }

        const data = await res.json();
        if (!res.ok) {
            showApplyResult('danger', `<i class="bi bi-x-circle-fill"></i> <strong>Error:</strong> ${escapeHtml(data.error || data.message || 'Error HTTP ' + res.status)}`);
        } else {
            const state = data.state || 'unknown';
            const applyId = data.apply_id;
            const freqResult = data.freq_khz ? (data.freq_khz / 1000).toFixed(1) : freqMhz;
            const bwResult = data.channel_width_mhz ? ` | BW ${data.channel_width_mhz} MHz` : '';
            const smErrors = (data.errors || []).filter(e => e.startsWith('SM'));
            const apError = (data.errors || []).find(e => e.startsWith('AP'));
            const extraOk = [
                data.contention_slots_ok !== false ? 'âœ“ CS=4' : 'âš  CS',
                data.broadcast_retry_ok !== false ? 'âœ“ BR=0' : 'âš  BR',
                data.reboot_ok !== false ? 'âœ“ Reboot' : 'âš  Reboot',
            ].join(' &nbsp;');

            if (state === 'completed') {
                let detail = `Frecuencia <strong>${freqResult} MHz${bwResult}</strong> aplicada correctamente`;
                if (smErrors.length > 0) detail += `<br><small style="color:#ffc107;"><i class="bi bi-exclamation-triangle"></i> ${smErrors.length} SM(s) con errores â€” AP OK</small>`;
                detail += `<br><small style="color:#aaa;">${extraOk}</small>`;
                if (data.reboot_ok !== false) {
                    detail += `<br><small style="color:#6edff6;"><i class="bi bi-arrow-clockwise"></i> El equipo estÃ¡ reiniciando (~30-60 s de inactividad)</small>`;
                }
                showApplyResult('success', `<i class="bi bi-check-circle-fill"></i> <strong>Completado</strong> (apply_id=${applyId})<br>${detail}`);
            } else {
                const errMsg = apError || (data.errors || []).join('; ') || 'Error desconocido';
                showApplyResult('danger', `<i class="bi bi-x-circle-fill"></i> <strong>Fallido</strong> (apply_id=${applyId}, state=${state})<br><small>${escapeHtml(errMsg)}</small>`);
            }
        }
    } catch (err) {
        showApplyResult('danger', `<i class="bi bi-x-circle-fill"></i> Error de red: ${escapeHtml(err.message)}`);
    } finally {
        _applyModal.submitting = false;
        if (submitBtn) { submitBtn.disabled = false; submitBtn.innerHTML = '<i class="bi bi-lightning-charge-fill"></i> Aplicar'; }
    }
}

function showApplyResult(type, html) {
    const area = document.getElementById('applyResultArea');
    if (!area) return;
    const c = { success: { bg:'#0f3d1f', border:'#198754', color:'#75b798' }, danger: { bg:'#3d0f0f', border:'#dc3545', color:'#ea868f' }, info: { bg:'#0d2137', border:'#0dcaf0', color:'#6edff6' }, warning: { bg:'#3d2e00', border:'#ffc107', color:'#ffda6a' } }[type] || { bg:'#0d2137', border:'#0dcaf0', color:'#6edff6' };
    area.style.cssText = `display:block;padding:.75rem;border-radius:6px;font-size:.87rem;background:${c.bg};border:1px solid ${c.border};color:${c.color};`;
    area.innerHTML = html;
}

