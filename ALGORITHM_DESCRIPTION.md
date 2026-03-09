---
name: Algoritmo de Selección de Frecuencias Tower Scan
description: Pseudocódigo detallado del proceso de escaneo, análisis y calificación de frecuencias para Cambium PMP 450i.
version: 2.0
---

# Algoritmo de Tower Scan y Selección de Frecuencias

Este documento describe la lógica operativa del sistema `Tower Scan Automation`, desde la adquisición de datos vía SNMP hasta la matemática de calificación de frecuencias óptimas.

## 1. Fase de Escaneo (Data Acquisition)

El objetivo es obtener una "radiografía" del espectro RF actual sincronizando APs y SMs para medir el ruido base.

### Entradas
- **Target APs**: Lista de IPs de Access Points.
- **Target SMs**: Lista opcional de IPs de Subscriber Modules.
- **Comunidad SNMP**: Credenciales de acceso.

### Pseudocódigo de Escaneo

```pseudo
PROCEDURE Iniciar_Escaneo(APs, SMs):
    
    // PASO 1: Bloqueo de Seguridad SMs (Estricto)
    // Primero aseguramos que todos los SMs inicien el escaneo.
    // Si uno falla, abortamos TODO para evitar desconexiones inútiles.
    
    PARA CADA sm EN SMs:
        Status = SNMP_GET(sm, OID_STATUS)
        SI Status != IDLE: Esperar()
        
        // Comandos SNMP
        SNMP_SET(sm, OID_DURATION, 60) // Duración extendida para SMs
        SNMP_SET(sm, OID_MODE, 8)      // Modo "Full Scan"
        Resultado = SNMP_SET(sm, OID_ACTION, 1) // Iniciar "Start"
        
        SI Resultado == FALLO:
            RETORNAR ERROR "Fallo inicio en SM crítico. Abortando AP scan."

    // PASO 2: Inicio de APs
    // Solo si los SMs arrancaron bien, iniciamos los APs.
    PARA CADA ap EN APs:
        SNMP_SET(ap, OID_DURATION, 40) // Duración estándar
        SNMP_SET(ap, OID_MODE, 8)
        SNMP_SET(ap, OID_ACTION, 1)

    // PASO 3: Bucle de Monitoreo (Polling)
    MIENTRAS Tiempo < Timeout:
        Todos_Terminados = VERDADERO
        
        PARA CADA dispositivo EN (APs + SMs):
            Estado = SNMP_GET(dispositivo, OID_STATUS)
            
            // Estado 4 = IDLE/DONE
            SI Estado != 4:
                Todos_Terminados = FALSO
        
        SI Todos_Terminados:
            ROMPER BUCLE
            
        ESPERAR(5 segundos)

    // PASO 4: Descarga de Datos
    PARA CADA dispositivo EN (APs + SMs):
        XML = HTTP_GET("http://{IP}/SpectrumAnalysis.xml")
        Datos_Espectro = Parsear_XML(XML)
        // Estructura: Lista de {Frecuencia, Max_V, Mean_V, Max_H, Mean_H}
        Guardar_Resultados(Datos_Espectro)
```

---

## 2. Fase de Análisis (Frequency Scoring Engine)

Esta fase procesa los datos crudos para recomendar el mejor canal posible. Utiliza una ventana deslizante para evaluar cada posible frecuencia central.

### Entradas
- **Datos Espectro**: Puntos crudos del escaneo.
- **Ancho de Canal**: (Ej. 20 MHz).
- **Target Rx**: Nivel de señal objetivo (Ej. -52 dBm).

### Pseudocódigo de Calificación (Scoring)

```pseudo
CLASE FrequencyAnalyzer:
    CONSTANTE TARGET_RX = -52 dBm
    CONSTANTE SNR_REQ_256QAM = 32 dB
    CONSTANTE MAX_IMBALANCE = 5 dB

    FUNCTION Get_Best_Channel(Datos_Espectro, Ancho_Banda):
        Lista_Candidatos = []
        
        // Ventana Deslizante: Mueve el "canal virtual" de 5 en 5 MHz
        Freq_Min = Min(Datos_Espectro.Frecuencias)
        Freq_Max = Max(Datos_Espectro.Frecuencias)
        
        PARA Centro_Freq DESDE Freq_Min HASTA Freq_Max CON PASO 5:
            
            // 1. Definir Ventana de Análisis
            Límite_Inf = Centro_Freq - (Ancho_Banda / 2)
            Límite_Sup = Centro_Freq + (Ancho_Banda / 2)
            
            Puntos_En_Ventana = Filtrar(Datos_Espectro, Límite_Inf, Límite_Sup)
            
            SI Puntos_En_Ventana ESTÁ VACÍO: Continuar
            
            // 2. Calcular Métricas de Ruido (Enfoque Conservador)
            // Tomamos el PEOR ruido (Max) registrado en cualquiera de los puntos dentro del canal
            Ruido_V = MAX(Puntos.Vertical_Max)
            Ruido_H = MAX(Puntos.Horizontal_Max)
            Ruido_Peor_Caso = MAX(Ruido_V, Ruido_H)
            
            // 3. Evaluar Desequilibrio de Polaridad (Chain Imbalance)
            Delta_VH = ABS(Ruido_V - Ruido_H)
            
            // 4. Estimar SNR (Signal-to-Noise Ratio)
            SNR_Estimado = TARGET_RX - Ruido_Peor_Caso
            
            // 5. Asignar Score Base (Basado en Modulación Teórica)
            SI SNR_Estimado >= 32:
                Score_Base = 100 // Soporta 256QAM (8X)
                Modulacion = "256QAM"
            SINO SI SNR_Estimado >= 24:
                Score_Base = 75  // Soporta 64QAM (6X)
                Modulacion = "64QAM"
            SINO SI SNR_Estimado >= 17:
                Score_Base = 50  // Soporta 16QAM (4X)
                Modulacion = "16QAM"
            SINO:
                Score_Base = 0   // Inestable
                Modulacion = "QPSK/Inestable"

            // 6. Aplicar Penalizaciones y Bonos
            Score_Final = Score_Base
            
            // Penalización por Desequilibrio (MIMO Kill)
            // Si la diferencia entre V y H es > 5dB, el radio cae a MIMO-A (mitad de velocidad)
            SI Delta_VH > MAX_IMBALANCE:
                Score_Final = Score_Final - 50
                Modulacion = Modulacion + " (Degradado a MIMO-A)"
            
            // Penalización por Ruido Intermitente (Burst Noise)
            // Si Diferencia (Max - Promedio) > 10dB, hay ráfagas de interferencia
            Burst_Level = MAX(Puntos.Max - Puntos.Mean)
            SI Burst_Level > 10:
                Generar_Advertencia("Interferencia Intermitente Detectada")
            
            // Bono por Contigüidad (Piso de ruido plano)
            Desviacion_Std = Calculate_StdDev(Puntos_En_Ventana)
            SI Desviacion_Std < 3 dB:
                Score_Final = Score_Final + 10
            
            // 7. Guardar Candidato
            Candidato = {
                Frecuencia: Centro_Freq,
                Score: MAX(0, Score_Final), // No negativos
                SNR: SNR_Estimado,
                Calidad: Clasificar_Calidad(Score_Final)
            }
            Lista_Candidatos.ADD(Candidato)

        // FIN DEL BUCLE
        
        // 8. Ranking Final
        Ordenar Lista_Candidatos POR Score DESCENDENTE
        
        RETORNAR Lista_Candidatos.TOP_5()
```

## 3. Matriz de Decisión de Calidad

El sistema asigna una etiqueta semántica al resultado final para facilitar la lectura humana:

| Calidad | Criterios | Significado |
| :--- | :--- | :--- |
| **EXCELENTE** | SNR >= 32 dB & ΔVH <= 3 dB | Canal ideal. Soporta 256QAM estable. |
| **BUENO** | SNR >= 24 dB | Buen rendimiento. Soporta 64QAM. |
| **ACEPTABLE** | SNR >= 17 dB | Funcional pero limitado a 16QAM. Monitorizar. |
| **MARGINAL** | SNR >= 10 dB | Enlace inestable. Riesgo de caídas. |
| **CRÍTICO** | SNR < 10 dB O ΔVH > 5 dB | **NO USAR**. Interferencia severa o falla de hardware. |

---
*Generado automáticamente por Tower Scan Automation System*
