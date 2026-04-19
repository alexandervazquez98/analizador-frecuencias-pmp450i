"""
Módulo para análisis cruzado entre AP y SMs
Implementa lógica de selección de frecuencia considerando ambos lados del enlace
Optimizado para tráfico Uplink (CCTV: SM → AP)
"""

import math
import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from app.frequency_analyzer import FrequencyAnalyzer, SpectrumPoint

logger = logging.getLogger(__name__)


@dataclass
class SMSpectrumData:
    """Datos de espectro de un Subscriber Module"""

    ip: str
    spectrum_points: List[SpectrumPoint]
    is_critical: bool = True  # Todos los SMs son críticos por defecto


@dataclass
class CrossAnalysisResult:
    """Resultado del análisis cruzado AP-SM"""

    frequency: float
    bandwidth: int  # MHz
    ap_score: float
    ap_noise_avg: float
    ap_snr: float  # SNR estimado del AP

    # SNR real calculado por SM (change-007)
    sm_snr_worst: float  # Peor SNR real entre todos los SMs evaluados

    # Datos de SMs
    sm_worst_noise: float  # Peor ruido entre todos los SMs
    sm_avg_noise: float  # Promedio de ruido de SMs
    sm_count_vetoed: int  # Cantidad de SMs que vetaron esta frecuencia

    # Performance
    throughput_est: float  # Mbps

    # Score combinado
    combined_score: float
    is_viable: bool
    veto_reason: str

    # Metadata de Calidad (Added for web_app compatibility)
    quality_level: str
    warnings: List[str]
    recommendations: List[str]
    is_optimal: bool
    requires_action: bool

    # Detalles por SM
    sm_details: List[Dict]


class APSMCrossAnalyzer:
    """
    Analizador de frecuencias con cruce AP-SM
    Optimizado para tráfico Uplink (CCTV)
    """

    # SNR mínimo por defecto (configurable vía min_snr en config del scan)
    # 18 dB → umbral mínimo para 16QAM confiable con fade margin aplicado
    DEFAULT_MIN_SNR = 18  # dB

    # Fade margin estándar de ingeniería RF
    FADE_MARGIN = 10  # dB

    # Penalizaciones de score combinado
    VETO_PENALTY = -50  # Puntos por cada SM que veta
    SM_NOISE_WEIGHT = 0.5  # Peso del ruido de SMs en score combinado

    def __init__(self, min_snr: float = None, config: Dict = None):
        self.analyzer = FrequencyAnalyzer(config=config or {})
        self.min_snr = min_snr if min_snr is not None else self.DEFAULT_MIN_SNR

    def _evaluate_channel_snr(
        self,
        ruido_mimo_peor: float,
        bandwidth: int,
        target_rx_level: float,
    ) -> Tuple[bool, str, float]:
        """
        Evalúa si un canal es viable para un SM dado su ruido MIMO real.

        Modelo de decisión (change-007):
          1. Penalización por expansión de canal: el escaneo es a resolución de 5 MHz,
             pero el canal real integra más potencia de ruido a mayor BW.
             Penalización = 10 * log10(bw / 5):  5→0 dB, 10→3 dB, 15→4.8 dB, 20→6 dB
          2. Ruido total del canal = ruido_mimo_peor + bw_expansion_db
          3. Señal estimada = target_rx_level - FADE_MARGIN (10 dB)
          4. SNR real = señal_estimada - ruido_total
          5. Si snr_real < min_snr → VETADO

        Args:
            ruido_mimo_peor: max(noise_V_max, noise_H_max) en la ventana del canal
            bandwidth:       ancho del canal evaluado en MHz (5/10/15/20)
            target_rx_level: RSSI esperado del enlace (configurado por operador, dBm)

        Returns:
            (is_viable, reason, snr_real)
        """
        # 1. Penalización dinámica por expansión de BW
        bw_expansion_db = 10 * math.log10(bandwidth / 5)

        # 2. Ruido total integrado en el canal real
        ruido_total = ruido_mimo_peor + bw_expansion_db

        # 3. Señal estimada con fade margin
        senal_estimada = target_rx_level - self.FADE_MARGIN

        # 4. SNR real del enlace en este canal
        snr_real = senal_estimada - ruido_total

        # 5. Decisión
        if snr_real < self.min_snr:
            reason = (
                f"SNR insuficiente: {snr_real:.1f} dB "
                f"(requerido: {self.min_snr} dB, "
                f"ruido canal: {ruido_total:.1f} dBm, "
                f"señal estimada: {senal_estimada:.1f} dBm)"
            )
            return False, reason, snr_real

        return True, "OK", snr_real

    def analyze_multiband_ap_with_sms(
        self,
        ap_spectrum: List[SpectrumPoint],
        sm_data: List[SMSpectrumData],
        top_n: int = 5,
        min_channel_width: int = 15,
        target_rx_level: float = -52.0,
    ) -> Tuple[pd.DataFrame, List[CrossAnalysisResult]]:
        """
        Analizar MÚLTIPLES anchos de banda >= min_channel_width.

        Args:
            min_channel_width: BW mínimo a evaluar (default 15 MHz).
                BWs menores son ignorados — no se recomendarán canales
                más angostos que este valor, independientemente de su score.
            target_rx_level: RSSI esperado del enlace configurado por el operador (dBm).
        """
        all_results = []
        # Evaluar solo BWs dentro del rango operativo (>= min_channel_width)
        all_bandwidths = [20, 15, 10, 5]
        bandwidths = [bw for bw in all_bandwidths if bw >= min_channel_width]

        logger.info(
            f"Iniciando análisis multibanda para {len(sm_data)} SMs "
            f"(BWs: {bandwidths} MHz, mínimo: {min_channel_width} MHz, "
            f"target_rx: {target_rx_level} dBm, min_snr: {self.min_snr} dB)"
        )

        for bw in bandwidths:
            logger.info(f"--- Evaluando ancho de canal: {bw} MHz ---")
            try:
                _, results = self.analyze_ap_with_sms(
                    ap_spectrum,
                    sm_data,
                    top_n,
                    bandwidth=bw,
                    target_rx_level=target_rx_level,
                )
                logger.info(
                    f"--- Ancho {bw} MHz: {len(results)} candidatos encontrados ---"
                )
                all_results.extend(results)
            except Exception as e:
                logger.error(f"Error analizando ancho {bw} MHz: {e}")

        logger.info(f"Total candidatos multibanda: {len(all_results)}")

        # Crear DataFrame consolidado
        df_combined = self._create_combined_dataframe(all_results)

        return df_combined, all_results

    def analyze_ap_with_sms(
        self,
        ap_spectrum: List[SpectrumPoint],
        sm_data: List[SMSpectrumData],
        top_n: int = 5,
        bandwidth: int = 20,
        target_rx_level: float = -52.0,
    ) -> Tuple[pd.DataFrame, List[CrossAnalysisResult]]:
        """
        Analizar frecuencias cruzando datos de AP y SMs para un ancho de banda específico.

        Args:
            target_rx_level: RSSI esperado del enlace (configurado por operador, dBm).
        """
        # PASO 1: Analizar espectro del AP con el ancho de banda específico
        ap_ranking = self.analyzer.analyze_spectrum(ap_spectrum, bandwidth=bandwidth)

        if ap_ranking.empty:
            return pd.DataFrame(), []

        # PASO 2: Obtener top N frecuencias candidatas del AP
        top_ap_frequencies = ap_ranking.head(top_n)

        # PASO 3: Cruzar con datos de SMs
        cross_results = []

        for idx, row in top_ap_frequencies.iterrows():
            freq = row["Frecuencia Central (MHz)"]
            ap_score = row["Puntaje Final"]
            ap_noise = row["Ruido Promedio (dBm)"]
            throughput = row.get("Throughput Est. (Mbps)", 0)
            snr = row.get("SNR Estimado (dB)", 0)

            result = self._analyze_frequency_in_sms(
                freq,
                ap_score,
                ap_noise,
                snr,
                sm_data,
                bandwidth,
                throughput,
                target_rx_level=target_rx_level,
            )

            cross_results.append(result)

        # PASO 4: Crear DataFrame de resultados combinados
        df_combined = self._create_combined_dataframe(cross_results)

        return df_combined, cross_results

    def _analyze_frequency_in_sms(
        self,
        frequency: float,
        ap_score: float,
        ap_noise: float,
        ap_snr: float,
        sm_data: List[SMSpectrumData],
        bandwidth: int,
        throughput_est: float,
        target_rx_level: float = -52.0,
    ) -> CrossAnalysisResult:
        """
        Analizar una frecuencia específica en todos los SMs usando evaluación SNR-based.

        Para cada SM:
          1. Obtiene el peor ruido MIMO (max de V_max y H_max) en la ventana del canal.
          2. Llama a _evaluate_channel_snr() para calcular el SNR real y decidir viabilidad.
          3. Expone snr_real por SM en sm_details para el frontend.
        """
        sm_details = []
        sm_noises = []
        sm_snrs = []
        vetoed_count = 0
        veto_reason = ""

        # Ventana de frecuencia (±BW/2)
        half_bw = bandwidth / 2
        freq_min = frequency - half_bw
        freq_max = frequency + half_bw

        for sm in sm_data:
            # Obtener puntos de espectro en esta ventana
            window_points = [
                p for p in sm.spectrum_points if freq_min <= p.frequency <= freq_max
            ]

            if not window_points:
                sm_details.append(
                    {
                        "ip": sm.ip,
                        "noise_v": None,
                        "noise_h": None,
                        "noise_mimo_worst": None,
                        "snr_real": None,
                        "vetoed": False,
                        "reason": "Sin datos en ventana de canal",
                    }
                )
                continue

            # Peor ruido MIMO: conservador, usa MAX de cada polaridad
            noise_v = max(p.vertical_max for p in window_points)
            noise_h = max(p.horizontal_max for p in window_points)
            ruido_mimo_peor = max(noise_v, noise_h)

            # Para métricas de referencia (promedio entre V y H)
            noise_avg = (noise_v + noise_h) / 2
            sm_noises.append(noise_avg)

            # Evaluación SNR-based (change-007)
            is_viable_sm, reason, snr_real = self._evaluate_channel_snr(
                ruido_mimo_peor, bandwidth, target_rx_level
            )
            sm_snrs.append(snr_real)

            if not is_viable_sm:
                vetoed_count += 1

            sm_details.append(
                {
                    "ip": sm.ip,
                    "noise_v": round(noise_v, 2),
                    "noise_h": round(noise_h, 2),
                    "noise_mimo_worst": round(ruido_mimo_peor, 2),
                    "snr_real": round(snr_real, 1),
                    "vetoed": not is_viable_sm,
                    "reason": reason,
                }
            )

        # Métricas agregadas de SMs
        if sm_noises:
            sm_worst_noise = float(max(sm_noises))
            sm_avg_noise = float(np.mean(sm_noises))
        else:
            sm_worst_noise = 0.0
            sm_avg_noise = 0.0

        # Peor SNR entre todos los SMs (el que más aprieta el enlace)
        sm_snr_worst = float(min(sm_snrs)) if sm_snrs else 0.0

        # SCORE COMBINADO
        combined_score = float(ap_score)
        is_viable = True
        warnings = []
        recommendations = []
        quality_level = "BUENO"

        if vetoed_count > 0:
            total_penalty = self.VETO_PENALTY * vetoed_count
            combined_score += total_penalty
            is_viable = False
            veto_reason = (
                f"{vetoed_count} SM(s) con SNR insuficiente "
                f"(peor SNR: {sm_snr_worst:.1f} dB, requerido: {self.min_snr} dB)"
            )
            quality_level = "NO VIABLE"
            warnings.append(
                f"Canal vetado: {vetoed_count} SM(s) no alcanzan SNR mínimo de {self.min_snr} dB."
            )
            recommendations.append("Buscar otra frecuencia con menor piso de ruido.")
        else:
            sm_penalty = (sm_avg_noise - (-100)) * self.SM_NOISE_WEIGHT
            combined_score -= sm_penalty

            if sm_snr_worst < self.min_snr + 5:
                veto_reason = f"Margen SNR ajustado (peor SM: {sm_snr_worst:.1f} dB)"
                quality_level = "MARGINAL"
                warnings.append(
                    f"Margen SNR estrecho en el peor SM ({sm_snr_worst:.1f} dB). "
                    f"Se recomienda mínimo {self.min_snr + 5} dB de margen."
                )

            if combined_score > 70:
                quality_level = "EXCELENTE"
            elif combined_score > 50:
                quality_level = "BUENO"
            else:
                quality_level = "ACEPTABLE"

        return CrossAnalysisResult(
            frequency=float(frequency),
            bandwidth=int(bandwidth),
            ap_score=float(ap_score),
            ap_noise_avg=float(ap_noise),
            ap_snr=float(ap_snr),
            sm_worst_noise=float(sm_worst_noise),
            sm_avg_noise=float(sm_avg_noise),
            sm_snr_worst=float(sm_snr_worst),
            sm_count_vetoed=int(vetoed_count),
            throughput_est=float(throughput_est),
            combined_score=float(round(combined_score, 2)),
            is_viable=bool(is_viable),
            veto_reason=str(veto_reason),
            quality_level=str(quality_level),
            warnings=warnings,
            recommendations=recommendations,
            is_optimal=bool(is_viable and combined_score > 80),
            requires_action=bool(not is_viable),
            sm_details=sm_details,
        )

    def _create_combined_dataframe(
        self, results: List[CrossAnalysisResult]
    ) -> pd.DataFrame:
        """Crear DataFrame con resultados combinados"""
        data = []

        for r in results:
            data.append(
                {
                    "Frecuencia (MHz)": r.frequency,
                    "Ancho (MHz)": r.bandwidth,
                    "Score AP": r.ap_score,
                    "Throughput Est. (Mbps)": r.throughput_est,
                    "Ruido AP (dBm)": round(r.ap_noise_avg, 2),
                    "SNR Estimado AP (dB)": round(r.ap_snr, 2),
                    "Peor Ruido SMs (dBm)": round(r.sm_worst_noise, 2),
                    "Peor SNR SM (dB)": round(r.sm_snr_worst, 2),
                    "SMs Vetados": r.sm_count_vetoed,
                    "Score Final": r.combined_score,
                    "Estado": "Viable" if r.is_viable else "VETADO",
                    "Detalle": r.veto_reason,
                }
            )

        df = pd.DataFrame(data)
        # Ordenar primero por viabilidad, luego por score
        if not df.empty:
            df = df.sort_values(
                ["Estado", "Score Final"], ascending=[False, False]
            ).reset_index(drop=True)

        return df

    def get_best_combined_frequency(
        self, results: List[CrossAnalysisResult]
    ) -> Optional[CrossAnalysisResult]:
        """Obtener la mejor frecuencia considerando AP y SMs"""
        if not results:
            return None

        viable_results = [r for r in results if r.is_viable]

        if viable_results:
            # Ordenar por score combinado
            viable_results.sort(key=lambda x: x.combined_score, reverse=True)
            # Regla de Estabilidad: Si el top 1 es 20MHz y el top 2 es 10MHz
            # y tienen scores similares (<10% dif), preferir 10MHz?
            # Por simplicidad y robustez del score (que ya incluye penalizaciones), confiamos en el score.
            best = viable_results[0]
            logger.info(
                f"Mejor candidata VIABLE: {best.frequency} MHz / {best.bandwidth} MHz (Score: {best.combined_score})"
            )
            return best

        # FALLBACK
        logger.warning("Modo Fallback: Sin frecuencias viables.")
        results.sort(key=lambda x: x.combined_score, reverse=True)
        best_fallback = results[0]
        return best_fallback


def analyze_ap_and_sms(
    ap_ip: str,
    sm_ips: List[str],
    ap_xml: str,
    sm_xmls: Dict[str, str],
    band_3ghz_min: int = FrequencyAnalyzer.BAND_3GHZ_MIN,
    band_3ghz_max: int = FrequencyAnalyzer.BAND_3GHZ_MAX,
) -> Dict:
    """
    Función helper para análisis completo AP-SM MULTIBANDA

    Args:
        band_3ghz_min: Límite inferior banda 3GHz en MHz (default 3300).
        band_3ghz_max: Límite superior banda 3GHz en MHz (default 3987).
    """
    band_config = {"band_3ghz_min": band_3ghz_min, "band_3ghz_max": band_3ghz_max}
    analyzer = APSMCrossAnalyzer(config=band_config)
    freq_analyzer = FrequencyAnalyzer(config=band_config)

    # Parsear espectro del AP
    ap_spectrum = freq_analyzer.parse_spectrum_xml(ap_xml)

    if not ap_spectrum:
        return {"error": "Error parseando espectro del AP", "ap_ip": ap_ip}

    # Parsear espectro de SMs
    sm_data = []
    for sm_ip in sm_ips:
        if sm_ip not in sm_xmls:
            continue
        sm_spectrum = freq_analyzer.parse_spectrum_xml(sm_xmls[sm_ip])
        if sm_spectrum:
            sm_data.append(SMSpectrumData(ip=sm_ip, spectrum_points=sm_spectrum))

    if not sm_data:
        # Fallback a análisis AP-ONLY (solo 20MHz por defecto o lo que sea mejor?)
        # Para mantener compatibilidad, hacemos análisis multibanda del AP solo
        logger.info("Analizando AP Multibanda (Sin SMs)...")
        results = []
        for bw in [20, 15, 10, 5]:
            df = freq_analyzer.analyze_spectrum(ap_spectrum, bandwidth=bw)
            if not df.empty:
                best = df.iloc[0].to_dict()
                best["bandwidth"] = bw
                results.append(best)

        # Ordenar por puntaje
        results.sort(key=lambda x: x["Puntaje Final"], reverse=True)
        best_overall = results[0] if results else None

        return {
            "ap_ip": ap_ip,
            "analysis_mode": "AP_ONLY_MULTIBAND",
            "best_frequency": best_overall,
        }

    # Análisis cruzado multibanda
    df_combined, cross_results = analyzer.analyze_multiband_ap_with_sms(
        ap_spectrum,
        sm_data,
        top_n=20,  # Increase to 20 per BW (Total ~80 candidates potentially)
    )

    best_combined = analyzer.get_best_combined_frequency(cross_results)

    return {
        "ap_ip": ap_ip,
        "sm_count": len(sm_data),
        "analysis_mode": "AP_SM_CROSS_MULTIBAND",
        "combined_ranking": df_combined.to_dict("records")[
            :50
        ],  # Retornar hasta 50 resultados para dar opciones al frontend
        "best_combined_frequency": {
            "frequency": best_combined.frequency,
            "bandwidth": best_combined.bandwidth,
            "throughput_est": best_combined.throughput_est,
            "ap_score": best_combined.ap_score,
            "combined_score": best_combined.combined_score,
            "sm_worst_noise": best_combined.sm_worst_noise,
            "is_viable": best_combined.is_viable,
            "veto_reason": best_combined.veto_reason,
        }
        if best_combined
        else None,
    }
