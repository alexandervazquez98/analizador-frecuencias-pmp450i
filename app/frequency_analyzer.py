"""
Módulo para analizar datos de espectro y seleccionar frecuencias óptimas
Implementa Matriz de Calificación de Frecuencias con ventana deslizante
Basado en especificaciones técnicas RF para Cambium PMP 450i
"""

import requests
import xml.etree.ElementTree as ET
import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass

# Logger del módulo (configuración centralizada en app/__init__.py)
logger = logging.getLogger(__name__)


@dataclass
class SpectrumPoint:
    """Punto individual de medición de espectro"""

    frequency: float  # MHz
    vertical_max: float  # dBm - Nivel máximo en polaridad vertical
    vertical_mean: float  # dBm - Nivel medio en polaridad vertical
    horizontal_max: float  # dBm - Nivel máximo en polaridad horizontal
    horizontal_mean: float  # dBm - Nivel medio en polaridad horizontal


@dataclass
class FrequencyScore:
    """
    Resultado de puntuación para una frecuencia candidata
    Implementa el sistema de calificación basado en RF
    """

    center_freq: float  # MHz - Frecuencia central del canal
    bandwidth: int  # MHz - Ancho de banda del canal

    # Métricas de ruido
    noise_vertical: float  # dBm - Piso de ruido en polaridad V
    noise_horizontal: float  # dBm - Piso de ruido en polaridad H
    noise_avg: float  # dBm - Promedio de ruido

    # Evaluación de desequilibrio
    chain_imbalance: float  # dB - Diferencia absoluta entre V y H
    imbalance_penalty: float  # Puntos - Penalización por desequilibrio

    # Evaluación de SNR
    snr_estimated: float  # dB - SNR proyectado con target -52 dBm
    modulation: str  # Modulación teórica soportada
    mimo_mode: str  # Modo MIMO (MIMO-B 2x2 o MIMO-A 1x1)
    throughput_estimated: float  # Mbps - Estimación de capacidad agregada

    # Puntuación
    base_score: float  # Puntos - Score base por modulación
    contiguity_bonus: float  # Puntos - Bonificación por espectro limpio
    final_score: float  # Puntos - Score total

    # Calidad del espectro
    spectrum_std_dev: float  # dB - Desviación estándar del ruido en el canal
    is_contiguous: bool  # Espectro contiguo y estable
    is_valid: bool  # Canal válido para operación

    # Advertencias de calidad
    high_burst_noise: bool  # Interferencia intermitente detectada (Max-Mean > 10 dB)
    burst_noise_level: float  # dB - Nivel de ruido intermitente


class FrequencyAnalyzer:
    """
    Analizador de frecuencias con algoritmo de Matriz de Calificación
    Implementa ventana deslizante y sistema de puntuación RF
    """

    # Constantes de configuración RF
    TARGET_LEVEL = -52  # dBm - Nivel de recepción objetivo para cálculo de SNR

    # Umbrales críticos
    MAX_CHAIN_IMBALANCE = 5  # dB - Máxima diferencia permitida entre polaridades
    IMBALANCE_PENALTY = 50  # Puntos - Penalización severa por desequilibrio >5dB

    # Umbrales de SNR para modulaciones (basado en especificaciones PMP 450i)
    SNR_256QAM = 32  # dB - SNR mínimo para 256QAM (8X)
    SNR_64QAM = 24  # dB - SNR mínimo para 64QAM (6X)
    SNR_16QAM = 17  # dB - SNR mínimo para 16QAM (4X)
    SNR_UNSTABLE = 10  # dB - Umbral de enlace inestable

    # Puntuaciones por modulación
    SCORE_256QAM = 100  # Puntos - Máximo rendimiento
    SCORE_64QAM = 75  # Puntos - Rendimiento alto
    SCORE_16QAM = 50  # Puntos - Rendimiento medio
    SCORE_UNSTABLE = 0  # Puntos - Enlace inestable

    # Bonificaciones
    CONTIGUITY_BONUS = 10  # Puntos - Bonificación por espectro limpio
    MAX_SPECTRUM_STD_DEV = 3  # dB - Máxima desviación estándar para bonificación

    # Umbrales de calidad de espectro
    BURST_NOISE_THRESHOLD = (
        10  # dB - Diferencia Max-Mean que indica interferencia intermitente
    )

    # Configuración de ventana deslizante
    DEFAULT_CHANNEL_WIDTH = 20  # MHz - Ancho de banda por defecto
    # Paso de la ventana deslizante: 1.25 MHz = mitad de la resolución del XML del PMP450i
    # (que muestrea cada 2.5 MHz). Este es el paso más fino con sentido físico: a cada
    # incremento de 1.25 MHz, al menos un punto de medición entra o sale de la ventana,
    # permitiendo encontrar frecuencias centrales que esquivan picos angostos de interferencia.
    # Impacto: ~4x más candidatos evaluados vs. 5 MHz (aceptable, todo en memoria).
    SLIDING_STEP = 1.25  # MHz - Mínimo útil dado resolución de 2.5 MHz del instrumento

    # Bonificaciones por Eficiencia Espectral
    # Lógica: preferir el menor BW que cumpla la demanda del sector.
    # Rango operativo estándar: 15-20 MHz.
    # BWs < 15 MHz solo se evalúan si min_channel_width lo permite
    # explícitamente (ver config MIN_CHANNEL_WIDTH); en ese caso reciben
    # penalización para que solo ganen si la calidad RF es significativamente
    # superior a las opciones de 15-20 MHz.
    BW_EFFICIENCY_BONUS = {
        5:  -10,  # Penalización: solo usar si min_channel_width < 15 y no hay alternativa
        10:  -5,  # Penalización leve: válido en escenarios muy congestionados
        15:   5,  # Preferencia leve sobre 20 MHz (mismo throughput útil, menor huella espectral)
        20:   0,  # Línea base
        30:  -5,  # Penalizar uso excesivo de espectro
        40: -10,
    }

    def __init__(self, config: Dict = None):
        """
        Inicializar analizador con configuración

        Args:
            config: Diccionario con parámetros (target_rx_level, min_snr, channel_width, etc.)
        """
        self.config = config or {}
        self.target_rx_level = self.config.get("target_rx_level", -52)
        self.min_snr = self.config.get("min_snr", 32)
        self.max_pol_diff = self.config.get("max_pol_diff", 5)
        self.channel_width = int(
            self.config.get("channel_width", self.DEFAULT_CHANNEL_WIDTH)
        )
        logger.info(
            f"FrequencyAnalyzer inicializado: TargetRx={self.target_rx_level}dBm, Width={self.channel_width}MHz"
        )

    def _estimate_throughput(
        self, modulation: str, bandwidth: int, mimo_mode: str
    ) -> float:
        """
        Estimación aproximada de throughput agregado (Mbps) para PMP 450i
        Basado en tablas de capacidad típicas (Uplink + Downlink)
        """
        # Eficiencia espectral aproximada (bps/Hz) para cada modulación (MIMO-B / 2x2)
        # Nota: Valores conservadores para estimación de campo
        mod_efficiency = {
            "256QAM (8X)": 8.0,
            "64QAM (6X)": 6.0,
            "16QAM (4X)": 4.0,
            "QPSK-3/4 (3X)": 3.0,  # Aproximado
            "QPSK (2X)": 2.0,
            "BPSK (1X)": 1.0,
            "Inestable": 0.0,
            "N/A": 0.0,
        }

        # Encontrar la modulación base (quitando [degradado] y otros textos)
        for key in mod_efficiency:
            if key.split("(")[0] in modulation:  # Match por nombre principal
                # Si la string contiene la clave exactamente, es mejor
                pass

        # Match más directo
        if "8X" in modulation:
            efficiency = 8.0
        elif "6X" in modulation:
            efficiency = 6.0
        elif "4X" in modulation:
            efficiency = 4.0
        elif "3X" in modulation:
            efficiency = 3.0
        elif "2X" in modulation:
            efficiency = 2.0
        elif "1X" in modulation:
            efficiency = 1.0
        else:
            efficiency = 0.0

        # Ajuste por sobrecarga de protocolo, TDD frame, etc. (~75% de eficiencia neta)
        net_efficiency_factor = 0.75

        throughput = bandwidth * efficiency * net_efficiency_factor

        # Ajuste por MIMO-A (ya está implícito en la degradación de modulación hecha antes en el código?
        # En calculate_frequency_score, si es MIMO-A, ya degradamos la modulación (ej 8X -> 4X).
        # Por tanto, no necesitamos dividir otra vez por 2, ya que la eficiencia "4X" es la mitad de "8X".
        # Sin embargo, si hubieramos mantenido la etiqueta de modulación igual, tendríamos que dividir.
        # Mi lógica anterior DEGRADÓ la etiqueta de modulación, así que el cálculo de arriba es correcto.

        return round(throughput, 1)

    def download_spectrum_data(
        self, ip: str, timeout: int = 30, max_retries: int = 3, retry_delay: int = 5
    ) -> Optional[str]:
        """
        Descargar datos XML de espectro desde un AP con reintentos

        Args:
            ip: Dirección IP del AP
            timeout: Timeout en segundos
            max_retries: Número máximo de reintentos
            retry_delay: Delay entre reintentos en segundos

        Returns:
            Contenido XML como string o None si hay error
        """
        url = f"http://{ip}/SpectrumAnalysis.xml"
        last_error = None

        for attempt in range(1, max_retries + 1):
            try:
                logger.info(
                    f"[Intento {attempt}/{max_retries}] Descargando datos de {ip}..."
                )
                response = requests.get(url, timeout=timeout)
                response.raise_for_status()

                # Validar que el contenido sea válido
                xml_content = response.text

                if not xml_content or len(xml_content) < 100:
                    raise ValueError(
                        f"XML muy pequeño o vacío ({len(xml_content) if xml_content else 0} bytes)"
                    )

                if "<Freq" not in xml_content:
                    raise ValueError("XML no contiene elementos <Freq> esperados")

                logger.info(
                    f"Descarga exitosa de {ip} ({len(response.content)} bytes) en intento {attempt}"
                )
                return xml_content

            except requests.Timeout as e:
                last_error = f"Timeout ({timeout}s): {str(e)}"
                logger.warning(
                    f"[Intento {attempt}/{max_retries}] Timeout descargando de {ip}: {e}"
                )

            except requests.ConnectionError as e:
                last_error = f"Error de conexión: {str(e)}"
                logger.warning(
                    f"[Intento {attempt}/{max_retries}] Error de conexión con {ip}: {e}"
                )

            except requests.HTTPError as e:
                last_error = f"Error HTTP {e.response.status_code}: {str(e)}"
                logger.error(
                    f"[Intento {attempt}/{max_retries}] Error HTTP de {ip}: {e}"
                )
                # Si es un error 404 o 403, no tiene sentido reintentar
                if e.response.status_code in [404, 403, 401]:
                    logger.error(
                        f"Error HTTP {e.response.status_code} no recuperable, abortando reintentos"
                    )
                    return None

            except ValueError as e:
                last_error = f"Validación fallida: {str(e)}"
                logger.warning(
                    f"[Intento {attempt}/{max_retries}] Validación fallida para {ip}: {e}"
                )

            except requests.RequestException as e:
                last_error = f"Error de petición: {str(e)}"
                logger.error(
                    f"[Intento {attempt}/{max_retries}] Error descargando de {ip}: {e}"
                )

            # Si no es el último intento, esperar antes de reintentar
            if attempt < max_retries:
                # Backoff exponencial
                wait_time = retry_delay * attempt
                logger.info(f"Esperando {wait_time}s antes del siguiente intento...")
                import time

                time.sleep(wait_time)

        # Si llegamos aquí, todos los intentos fallaron
        logger.error(
            f"Error descargando XML de {ip} después de {max_retries} intentos: {last_error}"
        )
        return None

    def parse_spectrum_xml(self, xml_content: str) -> List[SpectrumPoint]:
        """
        Parsear XML de espectro y extraer puntos de datos

        Formato real de Cambium PMP 450i:
        <Freq f="4900.000 V" instant="-100" avg="-100" max="-100" />
        <Freq f="4900.000 H" instant="-100" avg="-100" max="-100" />

        Args:
            xml_content: Contenido XML como string

        Returns:
            Lista de objetos SpectrumPoint ordenados por frecuencia
        """
        spectrum_points = []

        try:
            root = ET.fromstring(xml_content)

            # El XML puede tener namespace, intentar con y sin
            # Primero intentar sin namespace
            freq_elements = root.findall(".//Freq")

            # Si no encuentra, intentar con namespace de Cambium
            if len(freq_elements) == 0:
                namespaces = {"ns": "http://www.cambiumnetworks.com/spectrum"}
                freq_elements = root.findall(".//ns:Freq", namespaces)

            logger.info(f"Encontrados {len(freq_elements)} elementos Freq en el XML")

            # Agrupar por frecuencia (V y H vienen en pares)
            freq_data = {}

            for freq_elem in freq_elements:
                # Atributo "f" contiene "4900.000 V" o "4900.000 H"
                f_attr = freq_elem.get("f", "")
                parts = f_attr.split()

                if len(parts) != 2:
                    continue

                freq = float(parts[0])
                polarity = parts[1]  # 'V' o 'H'

                # Extraer valores (usar avg y max)
                avg_val = float(freq_elem.get("avg", 0))
                max_val = float(freq_elem.get("max", 0))

                # Inicializar entrada si no existe
                if freq not in freq_data:
                    freq_data[freq] = {"v_max": 0, "v_mean": 0, "h_max": 0, "h_mean": 0}

                # Almacenar según polaridad
                if polarity == "V":
                    freq_data[freq]["v_max"] = max_val
                    freq_data[freq]["v_mean"] = avg_val
                elif polarity == "H":
                    freq_data[freq]["h_max"] = max_val
                    freq_data[freq]["h_mean"] = avg_val

            # Convertir a SpectrumPoint
            for freq, data in freq_data.items():
                spectrum_points.append(
                    SpectrumPoint(
                        frequency=freq,
                        vertical_max=data["v_max"],
                        vertical_mean=data["v_mean"],
                        horizontal_max=data["h_max"],
                        horizontal_mean=data["h_mean"],
                    )
                )

            # Ordenar por frecuencia
            spectrum_points.sort(key=lambda p: p.frequency)

            logger.info(f"Parseados {len(spectrum_points)} puntos de frecuencia")
            return spectrum_points

        except ET.ParseError as e:
            logger.error(f"Error parseando XML: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Error procesando datos: {str(e)}")
            return []

    def calculate_frequency_score(
        self,
        spectrum_points: List[SpectrumPoint],
        center_freq: float,
        bandwidth: int = None,
    ) -> FrequencyScore:
        """
        Calcular puntaje para una frecuencia central específica
        """
        # Usar ancho de banda específico o el configurado por defecto
        width = (
            bandwidth
            if bandwidth is not None
            else getattr(self, "channel_width", self.DEFAULT_CHANNEL_WIDTH)
        )

        freq_min = center_freq - (width / 2)
        freq_max = center_freq + (width / 2)

        # Filtrar puntos dentro de la ventana
        window_points = [
            p for p in spectrum_points if freq_min <= p.frequency <= freq_max
        ]

        if not window_points:
            # Sin datos en este rango
            return FrequencyScore(
                center_freq=center_freq,
                bandwidth=width,
                noise_vertical=0,
                noise_horizontal=0,
                noise_avg=0,
                chain_imbalance=999,
                imbalance_penalty=self.IMBALANCE_PENALTY,
                snr_estimated=0,
                modulation="N/A",
                mimo_mode="N/A",
                throughput_estimated=0.0,
                base_score=0,
                contiguity_bonus=0,
                final_score=0,
                spectrum_std_dev=0,
                is_contiguous=False,
                is_valid=False,
                high_burst_noise=False,
                burst_noise_level=0,
            )

        # ===================================================================
        # PASO 1: Cálculo del Piso de Ruido (conservador, usando MaxLevel)
        # ===================================================================
        # Usar el máximo nivel registrado (peor caso) para cada polaridad
        noise_v = max(p.vertical_max for p in window_points)
        noise_h = max(p.horizontal_max for p in window_points)
        noise_avg = (noise_v + noise_h) / 2

        # ===================================================================
        # PASO 1.5: Detección de Interferencia Intermitente (Burst Noise)
        # Comparar niveles Max vs Mean para detectar picos de ruido
        # ===================================================================
        # Calcular diferencia máxima entre Max y Mean en cada polaridad
        max_mean_diffs_v = [
            abs(p.vertical_max - p.vertical_mean) for p in window_points
        ]
        max_mean_diffs_h = [
            abs(p.horizontal_max - p.horizontal_mean) for p in window_points
        ]

        burst_noise_v = max(max_mean_diffs_v) if max_mean_diffs_v else 0
        burst_noise_h = max(max_mean_diffs_h) if max_mean_diffs_h else 0
        burst_noise_level = max(burst_noise_v, burst_noise_h)

        high_burst_noise = burst_noise_level > self.BURST_NOISE_THRESHOLD

        if high_burst_noise:
            logger.warning(
                f"Frecuencia {center_freq} MHz: Ruido intermitente detectado ({burst_noise_level:.1f} dB Max-Mean)"
            )

        # ===================================================================
        # PASO 2: Evaluación de Desequilibrio (Chain Imbalance)
        # CRÍTICO: Diferencia >5 dB degrada a MIMO-A (mitad de capacidad)
        # ===================================================================
        chain_imbalance = abs(noise_v - noise_h)

        # Aplicar penalización severa si supera el umbral de 5 dB
        if chain_imbalance > self.MAX_CHAIN_IMBALANCE:
            imbalance_penalty = self.IMBALANCE_PENALTY
            logger.debug(
                f"Frecuencia {center_freq} MHz: Desequilibrio {chain_imbalance:.2f} dB > {self.MAX_CHAIN_IMBALANCE} dB - Penalización aplicada"
            )
        else:
            imbalance_penalty = 0

        # ===================================================================
        # PASO 3: Cálculo de SNR Proyectado
        # Usar el peor canal (mayor ruido) para ser conservadores
        # ===================================================================
        worst_noise = max(noise_v, noise_h)
        snr_estimated = self.target_rx_level - worst_noise

        # ===================================================================
        # PASO 4: Asignación de Puntaje por Modulación
        # Basado en tabla de sensibilidad oficial PMP 450i
        # Umbrales: 32, 24, 17, 10 dB para 8X, 6X, 4X, 2X respectivamente
        # ===================================================================
        if snr_estimated >= self.SNR_256QAM:
            # SNR >= 32 dB: Soporta 256QAM (8X) - Máximo rendimiento
            base_score = self.SCORE_256QAM
            modulation = "256QAM (8X)"
        elif snr_estimated >= self.SNR_64QAM:
            # SNR >= 24 dB: Soporta 64QAM (6X) - Rendimiento alto
            base_score = self.SCORE_64QAM
            modulation = "64QAM (6X)"
        elif snr_estimated >= self.SNR_16QAM:
            # SNR >= 17 dB: Soporta 16QAM (4X) - Rendimiento medio
            base_score = self.SCORE_16QAM
            modulation = "16QAM (4X)"
        elif snr_estimated >= self.SNR_UNSTABLE:
            # SNR >= 10 dB: Soporta QPSK (2X) - Enlace marginal
            base_score = 25
            modulation = "QPSK (2X)"
        else:
            # SNR < 10 dB: Enlace inestable
            base_score = self.SCORE_UNSTABLE
            modulation = "Inestable"

        # ===================================================================
        # PASO 4.5: Degradación a MIMO-A por Chain Imbalance
        # Si imbalance > 5 dB, el radio usa MIMO-A (1x1) en vez de MIMO-B (2x2)
        # Esto reduce la capacidad a la mitad: 8X→4X, 6X→3X, 4X→2X, 2X→1X
        # ===================================================================
        if chain_imbalance > self.MAX_CHAIN_IMBALANCE:
            mimo_mode = "MIMO-A (1x1)"

            # Degradar modulación a la mitad de capacidad
            if "8X" in modulation:
                modulation = "16QAM (4X) [degradado]"
                base_score = self.SCORE_16QAM  # Reducir score
            elif "6X" in modulation:
                modulation = "QPSK-3/4 (3X) [degradado]"
                base_score = int(self.SCORE_64QAM * 0.5)  # ~37 puntos
            elif "4X" in modulation:
                modulation = "QPSK (2X) [degradado]"
                base_score = 25
            elif "2X" in modulation:
                modulation = "BPSK (1X) [degradado]"
                base_score = 10

            logger.warning(
                f"Frecuencia {center_freq} MHz: Chain imbalance {chain_imbalance:.1f} dB > 5 dB - Degradado a {mimo_mode}"
            )
        else:
            mimo_mode = "MIMO-B (2x2)"

        # Calcular Throughput
        throughput = self._estimate_throughput(modulation, width, mimo_mode)

        # ===================================================================
        # PASO 5: Bonificación por Espectro Contiguo y Estable
        # Verifica limpieza del canal en toda su extensión
        # ===================================================================
        # Calcular desviación estándar del ruido dentro de la ventana
        all_noise_levels = []
        for p in window_points:
            all_noise_levels.append(p.vertical_max)
            all_noise_levels.append(p.horizontal_max)

        spectrum_std_dev = np.std(all_noise_levels) if all_noise_levels else 999

        # Verificar si el espectro es contiguo (baja variación)
        is_contiguous = spectrum_std_dev < self.MAX_SPECTRUM_STD_DEV

        # Aplicar bonificación si el canal es limpio y estable
        if is_contiguous:
            contiguity_bonus = self.CONTIGUITY_BONUS
            logger.debug(
                f"Frecuencia {center_freq} MHz: Espectro contiguo (std={spectrum_std_dev:.2f} dB) - Bonificación +{self.CONTIGUITY_BONUS} pts"
            )
        else:
            contiguity_bonus = 0

        # ===================================================================
        # PASO 6: Bonificación por Eficiencia de Ancho de Banda
        # ===================================================================
        bw_bonus = self.BW_EFFICIENCY_BONUS.get(width, 0)
        if bw_bonus != 0:
            logger.debug(
                f"Frecuencia {center_freq} MHz: Ancho {width} MHz - Bonus Eficiencia: {bw_bonus}"
            )

        # ===================================================================
        # CÁLCULO DE SCORE FINAL
        # ===================================================================
        final_score = base_score + contiguity_bonus + bw_bonus - imbalance_penalty
        final_score = max(0, final_score)  # No permitir scores negativos

        # Determinar validez del canal
        is_valid = (
            final_score > 0
            and chain_imbalance <= self.MAX_CHAIN_IMBALANCE
            and snr_estimated >= self.SNR_UNSTABLE
        )

        return FrequencyScore(
            center_freq=center_freq,
            bandwidth=width,
            noise_vertical=noise_v,
            noise_horizontal=noise_h,
            noise_avg=noise_avg,
            chain_imbalance=chain_imbalance,
            imbalance_penalty=imbalance_penalty,
            snr_estimated=snr_estimated,
            modulation=modulation,
            mimo_mode=mimo_mode,
            throughput_estimated=throughput,
            base_score=base_score,
            contiguity_bonus=contiguity_bonus,
            final_score=final_score,
            spectrum_std_dev=spectrum_std_dev,
            is_contiguous=is_contiguous,
            is_valid=is_valid,
            high_burst_noise=high_burst_noise,
            burst_noise_level=burst_noise_level,
        )

    def analyze_spectrum(
        self, spectrum_points: List[SpectrumPoint], bandwidth: int = None
    ) -> pd.DataFrame:
        """
        Analizar todo el espectro usando ventana deslizante
        Genera la Matriz de Calificación completa

        Args:
            spectrum_points: Lista de puntos de espectro
            bandwidth: Ancho de banda opcional (si no se especifica, usa el configurado en __init__)

        Returns:
            DataFrame con ranking de frecuencias ordenado por score
        """
        if not spectrum_points:
            logger.warning("No hay datos de espectro para analizar")
            return pd.DataFrame()

        # Definir ancho de banda a usar
        width = (
            bandwidth
            if bandwidth is not None
            else getattr(self, "channel_width", self.DEFAULT_CHANNEL_WIDTH)
        )

        # Obtener rango de frecuencias disponibles
        freq_min = min(p.frequency for p in spectrum_points)
        freq_max = max(p.frequency for p in spectrum_points)

        logger.info(
            f"Analizando espectro {freq_min:.1f} - {freq_max:.1f} MHz con ventana deslizante de {width} MHz"
        )

        # Generar candidatos con ventana deslizante
        results = []
        # round() inicial: evita trailing decimals en freq_min + width/2
        center = round(freq_min + (width / 2), 3)

        while center + (width / 2) <= freq_max:
            score = self.calculate_frequency_score(
                spectrum_points, center, bandwidth=width
            )

            # Convertir a diccionario para DataFrame
            results.append(
                {
                    "Frecuencia Central (MHz)": score.center_freq,
                    "Ancho Banda (MHz)": score.bandwidth,
                    "Ruido V (dBm)": round(score.noise_vertical, 2),
                    "Ruido H (dBm)": round(score.noise_horizontal, 2),
                    "Ruido Promedio (dBm)": round(score.noise_avg, 2),
                    "Delta V/H (dB)": round(score.chain_imbalance, 2),
                    "SNR Estimado (dB)": round(score.snr_estimated, 2),
                    "Throughput Est. (Mbps)": score.throughput_estimated,
                    "Modulación Teórica": score.modulation,
                    "Modo MIMO": score.mimo_mode,
                    "Score Base": score.base_score,
                    "Bonif. Contigua": score.contiguity_bonus,
                    "Penal. Deseq.": score.imbalance_penalty,
                    "Puntaje Final": score.final_score,
                    "Std Dev (dB)": round(score.spectrum_std_dev, 2),
                    "Espectro Contiguo": "Sí" if score.is_contiguous else "No",
                    "Burst Noise": "ADVERTENCIA" if score.high_burst_noise else "OK",
                    "Nivel Burst (dB)": round(score.burst_noise_level, 1),
                    "Válido": "Sí" if score.is_valid else "No",
                }
            )

            # round() en cada paso: previene drift acumulativo de IEEE 754 float
            # con sumas repetidas de 1.25 (e.g. 4900.0 + 1.25×80 → 4999.9999...)
            center = round(center + self.SLIDING_STEP, 3)

        # Crear DataFrame y ordenar por puntaje
        df = pd.DataFrame(results)
        df = df.sort_values("Puntaje Final", ascending=False).reset_index(drop=True)

        logger.info(f"Análisis completo: {len(df)} frecuencias candidatas evaluadas")

        return df

    def _classify_frequency_quality(
        self, row: pd.Series
    ) -> Tuple[str, List[str], List[str]]:
        """
        Clasificar la calidad de una frecuencia y generar advertencias/recomendaciones

        Args:
            row: Fila del DataFrame con datos de la frecuencia

        Returns:
            Tupla (quality_level, warnings, recommendations)
        """
        snr = row["SNR Estimado (dB)"]
        is_valid = row["Válido"] == "Sí"
        chain_imbalance = row["Delta V/H (dB)"]
        burst_noise = row["Burst Noise"] == "ADVERTENCIA"
        modulation = row["Modulación Teórica"]
        score = row["Puntaje Final"]

        warnings = []
        recommendations = []

        # Clasificación de calidad basada en SNR y validez
        if is_valid and snr >= 32 and chain_imbalance <= 3:
            quality = "EXCELENTE"
            # Sin advertencias para excelente calidad
        elif is_valid and snr >= 24:
            quality = "BUENO"
            if chain_imbalance > 3:
                warnings.append(
                    f"Desequilibrio de polarización moderado ({chain_imbalance:.1f} dB)"
                )
                recommendations.append("Verificar orientación y alineación de antenas")
        elif is_valid and snr >= 17:
            quality = "ACEPTABLE"
            warnings.append(f"SNR limitado ({snr:.1f} dB) - Modulación {modulation}")
            if chain_imbalance > 3:
                warnings.append(
                    f"Desequilibrio de polarización ({chain_imbalance:.1f} dB)"
                )
            recommendations.append("Monitorear rendimiento del enlace regularmente")
            recommendations.append(
                "Considerar mejorar línea de vista o reducir distancia"
            )
        elif is_valid and snr >= 10:
            quality = "MARGINAL"
            warnings.append(
                f"SNR marginal ({snr:.1f} dB) - Enlace puede ser inestable"
            )
            warnings.append(f"Modulación limitada a {modulation}")
            if chain_imbalance > 3:
                warnings.append(
                    f"Desequilibrio significativo ({chain_imbalance:.1f} dB)"
                )
            recommendations.append(
                "🔧 ACCIÓN REQUERIDA: Mejorar condiciones del enlace"
            )
            recommendations.append("Verificar obstrucciones en línea de vista")
            recommendations.append("Revisar altura y orientación de antenas")
        else:
            # No válido o SNR < 10 dB
            quality = "CRÍTICO"
            if snr < 10:
                warnings.append(f"SNR crítico ({snr:.1f} dB) - Enlace muy inestable")
                warnings.append(f"Modulación: {modulation} - Rendimiento muy bajo")
            if chain_imbalance > 5:
                warnings.append(
                    f"Desequilibrio severo ({chain_imbalance:.1f} dB) - MIMO degradado"
                )
                recommendations.append("URGENTE: Verificar y realinear antenas")
            if not is_valid:
                warnings.append(
                    "Frecuencia no cumple criterios mínimos de operación"
                )

            recommendations.append("🚨 ACCIÓN INMEDIATA REQUERIDA:")
            recommendations.append(
                "1. Revisar completamente el enlace (altura, LOS, obstrucciones)"
            )
            recommendations.append("2. Verificar que no haya interferencia externa")
            recommendations.append("3. Considerar cambiar ubicación de equipo")
            recommendations.append(
                "4. Esta es la MEJOR opción disponible pero NO es ideal"
            )

        # Advertencias adicionales comunes
        if burst_noise:
            warnings.append("⚠️ Interferencia intermitente detectada en esta frecuencia")
            recommendations.append(
                "Monitorear interferencia con analizador de espectro"
            )

        if score < 25 and quality != "CRÍTICO":
            warnings.append(f"Puntaje bajo ({score}) - Espectro con mucho ruido")

        return quality, warnings, recommendations

    def get_best_frequency(
        self, df: pd.DataFrame, strict_mode: bool = False
    ) -> Optional[Dict]:
        """
        Obtener la mejor frecuencia candidata del ranking con clasificación de calidad

        Args:
            df: DataFrame con resultados del análisis
            strict_mode: Si True, solo retorna frecuencias válidas. Si False, retorna la mejor disponible siempre

        Returns:
            Diccionario con datos de la mejor frecuencia incluyendo quality_level, warnings y recommendations
        """
        if df.empty:
            logger.error("No hay datos de frecuencias para analizar")
            return None

        # Intentar primero con canales válidos
        valid_df = df[df["Válido"] == "Sí"]

        if not valid_df.empty:
            # Hay al menos una frecuencia válida
            best = valid_df.iloc[0].to_dict()
            quality, warnings, recommendations = self._classify_frequency_quality(
                valid_df.iloc[0]
            )

            logger.info(
                f"✅ Mejor frecuencia VÁLIDA: {best['Frecuencia Central (MHz)']} MHz (Score: {best['Puntaje Final']}, Calidad: {quality})"
            )
        else:
            # NO hay frecuencias válidas
            if strict_mode:
                logger.warning(
                    "No se encontraron canales válidos y strict_mode está activado"
                )
                return None

            # Modo permisivo: retornar la mejor disponible aunque no sea válida
            best = df.iloc[0].to_dict()
            quality, warnings, recommendations = self._classify_frequency_quality(
                df.iloc[0]
            )

        # Enriquecer resultado con clasificación
        best["quality_level"] = quality
        best["warnings"] = warnings
        best["recommendations"] = recommendations
        best["is_optimal"] = quality in ["EXCELENTE", "BUENO"]
        best["requires_action"] = quality in ["MARGINAL", "CRÍTICO"]

        # Log de advertencias importantes
        if warnings:
            logger.warning(f"Advertencias para {best['Frecuencia Central (MHz)']} MHz:")
            for w in warnings:
                logger.warning(f"  - {w}")

        return best

    def generate_recommendations(self) -> List[str]:
        """
        Generar recomendaciones de configuración para Uplink

        Returns:
            Lista de strings con recomendaciones
        """
        return [
            "=== RECOMENDACIONES DE CONFIGURACIÓN PMP 450i ===",
            "",
            "OPTIMIZACIÓN PARA TRÁFICO DE SUBIDA (UPLINK CCTV):",
            "",
            "1. DOWNLINK DATA PERCENTAGE:",
            "   - Configurar en 15% (85% Uplink)",
            "   - Esto libera la mayor cantidad de tiempo de aire para subida",
            "   - Crítico para aplicaciones de videovigilancia",
            "   - Ruta: Configuration > Radio > TDD Synchronization",
            "",
            "2. CONTENTION SLOTS:",
            "   - Incrementar a 4 o más slots",
            "   - Firmware >= 16.1: Activar 'Auto Contention'",
            "   - Reduce latencia por colisiones de peticiones",
            "   - Mejora el tiempo de respuesta de solicitudes SM",
            "   - Ruta: Configuration > Radio > MAC Configuration",
            "",
            "3. FRAME PERIOD:",
            "   - Configurar 5ms en TODOS los sectores",
            "   - Uniformidad crítica para sincronización GPS",
            "   - Mayor throughput que 2.5ms",
            "   - Ruta: Configuration > Radio > TDD Synchronization",
            "",
            "4. MAX RANGE:",
            "   - Verificar que sea idéntico en todos los APs de la torre",
            "   - Ajustar al enlace más lejano + 10% margen",
            "   - Evita timeouts y retransmisiones",
            "   - Ruta: Configuration > Radio > MAC Configuration",
            "",
            "5. MODULACIÓN Y POTENCIA:",
            "   - Target Receive Level: -52 dBm (para 256QAM/8X)",
            "   - Habilitar modulación adaptativa",
            "   - Control automático de potencia (ATPC) activado",
            "   - Ruta: Configuration > Radio > Link Parameters",
            "",
            "6. QoS PARA CCTV:",
            "   - Priorizar tráfico de video (High Priority CIR)",
            "   - Configurar MIR adecuado por cámara",
            "   - Ejemplo: 2-4 Mbps por cámara HD",
            "   - Ruta: Configuration > Radio > QoS",
            "",
            "7. SINCRONIZACIÓN GPS:",
            "   - Verificar estado GPS en todos los APs (>= 5 satélites)",
            "   - Timing exacto evita auto-interferencia entre sectores",
            "   - Monitorear 'GPS Sync Status' diariamente",
            "",
            "8. MONITOREO CONTINUO:",
            "   - Revisar 'Spectrum Analyzer' mensualmente",
            "   - Vigilar cambios en el piso de ruido",
            "   - Ajustar frecuencias si hay nuevas interferencias",
            "",
            "IMPORTANTE:",
            "   - Aplicar cambios en ventanas de mantenimiento",
            "   - Configurar todos los sectores simultáneamente",
            "   - Documentar configuración baseline",
            "   - Realizar pruebas de throughput post-cambios",
            "",
            "FRECUENCIAS LIMPIAS:",
            "   - Usar las frecuencias recomendadas arriba",
            "   - Evitar canales con desequilibrio de polarización >5 dB",
            "   - Mantener SNR >= 32 dB para 256QAM",
            "",
        ]


class APAnalysisReport:
    """Reporte completo de análisis para un AP"""

    def __init__(self, ip: str):
        self.ip = ip
        self.xml_downloaded = False
        self.spectrum_points: List[SpectrumPoint] = []
        self.ranking_df: pd.DataFrame = pd.DataFrame()
        self.best_frequency: Optional[Dict] = None
        self.error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convertir a diccionario para JSON"""
        result = {
            "ip": self.ip,
            "xml_downloaded": self.xml_downloaded,
            "spectrum_points": len(self.spectrum_points),
            "error": self.error_message,
        }

        if not self.ranking_df.empty:
            # Convertir top 50 resultados a formato JSON y asignar a combined_ranking para compatibilidad
            top_50 = self.ranking_df.head(50).to_dict("records")
            result["top_frequencies"] = top_50
            result["combined_ranking"] = (
                top_50  # Alias para compatibilidad con frontend
            )

        if self.best_frequency:
            result["best_frequency"] = self.best_frequency

        # Incluir puntos de espectro para graficar (formato compatible con spectrum_viewer.html)
        if self.spectrum_points:
            serialized_points = [
                {
                    "frequency": p.frequency,
                    "vertical": p.vertical_max,
                    "horizontal": p.horizontal_max,
                    "vertical_avg": p.vertical_mean,
                    "horizontal_avg": p.horizontal_mean,
                }
                for p in self.spectrum_points
            ]
            result["spectrum_data"] = {
                "ap": serialized_points,
                "sms": {},  # Empty dict for SMs in single AP mode
            }

        return result


def analyze_ap(
    ip: str, target_rx: float = FrequencyAnalyzer.TARGET_LEVEL
) -> APAnalysisReport:
    """
    Analizar completamente un AP usando Matriz de Calificación

    Args:
        ip: Dirección IP del AP
        target_rx: Nivel de recepción objetivo

    Returns:
        APAnalysisReport con análisis completo
    """
    report = APAnalysisReport(ip)
    report = APAnalysisReport(ip)
    analyzer = FrequencyAnalyzer(config={"target_rx_level": target_rx})

    # Descargar datos
    xml_content = analyzer.download_spectrum_data(ip)
    if not xml_content:
        report.error_message = "Error descargando datos XML"
        return report

    report.xml_downloaded = True

    # Parsear datos
    spectrum_points = analyzer.parse_spectrum_xml(xml_content)
    if not spectrum_points:
        report.error_message = "Error parseando datos XML"
        return report

    report.spectrum_points = spectrum_points

    # Analizar espectro con ventana deslizante
    ranking_df = analyzer.analyze_spectrum(spectrum_points)
    report.ranking_df = ranking_df

    # Obtener mejor frecuencia (modo permisivo por defecto)
    best_freq = analyzer.get_best_frequency(ranking_df, strict_mode=False)
    report.best_frequency = best_freq

    return report


def main():
    """Función principal para testing"""
    import sys

    if len(sys.argv) < 2:
        print("Uso: python frequency_analyzer.py <IP_AP>")
        sys.exit(1)

    ip = sys.argv[1]

    print(f"\n{'=' * 70}")
    print("  MATRIZ DE CALIFICACIÓN DE FRECUENCIAS - Cambium PMP 450i")
    print(f"{'=' * 70}")
    print(f"AP: {ip}")
    print(f"{'=' * 70}\n")

    # Ejecutar análisis
    report = analyze_ap(ip)

    # Mostrar resultados
    if report.error_message:
        print(f"ERROR: {report.error_message}")
        sys.exit(1)

    print(f"Datos descargados: {len(report.spectrum_points)} puntos de frecuencia\n")

    if not report.ranking_df.empty:
        print(f"{'=' * 70}")
        print("  RANKING DE FRECUENCIAS (Top 10)")
        print(f"{'=' * 70}\n")

        # Mostrar tabla formateada
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", None)
        print(report.ranking_df.head(10).to_string(index=False))

        print(f"\n{'=' * 70}")
        print("  MEJOR FRECUENCIA CANDIDATA")
        print(f"{'=' * 70}\n")

        if report.best_frequency:
            for key, value in report.best_frequency.items():
                print(f"{key}: {value}")
        else:
            print("No se encontró ningún canal válido")

    # Mostrar recomendaciones
    print(f"\n{'=' * 70}")
    analyzer = FrequencyAnalyzer()
    recommendations = analyzer.generate_recommendations()
    for line in recommendations:
        print(line)


if __name__ == "__main__":
    main()
