"""
tests/test_frontend_buttons.py — T55: Frontend Button Functionality Tests (change-006).

Specification: change-006 + ap-sm-autodiscovery — verify that the main index page renders
critical button IDs, panels, and that vestige/deprecated elements are absent.

Scenarios:
  1.  GET / renders 200 for authenticated user
  2.  startScanBtn is present
  3.  clearBtn is present
  4.  newScanBtn is present
  5.  exportResultsBtn is present
  6.  discoverBtn is present (replaces openImportModalBtn after ap-sm-autodiscovery)
  7.  globalSpectrumBtn is present
  8.  #emptyState panel is present (bug fix: was welcomePanel → null crash)
  9.  #statusPanel is present
  10. #resultsPanel is present
  11. VESTIGE ABSENT: #workOrderModal removed
  12. VESTIGE ABSENT: #spectrumModal removed
  13. VESTIGE ABSENT: #textRecommendationsModal removed
  14. VESTIGE ABSENT: decorative #prioritizeUplink checkbox removed
  15. VESTIGE ABSENT: decorative #stabilityOverSpeed checkbox removed
  16. VESTIGE ABSENT: chart.js CDN script tag removed (not used in index)
  17. #scanAlert feedback div is present
  18. Discovery section (#discoverySection) is present (replaces #importModal)
  19. Log output (#logOutput) is present
  20. snmpCommunity input is present
"""

import pytest


class TestIndexPageRendering:
    """GET / — base page renders correctly for authenticated user."""

    def test_index_returns_200(self, authenticated_client):
        """GIVEN authenticated user WHEN GET / THEN 200."""
        response = authenticated_client.get("/")
        assert response.status_code == 200

    def test_index_contains_html(self, authenticated_client):
        """GIVEN GET / THEN response is HTML with <body>."""
        response = authenticated_client.get("/")
        html = response.data.decode("utf-8")
        assert "<body" in html


class TestCriticalButtonsPresent:
    """Verify all critical action button IDs are rendered in the index page."""

    def _html(self, authenticated_client):
        return authenticated_client.get("/").data.decode("utf-8")

    def test_start_scan_btn_present(self, authenticated_client):
        """GIVEN GET / THEN #startScanBtn button exists in HTML."""
        assert 'id="startScanBtn"' in self._html(authenticated_client)

    def test_clear_btn_present(self, authenticated_client):
        """GIVEN GET / THEN #clearBtn button exists in HTML."""
        assert 'id="clearBtn"' in self._html(authenticated_client)

    def test_new_scan_btn_present(self, authenticated_client):
        """GIVEN GET / THEN #newScanBtn button exists in HTML (Nuevo)."""
        assert 'id="newScanBtn"' in self._html(authenticated_client)

    def test_export_results_btn_present(self, authenticated_client):
        """GIVEN GET / THEN #exportResultsBtn button exists in HTML."""
        assert 'id="exportResultsBtn"' in self._html(authenticated_client)

    def test_discover_btn_present(self, authenticated_client):
        """GIVEN GET / THEN #discoverBtn button exists in HTML (ap-sm-autodiscovery)."""
        assert 'id="discoverBtn"' in self._html(authenticated_client)

    def test_global_spectrum_btn_present(self, authenticated_client):
        """GIVEN GET / THEN #globalSpectrumBtn button exists in HTML."""
        assert 'id="globalSpectrumBtn"' in self._html(authenticated_client)


class TestCriticalPanelsPresent:
    """Verify all critical UI panels are rendered."""

    def _html(self, authenticated_client):
        return authenticated_client.get("/").data.decode("utf-8")

    def test_empty_state_panel_present(self, authenticated_client):
        """GIVEN GET / THEN #emptyState div exists (fix: was welcomePanel → null crash)."""
        assert 'id="emptyState"' in self._html(authenticated_client)

    def test_status_panel_present(self, authenticated_client):
        """GIVEN GET / THEN #statusPanel div exists."""
        assert 'id="statusPanel"' in self._html(authenticated_client)

    def test_results_panel_present(self, authenticated_client):
        """GIVEN GET / THEN #resultsPanel div exists."""
        assert 'id="resultsPanel"' in self._html(authenticated_client)

    def test_scan_alert_div_present(self, authenticated_client):
        """GIVEN GET / THEN #scanAlert inline feedback div exists."""
        assert 'id="scanAlert"' in self._html(authenticated_client)

    def test_log_output_present(self, authenticated_client):
        """GIVEN GET / THEN #logOutput div exists."""
        assert 'id="logOutput"' in self._html(authenticated_client)

    def test_discovery_section_present(self, authenticated_client):
        """GIVEN GET / THEN #discoverySection exists (ap-sm-autodiscovery replaces importModal)."""
        assert 'id="discoverySection"' in self._html(authenticated_client)

    def test_snmp_community_input_present(self, authenticated_client):
        """GIVEN GET / THEN #snmpCommunity input exists."""
        assert 'id="snmpCommunity"' in self._html(authenticated_client)


class TestVestigeElementsAbsent:
    """Verify all dead/vestige elements are removed from the index page (change-006)."""

    def _html(self, authenticated_client):
        return authenticated_client.get("/").data.decode("utf-8")

    def test_work_order_modal_removed(self, authenticated_client):
        """GIVEN GET / THEN #workOrderModal vestige is NOT in HTML."""
        assert 'id="workOrderModal"' not in self._html(authenticated_client)

    def test_spectrum_modal_removed(self, authenticated_client):
        """GIVEN GET / THEN #spectrumModal vestige is NOT in HTML."""
        assert 'id="spectrumModal"' not in self._html(authenticated_client)

    def test_text_recommendations_modal_removed(self, authenticated_client):
        """GIVEN GET / THEN #textRecommendationsModal vestige is NOT in HTML."""
        assert 'id="textRecommendationsModal"' not in self._html(authenticated_client)

    def test_prioritize_uplink_checkbox_removed(self, authenticated_client):
        """GIVEN GET / THEN decorative #prioritizeUplink checkbox is NOT in HTML."""
        assert 'id="prioritizeUplink"' not in self._html(authenticated_client)

    def test_stability_over_speed_checkbox_removed(self, authenticated_client):
        """GIVEN GET / THEN decorative #stabilityOverSpeed checkbox is NOT in HTML."""
        assert 'id="stabilityOverSpeed"' not in self._html(authenticated_client)

    def test_chartjs_cdn_removed(self, authenticated_client):
        """GIVEN GET / THEN unused chart.js CDN script tag is NOT in HTML."""
        assert "cdn.jsdelivr.net/npm/chart.js" not in self._html(authenticated_client)

    def test_acknowledge_btn_removed(self, authenticated_client):
        """GIVEN GET / THEN dead #acknowledgeBtn (workOrderModal) is NOT in HTML."""
        assert 'id="acknowledgeBtn"' not in self._html(authenticated_client)
