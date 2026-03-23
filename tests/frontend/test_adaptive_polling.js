/**
 * test_adaptive_polling.js
 *
 * Tests unitarios para el sistema de adaptive polling.
 * Cubre todos los scenarios de la spec: sdd/adaptive-polling/spec
 *
 * Corre con: node tests/frontend/test_adaptive_polling.js
 * Requiere: Node.js >= 18 (usa node:test built-in)
 */

'use strict';

const { test, describe, beforeEach } = require('node:test');
const assert = require('node:assert/strict');
const { createPollingSystem } = require('./polling.js');

// ─── Helpers de mock ──────────────────────────────────────────────────────────

/**
 * Crea un mock de setTimeout/clearTimeout que captura los callbacks
 * sin ejecutarlos inmediatamente — permite control total del tiempo en tests.
 */
function createTimerMock() {
    let nextId = 1;
    const pending = new Map(); // id → { fn, delay }

    return {
        scheduled: pending,

        setTimeout(fn, delay) {
            const id = nextId++;
            pending.set(id, { fn, delay });
            return id;
        },

        clearTimeout(id) {
            pending.delete(id);
        },

        /**
         * Avanza el tiempo: ejecuta todos los timers pendientes
         * con delay <= maxDelay y los elimina de la cola.
         */
        flush(maxDelay = Infinity) {
            const toRun = [...pending.entries()]
                .filter(([, { delay }]) => delay <= maxDelay);
            toRun.forEach(([id, { fn }]) => {
                pending.delete(id);
                fn();
            });
        },

        /** Ejecuta UN timer específico por su ID */
        run(id) {
            const timer = pending.get(id);
            if (timer) {
                pending.delete(id);
                timer.fn();
            }
        },

        /** Retorna el delay del timer más reciente */
        lastDelay() {
            if (pending.size === 0) return null;
            const last = [...pending.values()].at(-1);
            return last.delay;
        },

        clear() { pending.clear(); }
    };
}

/**
 * Crea una respuesta de status simulada.
 */
function makeStatus(status, progress, extra = {}) {
    return { status, progress, ...extra };
}

// ─── REQUIREMENT: Adaptive Poll Interval ─────────────────────────────────────

describe('REQ: Adaptive Poll Interval', () => {

    test('SCENARIO: Poll rápido en fase temprana — progress < 40 usa delay 2000ms', (t) => {
        const timers = createTimerMock();
        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
        });

        // GIVEN un scan iniciado
        // WHEN se arranca el polling (progress implícito = 0)
        polling.startPolling('scan-001');

        // THEN el próximo poll debe programarse con 2000ms
        assert.equal(timers.lastDelay(), 2000, 'Delay inicial debe ser 2000ms');
    });

    test('SCENARIO: Poll lento en fase tardía — progress >= 40 usa delay 5000ms', () => {
        const polling = createPollingSystem({});
        // WHEN el progreso es >= 40
        // THEN getDelay debe retornar 5000
        assert.equal(polling.getDelay(40),  5000, 'progress=40 debe dar 5000ms');
        assert.equal(polling.getDelay(50),  5000, 'progress=50 debe dar 5000ms');
        assert.equal(polling.getDelay(99),  5000, 'progress=99 debe dar 5000ms');
        assert.equal(polling.getDelay(100), 5000, 'progress=100 debe dar 5000ms');
    });

    test('SCENARIO: Transición de intervalo — el delay se recalcula tras cada respuesta', async (t) => {
        const timers = createTimerMock();
        const responses = [
            makeStatus('scanning', 30),  // primera respuesta: progress 30 → 2000ms
            makeStatus('scanning', 45),  // segunda: progress 45 → 5000ms
        ];
        let callCount = 0;

        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
            fetchStatus: async () => responses[callCount++],
        });

        polling.startPolling('scan-001');

        // Primera consulta (progress=30)
        await polling.pollOnce();
        assert.equal(timers.lastDelay(), 2000, 'Tras progress=30, próximo delay debe ser 2000ms');

        // Segunda consulta (progress=45)
        await polling.pollOnce();
        assert.equal(timers.lastDelay(), 5000, 'Tras progress=45, próximo delay debe ser 5000ms');
    });

    test('BOUNDARY: progress=39 usa 2000ms, progress=40 usa 5000ms', () => {
        const polling = createPollingSystem({});
        assert.equal(polling.getDelay(39), 2000, 'progress=39 → 2000ms');
        assert.equal(polling.getDelay(40), 5000, 'progress=40 → 5000ms');
    });

});

// ─── REQUIREMENT: Terminación del Polling ────────────────────────────────────

describe('REQ: Terminación del Polling', () => {

    test('SCENARIO: Scan completado — polling se detiene y onCompleted se llama', async () => {
        const timers = createTimerMock();
        const completedResults = { analysis_results: { '10.0.0.1': {} } };
        let completedWith = null;

        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
            fetchStatus: async () => makeStatus('completed', 100, { results: completedResults }),
            onCompleted: (results) => { completedWith = results; },
        });

        polling.startPolling('scan-001');
        await polling.pollOnce();

        // THEN el polling se detiene (no hay timers pendientes)
        assert.equal(timers.scheduled.size, 0, 'No deben quedar timers pendientes');
        assert.equal(polling.state.pollTimeout, null, 'pollTimeout debe ser null');
        // AND onCompleted se llama con los resultados
        assert.deepEqual(completedWith, completedResults, 'onCompleted debe recibir los resultados');
    });

    test('SCENARIO: Scan fallido — polling se detiene y onFailed se llama', async () => {
        const timers = createTimerMock();
        let failedWith = null;

        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
            fetchStatus: async () => makeStatus('failed', 30, { error: 'SNMP timeout' }),
            onFailed: (err) => { failedWith = err; },
        });

        polling.startPolling('scan-001');
        await polling.pollOnce();

        assert.equal(timers.scheduled.size, 0, 'No deben quedar timers tras fallo');
        assert.equal(polling.state.pollTimeout, null, 'pollTimeout debe ser null');
        assert.equal(failedWith, 'SNMP timeout', 'onFailed debe recibir el mensaje de error');
    });

});

// ─── REQUIREMENT: Limpieza de Estado al Reiniciar ────────────────────────────

describe('REQ: Limpieza de Estado al Reiniciar', () => {

    test('SCENARIO: stopPolling() cancela el timeout activo', () => {
        const timers = createTimerMock();
        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
        });

        // GIVEN un polling activo
        polling.startPolling('scan-001');
        assert.equal(timers.scheduled.size, 1, 'Debe haber 1 timer activo');

        // WHEN se llama stopPolling
        polling.stopPolling();

        // THEN el timer se cancela y el handle se limpia
        assert.equal(timers.scheduled.size, 0, 'El timer debe cancelarse');
        assert.equal(polling.state.pollTimeout, null, 'pollTimeout debe ser null tras stop');
    });

    test('SCENARIO: Inicio de nuevo scan cancela el ciclo anterior', () => {
        const timers = createTimerMock();
        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
        });

        // GIVEN un primer scan activo
        polling.startPolling('scan-001');
        assert.equal(timers.scheduled.size, 1, 'Primer scan: 1 timer activo');

        // WHEN se inicia un segundo scan
        polling.startPolling('scan-002');

        // THEN el timer anterior se cancela y hay solo 1 nuevo timer
        assert.equal(timers.scheduled.size, 1, 'Solo debe quedar 1 timer (el nuevo)');
        assert.equal(polling.state.currentScanId, 'scan-002', 'El scan ID debe actualizarse');
    });

    test('SCENARIO: stopPolling() es idempotente — llamarlo dos veces no falla', () => {
        const timers = createTimerMock();
        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
        });

        polling.startPolling('scan-001');
        assert.doesNotThrow(() => {
            polling.stopPolling();
            polling.stopPolling();
        }, 'Llamar stopPolling dos veces no debe lanzar error');
        assert.equal(polling.state.pollTimeout, null);
    });

});

// ─── REQUIREMENT: Tolerancia ante errores de red ─────────────────────────────

describe('REQ: Tolerancia ante errores de red', () => {

    test('SCENARIO: Error de red — el ciclo se retoma con el último progreso conocido', async () => {
        const timers = createTimerMock();
        let callCount = 0;

        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
            fetchStatus: async () => {
                callCount++;
                if (callCount === 1) {
                    // Primera llamada: progreso en 50% (en zona lenta)
                    return makeStatus('scanning', 50);
                }
                // Segunda llamada: error de red
                throw new Error('Network timeout');
            },
        });

        polling.startPolling('scan-001');

        // Primera pollOnce exitosa (progress=50 → delay=5000)
        await polling.pollOnce();
        assert.equal(polling.state.lastProgress, 50, 'lastProgress debe ser 50 tras primera llamada');

        // Limpiamos los timers acumulados (start + primer reprograma) para medir solo el de la segunda pollOnce
        timers.clear();

        // Segunda pollOnce con error de red
        await polling.pollOnce();

        // THEN el ciclo se retoma con el delay correspondiente al lastProgress (50 → 5000ms)
        assert.equal(timers.lastDelay(), 5000, 'Tras error, debe reprogramar con delay del lastProgress=50');
        assert.equal(timers.scheduled.size, 1, 'Debe haber 1 timer pendiente (ciclo continúa)');
    });

    test('SCENARIO: Error de red con progress=0 — reprograma con 2000ms (zona rápida)', async () => {
        const timers = createTimerMock();

        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
            fetchStatus: async () => { throw new Error('Network error'); },
        });

        polling.startPolling('scan-001');
        await polling.pollOnce(); // Falla inmediatamente

        assert.equal(timers.lastDelay(), 2000, 'Con lastProgress=0, debe usar delay 2000ms');
    });

});

// ─── REQUIREMENT: Mecanismo (setTimeout recursivo) ───────────────────────────

describe('REQ: Mecanismo setTimeout recursivo (no setInterval)', () => {

    test('startPolling usa setTimeout, NO setInterval', () => {
        let setIntervalCalled = false;
        const timers = createTimerMock();

        // Sobreescribimos setInterval para detectar si se usa
        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
        });

        polling.startPolling('scan-001');

        assert.equal(setIntervalCalled, false, 'setInterval NO debe llamarse');
        assert.equal(timers.scheduled.size, 1, 'setTimeout SÍ debe llamarse una vez');
    });

    test('pollOnce programa exactamente 1 timeout tras una respuesta en curso', async () => {
        const timers = createTimerMock();

        const polling = createPollingSystem({
            setTimeout:   timers.setTimeout.bind(timers),
            clearTimeout: timers.clearTimeout.bind(timers),
            fetchStatus: async () => makeStatus('scanning', 10),
        });

        polling.startPolling('scan-001');
        timers.clear(); // limpiar el timer del start

        await polling.pollOnce();

        assert.equal(timers.scheduled.size, 1, 'Exactamente 1 timeout debe programarse tras respuesta en curso');
    });

});

console.log('✅ Tests de adaptive polling cargados. Ejecutando...\n');
