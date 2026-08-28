# Pre-Flight / Sign-off Checklist — risk-control-bypass-audit-logging

## Scope

- [ ] The firm's regulatory position is stated in writing: broker-dealer with market access (SEA Rule 15c3-5), EEA/UK investment firm engaged in algorithmic trading (RTS 6), both, or neither.
- [ ] If **neither**, the override log is documented as operational hygiene and is **not** presented internally or externally as compliance evidence.
- [ ] Emergency overrides are separated from routine configuration changes; routine limit recalibrations are routed to the change-approval process, not to this log.

## Engine configuration

- [ ] `authorized_principals` replaced with the firm's actual designated individuals — the module default is an illustrative example.
- [ ] `critical_controls` and `high_severity_controls` populated with the firm's real control names; the `"LIMIT"`/`"CAP"` substring fallback is not relied on as policy.
- [ ] Every control the firm operates under RTS 6 Article 15(1) — price collars, maximum order values, maximum order volumes, message limits — is registered at **at least** HIGH.
- [ ] `min_justification_chars` calibrated, and the calibration recorded as an **engineering default with no regulatory basis**.
- [ ] **If in scope of RTS 6 Article 15(6):** `require_risk_function_verification=True` and `require_expiry_for_critical=True`.

## Capture

- [ ] `log_bypass` is called on the same code path that applies the override, not from a later reconciliation job.
- [ ] `recorded_at` is passed explicitly, so output is reproducible and the event-vs-record time gap is meaningful.
- [ ] All timestamps are timezone-aware ISO-8601 with an explicit UTC offset.
- [ ] `requested_by` is populated — without it the self-authorisation check has nothing to compare.
- [ ] `risk_function_verifier` is populated where RTS 6 Article 15(6) applies.
- [ ] `expires_at_iso` is populated: the override is bounded in time, per "temporary basis".
- [ ] `strategy_id` / `instrument` scope the record to the specific trade, per "in relation to a specific trade".
- [ ] `RiskBypassAuditError` is routed to an operator, **never** swallowed by a bare `except` in the order path.

## Integrity and persistence

- [ ] Entries are persisted to storage the trading host cannot rewrite (WORM, object-lock bucket, or write-only sink).
- [ ] `chain_head_hash` is published externally on a defined cadence, and verified against the reloaded prefix on the next run.
- [ ] The storage layer is mapped to a **SEA Rule 17a-4(f)** alternative — WORM, or the audit-trail alternative (all modifications and deletions, create/modify/delete times, identity of the individual).
- [ ] It is documented that the in-process hash chain is tamper-**evident**, not immutable, and that immutability comes from the storage layer.
- [ ] `verify_integrity()` is run before any report is relied on, and `report.integrity_verified` is checked, not assumed.

## Governance

- [ ] Authorisation is enforced **upstream**, in the risk control itself; this engine records, it does not gate.
- [ ] Flagged entries are reviewed on a defined cadence by a function independent of the authorisers, and every flagged entry ends in a recorded disposition.
- [ ] CRITICAL and suspicious entries are wired into an alerting/escalation path — logging is not notifying.
- [ ] Retention period confirmed with counsel against the applicable regime; **RTS 6 Article 28(3)'s five years is not cited** for override records (it governs HFT order records).
- [ ] No SOX-based justification is used for this log unless the firm is an issuer and the control is genuinely in ICFR scope.

## Verification

- [ ] A `SPREAD_VETO` bypass reports the same severity from `log_bypass` and from `generate_audit_report()`.
- [ ] A flagged entry retains a non-`None` `flag_reason` in the generated report.
- [ ] A tampered stored record makes `verify_integrity()` return `(False, reason)` and sets `integrity_verified=False` on the report.
- [ ] An identical resubmission does not create a second record; a conflicting resubmission raises.
- [ ] A timezone-naive timestamp raises and does not advance `chain_head_hash`.
- [ ] Automated Testing: run `python -m unittest discover -s skills/risk-control-bypass-audit-logging/scripts` — 100% pass rate.

## Sign-off

- Reviewed by: ___________________________
- Risk / compliance sign-off (scope and retention): ___________________________
- Date: ___________________________
