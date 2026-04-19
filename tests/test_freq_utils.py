"""
tests/test_freq_utils.py — Unit tests for app/freq_utils.py.

Spec: change-006 tasks Phase 5 task 5.1.
"""

import pytest
from app.freq_utils import mhz_to_khz, khz_to_mhz, format_scan_list, parse_scan_list


class TestMhzToKhz:
    """Tests for mhz_to_khz() — MHz float → kHz int."""

    def test_round_frequency(self):
        """GIVEN 5180.0 MHz THEN returns 5180000 kHz."""
        assert mhz_to_khz(5180.0) == 5180000

    def test_half_mhz_boundary(self):
        """GIVEN 5487.5 MHz (boundary case from spec) THEN returns 5487500 kHz."""
        assert mhz_to_khz(5487.5) == 5487500

    def test_fractional_mhz_rounds(self):
        """GIVEN 5180.1 MHz THEN result is an int (rounded)."""
        result = mhz_to_khz(5180.1)
        assert isinstance(result, int)

    def test_low_frequency(self):
        """GIVEN 4940.0 MHz THEN returns 4940000 kHz."""
        assert mhz_to_khz(4940.0) == 4940000

    def test_high_frequency(self):
        """GIVEN 5950.0 MHz THEN returns 5950000 kHz."""
        assert mhz_to_khz(5950.0) == 5950000

    def test_converts_int_input(self):
        """GIVEN integer 5200 THEN returns 5200000."""
        assert mhz_to_khz(5200) == 5200000


class TestKhzToMhz:
    """Tests for khz_to_mhz() — kHz int → MHz float."""

    def test_round_frequency(self):
        """GIVEN 5180000 kHz THEN returns 5180.0 MHz."""
        assert khz_to_mhz(5180000) == pytest.approx(5180.0)

    def test_half_mhz_boundary(self):
        """GIVEN 5487500 kHz THEN returns 5487.5 MHz."""
        assert khz_to_mhz(5487500) == pytest.approx(5487.5)

    def test_returns_float(self):
        """GIVEN kHz int THEN result is float."""
        result = khz_to_mhz(5180000)
        assert isinstance(result, float)

    def test_round_trip(self):
        """GIVEN mhz → khz → mhz THEN value is preserved."""
        original = 5487.5
        assert khz_to_mhz(mhz_to_khz(original)) == pytest.approx(original)


class TestFormatScanList:
    """Tests for format_scan_list() — list[int] → comma-separated string."""

    def test_single_frequency(self):
        """GIVEN [5180000] THEN returns '5180000'."""
        assert format_scan_list([5180000]) == "5180000"

    def test_multiple_frequencies(self):
        """GIVEN [5180000, 5200000, 5220000] THEN comma-separated string NO space (OID strict format)."""
        result = format_scan_list([5180000, 5200000, 5220000])
        assert result == "5180000,5200000,5220000"

    def test_empty_list(self):
        """GIVEN [] THEN returns empty string."""
        assert format_scan_list([]) == ""

    def test_single_no_separator(self):
        """GIVEN [5180000] THEN returns '5180000' (no trailing separator)."""
        assert format_scan_list([5180000]) == "5180000"


class TestParseScanList:
    """Tests for parse_scan_list() — comma-separated string → list[int]."""

    def test_single_frequency(self):
        """GIVEN '5180000' THEN returns [5180000]."""
        assert parse_scan_list("5180000") == [5180000]

    def test_multiple_frequencies(self):
        """GIVEN '5180000,5200000,5220000' THEN returns list of 3 ints."""
        result = parse_scan_list("5180000,5200000,5220000")
        assert result == [5180000, 5200000, 5220000]

    def test_empty_string(self):
        """GIVEN '' THEN returns []."""
        assert parse_scan_list("") == []

    def test_round_trip(self):
        """GIVEN list → format → parse THEN original list recovered."""
        original = [5180000, 5487500, 5950000]
        assert parse_scan_list(format_scan_list(original)) == original

    def test_strips_whitespace(self):
        """GIVEN '5180000, 5200000' with spaces THEN parses correctly."""
        result = parse_scan_list("5180000, 5200000")
        assert result == [5180000, 5200000]
