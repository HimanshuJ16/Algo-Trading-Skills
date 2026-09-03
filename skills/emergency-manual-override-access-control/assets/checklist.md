# Pre-Flight / Sign-off Checklist — emergency-manual-override-access-control

## Identity and RBAC
- [ ] Operator id and role come from authenticated, server-derived IAM claims — never from a client-supplied field.
- [ ] MFA / step-up authentication is enforced in front of this engine (it verifies an assertion, not a human).
- [ ] `authorized_roles` reflects the firm's actual designated individuals (RTS 6 Art. 15(6) names no titles).
- [ ] Persons holding critical access are enumerated, restricted and traceable (RTS 6 Art. 18(5)).

## Action classification and quorum
- [ ] Every firm-wide action is listed in `critical_actions` — an unlisted action is `SEVERITY_HIGH` and needs only one operator.
- [ ] Four-eyes is enforced for critical actions: secondary identity differs after case-folding, and the secondary's role is itself authorised.
- [ ] A secondary id supplied without an authorised role is rejected, not silently ignored.

## Break-glass
- [ ] Tokens are pre-issued with a ≥16-character secret; only the SHA-256 digest is stored.
- [ ] Tokens carry an expiry, are single-use, and are bound to the intended operator.
- [ ] Without a configured registry the break-glass path fails closed (`BREAK_GLASS_NOT_CONFIGURED`).
- [ ] Every break-glass approval raises `post_incident_review_required` and enters a review queue with a named owner.

## Justification and TTL
- [ ] Justification is mandatory and retained as a record (SEC staff FAQ No. 18; 15c3-5(b)).
- [ ] `ttl_minutes` is bounded by `max_ttl_minutes`; the value is calibrated to the desk and the rationale recorded (it is a house default, not a regulatory limit).
- [ ] A supervisory loop calls `expire_due_overrides()` — nothing expires on its own.
- [ ] Expiry is surfaced to the desk as "the suppressed control is live again", not logged as silent cleanup.

## Audit evidence
- [ ] Denials are hashed and chained, not just approvals (NIST SP 800-53 Rev. 5 AC-6(9)).
- [ ] The published `decision_timestamp_utc` is the exact value in the hash pre-image, so `compute_record_hash()` reproduces the digest.
- [ ] The pre-image binds the secondary approver, severity, TTL, approval mode and outcome — not just the initiator.
- [ ] `audit_hmac_key` is set in production and the key is held outside the log store (AU-9(3)).
- [ ] Records are shipped to an append-only sink; for US broker-dealer books and records, WORM or the 17a-4(f) audit-trail alternative.
- [ ] `verify_audit_chain()` runs as a scheduled integrity check, and a non-`None` broken index pages someone.

## Operational safety
- [ ] The approved report is wired to an executor — this engine cancels nothing.
- [ ] No dual-sign-off gate sits in front of an *automated* circuit breaker (RTS 6 Art. 12 requires immediate cancellation).
- [ ] Active overrides survive a process restart, or the system fails closed on start-up.
- [ ] Break-glass usage rate is monitored — a rising rate means the normal path is too slow, not that the emergency is chronic.

## Testing
- [ ] Run `python -m unittest discover -s skills/emergency-manual-override-access-control/scripts` — 47 tests, 100% pass rate.
- [ ] Fire-drill the path in a non-production environment with real IAM identities and a real second approver.

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________
