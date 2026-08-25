# Deep Workflow Reference — emergency-manual-override-access-control

Full technical procedure behind `SKILL.md`. Reference implementation:
`scripts/override_access_control.py`; tests: `scripts/test_override_access_control.py`.

## Full Procedure

0. **Establish trusted identity before the request exists.**
   - `primary_operator_id` / `primary_operator_role` must come from authenticated
     IAM claims resolved server-side. A role read from a client request body makes
     every check below cosmetic.
   - Apply MFA / step-up authentication at the IAM layer; this engine does not
     verify a human, only an assertion about one.

1. **Classify severity** — `OverridePolicy.severity_for(action_type)`.
   - Matching is case-insensitive and whitespace-stripped.
   - An action not in `critical_actions` is `SEVERITY_HIGH`: one authorised
     operator, no second signature. This deliberately fails open on *quorum* so an
     unclassified action is never blocked mid-incident, which makes enumerating
     every firm-wide action a prerequisite, not an optimisation.

2. **Structural validation.** `request_id`, `target_system_id`, `action_type`,
   `primary_operator_id`, `primary_operator_role` must each be a non-empty string.
   A blank identity yields an audit record naming nobody.

3. **Replay and duplicate handling.**
   - The payload key covers target, action, primary and secondary identity,
     justification and TTL.
   - Identical resubmission → the **original** report, unchanged hash and
     timestamp, no second chain entry. A retried HTTP call must not fire a second
     kill switch.
   - Same `request_id`, different payload → `DUPLICATE_REQUEST_ID`. The stored
     decision is never overwritten.
   - Denied requests are not retained in the decision map: an operator may correct
     a short justification and resubmit under the same id.

4. **Justification.** Stripped length must meet `min_justification_chars`
   (SEC staff FAQ No. 18: reasons documented and retained as books and records;
   RTS 6 Art. 15(6): authorised by a designated individual). Treat the length
   check as a completeness gate; quality is a retrospective-review question.

5. **TTL bounds.** `ttl_minutes` must be an `int` in `1..max_ttl_minutes`
   (`bool` is rejected explicitly — `True` is an `int` in Python and would be
   accepted as a one-minute TTL). An unbounded override is a permanently disabled
   control; cf. NIST SP 800-53 Rev. 5 AC-2(2).

6. **RBAC on the initiator.** `primary_operator_role` must be in
   `authorized_roles`; for a critical action it must also be in
   `critical_approver_roles` when that narrower set is configured.

7. **Quorum for `SEVERITY_CRITICAL`** — exactly one of:
   - **Dual sign-off**: a secondary identity that (a) differs from the primary
     after case-folding and stripping, and (b) holds a role authorised for that
     severity. A secondary id supplied without an authorised role is rejected,
     never silently ignored.
   - **Break-glass**: a token matched against pre-issued SHA-256 digests, checked
     for expiry, prior consumption and operator binding. Absent a registry the
     path returns `BREAK_GLASS_NOT_CONFIGURED` — it fails closed. The token is
     consumed only after the request is approved, so an unrelated validation
     failure does not burn it. Approval sets `break_glass_used` and
     `post_incident_review_required`.

8. **Hash and chain the decision — approvals and denials alike.**
   - Pre-image (length-prefixed `key:<len>:<value>` lines, so moving a delimiter
     between adjacent fields cannot produce a collision): previous hash, request
     id, target, action, severity, both operator ids and roles, approval mode,
     justification, TTL, approved flag, rejection code, UTC ISO-8601 timestamp.
   - The timestamp in the pre-image is the one published as
     `decision_timestamp_utc`, so any holder of the archived request can recompute
     the hash with `compute_record_hash()`.
   - `audit_hmac_key` switches the digest to HMAC-SHA-256 and labels it in
     `hash_algorithm`.

9. **Execution and expiry.**
   - Hand the approved report to the executor (`execution-algorithm-kill-switch-integration`).
     This engine cancels nothing.
   - `expire_due_overrides()` must be called by a supervisory loop; expiry is at
     or after `expires_at`, so an override is not in force at its expiry instant.
     Every returned entry is a control that is live again — surface it to the desk.
   - `revoke_override()` stands an override down early and records who did it.

10. **Evidence verification.** `verify_audit_chain()` recomputes the chain and
    returns `(is_intact, first_broken_index)`. Verifying an externally persisted
    chain needs the archived requests *and*, for a keyed chain, the HMAC key.

## Concurrency and failure handling

- The whole decision path runs under one re-entrant engine lock: check-then-act on
  the duplicate map, token registry and active-override map must be atomic, or two
  operators racing on one `request_id` both win.
- The engine never raises on a denial; it raises `OverrideAccessError` only for
  misconfiguration (empty role set, naive datetime, weak break-glass secret,
  duplicate token id).
- State is in memory. On restart, active overrides and their pending expiry are
  lost — persist them or fail closed at start-up.

## Production Implementation Reference

- `EmergencyOverrideAccessEngine`, `OverridePolicy`, `OverrideRequest`,
  `OverrideControlReport`, `ActiveOverride`, `BreakGlassToken`,
  `BreakGlassTokenRegistry`, `compute_record_hash`, `verify_audit_chain`.
- Rejection codes are a stable contract for alerting: `INVALID_FIELD`,
  `MISSING_JUSTIFICATION`, `UNAUTHORIZED_ROLE`, `DUAL_SIGN_OFF_REQUIRED`,
  `SELF_APPROVAL`, `SECONDARY_ROLE_UNAUTHORIZED`, `BREAK_GLASS_INVALID`,
  `BREAK_GLASS_NOT_CONFIGURED`, `INVALID_TTL`, `DUPLICATE_REQUEST_ID`.
