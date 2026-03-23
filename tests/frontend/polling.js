/**
 * polling.js — Módulo de lógica de polling extraído de app.js
 *
 * Este módulo expone la lógica pura de adaptive polling para que pueda
 * ser testeada en Node.js sin DOM ni browser. Es un espejo fiel de la
 * implementación en static/js/app.js.
 *
 * IMPORTANTE: Cualquier cambio en las funciones de app.js DEBE reflejarse aquí.
 */

'use strict';

/**
 * Crea una instancia aislada del sistema de polling adaptativo.
 * Usa inyección de dependencias para que los tests puedan mockear
 * setTimeout/clearTimeout, fetch, y los callbacks de UI.
 *
 * @param {object} deps - Dependencias inyectables
 * @param {Function} deps.setTimeout  - Función de scheduling (default: global.setTimeout)
 * @param {Function} deps.clearTimeout - Función de cancelación (default: global.clearTimeout)
 * @param {Function} deps.fetchStatus  - Async fn(scanId) → { status, progress, logs?, error?, results? }
 * @param {Function} deps.onProgress   - Callback(progress) cuando llega un update
 * @param {Function} deps.onStatusChange - Callback(status) cuando cambia el estado
 * @param {Function} deps.onLogEntry   - Callback(msg, type) para nuevos logs
 * @param {Function} deps.onCompleted  - Callback(results) cuando el scan finaliza
 * @param {Function} deps.onFailed     - Callback(error) cuando el scan falla
 */
function createPollingSystem(deps = {}) {
    const _setTimeout   = deps.setTimeout   || global.setTimeout;
    const _clearTimeout = deps.clearTimeout || global.clearTimeout;
    const fetchStatus    = deps.fetchStatus  || (() => Promise.reject(new Error('fetchStatus not configured')));
    const onProgress     = deps.onProgress     || (() => {});
    const onStatusChange = deps.onStatusChange || (() => {});
    const onLogEntry     = deps.onLogEntry     || (() => {});
    const onCompleted    = deps.onCompleted    || (() => {});
    const onFailed       = deps.onFailed       || (() => {});

    // Estado interno (espejo de appState en app.js)
    const state = {
        currentScanId: null,
        pollTimeout: null,
        lastLogCount: 0,
        lastProgress: 0,
    };

    /**
     * Detiene el ciclo de polling cancelando cualquier timeout pendiente.
     * Spec: Requirement "Terminación del Polling" y "Limpieza de Estado".
     */
    function stopPolling() {
        if (state.pollTimeout) {
            _clearTimeout(state.pollTimeout);
            state.pollTimeout = null;
        }
    }

    /**
     * Retorna el delay en ms según el progreso actual.
     * Spec: Requirement "Adaptive Poll Interval"
     *   - progress < 40  → 2000ms
     *   - progress >= 40 → 5000ms
     */
    function getDelay(progress) {
        return progress >= 40 ? 5000 : 2000;
    }

    /**
     * Programa el próximo poll con delay adaptativo según el progreso.
     */
    function scheduleNextPoll(progress) {
        const delay = getDelay(progress);
        state.pollTimeout = _setTimeout(pollOnce, delay);
        return delay; // expuesto para tests
    }

    /**
     * Arranca el ciclo de polling. Cancela cualquier ciclo previo.
     * Spec: Requirement "Limpieza de Estado al Reiniciar" (nuevo scan)
     */
    function startPolling(scanId) {
        if (scanId !== undefined) state.currentScanId = scanId;
        stopPolling();
        return scheduleNextPoll(0); // Primera consulta siempre rápida (2s)
    }

    /**
     * Ejecuta una sola consulta y reprograma el siguiente poll.
     * Spec: Requirement "Adaptive Poll Interval", "Terminación", "Tolerancia errores"
     */
    async function pollOnce() {
        if (!state.currentScanId) return;

        try {
            const status = await fetchStatus(state.currentScanId);

            state.lastProgress = status.progress || 0;
            onProgress(status.progress);
            onStatusChange(status.status);

            // Procesar nuevos logs
            if (status.logs && Array.isArray(status.logs)) {
                if (status.logs.length > state.lastLogCount) {
                    const newLogs = status.logs.slice(state.lastLogCount);
                    newLogs.forEach(log => onLogEntry(log.msg, log.type || 'info'));
                    state.lastLogCount = status.logs.length;
                }
            }

            if (status.status === 'completed') {
                stopPolling();
                onCompleted(status.results);
            } else if (status.status === 'failed') {
                stopPolling();
                onFailed(status.error);
            } else {
                // Scan en curso: reprogramar con delay adaptativo
                scheduleNextPoll(status.progress || 0);
            }
        } catch (e) {
            // Error de red transitorio: retomar ciclo con último progreso conocido
            scheduleNextPoll(state.lastProgress);
        }
    }

    return {
        state,          // expuesto para inspección en tests
        stopPolling,
        startPolling,
        scheduleNextPoll,
        pollOnce,
        getDelay,
    };
}

module.exports = { createPollingSystem };
