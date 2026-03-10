# Analizador de Frecuencias PMP450i — Tower Scan Automation

**Versión:** 3.0.0 · **Python:** 3.13 · **Framework:** Flask 3.0

Plataforma web de automatización de escaneo y análisis de espectro RF para equipos de radioenlace **Cambium Networks PMP450i**. Controla los radios vía SNMP para iniciar escaneos de espectro sincronizados, descarga los datos XML de cada equipo y aplica un motor de scoring matemático para recomendar la frecuencia central óptima con mínima interferencia.

---

## Tabla de Contenidos

1. [Características](#características)
2. [Arquitectura del Sistema](#arquitectura-del-sistema)
3. [Stack Tecnológico](#stack-tecnológico)
4. [Instalación y Puesta en Marcha](#instalación-y-puesta-en-marcha)
5. [Configuración (.env)](#configuración-env)
6. [API REST](#api-rest)
7. [Motor Matemático de Análisis de Frecuencias](#motor-matemático-de-análisis-de-frecuencias)
8. [Análisis Cruzado AP-SM](#análisis-cruzado-ap-sm)
9. [Proceso de Escaneo SNMP](#proceso-de-escaneo-snmp)
10. [Base de Datos](#base-de-datos)
11. [Autenticación y Roles](#autenticación-y-roles)
12. [Tests](#tests)
13. [Despliegue con Docker](#despliegue-con-docker)
14. [Estructura del Proyecto](#estructura-del-proyecto)

---

## Características

| Categoría | Funcionalidad |
|-----------|---------------|
| **Escaneo RF** | Control SNMP de APs y SMs Cambium PMP450i para iniciar análisis de espectro sincronizado |
| **Análisis** | Motor de scoring con ventana deslizante, estimación de SNR, modulación y throughput teórico |
| **Análisis Cruzado** | Validación multibanda AP+SM simultánea (20/15/10/5 MHz) con sistema de veto por interferencia |
| **Burst Noise** | Detección de interferencia intermitente (diferencia Max-Mean > 10 dB) |
| **Integración cnMaestro** | Importación de inventario de red desde la API de Cambium cnMaestro |
| **Gestión de Torres** | CRUD completo de torres con validación de ID (`BAJ02-RTD-ENSE-003`) |
| **Historial de Escaneos** | Persistencia completa de resultados en SQLite |
| **Verificación de Configuración** | Registro de frecuencia recomendada vs. aplicada por el operador |
| **Auditoría** | Log de todas las acciones con ticket de mesa de ayuda obligatorio para escaneos |
| **RBAC** | Roles admin/operator, reset de contraseñas, gestión de usuarios |
| **UI Web** | SPA en vanilla JS con paneles de escaneo, torres, usuarios, historial y verificación |
| **Docker** | Despliegue containerizado con datos persistentes en volumen |

---

## Arquitectura del Sistema

```
┌─────────────────────────────────────────────────────────────┐
│                        UI Web (SPA)                         │
│          HTML + Vanilla JS + Bootstrap (dark theme)         │
└────────────────────────────┬────────────────────────────────┘
                             │ HTTP REST
┌────────────────────────────▼────────────────────────────────┐
│                     Flask App (Blueprints)                  │
│  auth_routes │ scan_routes │ tower_routes │ user_routes     │
│  spectrum_routes │ audit_routes │ config_routes             │
└──────┬──────────────┬──────────────┬──────────────┬─────────┘
       │              │              │              │
┌──────▼──────┐ ┌─────▼──────┐ ┌────▼─────┐ ┌─────▼─────────┐
│TowerScanner │ │Frequency   │ │APSMCross │ │DatabaseManager│
│  (SNMP)     │ │Analyzer    │ │Analyzer  │ │  (SQLite WAL) │
└──────┬──────┘ └─────┬──────┘ └────┬─────┘ └───────────────┘
       │ SNMP          │ pandas/numpy │ scoring           data/
       │               │              │                analyzer.db
┌──────▼──────┐        └──────────────┘
│Cambium PMP  │
│450i Radios  │
│ AP / SM     │
└─────────────┘
```

### Pipeline de un Escaneo Completo

```
1. VALIDATE  →  SNMP GET sysName en todos los dispositivos
2. SCAN      →  SNMP SET para iniciar análisis de espectro (40s AP, 60s SM)
3. POLL      →  SNMP GET de estado cada 10–15s hasta IDLE (estado 4)
4. DOWNLOAD  →  HTTP GET /SpectrumAnalysis.xml por cada equipo
5. ANALYZE   →  FrequencyAnalyzer (ventana deslizante) + APSMCrossAnalyzer (veto SM)
6. STORE     →  Resultados en analyzer.db (tabla scans)
7. AUDIT     →  Registro en audit_logs con ticket de mesa de ayuda
```

---

## Stack Tecnológico

| Capa | Tecnología |
|------|-----------|
| Backend | Python 3.13, Flask 3.0, Gunicorn |
| Análisis numérico | pandas 2.1, NumPy 1.26 |
| Protocolo RF | pysnmp 4.4 (SNMP v1/v2c) |
| HTTP | requests 2.31 |
| Parsing XML | xmltodict 0.13 |
| Base de datos | SQLite 3 (stdlib, WAL mode, FK) |
| Auth | werkzeug.security (bcrypt), flask.session |
| Config | python-dotenv 1.0 |
| Frontend | HTML5, CSS3, Vanilla JS (ES6+), Bootstrap |
| Contenedor | Docker, Docker Compose |
| Tests | pytest 9.0 (455 tests) |

---

## Instalación y Puesta en Marcha

### Requisitos previos

- Python 3.13+
- pip
- Docker + Docker Compose (para despliegue en producción)
- Acceso SNMP (comunidad de lectura/escritura) a los equipos Cambium PMP450i

### Desarrollo local

```bash
# Clonar el repositorio
git clone <repo-url>
cd "ANALIZADOR DE FRECUENCIAS PMP450I V2"

# Crear entorno virtual e instalar dependencias
python -m venv venv
source venv/bin/activate       # Linux/macOS
venv\Scripts\activate          # Windows
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores reales

# Ejecutar en modo desarrollo
flask --app app.web_app run --debug

# La app queda disponible en http://localhost:5000
# Credenciales por defecto: admin / admin (se fuerza cambio en primer login)
```

### Ejecutar tests

```bash
py -3.13 -m pytest -v          # Windows
python -m pytest -v            # Linux/macOS
```

Resultado esperado: **455 passed**.

---

## Configuración (.env)

Copiar `.env.example` a `.env` y ajustar:

```dotenv
# Flask
SECRET_KEY=un-secreto-muy-largo-y-aleatorio
FLASK_ENV=production
FLASK_DEBUG=0

# Red
HOST=0.0.0.0
PORT=5000

# Parámetros de escaneo (defaults para la UI)
SNMP_COMMUNITIES=Canopy              # Separadas por coma; se prueban en orden
DEFAULT_TARGET_RX_LEVEL=-52         # dBm — nivel objetivo de recepción
DEFAULT_MIN_SNR=32                   # dB  — SNR mínimo aceptable
DEFAULT_MAX_POLARIZATION_DIFF=5      # dB  — desequilibrio máximo V/H
DEFAULT_CHANNEL_WIDTH=20             # MHz — 5 | 10 | 15 | 20 | 30 | 40

# Base de datos unificada
DB_PATH=/app/data/analyzer.db       # montado como volumen en Docker

# cnMaestro API (opcional)
CNMAESTRO_URL=https://your-cnmaestro-host/api/v1
CNMAESTRO_ID=your-client-id
CNMAESTRO_SECRET=your-client-secret
```

---

## API REST

Todas las rutas (excepto `/login` y `/api/health`) requieren sesión autenticada.

### Autenticación

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/login` | Login (form: `username`, `password`) |
| `POST` | `/logout` | Cerrar sesión |
| `POST` | `/change-password` | Cambiar contraseña propia |

### Escaneo

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/scan` | Iniciar escaneo (requiere `ticket_id`) |
| `GET` | `/api/status/<scan_id>` | Estado en tiempo real del escaneo |
| `GET` | `/api/results/<scan_id>` | Resultados completos del análisis |
| `GET` | `/api/scans` | Historial de todos los escaneos |
| `GET` | `/api/config` | Valores por defecto de configuración |
| `GET` | `/api/health` | Health check (sin auth) |

**Body de `/api/scan`:**
```json
{
  "ap_ips": ["10.0.0.1", "10.0.0.2"],
  "sm_ips": ["10.0.1.1"],
  "snmp_community": "Canopy",
  "ticket_id": 12345,
  "config": {
    "target_rx_level": -52,
    "channel_width": 20
  }
}
```

### Torres

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/towers` | Listar torres |
| `POST` | `/api/towers` | Crear torre |
| `PUT` | `/api/towers/<tower_id>` | Actualizar torre |
| `DELETE` | `/api/towers/<tower_id>` | Eliminar torre (admin) |

**Formato de tower_id:** `^[A-Z0-9]{2,5}-[A-Z]{2,5}-[A-Z]{2,5}-\d{3}$`  
Ejemplo válido: `BAJ02-RTD-ENSE-003`

### Usuarios (solo admin)

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/users` | Listar usuarios |
| `POST` | `/api/users` | Crear usuario |
| `PUT` | `/api/users/<id>` | Actualizar rol |
| `DELETE` | `/api/users/<id>` | Eliminar usuario |
| `POST` | `/api/users/<id>/reset-password` | Resetear a primer login |

### Auditoría

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/audit/logs` | Historial de auditoría (params: `limit`, `offset`, `username`, `action_type`) |
| `GET` | `/api/audit/logs/<id>` | Registro específico |

### Verificación de Configuración

| Método | Ruta | Descripción |
|--------|------|-------------|
| `POST` | `/api/config-verifications` | Registrar frecuencia aplicada |
| `GET` | `/api/config-verifications` | Listar verificaciones |
| `GET` | `/api/config-verifications/<id>` | Detalle de verificación |
| `GET` | `/api/scans/<scan_id>/verifications` | Verificaciones de un scan |
| `PUT` | `/api/config-verifications/<id>` | Actualizar |
| `DELETE` | `/api/config-verifications/<id>` | Eliminar (admin) |

### Espectro

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/spectrum_data/<ip>` | Datos crudos de espectro de un AP |
| `GET` | `/api/recommendations` | Recomendaciones del último análisis |
| `GET` | `/spectrum/<ip>` | Visor de espectro HTML |

---

## Motor Matemático de Análisis de Frecuencias

El corazón del sistema es `app/frequency_analyzer.py`. Implementa un algoritmo de **scoring por ventana deslizante** para evaluar cada posible frecuencia central del espectro disponible.

### Datos de entrada — Formato XML Cambium

Cada equipo PMP450i expone su espectro en `http://{ip}/SpectrumAnalysis.xml`. El formato es:

```xml
<Freq f="5180.000 V" instant="-92" avg="-94" max="-90" />
<Freq f="5180.000 H" instant="-95" avg="-96" max="-93" />
<Freq f="5185.000 V" instant="-88" avg="-90" max="-85" />
...
```

Por cada frecuencia se obtienen dos registros (polaridad `V` y `H`) con tres niveles en dBm:
- `avg` → nivel promedio
- `max` → nivel máximo (pico)
- `instant` → nivel instantáneo

Los pares V/H de la misma frecuencia se fusionan en un único `SpectrumPoint`:

```
SpectrumPoint {
    frequency      float   MHz
    vertical_max   float   dBm — nivel máximo vertical
    vertical_mean  float   dBm — nivel promedio vertical
    horizontal_max float   dBm — nivel máximo horizontal
    horizontal_mean float  dBm — nivel promedio horizontal
}
```

### Ventana Deslizante

El análisis evalúa **todos los canales virtuales posibles** del espectro. Para cada ancho de banda candidato (`channel_width` MHz), la ventana se desplaza de 5 en 5 MHz:

```
ventana = [centro - bw/2  ...  centro + bw/2]

Ejemplo con bw=20 MHz:
  centro=5180 → ventana [5170, 5190]
  centro=5185 → ventana [5175, 5195]
  centro=5190 → ventana [5180, 5200]
  ...
```

Para cada posición de ventana se extraen todos los `SpectrumPoint` que caen dentro del rango y se calculan las métricas a continuación.

---

### Paso 1 — Piso de Ruido (Criterio Conservador)

Se usa el **valor máximo** de todos los registros de pico dentro de la ventana, no el promedio. Esto garantiza que el canal elegido sea robusto frente al peor pico de interferencia:

```
noise_V = max(p.vertical_max   para p en ventana)
noise_H = max(p.horizontal_max para p en ventana)
noise_avg = (noise_V + noise_H) / 2
noise_worst = max(noise_V, noise_H)
```

### Paso 2 — Detección de Burst Noise (Interferencia Intermitente)

Si la diferencia entre el nivel pico y el nivel promedio supera 10 dB, existe interferencia de ráfaga (radares, sistemas pulsados, handshakes WiFi):

```
burst_V = max(|p.vertical_max   - p.vertical_mean|   para p en ventana)
burst_H = max(|p.horizontal_max - p.horizontal_mean| para p en ventana)
burst_noise_level = max(burst_V, burst_H)
high_burst_noise  = burst_noise_level > 10 dB
```

> El burst noise no descalifica el canal, pero se reporta en la UI como advertencia para que el operador lo tenga en cuenta.

### Paso 3 — Chain Imbalance (Desequilibrio de Polarización)

El PMP450i opera con dos cadenas de RF en polaridades ortogonales (V y H) usando MIMO-B (2×2). Si el piso de ruido entre las dos polaridades difiere más de 5 dB, el radio cae a modo **MIMO-A (1×1)**, reduciendo el throughput a la mitad:

```
chain_imbalance = |noise_V - noise_H|

if chain_imbalance > 5 dB:
    mimo_mode      = "MIMO-A (1×1)"   ← degradación
    imbalance_penalty = 50 puntos
else:
    mimo_mode      = "MIMO-B (2×2)"   ← modo normal
    imbalance_penalty = 0
```

### Paso 4 — Estimación de SNR

El SNR proyectado se calcula contra el nivel de señal objetivo (`TARGET_RX_LEVEL`, configurable, default −52 dBm). Se usa el peor caso de las dos polaridades:

```
SNR_estimado = TARGET_RX_LEVEL − noise_worst
             = −52 dBm − noise_worst_dBm

Ejemplo:
  noise_worst = −85 dBm  →  SNR = −52 − (−85) = 33 dB  →  256QAM
  noise_worst = −78 dBm  →  SNR = −52 − (−78) = 26 dB  →  64QAM
  noise_worst = −68 dBm  →  SNR = −52 − (−68) = 16 dB  →  degradado
```

> El `TARGET_RX_LEVEL` representa el nivel de señal recibida esperado desde el equipo remoto. El SNR resultante es conservador porque usa el piso de ruido máximo, no el promedio.

### Paso 5 — Score Base por Modulación

Los umbrales de SNR corresponden a las especificaciones reales del PMP450i:

| SNR estimado | Modulación | Score base | Throughput (20 MHz) |
|:---:|---|:---:|:---:|
| ≥ 32 dB | 256QAM (8X) | 100 | ~120 Mbps |
| ≥ 24 dB | 64QAM (6X) | 75 | ~90 Mbps |
| ≥ 17 dB | 16QAM (4X) | 50 | ~60 Mbps |
| ≥ 10 dB | QPSK (2X) | 25 | ~30 Mbps |
| < 10 dB | Inestable | 0 | — |

Si hay `chain_imbalance > 5 dB`, la modulación se degrada (MIMO-A reduce la capacidad):

| Modulación original | Modulación degradada | Score degradado |
|---|---|:---:|
| 256QAM (8X) | 16QAM 4X [degradado] | 50 |
| 64QAM (6X) | QPSK-3/4 3X [degradado] | 37 |
| 16QAM (4X) | QPSK 2X [degradado] | 25 |
| QPSK (2X) | BPSK 1X [degradado] | 10 |

### Paso 6 — Bonificaciones y Penalizaciones

#### Contigüidad espectral

Si el piso de ruido dentro de la ventana es **plano y estable** (desviación estándar < 3 dB), el canal es más predecible y recibe un bonus:

```
all_levels    = [p.vertical_max, p.horizontal_max, ...]  ← todos los puntos en ventana
std_dev       = numpy.std(all_levels)
is_contiguous = std_dev < 3 dB
contiguity_bonus = 10 puntos  si is_contiguous  else  0
```

#### Eficiencia de ancho de banda

Canales más angostos son más eficientes espectralmente pero ofrecen menos throughput absoluto. El sistema aplica una penalización/bonificación para equilibrar ambos factores:

| Ancho de banda | Bonus |
|:---:|:---:|
| 5 MHz | +15 |
| 10 MHz | +10 |
| 15 MHz | +5 |
| 20 MHz | 0 (basal) |
| 30 MHz | −5 |
| 40 MHz | −10 |

### Cálculo Final del Score

```
score_final = score_base + bonus_contigüidad + bonus_bw − penalización_imbalance
score_final = max(0, score_final)   ← nunca negativo
```

Un canal es **válido** si cumple las tres condiciones simultáneamente:
```
is_valid = (score_final > 0)
       AND (chain_imbalance ≤ 5 dB)
       AND (SNR_estimado ≥ 10 dB)
```

### Estimación de Throughput

```
eficiencia = {
    "256QAM (8X)":   8.0 bps/Hz,
    "64QAM (6X)":    6.0 bps/Hz,
    "16QAM (4X)":    4.0 bps/Hz,
    "QPSK-3/4 (3X)": 3.0 bps/Hz,
    "QPSK (2X)":     2.0 bps/Hz,
    "BPSK (1X)":     1.0 bps/Hz
}

throughput_Mbps = channel_width_MHz × eficiencia × 0.75
# 0.75 = factor de overhead TDD / protocolo (≈25% de overhead)

Ejemplo: 20 MHz × 8.0 × 0.75 = 120 Mbps  (256QAM, 20 MHz)
```

### Matriz de Calidad

El score y el SNR se traducen a una etiqueta semántica para facilitar la lectura humana:

| Calidad | Criterios | Significado operativo |
|---|---|---|
| **EXCELENTE** | `is_valid` AND SNR ≥ 32 dB AND ΔVH ≤ 3 dB | Canal ideal. 256QAM estable. Usar sin dudas. |
| **BUENO** | `is_valid` AND SNR ≥ 24 dB | Buen rendimiento. 64QAM. |
| **ACEPTABLE** | `is_valid` AND SNR ≥ 17 dB | Funcional, limitado a 16QAM. Monitorizar. |
| **MARGINAL** | `is_valid` AND SNR ≥ 10 dB | Enlace inestable. Riesgo de caídas de servicio. |
| **CRÍTICO** | `!is_valid` OR SNR < 10 dB | **NO USAR.** Interferencia severa o falla HW. |

Flags adicionales:
- `is_optimal = calidad in ["EXCELENTE", "BUENO"]`
- `requires_action = calidad in ["MARGINAL", "CRÍTICO"]`

---

## Análisis Cruzado AP-SM

El análisis cruzado (`app/cross_analyzer.py`) es la funcionalidad diferencial del sistema: no solo evalúa la calidad del espectro en el AP, sino que **valida simultáneamente** si esa frecuencia es viable en el extremo de cada SM (Subscriber Module / cliente).

Esto es crítico porque una frecuencia puede tener ruido bajo en el AP pero alta interferencia en el sitio del cliente — especialmente en entornos urbanos con fuentes de interferencia locales.

### Constantes del Análisis Cruzado

```
SM_VETO_THRESHOLD    = −75 dBm   ← Si SM tiene ruido ≥ −75 dBm: VETO
SM_DOWNLINK_THRESHOLD = −85 dBm  ← Si ruido ≥ −85 dBm: advertencia downlink
VETO_PENALTY         = −50 pts   ← Penalización por cada SM que veta
SM_NOISE_WEIGHT      =  0.5      ← Peso del ruido de SMs en score combinado
```

### Flujo Multibanda

El sistema evalúa automáticamente **cuatro anchos de banda** por cada candidato: `[20, 15, 10, 5]` MHz. Para cada ancho de banda toma las 20 mejores frecuencias del AP y las cruza contra todos los SMs. El resultado es un ranking unificado con cientos de candidatos ordenados por `combined_score`.

```
analyze_multiband_ap_with_sms(ap_spectrum, sm_data, top_n=20):
    para bw en [20, 15, 10, 5] MHz:
        candidatos_AP = FrequencyAnalyzer.analyze_spectrum(bw).top_20
        para cada candidato:
            result = _analyze_frequency_in_sms(candidato, sm_data, bw)
        consolidar resultados
    retornar DataFrame unificado ordenado por combined_score
```

### Cálculo del Score Combinado

Para cada candidato de frecuencia y para cada SM, se extraen los puntos del espectro SM en la misma ventana `[freq − bw/2, freq + bw/2]` y se calcula el ruido del SM:

```
noise_V_sm = max(p.vertical_max   para p en ventana_SM)
noise_H_sm = max(p.horizontal_max para p en ventana_SM)
noise_avg_sm = (noise_V_sm + noise_H_sm) / 2
```

**Regla de veto** (por SM):
```
si noise_avg_sm > −75 dBm:   → SM veta la frecuencia
si noise_avg_sm > −85 dBm:   → advertencia de riesgo downlink (no veta)
```

**Score combinado** (por candidato):

*Caso A — Sin vetos:*
```
sm_penalty   = (sm_avg_noise − (−100)) × 0.5
             = (sm_avg_noise + 100) × 0.5

Ejemplo: sm_avg_noise = −80 dBm
  → sm_penalty = (−80 + 100) × 0.5 = 10 puntos

combined_score = ap_score − sm_penalty
```

*Caso B — Con vetos:*
```
combined_score = ap_score + (VETO_PENALTY × cantidad_SMs_vetados)
               = ap_score − (50 × vetoed_count)
is_viable      = False
quality_level  = "NO VIABLE"
```

La penalización suave del Caso A castiga que los SMs tengan ruido elevado aunque no lleguen al umbral de veto, produciendo un score más conservador. La lógica completa:

```
sm_worst_noise = max(noise_avg_sm para cada SM)
sm_avg_noise   = promedio(noise_avg_sm para cada SM)

if vetoed_count > 0:
    combined_score = ap_score − 50 × vetoed_count
    is_viable      = False
else:
    sm_penalty     = (sm_avg_noise + 100) × 0.5
    combined_score = ap_score − sm_penalty
    is_viable      = True

is_optimal     = is_viable AND combined_score > 80
```

### Clasificación de Calidad Cruzada

| Calidad | Criterio |
|---|---|
| NO VIABLE | Vetado por ≥ 1 SM |
| EXCELENTE | `is_viable` AND combined_score > 70 |
| BUENO | `is_viable` AND combined_score > 50 |
| ACEPTABLE | `is_viable` |

### Selección Final

1. Filtrar resultados viables (`is_viable = True`)
2. Ordenar por `combined_score` descendente
3. Retornar el primero como `best_combined_frequency`
4. Si no hay viables → fallback al de mayor `combined_score` (se reporta como no viable)

---

## Proceso de Escaneo SNMP

### OIDs Utilizados (Cambium PMP450i MIB)

| OID | Nombre | Uso |
|---|---|---|
| `1.3.6.1.4.1.161.19.3.3.2.221.0` | Spectrum Action | Control start/stop/mode del análisis |
| `1.3.6.1.4.1.161.19.3.3.2.222.0` | Spectrum Duration | Duración en segundos del escaneo |
| `1.3.6.1.2.1.1.5.0` | sysName | Validación de conectividad (primario) |
| `1.3.6.1.2.1.1.1.0` | sysDescr | Validación de conectividad (fallback) |
| `1.3.6.1.4.1.161.19.3.3.1.1.0` | SW Version | Validación de equipo Cambium (fallback) |

### Valores de Control del Spectrum Action OID

| Valor | Significado |
|:---:|---|
| `8` | Modo Full Scan (preparación) |
| `1` | Start Analysis (iniciar escaneo) |
| `4` | IDLE / DONE (escaneo terminado — legacy v1) |
| `0` | Stop (también indica fin en firmware moderno) |

### Secuencia de Inicio con Candados de Seguridad

El sistema implementa tres candados para garantizar que el escaneo de SMs y AP esté sincronizado:

```
CANDADO 1 — Verificar SNMP en todos los SMs:
  → Si cualquier SM no responde: ABORTAR (no se toca el AP)
  → Razón: iniciar el AP sin los SMs produce datos asimétricos e inútiles

CANDADO 2 — Preparar y arrancar todos los SMs (en paralelo):
  SET duration = 60s
  SET mode = 8 (Full Scan)
  SET action = 1 (Start) — hasta 3 reintentos con 1.5s de espera
  → Si cualquier SM falla al iniciar: ABORTAR (el AP no arranca)

CANDADO 3 — Arrancar el AP (solo si CANDADO 2 exitoso):
  SET duration = 40s
  SET mode = 8
  SET action = 1
```

### Polling de Estado y Timeouts

```
Para APs:
  CHECK_INTERVAL   = 10 s
  MAX_WAIT         = 300 s (5 min)
  MAX_ERRORS       = 5 errores consecutivos

Para SMs (más lentos y menos confiables):
  CHECK_INTERVAL   = 15 s
  MAX_WAIT         = 600 s (10 min)
  MAX_ERRORS       = 15 errores consecutivos
  INITIAL_DELAY    = 5 s  ← espera inicial antes de verificar
```

### Descarga de XML con Backoff Exponencial

Los SMs a veces necesitan tiempo extra para generar el XML. Se implementan 3 reintentos con backoff exponencial:

```
espera = retry_delay × 2^(intento − 1)

intento 1: espera = 10s
intento 2: espera = 20s
intento 3: espera = 40s
```

### Auto-descubrimiento de Comunidad SNMP

Para cada IP, el sistema prueba automáticamente las comunidades configuradas en orden, usando 3 OIDs diferentes como validación. La comunidad correcta queda almacenada en un mapa interno `{ip: comunidad}` para el resto del escaneo.

---

## Base de Datos

Archivo único: `data/analyzer.db` (SQLite 3, modo WAL, foreign keys activados).

### Esquema

```sql
-- Usuarios con roles
users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    username         TEXT UNIQUE NOT NULL,
    password_hash    TEXT NOT NULL,
    role             TEXT NOT NULL DEFAULT 'operator',  -- 'admin' | 'operator'
    must_change_password INTEGER DEFAULT 1,
    created_at       TEXT,
    last_login       TEXT
)

-- Torres de comunicación
towers (
    tower_id   TEXT PRIMARY KEY,    -- Ej: BAJ02-RTD-ENSE-003
    name       TEXT NOT NULL,
    location   TEXT,
    notes      TEXT,
    created_by INTEGER REFERENCES users(id),
    created_at TEXT,
    updated_at TEXT
)

-- Escaneos de espectro
scans (
    id              TEXT PRIMARY KEY,   -- UUID
    tower_id        TEXT REFERENCES towers(tower_id) ON DELETE SET NULL,
    user_id         INTEGER REFERENCES users(id),
    username        TEXT NOT NULL,
    ticket_id       INTEGER NOT NULL,
    scan_type       TEXT DEFAULT 'AP_ONLY',  -- 'AP_ONLY' | 'AP_SM_CROSS'
    status          TEXT DEFAULT 'initializing',
    ap_ips          TEXT NOT NULL,   -- JSON list
    sm_ips          TEXT,            -- JSON list
    config          TEXT,            -- JSON dict
    results         TEXT,            -- JSON dict (puede ser NULL si falla)
    started_at      TEXT,
    completed_at    TEXT,
    duration_seconds REAL,
    error           TEXT
)

-- Log de auditoría
audit_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    username        TEXT NOT NULL,
    ticket_id       INTEGER,          -- obligatorio solo para SCAN
    action_type     TEXT NOT NULL,    -- SCAN | LOGIN | LOGOUT | USER_* | TOWER_* | CONFIG_VERIFY
    scan_id         TEXT REFERENCES scans(id) ON DELETE SET NULL,
    tower_id        TEXT REFERENCES towers(tower_id) ON DELETE SET NULL,
    devices         TEXT,             -- JSON list de IPs
    start_timestamp TEXT,
    end_timestamp   TEXT,
    duration_seconds REAL,
    result          TEXT,
    details         TEXT              -- JSON dict
)

-- Verificaciones de configuración aplicada
config_verifications (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id          TEXT NOT NULL REFERENCES scans(id) ON DELETE CASCADE,
    tower_id         TEXT REFERENCES towers(tower_id) ON DELETE SET NULL,
    ap_ip            TEXT,
    recommended_freq INTEGER NOT NULL,  -- MHz, según el análisis
    applied_freq     INTEGER,           -- MHz, lo que el operador configuró
    channel_width    INTEGER,
    verified_by      INTEGER REFERENCES users(id),
    verified_at      TEXT,
    notes            TEXT,
    created_at       TEXT
)
```

---

## Autenticación y Roles

El sistema usa **cookies de sesión Flask firmadas** (SECRET_KEY). No hay JWT ni API keys.

### Flujo de primer login

1. Credenciales por defecto: `admin` / `admin`
2. En el primer login se fuerza el cambio de contraseña (redirect a `/change-password`)
3. El campo `must_change_password = 1` se desactiva al cambiar la contraseña

### Roles

| Rol | Permisos |
|---|---|
| `operator` | Ver y ejecutar escaneos, gestionar torres, consultar historial y auditoría |
| `admin` | Todo lo anterior + crear/eliminar usuarios, resetear contraseñas, eliminar registros |

### Sistema de Auditoría

Todos los escaneos requieren un **ticket de mesa de ayuda** (`ticket_id`): entero positivo obligatorio. El decorador `@requires_audit_ticket` intercepta la petición y bloquea el escaneo si el ticket es inválido. El ticket se registra en la tabla `audit_logs` junto con usuario, timestamps de inicio/fin, duración y resumen del resultado.

---

## Tests

El proyecto usa **pytest**. La suite cubre el motor matemático, las rutas HTTP, los managers de datos y la seguridad RBAC.

```bash
py -3.13 -m pytest -v          # Todos los tests con detalle
py -3.13 -m pytest tests/test_frequency_analyzer.py -v  # Solo análisis de frecuencias
py -3.13 -m pytest tests/test_cross_analyzer.py -v      # Solo análisis cruzado
py -3.13 -m pytest -k "audit" -v                        # Filtrar por nombre
```

### Cobertura por módulo

| Archivo de test | Módulo | Tests |
|---|---|:---:|
| `test_frequency_analyzer.py` | Motor de scoring y parsing XML | ~46 |
| `test_cross_analyzer.py` | Análisis cruzado AP-SM | ~19 |
| `test_tower_scan.py` | Orquestador SNMP | ~41 |
| `test_storage.py` | ScanStorageManager SQLite | ~35 |
| `test_auth_manager.py` | AuthManager + roles | ~46 |
| `test_auth_routes.py` | Rutas de autenticación | ~28 |
| `test_tower_manager.py` | TowerManager + validación | ~66 |
| `test_user_routes.py` | CRUD de usuarios + RBAC | ~24 |
| `test_db_manager.py` | DatabaseManager + migraciones | ~20 |
| `test_audit.py` | AuditManager JSONL legacy | ~32 |
| `test_audit_v2.py` | AuditManagerV2 SQLite | ~32 |
| `test_config_verification.py` | ConfigVerificationManager | ~34 |
| `test_api_security.py` | Seguridad HTTP (401/403) | ~21 |
| `test_cnmaestro_client.py` | Cliente cnMaestro API | ~24 |
| **Total** | | **455** |

---

## Despliegue con Docker

### Producción

```bash
# Crear y editar variables de entorno
cp .env.example .env

# Construir y levantar
docker compose up -d --build

# La app queda disponible en http://localhost:5002
# Los datos persisten en ./data/analyzer.db
```

### Variables de entorno en docker-compose.yml

El `docker-compose.yml` pasa automáticamente todas las variables del `.env` al contenedor. El volumen `./data:/app/data` garantiza que la base de datos SQLite persiste entre reinicios.

### Health check

El contenedor incluye un health check que verifica `GET /api/health` cada 30 segundos (3 reintentos, 40 segundos de período inicial de inicio).

```bash
docker compose ps               # Ver estado del contenedor
docker compose logs -f          # Ver logs en tiempo real
docker compose down             # Detener (los datos en ./data/ se conservan)
```

---

## Estructura del Proyecto

```
.
├── app/
│   ├── __init__.py                     # create_app() factory
│   ├── web_app.py                      # Instancia managers, registra blueprints
│   │
│   ├── # ── Managers de datos ─────────────────────────────────────
│   ├── db_manager.py                   # DatabaseManager — SQLite WAL, 5 tablas, migraciones
│   ├── auth_manager.py                 # AuthManager — usuarios, hash, roles, reset
│   ├── tower_manager.py                # TowerManager — CRUD torres, validación regex ID
│   ├── scan_storage_manager.py         # ScanStorageManager — CRUD tabla scans
│   ├── audit_manager.py                # AuditManager legacy (JSONL) — aún en uso por decorator
│   ├── audit_manager_v2.py             # AuditManagerV2 — log SQLite, 11 action_types
│   ├── config_verification_manager.py  # ConfigVerificationManager — frecuencias aplicadas
│   │
│   ├── # ── Motor de Análisis RF ───────────────────────────────────
│   ├── frequency_analyzer.py           # Scoring por ventana deslizante, SNR, modulación
│   ├── cross_analyzer.py               # Análisis cruzado AP-SM, veto, score combinado
│   ├── tower_scan.py                   # Orquestador SNMP (scan, poll, download XML)
│   ├── scan_task.py                    # Pipeline asíncrono de un escaneo completo
│   ├── scan_helpers.py                 # parse_ip_list, get_scan_defaults
│   ├── cnmaestro_client.py             # Cliente API REST cnMaestro
│   │
│   ├── # ── Blueprints Flask ───────────────────────────────────────
│   ├── routes/
│   │   ├── __init__.py                 # register_blueprints()
│   │   ├── auth_routes.py              # /login, /logout, decorators login_required/admin_required
│   │   ├── scan_routes.py              # /api/scan, /api/status, /api/results, /api/scans
│   │   ├── spectrum_routes.py          # /spectrum/*, /api/spectrum_data, /api/recommendations
│   │   ├── tower_routes.py             # /api/towers CRUD
│   │   ├── user_routes.py              # /api/users CRUD (admin)
│   │   ├── audit_routes.py             # /api/audit/logs (GET)
│   │   └── config_routes.py            # /api/config-verifications CRUD
│   │
│   └── templates/
│       ├── index.html                  # SPA principal (escaneo, torres, usuarios, historial)
│       ├── login.html
│       ├── change_password.html
│       └── spectrum_viewer.html
│
├── static/
│   ├── js/app.js                       # Lógica frontend SPA (1000+ líneas)
│   └── css/                            # Estilos
│
├── tests/                              # 455 tests pytest
├── data/                               # analyzer.db (SQLite, montado como volumen)
├── .artifacts/                         # Documentación SDD (proposal, specs, design, tasks)
├── ALGORITHM_DESCRIPTION.md           # Pseudocódigo y matriz de decisión del algoritmo
├── .env.example                        # Plantilla de variables de entorno
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── VERSION                             # 3.0.0
```

---

## Créditos y Contexto

Sistema desarrollado para automatizar el proceso de optimización de frecuencias en redes Cambium PMP450i. El algoritmo de scoring y el análisis cruzado AP-SM fueron diseñados a partir del conocimiento operativo del comportamiento real de los radios en campo, los requisitos de modulación de la especificación técnica del PMP450i y las mejores prácticas de planificación espectral en bandas no licenciadas.
