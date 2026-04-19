"""
app/freq_apply_manager.py — Orchestrator for SM-first → AP-last frequency apply.

FrequencyApplyManager owns the state machine that applies a frequency change to a
Cambium PMP 450i sector:

  1. Read SM IPs from the scan record (scans.sm_ips, JSON array).
  2. Convert freq_mhz → freq_khz (always done by caller or internally).
  3. GET current AP frequency for prev_freq_khz (best-effort, None on failure).
  4. INSERT frequency_applies row (state='pending').
  5. SET rfScanList on ALL SMs sequentially (collect per-SM results).
  6. UPDATE state → 'sms_applied' (or 'failed' if all SMs failed AND AP also fails).
  7. SET rfFreqCarrier on AP.
  8. UPDATE state → 'completed' (or 'failed' if AP SET failed).
  9. Log APPLY_FREQUENCY audit event.
 10. Return result dict.

Design decision — SM IPs are per-scan:
    scans.sm_ips is a TEXT column that stores a JSON array of IP strings.
    The manager reads SM IPs from the scan record, NOT from the towers table
    (towers has no IP columns in the current schema).

Design decision — channel_width:
    channel_width is stored in frequency_applies for future use, but the SET
    via a dedicated OID is deferred to v2. See TODO(v2) below.

Design decision — viability gate:
    run_apply() validates is_viable=True AND combined_score >= 0.65 before
    proceeding, unless force=True is passed. This threshold mirrors the
    is_optimal threshold from cross_analyzer.

Specification: change-006 spec — Domain 1 (SM-First Apply Order), Domain 3
               (Auto-Apply Behavior), Domain 4 (Manual Override API).
Design:        change-006 design — FrequencyApplyManager.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.freq_utils import mhz_to_khz
from app.audit_manager_v2 import AuditManagerV2

logger = logging.getLogger(__name__)

# Minimum combined_score required for auto-apply and un-forced manual apply.
_VIABILITY_SCORE_THRESHOLD = 0.65

# Maximum number of entries kept in the SM rfScanList after merging (firmware limit).
SM_SCAN_LIST_MAX_ENTRIES = 8


class FrequencyApplyManager:
    """Orchestrates the SM-first → AP-last frequency apply sequence.

    Args:
        db_manager: Initialized DatabaseManager instance.
        tower_scanner: Initialized TowerScanner instance (must have write_community
                       configured and set_frequency / set_sm_scan_list methods).

    Thread Safety:
        Each call to apply() / run_apply() opens DB connections via db_manager
        internally, which are safe for concurrent Flask + background threads.
    """

    def __init__(self, db_manager, tower_scanner):
        self._db = db_manager
        self._scanner = tower_scanner

    # ── Public API ────────────────────────────────────────────────────────

    def run_apply(
        self,
        scan_id: str,
        freq_mhz: float,
        tower_id: str,
        applied_by: str,
        channel_width_mhz: Optional[float] = None,
        force: bool = False,
    ) -> Dict:
        """Validate viability and execute the apply sequence.

        Args:
            scan_id:          ID of the completed scan to apply frequency from.
            freq_mhz:         Target frequency in MHz (e.g. 3554.0).
            tower_id:         Tower identifier (FK towers.tower_id).
            applied_by:       Username of the operator or 'system' for auto-apply.
            channel_width_mhz: Channel width in MHz. Stored but SET deferred (v2).
            force:            If True, bypass viability check (admin only at route layer).

        Returns:
            Dict with keys: success, apply_id, state, errors, freq_khz, sm_results, ap_result.

        Raises:
            ValueError: If scan not found or viability gate blocks apply.
        """
        # 1. Load scan record
        scan = self._get_scan(scan_id)
        if scan is None:
            raise ValueError(f"Scan '{scan_id}' not found")

        # 2. Viability gate (unless force=True)
        if not force:
            results = scan.get("results") or {}

            # AP_SM_CROSS mode: combined score + is_viable
            best_combined = results.get("best_combined_frequency") or {}

            # AP_ONLY mode: per-AP best_frequency inside analysis_results
            # Pick the first AP's best_frequency as the gate signal
            best_ap = {}
            analysis_results = results.get("analysis_results") or {}
            for ap_data in analysis_results.values():
                if isinstance(ap_data, dict) and ap_data.get("best_frequency"):
                    best_ap = ap_data["best_frequency"]
                    break

            if best_combined:
                # AP_SM_CROSS path
                is_viable = best_combined.get("is_viable", False)
                combined_score = best_combined.get("combined_score", 0.0)
                if not is_viable:
                    raise ValueError("Analysis not viable. Use force=true to override.")
                if combined_score < _VIABILITY_SCORE_THRESHOLD:
                    raise ValueError(
                        f"combined_score {combined_score:.2f} is below threshold "
                        f"{_VIABILITY_SCORE_THRESHOLD}. Use force=true to override."
                    )
            elif best_ap:
                # AP_ONLY path — 'Válido'='Sí' is the viability signal
                is_viable_ap = best_ap.get("Válido") == "Sí" or best_ap.get(
                    "is_optimal", False
                )
                if not is_viable_ap:
                    raise ValueError(
                        "Analysis not viable (AP_ONLY). Use force=true to override."
                    )
            else:
                # No analysis results at all — block unless force
                raise ValueError(
                    "No analysis results found in scan. Use force=true to override."
                )

        # 3. Extract SM IPs from scan record
        sm_ips = self._extract_sm_ips(scan)

        # 4. Extract AP IP from scan record
        ap_ips = scan.get("ap_ips") or []
        if not ap_ips:
            raise ValueError(f"Scan '{scan_id}' has no ap_ips — cannot apply")
        ap_ip = ap_ips[0]  # Use first AP IP

        # 5. Convert freq
        freq_khz = mhz_to_khz(freq_mhz)
        channel_width = int(channel_width_mhz) if channel_width_mhz else None

        # 6. GET prev_freq_khz (best-effort)
        prev_freq_khz = self._get_current_ap_freq(ap_ip)

        # 7. Execute apply sequence
        return self._apply(
            scan_id=scan_id,
            tower_id=tower_id,
            ap_ip=ap_ip,
            sm_ips=sm_ips,
            freq_khz=freq_khz,
            prev_freq_khz=prev_freq_khz,
            channel_width=channel_width,
            applied_by=applied_by,
        )

    # ── Internal state machine ────────────────────────────────────────────

    def _apply(
        self,
        scan_id: str,
        tower_id: str,
        ap_ip: str,
        sm_ips: List[str],
        freq_khz: int,
        prev_freq_khz: Optional[int],
        channel_width: Optional[int],
        applied_by: str,
    ) -> Dict:
        """Execute the SM-first → AP-last apply sequence.

        State machine: pending → sms_applied → ap_applied → completed
                       or → failed at any step.
        """
        errors: List[str] = []

        # ── Step 1: Create apply record (state=pending) ───────────────────
        apply_id = self._db.create_frequency_apply(
            tower_id=tower_id,
            scan_id=scan_id,
            freq_khz=freq_khz,
            applied_by_username=applied_by,
            channel_width=channel_width,
            prev_freq_khz=prev_freq_khz,
        )
        logger.info(
            "[APPLY %d] Created: tower=%s scan=%s freq=%d kHz applied_by=%s",
            apply_id,
            tower_id,
            scan_id,
            freq_khz,
            applied_by,
        )

        # ── Step 2: SET rfScanList + bandwidthScan on all SMs (SM-first) ──
        #
        # Secuencia por SM:
        #   a) bandwidthScan.0 — ancho de canal permitido para re-registro
        #   b) rfScanList      — frecuencias a escanear
        #
        # bandwidthScan se envía SIEMPRE que channel_width esté disponible.
        # Si falla bandwidthScan pero rfScanList OK → warning, no fatal.
        # Si falla rfScanList → SM se marca como fallido.
        sm_results: Dict[str, Dict] = {}
        sm_failures: List[str] = []
        sm_skipped: List[str] = []

        if sm_ips:
            for sm_ip in sm_ips:
                sm_entry: Dict = {}

                # ── Make-Before-Break: GET current config before SET ──────────
                # Read current rfScanList and bandwidthScan from SM so we can
                # merge (not replace) — if AP rolls back, SM can still find it.

                # 2a. GET current rfScanList (best-effort, non-fatal on failure)
                # If GET fails we SKIP the SET entirely — writing new-only would destroy
                # the SM's existing scan list and that is exactly what make-before-break
                # was designed to prevent (rollback safety).
                get_ok, current_freqs, get_msg = self._scanner.get_sm_scan_list(sm_ip)
                if not get_ok:
                    logger.warning(
                        "[APPLY %d] SM %s: skipping rfScanList mutation — could not read "
                        "current config (rollback safety): %s",
                        apply_id,
                        sm_ip,
                        get_msg,
                    )
                    sm_entry["skipped_preservation"] = True
                    sm_results[sm_ip] = sm_entry
                    sm_skipped.append(sm_ip)
                    errors.append(
                        f"SM {sm_ip}: skipped — could not read current scan list for preservation"
                    )
                    continue  # Skip this SM entirely — AP will still be changed below

                # Merge: deduplicated union of current + new frequency, capped to max entries
                merged_freqs = list(dict.fromkeys(current_freqs + [freq_khz]))
                merged_freqs = merged_freqs[-SM_SCAN_LIST_MAX_ENTRIES:]
                logger.info(
                    "[APPLY] SM %s: merging rfScanList: current=%s + new=%s → merged=%s",
                    sm_ip,
                    current_freqs,
                    [freq_khz],
                    merged_freqs,
                )

                # 2b. GET current bandwidthScan (best-effort, non-fatal on failure)
                # If GET fails, skip SET for bandwidthScan too (same rollback-safety logic).
                if channel_width:
                    bw_get_ok, current_bws, bw_get_msg = (
                        self._scanner.get_sm_bandwidth_scan(sm_ip)
                    )
                    if not bw_get_ok:
                        logger.warning(
                            "[APPLY %d] SM %s: skipping bandwidthScan mutation — could not "
                            "read current config (rollback safety): %s",
                            apply_id,
                            sm_ip,
                            bw_get_msg,
                        )
                        sm_entry["bw_scan"] = {"skipped_preservation": True}
                    else:
                        new_bw_str = f"{float(channel_width):.1f} MHz"

                        # Normalize current_bws to canonical "X.0 MHz" form before
                        # deduplication — firmware may return "20 MHz" (no decimal)
                        # while new_bw_str is "20.0 MHz", causing silent duplicates.
                        def _normalize_bw(s: str) -> str:
                            try:
                                return f"{float(s.replace('MHz', '').strip()):.1f} MHz"
                            except (ValueError, AttributeError):
                                return s

                        normalized_current = [_normalize_bw(b) for b in current_bws]
                        normalized_new = _normalize_bw(new_bw_str)
                        # Merge: deduplicated union of current + new bandwidth strings, capped
                        merged_bws_str = list(
                            dict.fromkeys(normalized_current + [normalized_new])
                        )
                        merged_bws_str = merged_bws_str[-SM_SCAN_LIST_MAX_ENTRIES:]
                        logger.info(
                            "[APPLY] SM %s: merging bandwidthScan: current=%s + new=%s → merged=%s",
                            sm_ip,
                            current_bws,
                            [new_bw_str],
                            merged_bws_str,
                        )

                        # Convert merged bandwidth strings back to int list for the setter
                        # e.g. ["15.0 MHz", "20.0 MHz"] → [15, 20]
                        merged_bws_int = []
                        for bw_s in merged_bws_str:
                            try:
                                merged_bws_int.append(
                                    int(float(bw_s.replace("MHz", "").strip()))
                                )
                            except ValueError:
                                pass  # Skip malformed values

                        # 2c. SET bandwidthScan with merged list
                        bw_ok, bw_msg = self._scanner.set_sm_bandwidth_scan(
                            sm_ip, merged_bws_int
                        )
                        sm_entry["bw_scan"] = {
                            "success": bw_ok,
                            "error": bw_msg if not bw_ok else None,
                        }
                        if bw_ok:
                            logger.info(
                                "[APPLY %d] SM %s: bandwidthScan=%s OK",
                                apply_id,
                                sm_ip,
                                merged_bws_str,
                            )
                        else:
                            # Non-fatal: SM puede seguir con rfScanList
                            errors.append(f"SM {sm_ip} bandwidthScan: {bw_msg}")
                            logger.warning(
                                "[APPLY %d] SM %s bandwidthScan failed (non-fatal): %s",
                                apply_id,
                                sm_ip,
                                bw_msg,
                            )

                # 2d. SET rfScanList with merged list (frecuencia — siempre)
                success, msg = self._scanner.set_sm_scan_list(sm_ip, merged_freqs)
                sm_entry["success"] = success
                sm_entry["error"] = msg if not success else None

                sm_results[sm_ip] = sm_entry

                if not success:
                    sm_failures.append(sm_ip)
                    errors.append(f"SM {sm_ip}: {msg}")
                    logger.warning("[APPLY %d] SM %s failed: %s", apply_id, sm_ip, msg)
                else:
                    logger.info("[APPLY %d] SM %s: rfScanList OK", apply_id, sm_ip)
        else:
            logger.info("[APPLY %d] No SMs in scan — skipping SM step", apply_id)

        # ── Step 2e: VERIFY SM config was actually written ────────────────
        # After all SETs, read back rfScanList and bandwidthScan from each SM
        # to confirm the values were persisted.  Non-fatal: AP change proceeds
        # regardless, but unverified SMs are flagged in sm_results and logged.
        #
        # Verification logic:
        #   - rfScanList  → GET OID .1.0 and check freq_khz is in the list
        #   - bandwidthScan → GET OID .131.0 and check channel_width is present
        #
        # SMs that were skipped (no GET) are not re-verified here.
        if sm_ips:
            for sm_ip in sm_ips:
                entry = sm_results.get(sm_ip, {})
                if entry.get("skipped_preservation"):
                    continue  # No point verifying — we never wrote anything

                # Only verify SMs where rfScanList SET succeeded
                if not entry.get("success"):
                    continue

                verify: Dict = {}

                # --- Verify rfScanList ---
                vget_ok, vfreqs, vget_msg = self._scanner.get_sm_scan_list(sm_ip)
                if vget_ok:
                    freq_confirmed = freq_khz in vfreqs
                    verify["scan_list_ok"] = freq_confirmed
                    if freq_confirmed:
                        logger.info(
                            "[APPLY %d] SM %s: VERIFY rfScanList OK — %d kHz confirmed",
                            apply_id,
                            sm_ip,
                            freq_khz,
                        )
                    else:
                        logger.warning(
                            "[APPLY %d] SM %s: VERIFY rfScanList FAIL — %d kHz not found in %s",
                            apply_id,
                            sm_ip,
                            freq_khz,
                            vfreqs,
                        )
                        errors.append(
                            f"SM {sm_ip}: VERIFY — freq {freq_khz} kHz not confirmed in rfScanList"
                        )
                else:
                    verify["scan_list_ok"] = None  # indeterminate
                    logger.warning(
                        "[APPLY %d] SM %s: VERIFY rfScanList GET failed: %s",
                        apply_id,
                        sm_ip,
                        vget_msg,
                    )

                # --- Verify bandwidthScan (solo si el SET de bw tuvo éxito) ---
                # Si el SET falló (non-fatal), no verificamos — ya está en errors
                # y verificar solo agregaría un falso positivo al gate.
                if channel_width and entry.get("bw_scan", {}).get("success") is True:
                    bv_ok, bv_bws, bv_msg = self._scanner.get_sm_bandwidth_scan(sm_ip)
                    if bv_ok:
                        # Normalize to int for comparison (firmware may return "20.0 MHz")
                        bv_ints = []
                        for bw_s in bv_bws:
                            try:
                                bv_ints.append(
                                    int(float(bw_s.replace("MHz", "").strip()))
                                )
                            except (ValueError, AttributeError):
                                pass
                        bw_confirmed = channel_width in bv_ints
                        verify["bw_scan_ok"] = bw_confirmed
                        if bw_confirmed:
                            logger.info(
                                "[APPLY %d] SM %s: VERIFY bandwidthScan OK — %d MHz confirmed",
                                apply_id,
                                sm_ip,
                                channel_width,
                            )
                        else:
                            logger.warning(
                                "[APPLY %d] SM %s: VERIFY bandwidthScan FAIL — %d MHz not found in %s",
                                apply_id,
                                sm_ip,
                                channel_width,
                                bv_bws,
                            )
                            errors.append(
                                f"SM {sm_ip}: VERIFY — bw {channel_width} MHz not confirmed in bandwidthScan"
                            )
                    else:
                        verify["bw_scan_ok"] = None  # indeterminate
                        logger.warning(
                            "[APPLY %d] SM %s: VERIFY bandwidthScan GET failed: %s",
                            apply_id,
                            sm_ip,
                            bv_msg,
                        )

                sm_results[sm_ip]["verify"] = verify

        # ── Step 2f: GATE — block AP if any SM failed verification ───────
        # A SM that had a successful SET but whose config is NOT confirmed by
        # a subsequent GET would be left without the new frequency in its scan
        # list.  Changing the AP while that SM is unconfirmed means the SM
        # cannot re-register on the new frequency → permanent outage for that SM.
        #
        # Block rule: if scan_list_ok is explicitly False (not None/indeterminate)
        # for ANY SM → abort, do not touch AP, set state to failed.
        #
        # scan_list_ok=None (GET failed) is treated as indeterminate — we allow
        # the AP change because we cannot confirm the write failed either; the
        # operator can investigate via logs.
        sm_verify_blocked: List[str] = [
            sm_ip
            for sm_ip, entry in sm_results.items()
            if entry.get("verify", {}).get("scan_list_ok") is False
            or entry.get("verify", {}).get("bw_scan_ok") is False
        ]

        sm_results_json = json.dumps(sm_results)

        # ── Step 3: Update state to sms_applied ──────────────────────────
        self._db.update_frequency_apply_status(
            apply_id=apply_id,
            state="sms_applied",
            sm_results=sm_results_json,
        )

        if sm_verify_blocked:
            block_msg = (
                f"AP change BLOCKED — {len(sm_verify_blocked)} SM(s) did not confirm "
                f"new config: {sm_verify_blocked}. Fix SM config and retry."
            )
            logger.error("[APPLY %d] %s", apply_id, block_msg)
            errors.append(block_msg)
            self._db.update_frequency_apply_status(
                apply_id=apply_id,
                state="failed",
                ap_result=json.dumps({"success": False, "error": block_msg}),
                sm_results=sm_results_json,
                error=block_msg,
                completed=True,
            )
            self._log_audit(
                apply_id=apply_id,
                scan_id=scan_id,
                tower_id=tower_id,
                freq_khz=freq_khz,
                applied_by=applied_by,
                final_state="failed",
                sm_count=len(sm_ips),
                failed_sm_count=len(sm_failures),
                skipped_sm_count=len(sm_skipped),
                ap_success=False,
                errors=errors,
            )
            return {
                "success": False,
                "apply_id": apply_id,
                "state": "failed",
                "freq_khz": freq_khz,
                "channel_width_mhz": channel_width,
                "channel_width_result": None,
                "contention_slots_ok": None,
                "broadcast_retry_ok": None,
                "reboot_ok": None,
                "sm_results": sm_results,
                "ap_result": {"success": False, "error": block_msg},
                "errors": errors,
            }

        # ── Step 4: SET rfFreqCarrier on AP (AP-last, only after SM verify) ──
        ap_success, ap_msg = self._scanner.set_frequency(ap_ip, freq_khz)
        ap_result = {"success": ap_success, "error": ap_msg if not ap_success else None}
        ap_result_json = json.dumps(ap_result)

        if ap_success:
            logger.info(
                "[APPLY %d] AP %s: rfFreqCarrier=%d kHz OK", apply_id, ap_ip, freq_khz
            )
        else:
            errors.append(f"AP {ap_ip}: {ap_msg}")
            logger.error("[APPLY %d] AP %s failed: %s", apply_id, ap_ip, ap_msg)

        # ── Step 4b: SET channel_width (opcional, si se provee) ───────────
        channel_width_result = None
        if channel_width and ap_success:
            # Pasar freq_mhz para detectar banda (3GHz vs 4/5GHz) sin GET extra
            ap_freq_mhz = freq_khz / 1000.0  # freq_khz ya está en kHz
            bw_success, bw_msg = self._scanner.set_channel_width(
                ap_ip, channel_width, ap_freq_mhz=ap_freq_mhz
            )
            channel_width_result = {
                "success": bw_success,
                "error": bw_msg if not bw_success else None,
            }
            if bw_success:
                logger.info(
                    "[APPLY %d] AP %s: channelBandwidth=%d MHz OK",
                    apply_id,
                    ap_ip,
                    channel_width,
                )
            else:
                # NO falla el apply — solo warning
                errors.append(f"channel_width {ap_ip}: {bw_msg}")
                logger.warning(
                    "[APPLY %d] channelBandwidth SET falló (non-fatal): %s",
                    apply_id,
                    bw_msg,
                )

        # ── Step 4c: SET contention_slots = 4 (OBLIGATORIO, non-fatal) ────
        ct_success, ct_msg = self._scanner.set_contention_slots(ap_ip)
        if ct_success:
            logger.info("[APPLY %d] AP %s: contention_slots=4 OK", apply_id, ap_ip)
        else:
            errors.append(f"contention_slots {ap_ip}: {ct_msg}")
            logger.warning(
                "[APPLY %d] contention_slots SET falló (non-fatal): %s",
                apply_id,
                ct_msg,
            )

        # ── Step 4d: SET broadcast_retry = 0 (OBLIGATORIO, non-fatal) ─────
        br_success, br_msg = self._scanner.set_broadcast_retry(ap_ip)
        if br_success:
            logger.info("[APPLY %d] AP %s: broadcastRetryCount=0 OK", apply_id, ap_ip)
        else:
            errors.append(f"broadcast_retry {ap_ip}: {br_msg}")
            logger.warning(
                "[APPLY %d] broadcast_retry SET falló (non-fatal): %s", apply_id, br_msg
            )

        # ── Step 4e: rebootIfRequired = 1 (SIEMPRE, último paso) ──────────
        # El equipo evaluará si los cambios requieren reinicio y lo ejecutará
        # automáticamente. El AP quedará inaccesible ~30-60 s (esperado).
        rb_success, rb_msg = self._scanner.reboot_if_required(ap_ip)
        if rb_success:
            logger.info(
                "[APPLY %d] AP %s: rebootIfRequired=1 enviado OK", apply_id, ap_ip
            )
        else:
            errors.append(f"reboot {ap_ip}: {rb_msg}")
            logger.warning(
                "[APPLY %d] reboot_if_required SET falló (non-fatal): %s",
                apply_id,
                rb_msg,
            )

        # ── Step 5: Determine final state ─────────────────────────────────
        # State machine rules (from spec Domain 8):
        #   AP fails → failed (regardless of SMs)
        #   AP OK, some SMs failed → completed (partial is reflected in sm_results)
        #   All OK → completed
        if not ap_success:
            final_state = "failed"
            error_msg = "; ".join(errors)
        else:
            final_state = "completed"
            error_msg = ("; ".join(errors)) if errors else None

        self._db.update_frequency_apply_status(
            apply_id=apply_id,
            state=final_state,
            ap_result=ap_result_json,
            sm_results=sm_results_json,
            error=error_msg,
            completed=True,
        )
        logger.info("[APPLY %d] Final state: %s", apply_id, final_state)

        # ── Step 6: Audit log ─────────────────────────────────────────────
        self._log_audit(
            apply_id=apply_id,
            scan_id=scan_id,
            tower_id=tower_id,
            freq_khz=freq_khz,
            applied_by=applied_by,
            final_state=final_state,
            sm_count=len(sm_ips),
            failed_sm_count=len(sm_failures),
            skipped_sm_count=len(sm_skipped),
            ap_success=ap_success,
            errors=errors,
        )

        # ── Step 7: Return result dict ────────────────────────────────────
        return {
            "success": final_state == "completed",
            "apply_id": apply_id,
            "state": final_state,
            "freq_khz": freq_khz,
            "channel_width_mhz": channel_width,
            "channel_width_result": channel_width_result,
            "contention_slots_ok": ct_success,
            "broadcast_retry_ok": br_success,
            "reboot_ok": rb_success,
            "sm_results": sm_results,
            "ap_result": ap_result,
            "errors": errors,
        }

    # ── Helpers ──────────────────────────────────────────────────────────

    def _get_scan(self, scan_id: str) -> Optional[Dict]:
        """Retrieve scan record from DB via direct SQL (no ScanStorageManager dep)."""
        conn = self._db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM scans WHERE id = ?", (scan_id,)
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            # Deserialize JSON fields
            for field in ("ap_ips", "sm_ips", "config", "results", "recommendations"):
                if d.get(field) is not None:
                    try:
                        d[field] = json.loads(d[field])
                    except (ValueError, TypeError):
                        pass
            return d
        finally:
            conn.close()

    def _extract_sm_ips(self, scan: Dict) -> List[str]:
        """Extract SM IPs from scan record.

        scans.sm_ips is stored as a JSON array of IP strings.
        Returns empty list if no SMs were part of the scan.
        """
        sm_ips = scan.get("sm_ips")
        if sm_ips is None:
            return []
        if isinstance(sm_ips, list):
            return [ip for ip in sm_ips if ip]
        # Fallback: try to parse if it's still a string (defensive)
        try:
            parsed = json.loads(sm_ips)
            return [ip for ip in parsed if ip] if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []

    def _get_current_ap_freq(self, ap_ip: str) -> Optional[int]:
        """GET current rfFreqCarrier from AP. Returns None on any failure."""
        try:
            success, value, _msg = self._scanner._snmp_get(
                ip=ap_ip,
                oid=self._scanner.RF_FREQ_CARRIER_OID,
            )
            if success and value:
                return int(value)
        except Exception as exc:
            logger.debug("[APPLY] Could not read prev AP freq from %s: %s", ap_ip, exc)
        return None

    def _log_audit(
        self,
        apply_id: int,
        scan_id: str,
        tower_id: str,
        freq_khz: int,
        applied_by: str,
        final_state: str,
        sm_count: int,
        failed_sm_count: int,
        skipped_sm_count: int,
        ap_success: bool,
        errors: List[str],
    ) -> None:
        """Log APPLY_FREQUENCY audit event via AuditManagerV2."""
        try:
            audit = AuditManagerV2(
                db_manager=self._db,
                user=applied_by if applied_by else "system",
                action_type="APPLY_FREQUENCY",
            )
            result_summary = (
                f"apply_id={apply_id} state={final_state} "
                f"freq={freq_khz}kHz sm={sm_count}(failed={failed_sm_count},skipped={skipped_sm_count}) "
                f"ap={'OK' if ap_success else 'FAILED'}"
            )
            audit.log_action(
                result_summary=result_summary,
                scan_id=scan_id,
                tower_id=tower_id,
                details={
                    "apply_id": apply_id,
                    "freq_khz": freq_khz,
                    "state": final_state,
                    "sm_count": sm_count,
                    "failed_sm_count": failed_sm_count,
                    "skipped_sm_count": skipped_sm_count,
                    "ap_success": ap_success,
                    "errors": errors,
                },
            )
        except Exception as exc:
            # Audit failure must NOT propagate — apply already completed.
            logger.error("[APPLY %d] Audit log failed: %s", apply_id, exc)

    def get_apply_history(self, tower_id: str, limit: int = 50) -> List[Dict]:
        """Retrieve frequency apply history for a tower.

        Args:
            tower_id: Tower identifier.
            limit:    Maximum rows to return (default 50).

        Returns:
            List of dicts ordered by created_at DESC.
        """
        conn = self._db.get_connection()
        try:
            rows = conn.execute(
                """SELECT id, scan_id, freq_khz, prev_freq_khz, channel_width,
                          state, applied_by_username, created_at, completed_at,
                          sm_results, ap_result, error
                   FROM frequency_applies
                   WHERE tower_id = ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (tower_id, limit),
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["freq_mhz"] = d["freq_khz"] / 1000.0 if d.get("freq_khz") else None
                # Deserialize JSON blobs
                for field in ("sm_results", "ap_result"):
                    if d.get(field):
                        try:
                            d[field] = json.loads(d[field])
                        except (ValueError, TypeError):
                            pass
                result.append(d)
            return result
        finally:
            conn.close()
