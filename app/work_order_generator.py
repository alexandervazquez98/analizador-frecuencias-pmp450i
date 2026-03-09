"""
Módulo para Generar Órdenes de Trabajo de Ingeniería
Transforma resultados técnicos en instrucciones claras para técnicos de campo
"""

import datetime
from typing import Dict, Optional, List

class WorkOrderGenerator:
    """Generador de Órdenes de Trabajo para PMP 450i"""
    
    # Parámetros recomendados fijos para Uplink CCTV
    RECOMMENDED_CONFIG = {
        "Downlink Data": "15%",
        "Contention Slots": "4",
        "Frame Period": "2.5 ms",
        "Max Range": "Validar con distancia real + 10%"
    }
    
    def generate_work_order(self, analysis_result: Dict) -> Dict:
        """
        Generar objeto de Orden de Trabajo desde resultados de análisis
        
        Args:
            analysis_result: Diccionario retornado por analyze_ap_and_sms
            
        Returns:
            Diccionario con estructura de Orden de Trabajo
        """
        ap_ip = analysis_result.get('ap_ip', 'Unknown')
        best = analysis_result.get('best_combined_frequency')
        
        work_order_id = f"WO-{datetime.datetime.now().strftime('%Y%m%d-%H%M')}-{ap_ip.split('.')[-1]}"
        
        if not best:
            return {
                "id": work_order_id,
                "ap_ip": ap_ip,
                "status": "FAILED",
                "message": "No se encontró ninguna frecuencia viable para operar."
            }
            
        # Extraer datos clave
        freq = best.get('frequency')
        bw = best.get('bandwidth')
        throughput = best.get('throughput_est', 0)
        score = best.get('combined_score', 0)
        is_viable = best.get('is_viable', False)
        veto_reason = best.get('veto_reason', '')
        
        # Generar explicación técnica
        explanation = self._generate_explanation(best)
        
        # Construir objeto de orden
        work_order = {
            "id": work_order_id,
            "created_at": datetime.datetime.now().isoformat(),
            "target_ap": ap_ip,
            "action": "CONFIGURAR_RADIO",
            "priority": "ALTA",
            "status": "PENDING_PUP", # Pending Pop Up acknowledgment
            
            "configuration": {
                "Center Frequency": f"{freq:.1f} MHz",
                "Channel Bandwidth": f"{bw} MHz",
                **self.RECOMMENDED_CONFIG
            },
            
            "performance_prediction": {
                "Estimated Uplink Throughput": f"{throughput} Mbps",
                "System Score": f"{score:.1f}/100",
                "Viability": "OPTIMA" if is_viable else "DEGRADADA (Best Effort)"
            },
            
            "justification": explanation,
            
            "warnings": []
        }
        
        if not is_viable:
            work_order["warnings"].append(f"ATENCIÓN: Frecuencia seleccionada en modo fallback. {veto_reason}")
            
        return work_order
    
    def _generate_explanation(self, best_result: Dict) -> str:
        """Generar texto explicativo para humanos"""
        freq = best_result.get('frequency')
        bw = best_result.get('bandwidth')
        throughput = best_result.get('throughput_est', 0)
        sm_noise = best_result.get('sm_worst_noise', -100)
        ap_score = best_result.get('ap_score', 0)
        
        reason = (
            f"Se ha seleccionado la frecuencia **{freq} MHz** con un ancho de canal de **{bw} MHz** "
            f"porque ofrece el mejor balance entre estabilidad y velocidad.\n\n"
            f"- **Capacidad de Subida:** Se estiman **{throughput} Mbps** netos disponibles para cámaras.\n"
            f"- **Limpieza Espectral:** El peor ruido detectado en los clientes es {sm_noise:.1f} dBm, "
            f"lo que permite mantener una modulación estable.\n"
            f"- **Calidad RF:** El AP reporta una calidad de señal de {ap_score:.0f}/100 puntos en esta banda."
        )
        
        return reason
