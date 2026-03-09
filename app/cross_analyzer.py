"""
Módulo para análisis cruzado entre AP y SMs
Implementa lógica de selección de frecuencia considerando ambos lados del enlace
Optimizado para tráfico Uplink (CCTV: SM → AP)
"""

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
    ap_snr: float # Added: SNR Estimado del AP
    
    # Datos de SMs
    sm_worst_noise: float  # Peor ruido entre todos los SMs
    sm_avg_noise: float    # Promedio de ruido de SMs
    sm_count_vetoed: int   # Cantidad de SMs que vetaron esta frecuencia
    
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
    
    # Umbrales críticos
    SM_VETO_THRESHOLD = -75  # dBm - Si SM tiene ruido peor que esto, vetar frecuencia
    SM_DOWNLINK_THRESHOLD = -85  # dBm - SM debe escuchar ACKs del AP (QPSK sensitivity)
    
    # Penalizaciones
    VETO_PENALTY = -50  # Puntos - Penalización por cada SM que veta (reducido de -1000)
    SM_NOISE_WEIGHT = 0.5  # Factor de peso del ruido de SMs en score combinado
    
    def __init__(self):
        self.analyzer = FrequencyAnalyzer()
        
    def analyze_multiband_ap_with_sms(
        self,
        ap_spectrum: List[SpectrumPoint],
        sm_data: List[SMSpectrumData],
        top_n: int = 5
    ) -> Tuple[pd.DataFrame, List[CrossAnalysisResult]]:
        """
        Analizar MÚLTIPLES anchos de banda (20, 15, 10, 5 MHz)
        Devuelve el ranking consolidado
        """
        all_results = []
        bandwidths = [20, 15, 10, 5]
        
        logger.info(f"Iniciando análisis multibanda para {len(sm_data)} SMs")
        
        
        for bw in bandwidths:
            logger.info(f"--- Evaluando ancho de canal: {bw} MHz ---")
            try:
                _, results = self.analyze_ap_with_sms(
                    ap_spectrum, sm_data, top_n, bandwidth=bw
                )
                logger.info(f"--- Ancho {bw} MHz: {len(results)} candidatos encontrados ---")
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
        bandwidth: int = 20
    ) -> Tuple[pd.DataFrame, List[CrossAnalysisResult]]:
        """
        Analizar frecuencias cruzando datos de AP y SMs para un ancho de banda específico
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
            freq = row['Frecuencia Central (MHz)']
            ap_score = row['Puntaje Final']
            ap_noise = row['Ruido Promedio (dBm)']
            # Capturar throughput estimado del análisis del AP si existe
            throughput = row.get('Throughput Est. (Mbps)', 0)
            
            # Capturar SNR del AP
            snr = row.get('SNR Estimado (dB)', 0)
            
            # Analizar esta frecuencia en todos los SMs
            result = self._analyze_frequency_in_sms(
                freq, 
                ap_score, 
                ap_noise,
                snr, # Pass SNR
                sm_data,
                bandwidth,
                throughput
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
        throughput_est: float
    ) -> CrossAnalysisResult:
        """
        Analizar una frecuencia específica en todos los SMs
        """
        sm_details = []
        sm_noises = []
        vetoed_count = 0
        veto_reason = ""
        
        # Ventana de frecuencia (±BW/2)
        half_bw = bandwidth / 2
        freq_min = frequency - half_bw
        freq_max = frequency + half_bw
        
        for sm in sm_data:
            # Obtener puntos de espectro en esta ventana
            window_points = [
                p for p in sm.spectrum_points
                if freq_min <= p.frequency <= freq_max
            ]
            
            if not window_points:
                sm_details.append({
                    'ip': sm.ip,
                    'noise_v': None,
                    'noise_h': None,
                    'noise_avg': None,
                    'vetoed': False,
                    'reason': 'Sin datos'
                })
                continue
            
            # Calcular ruido en el SM (usar MAX para ser conservadores)
            noise_v = max(p.vertical_max for p in window_points)
            noise_h = max(p.horizontal_max for p in window_points)
            noise_avg = (noise_v + noise_h) / 2
            
            sm_noises.append(noise_avg)
            
            # REGLA DE VETO
            is_vetoed = False
            reason = "OK"
            
            if noise_avg > self.SM_VETO_THRESHOLD:
                is_vetoed = True
                vetoed_count += 1
                reason = f"VETO: Ruido {noise_avg:.1f} dBm > {self.SM_VETO_THRESHOLD} dBm"
            elif noise_avg > self.SM_DOWNLINK_THRESHOLD:
                reason = f"ADVERTENCIA: Ruido {noise_avg:.1f} dBm afecta recepción downlink"
            
            sm_details.append({
                'ip': sm.ip,
                'noise_v': round(noise_v, 2),
                'noise_h': round(noise_h, 2),
                'noise_avg': round(noise_avg, 2),
                'vetoed': is_vetoed,
                'reason': reason
            })
        
        # Calcular métricas agregadas de SMs
        if sm_noises:
            sm_worst_noise = float(max(sm_noises))
            sm_avg_noise = float(np.mean(sm_noises))
        else:
            sm_worst_noise = 0.0
            sm_avg_noise = 0.0
        
        # CALCULAR SCORE COMBINADO
        combined_score = float(ap_score)
        is_viable = True
        warnings = []
        recommendations = []
        quality_level = "BUENO"
        
        if vetoed_count > 0:
            total_penalty = self.VETO_PENALTY * vetoed_count
            combined_score += total_penalty
            is_viable = False
            veto_reason = f"{vetoed_count} SM(s) vetaron ({total_penalty} pts)"
            quality_level = "NO VIABLE"
            warnings.append(f"Frecuencia vetada por {vetoed_count} SMs debido a alto ruido.")
            recommendations.append("Buscar otra frecuencia.")
        else:
            sm_penalty = (sm_avg_noise - (-100)) * self.SM_NOISE_WEIGHT
            combined_score -= sm_penalty
            
            if sm_worst_noise > self.SM_DOWNLINK_THRESHOLD:
                veto_reason = f"Riesgo Downlink (peor: {sm_worst_noise:.1f} dBm)"
                quality_level = "MARGINAL"
                warnings.append(f"Riesgo de interferencia en Downlink (Ruido {sm_worst_noise:.1f} dBm)")
            
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
            sm_details=sm_details
        )
    
    def _create_combined_dataframe(
        self,
        results: List[CrossAnalysisResult]
    ) -> pd.DataFrame:
        """Crear DataFrame con resultados combinados"""
        data = []
        
        for r in results:
            data.append({
                'Frecuencia (MHz)': r.frequency,
                'Ancho (MHz)': r.bandwidth,
                'Score AP': r.ap_score,
                'Throughput Est. (Mbps)': r.throughput_est, # Match Frontend Key
                'Ruido AP (dBm)': round(r.ap_noise_avg, 2),
                'SNR Estimado (dB)': round(r.ap_snr, 2), # Added SNR
                'Peor Ruido SMs': round(r.sm_worst_noise, 2),
                'SMs Vetados': r.sm_count_vetoed,
                'Score Final': r.combined_score,
                'Estado': 'Viable' if r.is_viable else 'VETADO',
                'Detalle': r.veto_reason
            })
        
        df = pd.DataFrame(data)
        # Ordenar primero por viabilidad, luego por score
        if not df.empty:
            df = df.sort_values(['Estado', 'Score Final'], ascending=[False, False]).reset_index(drop=True)
        
        return df
    
    def get_best_combined_frequency(
        self,
        results: List[CrossAnalysisResult]
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
            logger.info(f"Mejor candidata VIABLE: {best.frequency} MHz / {best.bandwidth} MHz (Score: {best.combined_score})")
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
    sm_xmls: Dict[str, str]
) -> Dict:
    """
    Función helper para análisis completo AP-SM MULTIBANDA
    """
    analyzer = APSMCrossAnalyzer()
    freq_analyzer = FrequencyAnalyzer()
    
    # Parsear espectro del AP
    ap_spectrum = freq_analyzer.parse_spectrum_xml(ap_xml)
    
    if not ap_spectrum:
        return {'error': 'Error parseando espectro del AP', 'ap_ip': ap_ip}
    
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
               best['bandwidth'] = bw
               results.append(best)
        
        # Ordenar por puntaje
        results.sort(key=lambda x: x['Puntaje Final'], reverse=True)
        best_overall = results[0] if results else None
        
        return {
            'ap_ip': ap_ip,
            'analysis_mode': 'AP_ONLY_MULTIBAND',
            'best_frequency': best_overall
        }
    
    # Análisis cruzado multibanda
    df_combined, cross_results = analyzer.analyze_multiband_ap_with_sms(
        ap_spectrum,
        sm_data,
        top_n=20 # Increase to 20 per BW (Total ~80 candidates potentially)
    )
    
    best_combined = analyzer.get_best_combined_frequency(cross_results)
    
    return {
        'ap_ip': ap_ip,
        'sm_count': len(sm_data),
        'analysis_mode': 'AP_SM_CROSS_MULTIBAND',
        'combined_ranking': df_combined.to_dict('records')[:50], # Retornar hasta 50 resultados para dar opciones al frontend
        'best_combined_frequency': {
            'frequency': best_combined.frequency,
            'bandwidth': best_combined.bandwidth,
            'throughput_est': best_combined.throughput_est,
            'ap_score': best_combined.ap_score,
            'combined_score': best_combined.combined_score,
            'sm_worst_noise': best_combined.sm_worst_noise,
            'is_viable': best_combined.is_viable,
            'veto_reason': best_combined.veto_reason
        } if best_combined else None
    }
