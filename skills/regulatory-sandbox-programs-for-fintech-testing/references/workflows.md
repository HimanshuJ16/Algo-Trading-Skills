# Workflows for Regulatory Sandbox Programs for Fintech Testing

1. **Transcribe & Register Approved Boundary Conditions**:
   - Copy each cap verbatim from the regulator's approval letter into `SandboxParameters`
     (`max_allowed_clients`, `max_transaction_volume_usd`, `max_aum_usd`,
     `max_duration_months`); set `framework_key` for provenance.
   - Do not substitute an industry default for a value you cannot locate in the approval.
     An unregistered program fails closed with `PROGRAM_NOT_FOUND`, which is the correct
     outcome.
   - Set `approved_extension_months` only against a written extension grant.
2. **Telemetry Ingestion & Capacity Calculation**:
   - Compute capacity utilisation % for active clients, **cumulative** transaction
     volume since test start, and current AUM.
3. **Boundary Compliance Auditing**:
   - Compare clients, cumulative volume, AUM, and elapsed months against the approved
     limits. Caps are inclusive maxima: at the cap is compliant, strictly above is a breach.
4. **Exit Plan Verification**:
   - Where the approval requires it, confirm a documented exit / client-transition plan
     covering how existing clients are protected when testing ends, is extended, or is
     stopped early.
5. **Warning & Report Generation**:
   - Emit pre-breach warnings at or above the configured utilisation threshold and in the
     final month of the approved window; output the structured `SandboxAuditReport`.
6. **Escalation**:
   - On any breach, freeze the activity that caused it, notify the compliance owner, and
     follow the approval's notification terms. Detection is not remediation, and the
     engine does not decide whether a breach is reportable.
