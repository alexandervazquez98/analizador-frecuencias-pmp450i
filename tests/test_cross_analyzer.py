"""
TDD Tests para APSMCrossAnalyzer — Validación de análisis cruzado AP-SM

Metodología: TDD — Los tests definen el comportamiento esperado del cruce
de datos de espectro entre APs y Subscriber Modules.

Umbrales clave:
- SM Veto Threshold: -75 dBm (ruido por encima veta la frecuencia)
- SM Downlink Threshold: -85 dBm (riesgo de pérdida de ACK del AP)
- Veto Penalty: -50 pts por cada SM que veta
"""

from app.frequency_analyzer import SpectrumPoint
from app.cross_analyzer import APSMCrossAnalyzer, SMSpectrumData
import pytest


# ===========================================================================
# Helpers: Fábricas de datos sintéticos
# ===========================================================================


def make_ap_spectrum(
    freq_start: float = 5000.0,
    count: int = 20,
    v_max: float = -90.0,
    h_max: float = -90.0,
) -> list:
    """
    Genera espectro de AP limpio (ruido bajo, simétrico).
    20 puntos = 100 MHz de rango (5000-5095 MHz).
    """
    return [
        SpectrumPoint(
            frequency=freq_start + i * 5.0,
            vertical_max=v_max,
            vertical_mean=v_max - 5.0,
            horizontal_max=h_max,
            horizontal_mean=h_max - 5.0,
        )
        for i in range(count)
    ]


def make_sm_spectrum(
    freq_start: float = 5000.0,
    count: int = 20,
    v_max: float = -90.0,
    h_max: float = -90.0,
) -> list:
    """Genera espectro de SM con ruido controlado."""
    return [
        SpectrumPoint(
            frequency=freq_start + i * 5.0,
            vertical_max=v_max,
            vertical_mean=v_max - 5.0,
            horizontal_max=h_max,
            horizontal_mean=h_max - 5.0,
        )
        for i in range(count)
    ]


def make_sm_data(
    ip: str,
    v_max: float = -90.0,
    h_max: float = -90.0,
    freq_start: float = 5000.0,
    count: int = 20,
) -> SMSpectrumData:
    """Helper para crear un SMSpectrumData con espectro controlado."""
    return SMSpectrumData(
        ip=ip,
        spectrum_points=make_sm_spectrum(
            freq_start=freq_start,
            count=count,
            v_max=v_max,
            h_max=h_max,
        ),
    )


# ===========================================================================
# SM Veto Threshold (-75 dBm)
# ===========================================================================


class TestSMVetoThreshold:
    """
    Un SM con ruido promedio > -75 dBm VETA la frecuencia.
    noise_avg = (noise_v + noise_h) / 2
    """

    def test_sm_clean_no_veto(self):
        """
        GIVEN SM con ruido = -90 dBm (muy por debajo del umbral)
        WHEN se analiza cruzado
        THEN sm_count_vetoed = 0 y is_viable = True
        """
        ap_spectrum = make_ap_spectrum()
        sm = make_sm_data("10.0.0.1", v_max=-90.0, h_max=-90.0)

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, [sm], top_n=1, bandwidth=20
        )

        assert len(results) > 0
        best = results[0]
        assert best.sm_count_vetoed == 0
        assert best.is_viable is True

    def test_sm_noisy_triggers_veto(self):
        """
        GIVEN SM con ruido = -70 dBm (> -75 dBm umbral de veto)
        WHEN se analiza cruzado
        THEN sm_count_vetoed = 1 y is_viable = False
        """
        ap_spectrum = make_ap_spectrum()
        sm = make_sm_data("10.0.0.1", v_max=-70.0, h_max=-70.0)

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, [sm], top_n=1, bandwidth=20
        )

        assert len(results) > 0
        best = results[0]
        assert best.sm_count_vetoed == 1
        assert best.is_viable is False

    def test_sm_at_exactly_threshold_no_veto(self):
        """
        GIVEN SM con ruido = -75 dBm (exactamente en el umbral)
        WHEN se analiza cruzado
        THEN NO se veta (umbral es estricto: > -75, no >=)
        """
        ap_spectrum = make_ap_spectrum()
        sm = make_sm_data("10.0.0.1", v_max=-75.0, h_max=-75.0)

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, [sm], top_n=1, bandwidth=20
        )

        assert len(results) > 0
        best = results[0]
        assert best.sm_count_vetoed == 0

    def test_multiple_sms_partial_veto(self):
        """
        GIVEN 3 SMs: uno limpio (-90dBm), uno ruidoso (-70dBm), uno límite (-80dBm)
        WHEN se analiza cruzado
        THEN sm_count_vetoed = 1 (solo el de -70 dBm veta)
        """
        ap_spectrum = make_ap_spectrum()
        sms = [
            make_sm_data("10.0.0.1", v_max=-90.0, h_max=-90.0),  # limpio
            make_sm_data("10.0.0.2", v_max=-70.0, h_max=-70.0),  # ruidoso → veto
            make_sm_data("10.0.0.3", v_max=-80.0, h_max=-80.0),  # moderado, no veta
        ]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        assert len(results) > 0
        best = results[0]
        assert best.sm_count_vetoed == 1

    def test_all_sms_veto(self):
        """
        GIVEN 2 SMs ambos con ruido > -75 dBm
        WHEN se analiza cruzado
        THEN sm_count_vetoed = 2 y is_viable = False
        """
        ap_spectrum = make_ap_spectrum()
        sms = [
            make_sm_data("10.0.0.1", v_max=-65.0, h_max=-65.0),
            make_sm_data("10.0.0.2", v_max=-60.0, h_max=-60.0),
        ]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        assert len(results) > 0
        best = results[0]
        assert best.sm_count_vetoed == 2
        assert best.is_viable is False


# ===========================================================================
# Combined Score Calculation
# ===========================================================================


class TestCombinedScore:
    """
    El score combinado integra el score del AP con datos de ruido de SMs.
    Veto Penalty = -50 pts por SM que veta.
    """

    def test_clean_sms_combined_score_positive(self):
        """
        GIVEN AP con buen score y SMs limpios
        WHEN se calcula combined_score
        THEN combined_score > 0
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sms = [make_sm_data("10.0.0.1", v_max=-95.0, h_max=-95.0)]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        assert results[0].combined_score > 0

    def test_veto_reduces_combined_score(self):
        """
        GIVEN un SM que veta la frecuencia
        WHEN se calcula combined_score
        THEN el score se reduce por VETO_PENALTY (-50 pts)
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sm_noisy = make_sm_data("10.0.0.1", v_max=-65.0, h_max=-65.0)

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, [sm_noisy], top_n=1, bandwidth=20
        )

        result = results[0]
        # With one SM vetoing, combined_score = ap_score + VETO_PENALTY
        assert result.combined_score <= result.ap_score  # penalized

    def test_multiple_vetos_stack_penalty(self):
        """
        GIVEN 2 SMs que vetan
        WHEN se calcula combined_score
        THEN la penalización se aplica por cada SM (2 * -50 = -100 pts)
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sms = [
            make_sm_data("10.0.0.1", v_max=-60.0, h_max=-60.0),
            make_sm_data("10.0.0.2", v_max=-60.0, h_max=-60.0),
        ]

        analyzer = APSMCrossAnalyzer()
        _, results_2veto = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        # Compare with single veto
        _, results_1veto = analyzer.analyze_ap_with_sms(
            ap_spectrum, [sms[0]], top_n=1, bandwidth=20
        )

        # 2 vetos should score less than 1 veto
        assert results_2veto[0].combined_score < results_1veto[0].combined_score


# ===========================================================================
# Quality Level Classification
# ===========================================================================


class TestQualityLevel:
    """El resultado cruzado incluye metadata de calidad."""

    def test_viable_high_score_is_excellent(self):
        """
        GIVEN frecuencia viable con combined_score > 70
        WHEN se clasifica quality_level
        THEN es EXCELENTE
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sms = [make_sm_data("10.0.0.1", v_max=-95.0, h_max=-95.0)]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        result = results[0]
        # With very clean spectrum, score should be high
        if result.combined_score > 70:
            assert result.quality_level == "EXCELENTE"

    def test_vetoed_frequency_not_viable(self):
        """
        GIVEN frecuencia vetada por SMs
        WHEN se clasifica
        THEN quality_level = "NO VIABLE" y is_viable = False
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sms = [make_sm_data("10.0.0.1", v_max=-60.0, h_max=-60.0)]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        result = results[0]
        assert result.quality_level == "NO VIABLE"
        assert result.is_viable is False
        assert result.requires_action is True

    def test_result_has_complete_metadata(self):
        """
        GIVEN análisis cruzado completado
        WHEN se revisa el resultado
        THEN contiene todos los campos de metadata esperados
        """
        ap_spectrum = make_ap_spectrum()
        sms = [make_sm_data("10.0.0.1")]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        result = results[0]
        assert hasattr(result, "quality_level")
        assert hasattr(result, "warnings")
        assert hasattr(result, "recommendations")
        assert hasattr(result, "is_optimal")
        assert hasattr(result, "requires_action")
        assert hasattr(result, "sm_details")
        assert isinstance(result.sm_details, list)


# ===========================================================================
# Best Combined Frequency Selection
# ===========================================================================


class TestBestCombinedFrequency:
    """Validar selección de la mejor frecuencia cruzada."""

    def test_selects_viable_over_vetoed(self):
        """
        GIVEN resultados con una frecuencia viable y una vetada
        WHEN se selecciona la mejor
        THEN se elige la viable
        """
        ap_spectrum = make_ap_spectrum()
        sms = [make_sm_data("10.0.0.1", v_max=-90.0, h_max=-90.0)]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=5, bandwidth=20
        )

        best = analyzer.get_best_combined_frequency(results)
        assert best is not None
        assert best.is_viable is True

    def test_returns_fallback_when_all_vetoed(self):
        """
        GIVEN TODAS las frecuencias están vetadas
        WHEN se selecciona la mejor
        THEN retorna la menos mala (fallback)
        """
        ap_spectrum = make_ap_spectrum()
        sms = [make_sm_data("10.0.0.1", v_max=-60.0, h_max=-60.0)]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=3, bandwidth=20
        )

        # All should be vetoed since SM noise is uniform and high
        best = analyzer.get_best_combined_frequency(results)
        assert best is not None  # Fallback mode always returns something

    def test_returns_none_for_empty_results(self):
        """
        GIVEN lista vacía de resultados
        WHEN se busca la mejor frecuencia
        THEN retorna None
        """
        analyzer = APSMCrossAnalyzer()
        best = analyzer.get_best_combined_frequency([])
        assert best is None


# ===========================================================================
# SM Detail Records
# ===========================================================================


class TestSMDetails:
    """Validar que los detalles por SM se registran correctamente."""

    def test_sm_details_contain_ip_and_noise(self):
        """
        GIVEN análisis cruzado con 2 SMs
        WHEN se revisan sm_details del resultado
        THEN cada entrada tiene ip, noise_avg, vetoed, reason
        """
        ap_spectrum = make_ap_spectrum()
        sms = [
            make_sm_data("10.0.0.1", v_max=-90.0, h_max=-90.0),
            make_sm_data("10.0.0.2", v_max=-85.0, h_max=-85.0),
        ]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        details = results[0].sm_details
        assert len(details) == 2
        for d in details:
            assert "ip" in d
            assert "noise_avg" in d
            assert "vetoed" in d
            assert "reason" in d

    def test_vetoed_sm_has_veto_reason(self):
        """
        GIVEN un SM con ruido > -75 dBm que veta
        WHEN se revisan sm_details
        THEN su campo vetoed=True y reason contiene "VETO"
        """
        ap_spectrum = make_ap_spectrum()
        sms = [make_sm_data("10.0.0.1", v_max=-65.0, h_max=-65.0)]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        details = results[0].sm_details
        assert len(details) == 1
        assert details[0]["vetoed"] is True
        assert "VETO" in details[0]["reason"]

    def test_clean_sm_has_ok_reason(self):
        """
        GIVEN un SM con ruido limpio (< -85 dBm)
        WHEN se revisan sm_details
        THEN vetoed=False y reason='OK'
        """
        ap_spectrum = make_ap_spectrum()
        sms = [make_sm_data("10.0.0.1", v_max=-95.0, h_max=-95.0)]

        analyzer = APSMCrossAnalyzer()
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        details = results[0].sm_details
        assert len(details) == 1
        assert details[0]["vetoed"] is False
        assert details[0]["reason"] == "OK"


# ===========================================================================
# Multiband Analysis
# ===========================================================================


class TestMultibandAnalysis:
    """Validar análisis multibanda (20, 15, 10, 5 MHz)."""

    def test_multiband_returns_results_for_multiple_bandwidths(self):
        """
        GIVEN espectro suficiente para análisis multibanda
        WHEN se ejecuta analyze_multiband_ap_with_sms
        THEN se obtienen resultados para múltiples anchos de banda
        """
        ap_spectrum = make_ap_spectrum(count=30)  # 150 MHz de rango
        sms = [make_sm_data("10.0.0.1", count=30)]

        analyzer = APSMCrossAnalyzer()
        df, results = analyzer.analyze_multiband_ap_with_sms(ap_spectrum, sms, top_n=2)

        # Debe haber resultados para al menos 2 anchos de banda distintos
        bandwidths_seen = set(r.bandwidth for r in results)
        assert len(bandwidths_seen) >= 2

    def test_combined_dataframe_has_expected_columns(self):
        """
        GIVEN resultados del análisis cruzado
        WHEN se genera el DataFrame combinado
        THEN tiene las columnas esperadas por el frontend
        """
        ap_spectrum = make_ap_spectrum()
        sms = [make_sm_data("10.0.0.1")]

        analyzer = APSMCrossAnalyzer()
        df, _ = analyzer.analyze_ap_with_sms(ap_spectrum, sms, top_n=1, bandwidth=20)

        expected_cols = [
            "Frecuencia (MHz)",
            "Ancho (MHz)",
            "Canal Bajo (MHz)",
            "Canal Alto (MHz)",
            "Score AP",
            "Throughput Est. (Mbps)",
            "Capacidad Requerida (Mbps)",
            "Capacidad Estimada (Mbps)",
            "Margen Capacidad (Mbps)",
            "Capacidad OK",
            "Score Final",
            "Estado",
        ]
        for col in expected_cols:
            assert col in df.columns, f"Columna faltante: {col}"

    def test_centered_window_evidence_for_ap_and_sm_details(self):
        """
        GIVEN a 3010 MHz / 20 MHz candidate
        THEN AP/SM evidence exposes the analyzed 3000-3020 MHz channel window.
        """
        ap_spectrum = make_ap_spectrum(freq_start=3000.0, count=5)
        sms = [make_sm_data("10.0.0.1", freq_start=3000.0, count=5)]

        analyzer = APSMCrossAnalyzer()
        df, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20
        )

        best = results[0]
        assert best.frequency == 3010.0
        assert df.iloc[0]["Canal Bajo (MHz)"] == 3000.0
        assert df.iloc[0]["Canal Alto (MHz)"] == 3020.0
        assert best.sm_details[0]["channel_low_mhz"] == 3000.0
        assert best.sm_details[0]["channel_high_mhz"] == 3020.0

    def test_capacity_demand_rejects_narrower_clean_channel(self):
        """
        GIVEN clean 15/20 MHz candidates but a 55 Mbps sector demand
        THEN 15 MHz is marked not viable and best selection chooses 20 MHz.
        """
        ap_spectrum = make_ap_spectrum(count=30, v_max=-90.0, h_max=-90.0)
        sms = [make_sm_data("10.0.0.1", count=30, v_max=-90.0, h_max=-90.0)]

        analyzer = APSMCrossAnalyzer(config={"min_sector_throughput_mbps": 55})
        _, results = analyzer.analyze_multiband_ap_with_sms(
            ap_spectrum, sms, top_n=1, min_channel_width=15, target_rx_level=-52
        )

        by_width = {r.bandwidth: r for r in results}
        assert by_width[15].capacity_ok is False
        assert by_width[15].is_viable is False
        assert by_width[20].capacity_ok is True
        assert by_width[20].is_viable is True
        best = analyzer.get_best_combined_frequency(results)
        assert best is not None
        assert best.bandwidth == 20

    def test_missing_sm_window_fails_capacity_gate(self):
        """
        GIVEN AP capacity is high but one SM has no data in the centered window
        THEN the bottleneck capacity is 0 and demand is not satisfied.
        """
        ap_spectrum = make_ap_spectrum(freq_start=5000.0, count=20)
        sms = [make_sm_data("10.0.0.1", freq_start=6000.0, count=20)]

        analyzer = APSMCrossAnalyzer(config={"min_sector_throughput_mbps": 10})
        _, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sms, top_n=1, bandwidth=20, target_rx_level=-52
        )

        result = results[0]
        assert result.capacity_estimated_mbps == 0.0
        assert result.capacity_ok is False
        assert result.is_viable is False
        assert result.sm_details[0]["capacity_estimated_mbps"] == 0.0
        assert result.sm_details[0]["reason"] == "Sin datos en ventana de canal"


class TestBandFilter:
    """Regresión: APSMCrossAnalyzer debe respetar el filtro de banda 3GHz."""

    def test_3ghz_band_filter_excludes_unsupported_candidates_above_3900(self):
        """
        GIVEN un AP de 3 GHz con espectro que incluye puntos en 4000+ MHz
        WHEN se analiza con config band_3ghz_min=3300 band_3ghz_max=3900
        THEN ningún candidato recomendado supera 3900 MHz.

        Regresión: bug donde APSMCrossAnalyzer creaba FrequencyAnalyzer() sin config,
        ignorando el filtro de banda y generando candidatos en 4100+ MHz para equipos 3GHz.
        """
        # Espectro AP que cruza la frontera 3/4 GHz (3500-4200 MHz)
        ap_spectrum = [
            SpectrumPoint(
                frequency=3500.0 + i * 25.0,
                vertical_max=-90.0,
                vertical_mean=-95.0,
                horizontal_max=-90.0,
                horizontal_mean=-95.0,
            )
            for i in range(30)  # 3500, 3525, ... 4225 MHz
        ]

        sm_spectrum = [
            SpectrumPoint(
                frequency=3500.0 + i * 25.0,
                vertical_max=-90.0,
                vertical_mean=-95.0,
                horizontal_max=-90.0,
                horizontal_mean=-95.0,
            )
            for i in range(30)
        ]

        analyzer = APSMCrossAnalyzer(
            config={"band_3ghz_min": 3300, "band_3ghz_max": 3900}
        )
        sm_data = [SMSpectrumData(ip="10.0.0.1", spectrum_points=sm_spectrum)]

        df, results = analyzer.analyze_multiband_ap_with_sms(
            ap_spectrum, sm_data, top_n=20, min_channel_width=15
        )

        for r in results:
            assert r.frequency <= 3900.0, (
                f"Candidato fuera de banda 3GHz: {r.frequency} MHz — "
                f"el filtro band_3ghz_max=3900 no fue aplicado por el cross analyzer"
            )

    def test_3ghz_band_filter_is_channel_edge_aware(self):
        """
        GIVEN a 3 GHz band profile capped at 3900 MHz
        WHEN 20 MHz channels are evaluated near the upper edge
        THEN 3890/20 is allowed but 3895/20 is excluded because it reaches 3905 MHz.
        """
        ap_spectrum = [
            SpectrumPoint(
                frequency=3300.0 + i * 5.0,
                vertical_max=-90.0,
                vertical_mean=-95.0,
                horizontal_max=-90.0,
                horizontal_mean=-95.0,
            )
            for i in range(121)  # 3300..3900 MHz
        ]
        sm_spectrum = [
            SpectrumPoint(
                frequency=3300.0 + i * 5.0,
                vertical_max=-90.0,
                vertical_mean=-95.0,
                horizontal_max=-90.0,
                horizontal_mean=-95.0,
            )
            for i in range(121)
        ]

        analyzer = APSMCrossAnalyzer(
            config={"band_3ghz_min": 3300, "band_3ghz_max": 3900}
        )
        sm_data = [SMSpectrumData(ip="10.0.0.1", spectrum_points=sm_spectrum)]

        df, results = analyzer.analyze_ap_with_sms(
            ap_spectrum, sm_data, top_n=500, bandwidth=20
        )

        frequencies = {r.frequency for r in results}
        assert 3890.0 in frequencies
        assert 3895.0 not in frequencies
        assert all(r.frequency + (r.bandwidth / 2) <= 3900.0 for r in results)
        assert "Canal Alto (MHz)" in df.columns
        assert df["Canal Alto (MHz)"].max() <= 3900.0


# ===========================================================================
# Scenario Tiers (feat-sm-sacrifice)
# ===========================================================================


class TestScenarioTiers:
    """Scenario-based SM sacrifice: 6-tier quality level system."""

    def test_legacy_scenario_binary_veto(self):
        """
        GIVEN 3 SMs: one clean, one vetoed (noise=-70), one borderline
        WHEN scenario=LEGACY and noise_avg > -75 threshold
        THEN sm_count_vetoed = 1, is_viable = False
        """
        ap_spectrum = make_ap_spectrum()
        sms = [
            make_sm_data("10.0.0.1", v_max=-90.0, h_max=-90.0),  # clean
            make_sm_data("10.0.0.2", v_max=-70.0, h_max=-70.0),  # veto
            make_sm_data("10.0.0.3", v_max=-80.0, h_max=-80.0),  # borderline
        ]
        analyzer = APSMCrossAnalyzer(config={"scenario": "LEGACY"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, sms, top_n=1, bandwidth=20)
        best = results[0]
        assert best.sm_count_vetoed == 1
        assert best.is_viable is False
        assert best.scenario == "LEGACY"

    def test_cctv_within_threshold_sacrificable(self):
        """
        GIVEN 10 SMs, 2 vetoed (sacrifice_ratio=0.20), scenario=CCTV (20% max)
        WHEN analyzed
        THEN quality_level = SACRIFICABLE (within 20% threshold)
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        # 8 clean + 2 noisy (vetoed by SNR model)
        sms = [make_sm_data(f"10.0.0.{i}", v_max=-95.0, h_max=-95.0) for i in range(8)]
        sms.extend([make_sm_data(f"10.0.0.{i}", v_max=-60.0, h_max=-60.0) for i in range(8, 10)])
        analyzer = APSMCrossAnalyzer(config={"scenario": "CCTV"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, sms, top_n=1, bandwidth=20)
        best = results[0]
        assert best.sm_count_viable == 8
        assert best.sm_count_sacrificed == 2
        assert best.sacrifice_ratio == 0.2
        assert best.quality_level == "SACRIFICABLE"
        assert best.is_viable is True

    def test_cctv_exceeds_threshold_no_viable(self):
        """
        GIVEN 10 SMs, 4 vetoed (sacrifice_ratio=0.40), scenario=CCTV (20% max)
        WHEN analyzed
        THEN quality_level = NO VIABLE
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sms = [make_sm_data(f"10.0.0.{i}", v_max=-95.0, h_max=-95.0) for i in range(6)]
        sms.extend([make_sm_data(f"10.0.0.{i}", v_max=-60.0, h_max=-60.0) for i in range(6, 10)])
        analyzer = APSMCrossAnalyzer(config={"scenario": "CCTV"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, sms, top_n=1, bandwidth=20)
        best = results[0]
        assert best.sacrifice_ratio == 0.4
        assert best.quality_level == "NO VIABLE"
        assert best.is_viable is False

    def test_all_sms_viable_excellent(self):
        """
        GIVEN all 8 SMs clean, scenario=BALANCED
        WHEN analyzed with high combined_score
        THEN quality_level = EXCELENTE
        """
        ap_spectrum = make_ap_spectrum(v_max=-95.0, h_max=-95.0)
        sms = [make_sm_data(f"10.0.0.{i}", v_max=-95.0, h_max=-95.0) for i in range(8)]
        analyzer = APSMCrossAnalyzer(config={"scenario": "BALANCED"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, sms, top_n=1, bandwidth=20)
        best = results[0]
        assert best.sm_count_viable == 8
        assert best.sm_count_sacrificed == 0
        assert best.sm_count_total == 8
        assert best.quality_level == "EXCELENTE"

    def test_soft_penalty_scoring(self):
        """
        GIVEN 8 SMs, 2 vetoed, scenario=BALANCED
        WHEN combined_score is calculated
        THEN combined_score = ap_score + (VETO_PENALTY * 2) = ap_score - 100
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sms = [make_sm_data(f"10.0.0.{i}", v_max=-95.0, h_max=-95.0) for i in range(6)]
        sms.extend([make_sm_data(f"10.0.0.{i}", v_max=-60.0, h_max=-60.0) for i in range(6, 8)])
        analyzer = APSMCrossAnalyzer(config={"scenario": "BALANCED"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, sms, top_n=1, bandwidth=20)
        best = results[0]
        # combined_score = ap_score + (-50 * 2) = ap_score - 100
        assert best.combined_score == pytest.approx(best.ap_score - 100, abs=5)
        assert best.sm_count_sacrificed == 2

    def test_fallback_when_all_exceed_threshold(self):
        """
        GIVEN all SMs vetoed (sacrifice_ratio=1.0), scenario=CCTV
        WHEN get_best_combined_frequency is called
        THEN returns FALLBACK result with quality_level=FALLBACK
        """
        ap_spectrum = make_ap_spectrum(v_max=-90.0, h_max=-90.0)
        sms = [make_sm_data(f"10.0.0.{i}", v_max=-60.0, h_max=-60.0) for i in range(4)]
        analyzer = APSMCrossAnalyzer(config={"scenario": "CCTV"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, sms, top_n=3, bandwidth=20)
        best = analyzer.get_best_combined_frequency(results)
        assert best is not None
        assert best.quality_level == "FALLBACK"
        assert "FALLBACK" in best.veto_reason

    def test_scenario_field_in_result(self):
        """
        GIVEN scenario=CCTV
        WHEN analysis runs
        THEN CrossAnalysisResult has scenario=CCTV
        """
        ap_spectrum = make_ap_spectrum()
        sm = make_sm_data("10.0.0.1", v_max=-95.0, h_max=-95.0)
        analyzer = APSMCrossAnalyzer(config={"scenario": "CCTV"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, [sm], top_n=1, bandwidth=20)
        assert results[0].scenario == "CCTV"

    def test_legacy_scenario_exact_threshold_no_veto(self):
        """
        GIVEN SM with noise exactly -75 dBm, scenario=LEGACY
        WHEN noise_avg = -75.0 (not > -75.0)
        THEN sm_count_vetoed = 0 (strict inequality)
        """
        ap_spectrum = make_ap_spectrum()
        sm = make_sm_data("10.0.0.1", v_max=-75.0, h_max=-75.0)
        analyzer = APSMCrossAnalyzer(config={"scenario": "LEGACY"})
        _, results = analyzer.analyze_ap_with_sms(ap_spectrum, [sm], top_n=1, bandwidth=20)
        best = results[0]
        assert best.sm_count_vetoed == 0
        assert best.is_viable is True
