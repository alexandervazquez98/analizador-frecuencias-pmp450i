"""
TDD Tests para FrequencyAnalyzer — Validación de fórmulas RF (02_specs.md § S1)

Metodología: TDD — Los tests definen el comportamiento esperado de las fórmulas
matemáticas de RF ANTES de modificar lógica en frequency_analyzer.py.

Se usan datos sintéticos (SpectrumPoint) para aislar cálculos de I/O real.
"""

import pytest
import numpy as np
from app.frequency_analyzer import FrequencyAnalyzer, SpectrumPoint, FrequencyScore


# ===========================================================================
# Helpers: Fábricas de datos sintéticos
# ===========================================================================


def make_spectrum_points(
    freq_start: float = 5000.0,
    freq_step: float = 5.0,
    count: int = 8,
    v_max: float = -90.0,
    v_mean: float = -95.0,
    h_max: float = -90.0,
    h_mean: float = -95.0,
) -> list:
    """
    Genera una lista uniforme de SpectrumPoint para testeo.
    Por defecto: 8 puntos de 5000 a 5035 MHz con ruido bajo y simétrico.
    """
    return [
        SpectrumPoint(
            frequency=freq_start + i * freq_step,
            vertical_max=v_max,
            vertical_mean=v_mean,
            horizontal_max=h_max,
            horizontal_mean=h_mean,
        )
        for i in range(count)
    ]


def make_imbalanced_points(
    freq_start: float = 5000.0,
    count: int = 8,
    v_max: float = -85.0,
    h_max: float = -92.0,
) -> list:
    """Genera puntos con desequilibrio V/H controlado."""
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


def make_burst_noise_points(
    freq_start: float = 5000.0,
    count: int = 8,
    noise_max: float = -75.0,
    noise_mean: float = -90.0,
) -> list:
    """Genera puntos con diferencia Max-Mean significativa (burst noise)."""
    return [
        SpectrumPoint(
            frequency=freq_start + i * 5.0,
            vertical_max=noise_max,
            vertical_mean=noise_mean,
            horizontal_max=noise_max,
            horizontal_mean=noise_mean,
        )
        for i in range(count)
    ]


# ===========================================================================
# S1-A: Cálculo del Piso de Ruido (Noise Floor)
# ===========================================================================


class TestNoiseFloorCalculation:
    """
    Spec S1-A: Noise_Floor = MAX(Ruido_Max_V, Ruido_Max_H) dentro de la ventana.
    El sistema SIEMPRE toma el peor caso.
    """

    def test_symmetric_noise_floor(self):
        """
        GIVEN puntos con ruido V y H iguales a -90 dBm
        WHEN se calcula el score de frecuencia
        THEN noise_vertical y noise_horizontal son ambos -90 dBm
        """
        points = make_spectrum_points(v_max=-90.0, h_max=-90.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.noise_vertical == -90.0
        assert score.noise_horizontal == -90.0

    def test_worst_case_vertical_dominates(self):
        """
        GIVEN V más ruidoso (-80 dBm) que H (-90 dBm) dentro de la ventana
        WHEN se calcula SNR
        THEN el Noise Floor usado es -80 dBm (peor caso V)
        """
        points = make_spectrum_points(v_max=-80.0, h_max=-90.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.noise_vertical == -80.0
        assert score.noise_horizontal == -90.0
        # SNR debe usar el peor: -52 - (-80) = 28 dB
        assert score.snr_estimated == pytest.approx(28.0)

    def test_worst_case_horizontal_dominates(self):
        """
        GIVEN H más ruidoso (-78 dBm) que V (-92 dBm)
        WHEN se calcula SNR
        THEN el Noise Floor usado es -78 dBm (peor caso H)
        """
        points = make_spectrum_points(v_max=-92.0, h_max=-78.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.noise_horizontal == -78.0
        # SNR usa peor: -52 - (-78) = 26 dB
        assert score.snr_estimated == pytest.approx(26.0)

    def test_noise_floor_uses_max_within_window(self):
        """
        GIVEN puntos con ruido variable dentro de la ventana de 20 MHz
        WHEN se calcula el piso de ruido
        THEN se usa el MAX de todos los puntos dentro de la ventana
        """
        # Crear puntos con ruido que varía: un "spike" en una frecuencia
        points = make_spectrum_points(v_max=-95.0, h_max=-95.0)
        # Inyectar un spike en el tercer punto
        points[2] = SpectrumPoint(
            frequency=points[2].frequency,
            vertical_max=-70.0,  # Spike
            vertical_mean=-85.0,
            horizontal_max=-95.0,
            horizontal_mean=-95.0,
        )
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        # El noise_vertical debe capturar el spike
        assert score.noise_vertical == -70.0


# ===========================================================================
# S1-B: Estimación de SNR (Signal-to-Noise Ratio)
# ===========================================================================


class TestSNRCalculation:
    """
    Spec S1-B: SNR_Estimado = RSSI_Objetivo (-52 dBm) - Noise_Floor
    """

    def test_snr_clean_spectrum(self):
        """
        GIVEN Noise_Floor = -90 dBm (espectro muy limpio)
        WHEN se calcula SNR
        THEN SNR = -52 - (-90) = 38 dB (ejemplo literal de la spec)
        """
        points = make_spectrum_points(v_max=-90.0, h_max=-90.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.snr_estimated == pytest.approx(38.0)

    def test_snr_noisy_spectrum(self):
        """
        GIVEN Noise_Floor = -60 dBm (espectro muy ruidoso)
        WHEN se calcula SNR
        THEN SNR = -52 - (-60) = 8 dB (enlace inestable)
        """
        points = make_spectrum_points(v_max=-60.0, h_max=-60.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.snr_estimated == pytest.approx(8.0)

    def test_snr_uses_custom_target_rx(self):
        """
        GIVEN target_rx_level configurado en -55 dBm (no el default) y Noise = -90 dBm
        WHEN se calcula SNR
        THEN SNR = -55 - (-90) = 35 dB
        """
        points = make_spectrum_points(v_max=-90.0, h_max=-90.0)
        analyzer = FrequencyAnalyzer(config={"target_rx_level": -55})
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.snr_estimated == pytest.approx(35.0)

    def test_snr_negative_means_noise_above_target(self):
        """
        GIVEN Noise_Floor = -45 dBm (ruido por encima del target)
        WHEN se calcula SNR
        THEN SNR = -52 - (-45) = -7 dB (negativo = imposible establecer enlace)
        """
        points = make_spectrum_points(v_max=-45.0, h_max=-45.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.snr_estimated == pytest.approx(-7.0)


# ===========================================================================
# S1-C: Detección de Burst Noise (Interferencia Intermitente)
# ===========================================================================


class TestBurstNoiseDetection:
    """
    Spec S1-C: Si MAX(Max - Mean) > 10 dB → ADVERTENCIA de Interferencia Intermitente.
    """

    def test_no_burst_noise_when_stable(self):
        """
        GIVEN Max-Mean = 5 dB (< 10 dB umbral)
        WHEN se evalúa burst noise
        THEN high_burst_noise = False
        """
        points = make_spectrum_points(
            v_max=-90.0, v_mean=-95.0, h_max=-90.0, h_mean=-95.0
        )
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.high_burst_noise is False
        assert score.burst_noise_level == pytest.approx(5.0)

    def test_burst_noise_detected_when_high_delta(self):
        """
        GIVEN Max-Mean = 15 dB (> 10 dB umbral)
        WHEN se evalúa burst noise
        THEN high_burst_noise = True y burst_noise_level = 15.0
        """
        points = make_burst_noise_points(noise_max=-75.0, noise_mean=-90.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.high_burst_noise is True
        assert score.burst_noise_level == pytest.approx(15.0)

    def test_burst_noise_exactly_at_threshold(self):
        """
        GIVEN Max-Mean = 10 dB (exactamente en el umbral)
        WHEN se evalúa burst noise
        THEN high_burst_noise = False (umbral es estricto: > 10, no >=)
        """
        points = make_spectrum_points(
            v_max=-80.0, v_mean=-90.0, h_max=-80.0, h_mean=-90.0
        )
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.high_burst_noise is False
        assert score.burst_noise_level == pytest.approx(10.0)

    def test_burst_noise_asymmetric_polarities(self):
        """
        GIVEN V tiene Max-Mean=5 dB pero H tiene Max-Mean=12 dB
        WHEN se evalúa burst noise
        THEN high_burst_noise = True (basta un canal con burst)
        """
        points = [
            SpectrumPoint(
                frequency=5000.0 + i * 5.0,
                vertical_max=-90.0,
                vertical_mean=-95.0,  # V delta = 5 dB
                horizontal_max=-78.0,
                horizontal_mean=-90.0,  # H delta = 12 dB
            )
            for i in range(8)
        ]
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.high_burst_noise is True
        assert score.burst_noise_level == pytest.approx(12.0)


# ===========================================================================
# S1-D: Chain Imbalance (Desequilibrio de Polaridades)
# ===========================================================================


class TestChainImbalance:
    """
    Spec S1-D: Si ABS(Ruido_Max_V - Ruido_Max_H) > 5 dB → penalización -50 pts
    y degradación a MIMO-A (1x1).
    """

    def test_balanced_chains_no_penalty(self):
        """
        GIVEN V=-90 dBm, H=-90 dBm (imbalance = 0 dB)
        WHEN se calcula el score
        THEN imbalance_penalty = 0, mimo_mode = MIMO-B (2x2)
        """
        points = make_spectrum_points(v_max=-90.0, h_max=-90.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.chain_imbalance == pytest.approx(0.0)
        assert score.imbalance_penalty == 0
        assert score.mimo_mode == "MIMO-B (2x2)"

    def test_imbalance_within_threshold_no_penalty(self):
        """
        GIVEN V=-88 dBm, H=-92 dBm (imbalance = 4 dB, < 5 dB)
        WHEN se calcula el score
        THEN imbalance_penalty = 0, mimo_mode = MIMO-B (2x2)
        """
        points = make_spectrum_points(v_max=-88.0, h_max=-92.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.chain_imbalance == pytest.approx(4.0)
        assert score.imbalance_penalty == 0
        assert score.mimo_mode == "MIMO-B (2x2)"

    def test_imbalance_exactly_5db_no_penalty(self):
        """
        GIVEN V=-85 dBm, H=-90 dBm (imbalance = 5 dB exacto)
        WHEN se calcula el score
        THEN imbalance_penalty = 0 (umbral es estricto: > 5, no >=)
        """
        points = make_spectrum_points(v_max=-85.0, h_max=-90.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.chain_imbalance == pytest.approx(5.0)
        assert score.imbalance_penalty == 0
        assert score.mimo_mode == "MIMO-B (2x2)"

    def test_imbalance_exceeds_threshold_penalty_applied(self):
        """
        GIVEN V=-85 dBm, H=-92 dBm (imbalance = 7 dB > 5 dB)
        WHEN se calcula el score
        THEN imbalance_penalty = 50 pts, mimo_mode = MIMO-A (1x1)
        """
        points = make_imbalanced_points(v_max=-85.0, h_max=-92.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.chain_imbalance == pytest.approx(7.0)
        assert score.imbalance_penalty == 50
        assert score.mimo_mode == "MIMO-A (1x1)"

    def test_imbalance_degrades_256qam_to_16qam(self):
        """
        GIVEN SNR=38 dB (normalmente 256QAM/8X) pero imbalance=7 dB
        WHEN se calcula modulación
        THEN se degrada a 16QAM (4X) [degradado]
        """
        # Noise = -90 dBm → SNR = 38 dB → 256QAM sin imbalance
        points = make_imbalanced_points(
            v_max=-90.0, h_max=-83.0
        )  # imbalance=7dB, worst=-83
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        # SNR based on worst noise (-83): -52 - (-83) = 31 dB → 64QAM tier
        # But imbalance > 5 degrades: 64QAM → QPSK-3/4 (3X) [degradado]
        assert "degradado" in score.modulation
        assert score.mimo_mode == "MIMO-A (1x1)"

    def test_imbalance_penalty_in_final_score(self):
        """
        GIVEN frecuencia con imbalance > 5 dB
        WHEN se calcula final_score
        THEN final_score incluye la deducción de -50 pts por imbalance
        """
        points = make_imbalanced_points(v_max=-85.0, h_max=-92.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        # final_score = base_score + contiguity_bonus + bw_bonus - imbalance_penalty
        expected_final = score.base_score + score.contiguity_bonus - 50
        # Add bandwidth bonus (20MHz → 0)
        assert score.final_score == max(0, expected_final)

    def test_channel_validity_requires_no_imbalance(self):
        """
        GIVEN frecuencia con SNR adecuado pero imbalance > 5 dB
        WHEN se evalúa is_valid
        THEN is_valid = False (requires chain_imbalance <= MAX_CHAIN_IMBALANCE)
        """
        points = make_imbalanced_points(v_max=-85.0, h_max=-92.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.is_valid is False


# ===========================================================================
# Modulación: Clasificación por SNR
# ===========================================================================


class TestModulationScoring:
    """
    Validar los umbrales de modulación y scoring definidos en la spec.
    Umbrales: 32 dB → 256QAM (100pts), 24 dB → 64QAM (75pts),
              17 dB → 16QAM (50pts), 10 dB → QPSK (25pts), <10 → Inestable (0pts)
    """

    @pytest.mark.parametrize(
        "noise_floor, expected_snr, expected_mod, expected_score",
        [
            (-90.0, 38.0, "256QAM (8X)", 100),  # SNR=38 ≥ 32 → 256QAM
            (-84.0, 32.0, "256QAM (8X)", 100),  # SNR=32 (exactamente en umbral)
            (-80.0, 28.0, "64QAM (6X)", 75),  # 24 ≤ SNR=28 < 32 → 64QAM
            (-76.0, 24.0, "64QAM (6X)", 75),  # SNR=24 (exactamente en umbral)
            (-72.0, 20.0, "16QAM (4X)", 50),  # 17 ≤ SNR=20 < 24 → 16QAM
            (-69.0, 17.0, "16QAM (4X)", 50),  # SNR=17 (exactamente en umbral)
            (-65.0, 13.0, "QPSK (2X)", 25),  # 10 ≤ SNR=13 < 17 → QPSK
            (-62.0, 10.0, "QPSK (2X)", 25),  # SNR=10 (exactamente en umbral)
            (-60.0, 8.0, "Inestable", 0),  # SNR=8 < 10 → Inestable
            (-52.0, 0.0, "Inestable", 0),  # SNR=0 (ruido = target)
        ],
    )
    def test_modulation_tiers(
        self, noise_floor, expected_snr, expected_mod, expected_score
    ):
        """
        GIVEN un Noise Floor determinado (con V y H iguales, sin imbalance)
        WHEN se calcula SNR y modulación
        THEN se asigna la modulación y score correcto según los umbrales
        """
        points = make_spectrum_points(v_max=noise_floor, h_max=noise_floor)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.snr_estimated == pytest.approx(expected_snr)
        assert score.modulation == expected_mod
        assert score.base_score == expected_score


# ===========================================================================
# Contiguity Bonus (Espectro Limpio)
# ===========================================================================


class TestContiguityBonus:
    """
    Si la desviación estándar del ruido dentro de la ventana es < 3 dB,
    se otorga un bonus de +10 puntos.
    """

    def test_uniform_spectrum_gets_bonus(self):
        """
        GIVEN puntos con ruido totalmente uniforme (std_dev = 0)
        WHEN se calcula contiguity_bonus
        THEN bonus = 10 pts e is_contiguous = True
        """
        points = make_spectrum_points(v_max=-90.0, h_max=-90.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.is_contiguous == True  # noqa: E712 (numpy bool)
        assert score.contiguity_bonus == 10

    def test_variable_spectrum_no_bonus(self):
        """
        GIVEN puntos con ruido variable (alto std_dev > 3 dB)
        WHEN se calcula contiguity_bonus
        THEN bonus = 0 pts e is_contiguous = False
        """
        # Crear puntos con gran variación de ruido
        points = []
        for i in range(8):
            # Alternar entre -70 y -95 para crear alta variación
            noise = -70.0 if i % 2 == 0 else -95.0
            points.append(
                SpectrumPoint(
                    frequency=5000.0 + i * 5.0,
                    vertical_max=noise,
                    vertical_mean=noise - 5.0,
                    horizontal_max=noise,
                    horizontal_mean=noise - 5.0,
                )
            )
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.is_contiguous == False  # noqa: E712 (numpy bool)
        assert score.contiguity_bonus == 0


# ===========================================================================
# XML Parsing
# ===========================================================================


class TestXMLParsing:
    """Validar parseo de XML de espectro de Cambium PMP 450i."""

    SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
    <SpectrumAnalysis>
        <Freq f="5000.000 V" instant="-85" avg="-90" max="-80" />
        <Freq f="5000.000 H" instant="-87" avg="-92" max="-82" />
        <Freq f="5005.000 V" instant="-86" avg="-91" max="-81" />
        <Freq f="5005.000 H" instant="-88" avg="-93" max="-83" />
        <Freq f="5010.000 V" instant="-84" avg="-89" max="-79" />
        <Freq f="5010.000 H" instant="-86" avg="-91" max="-81" />
    </SpectrumAnalysis>
    """

    def test_parses_correct_number_of_points(self):
        """
        GIVEN XML con 3 frecuencias (6 entradas V+H)
        WHEN se parsea
        THEN se generan 3 SpectrumPoint (agrupados por frecuencia)
        """
        analyzer = FrequencyAnalyzer()
        points = analyzer.parse_spectrum_xml(self.SAMPLE_XML)

        assert len(points) == 3

    def test_parses_frequencies_correctly(self):
        """
        GIVEN XML con frecuencias 5000, 5005, 5010
        WHEN se parsea
        THEN los puntos contienen las frecuencias correctas y están ordenados
        """
        analyzer = FrequencyAnalyzer()
        points = analyzer.parse_spectrum_xml(self.SAMPLE_XML)

        freqs = [p.frequency for p in points]
        assert freqs == [5000.0, 5005.0, 5010.0]

    def test_parses_vertical_horizontal_correctly(self):
        """
        GIVEN XML con V max=-80, H max=-82 para 5000 MHz
        WHEN se parsea
        THEN vertical_max=-80, horizontal_max=-82
        """
        analyzer = FrequencyAnalyzer()
        points = analyzer.parse_spectrum_xml(self.SAMPLE_XML)

        first = points[0]
        assert first.vertical_max == -80.0
        assert first.horizontal_max == -82.0
        assert first.vertical_mean == -90.0
        assert first.horizontal_mean == -92.0

    def test_empty_xml_returns_empty(self):
        """
        GIVEN XML vacío o malformado
        WHEN se parsea
        THEN retorna lista vacía sin crash
        """
        analyzer = FrequencyAnalyzer()
        points = analyzer.parse_spectrum_xml("<empty/>")
        assert points == []

    def test_invalid_xml_returns_empty(self):
        """
        GIVEN string que no es XML válido
        WHEN se parsea
        THEN retorna lista vacía sin crash
        """
        analyzer = FrequencyAnalyzer()
        points = analyzer.parse_spectrum_xml("esto no es xml")
        assert points == []


# ===========================================================================
# Empty / No Data Edge Cases
# ===========================================================================


class TestEdgeCases:
    """Comportamiento con datos ausentes o fuera de rango."""

    def test_no_points_in_window(self):
        """
        GIVEN puntos fuera del rango de la ventana solicitada
        WHEN se calcula score
        THEN retorna un FrequencyScore inválido con score=0
        """
        # Puntos en 5000-5035, pero consultamos 5200 MHz
        points = make_spectrum_points(freq_start=5000.0)
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5200.0, bandwidth=20
        )

        assert score.is_valid is False
        assert score.final_score == 0
        assert score.modulation == "N/A"

    def test_analyze_empty_spectrum_returns_empty_df(self):
        """
        GIVEN lista vacía de puntos de espectro
        WHEN se ejecuta analyze_spectrum
        THEN retorna DataFrame vacío
        """
        analyzer = FrequencyAnalyzer()
        df = analyzer.analyze_spectrum([])

        assert df.empty

    def test_final_score_never_negative(self):
        """
        GIVEN frecuencia con muchas penalizaciones
        WHEN se calcula final_score
        THEN el score es >= 0 (clamped)
        """
        # Noise -60 dBm → SNR=8 → Inestable (0 pts) + imbalance penalty
        points = make_imbalanced_points(v_max=-55.0, h_max=-62.0)  # imbalance=7dB
        analyzer = FrequencyAnalyzer()
        score = analyzer.calculate_frequency_score(
            points, center_freq=5017.5, bandwidth=20
        )

        assert score.final_score >= 0


# ===========================================================================
# Sliding Window Analysis
# ===========================================================================


class TestSlidingWindowAnalysis:
    """Validar que el análisis con ventana deslizante genera el ranking correcto."""

    def test_analyze_returns_sorted_dataframe(self):
        """
        GIVEN espectro con 20 puntos (100 MHz de rango)
        WHEN se ejecuta analyze_spectrum con ventana de 20 MHz
        THEN retorna DataFrame no vacío y ordenado descendente por Puntaje Final
        """
        points = make_spectrum_points(freq_start=5000.0, count=20)
        analyzer = FrequencyAnalyzer()
        df = analyzer.analyze_spectrum(points, bandwidth=20)

        assert not df.empty
        scores = df["Puntaje Final"].tolist()
        assert scores == sorted(scores, reverse=True)

    def test_sliding_window_step_is_5mhz(self):
        """
        GIVEN espectro suficiente para múltiples ventanas
        WHEN se ejecuta analyze_spectrum
        THEN las frecuencias centrales están separadas por el paso de 5 MHz
        """
        points = make_spectrum_points(freq_start=5000.0, count=20)
        analyzer = FrequencyAnalyzer()
        df = analyzer.analyze_spectrum(points, bandwidth=20)

        # Obtener frecuencias centrales antes de ordenar por score
        freqs_sorted_by_freq = sorted(df["Frecuencia Central (MHz)"].tolist())
        if len(freqs_sorted_by_freq) > 1:
            steps = [
                freqs_sorted_by_freq[i + 1] - freqs_sorted_by_freq[i]
                for i in range(len(freqs_sorted_by_freq) - 1)
            ]
            for step in steps:
                assert step == pytest.approx(5.0)


# ===========================================================================
# Best Frequency Selection
# ===========================================================================


class TestBestFrequencySelection:
    """Validar selección de mejor frecuencia con clasificación de calidad."""

    def test_best_frequency_has_quality_metadata(self):
        """
        GIVEN un análisis de espectro con resultados válidos
        WHEN se obtiene la mejor frecuencia
        THEN el resultado incluye quality_level, warnings y recommendations
        """
        points = make_spectrum_points(
            freq_start=5000.0, count=20, v_max=-90.0, h_max=-90.0
        )
        analyzer = FrequencyAnalyzer()
        df = analyzer.analyze_spectrum(points, bandwidth=20)
        best = analyzer.get_best_frequency(df)

        assert best is not None
        assert "quality_level" in best
        assert "warnings" in best
        assert "recommendations" in best
        assert "is_optimal" in best
        assert "requires_action" in best

    def test_clean_spectrum_quality_excellent_or_good(self):
        """
        GIVEN espectro limpio con SNR alto (>32 dB) y sin imbalance
        WHEN se clasifica la calidad
        THEN quality_level es EXCELENTE o BUENO
        """
        points = make_spectrum_points(
            freq_start=5000.0, count=20, v_max=-90.0, h_max=-90.0
        )
        analyzer = FrequencyAnalyzer()
        df = analyzer.analyze_spectrum(points, bandwidth=20)
        best = analyzer.get_best_frequency(df)

        assert best is not None
        assert best["quality_level"] in ["EXCELENTE", "BUENO"]
        assert best["is_optimal"] is True

    def test_strict_mode_returns_none_if_no_valid(self):
        """
        GIVEN espectro donde ninguna frecuencia es válida (SNR < 10 dB)
        WHEN se busca best_frequency en strict_mode
        THEN retorna None
        """
        points = make_spectrum_points(
            freq_start=5000.0, count=20, v_max=-50.0, h_max=-50.0
        )
        analyzer = FrequencyAnalyzer()
        df = analyzer.analyze_spectrum(points, bandwidth=20)
        best = analyzer.get_best_frequency(df, strict_mode=True)

        assert best is None

    def test_permissive_mode_always_returns_something(self):
        """
        GIVEN espectro donde ninguna frecuencia es válida
        WHEN se busca best_frequency en modo permisivo (default)
        THEN retorna la mejor disponible aunque no sea válida
        """
        points = make_spectrum_points(
            freq_start=5000.0, count=20, v_max=-50.0, h_max=-50.0
        )
        analyzer = FrequencyAnalyzer()
        df = analyzer.analyze_spectrum(points, bandwidth=20)
        best = analyzer.get_best_frequency(df, strict_mode=False)

        assert best is not None

    def test_empty_dataframe_returns_none(self):
        """
        GIVEN DataFrame vacío
        WHEN se busca best_frequency
        THEN retorna None
        """
        import pandas as pd

        analyzer = FrequencyAnalyzer()
        best = analyzer.get_best_frequency(pd.DataFrame())

        assert best is None
