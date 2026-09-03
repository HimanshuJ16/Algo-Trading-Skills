---
name: emergency-manual-override-access-control
description: >-
  Use when designing the break-glass path a human uses to fire a kill switch, halt a
  strategy or pause orders: role checks, four-eyes sign-off, verified single-use tokens
  and a record of who authorised what. It authorises; it does not cancel.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: emergency-override, break-glass, kill-switch, rbac, dual-sign-off, audit-logging, compliance
  brokers_frameworks: "MiFID II RTS 6 (EU 2017/589); SEC Rule 15c3-5; NIST SP 800-53 Rev. 5; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when designing or reviewing the human authorisation path in front of an emergency control action — `KILL_SWITCH_ALL_ALGOS`, `HALT_STRATEGY`, `PAUSE_ORDERS`, or any manual intervention that suppresses or bypasses an automated risk control. It answers four questions and records the answers: **who** may fire it, under **what quorum**, for **how long**, and with **what evidence**.

The regulatory pressure is specific. MiFID II RTS 6 Art. 12 requires the firm to be able to cancel any or all unexecuted orders immediately as an emergency measure; Art. 15(6) requires exceptional handling to be verified by the risk management function and authorised by a designated individual; Art. 18(5) requires the firm to identify and restrict who holds critical access rights and to monitor that access "with complete traceability." SEC staff FAQ No. 18 under Rule 15c3-5 states the reasons for threshold modifications should be documented and retained as books and records.

## When NOT to Use

- **As the kill switch itself.** This engine authorises and records; it cancels nothing. An approved report must be handed to an executor — see `execution-algorithm-kill-switch-integration`. If your break-glass path can approve an action the executor cannot actually perform, you have a governance record and a live runaway algo.
- **In front of an automated risk control.** Never put a dual-sign-off gate on a drawdown circuit breaker's own trigger. RTS 6 Art. 12 requires the emergency cancel to be *immediate*; a control that waits for a second human is not a circuit breaker. Automated triggers fire automatically and are recorded; this path governs *manual* intervention only.
- **For scheduled or routine configuration change.** Loosening a limit at 09:15 because a strategy keeps getting rejected is change control, not an emergency — use `risk-control-configuration-change-approval-workflow`. Break-glass exists to make the rare case auditable, not to become the everyday path.
- **With client-supplied identity.** Every check here is only as good as `primary_operator_role`. If that field is read from a request body an operator can edit, the RBAC matrix is decorative. Roles must come from authenticated, server-derived IAM claims.
- **As durable storage.** The in-memory chain, active-override map and token registry are reference adapters. Production needs an append-only sink (SEC Rule 17a-4(f) offers WORM or the audit-trail alternative for US broker-dealer records) and a durable token store.

## Prerequisites

- Python 3.10+ (`from __future__ import annotations` with builtin generics; dependency-free stdlib).
- Authenticated operator identity and **server-derived** role, never client-asserted.
- A firm-approved `OverridePolicy`: authorised roles, which actions count as firm-wide critical, which roles may approve a critical action, the maximum TTL, and the minimum justification length. All are firm policy — no regulator publishes these values.
- Pre-issued break-glass tokens (`BreakGlassToken.from_secret`) if a single-operator emergency path is required at all; without a registry the break-glass path fails closed and dual sign-off is the only route.
- A supervisory loop that calls `expire_due_overrides()` — nothing expires on its own.
- An append-only audit sink, and for real tamper evidence an `audit_hmac_key` held outside that sink.

## Workflow

1. **Classify the action**: `OverridePolicy.severity_for()` maps the action to `SEVERITY_CRITICAL` or `SEVERITY_HIGH`.
   - **Decision point — unlisted actions are never critical.** An action absent from `critical_actions` is classified `SEVERITY_HIGH` and needs only one authorised operator. That fails *open* on quorum by design, so that a typo cannot silently downgrade a firm-wide kill switch to a single-approver action *and* so that an unclassified action is not blocked in an emergency. Every firm-wide action must be enumerated in the policy — auditing that list is part of adopting this skill.
2. **Validate structure, replay and TTL before authorisation**:
   - **Decision point — a resubmitted `request_id` is either a retry or an attack.** An identical payload returns the *original* decision (a retried HTTP call must not fire a second kill switch). A *different* payload under a decided id is denied as `DUPLICATE_REQUEST_ID` rather than overwriting the record. Denied requests are not retained, so an operator can fix a justification and resubmit under the same id.
   - Reject `ttl_minutes` outside `1..max_ttl_minutes`. An override with no expiry is a permanently disabled control (cf. NIST SP 800-53 Rev. 5 AC-2(2)).
3. **Authorise**: check the primary role against the RBAC matrix, then for `SEVERITY_CRITICAL` require four-eyes or a verified break-glass token.
   - **Decision point — a second identity is not a second person.** Compare identities case-folded and stripped; `usr_risk_01` approving `USR_RISK_01` is self-approval, and the secondary's role must itself be authorised for that severity.
   - **Decision point — break-glass is a credential, not a string.** A presented token is matched against pre-issued digests, checked for expiry, single-use consumption and operator binding. It is consumed only on a *successful* authorisation, so an unrelated validation failure does not burn it. Every break-glass approval sets `post_incident_review_required`.
4. **Hash and chain the decision** — approvals *and* denials. The pre-image is length-prefixed and binds the request id, target, action, severity, both operators, approval mode, justification, TTL, outcome, rejection code, UTC timestamp, and the previous record's hash.
5. **Execute and track expiry**: hand the approved report to the executor, then run `expire_due_overrides()` on a schedule.
   - **Decision point — expiry re-arms the control.** Each entry returned by `expire_due_overrides()` is a suppressed control that is live again. That is the intent, but it is a state change the desk must be told about, not a silent cleanup.
6. **Verify the evidence**: `verify_audit_chain()` recomputes the chain and returns the first broken index.

> Full procedure: see `references/workflows.md`.
> Standards and citations: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a break-glass token as a password-shaped string.** Accepting any token above a length threshold means `"aaaaaaaa"` unlocks a firm-wide kill switch with one operator — the dual-sign-off requirement is then decoration. Verify against pre-issued, expiring, single-use, operator-bound credentials, and fail closed when no registry is configured.
- **Not logging the denials.** A rejected firm-wide kill-switch attempt by an unauthorised operator is the single most investigation-worthy event this system produces. If only approvals are hashed, the attempt leaves no evidence (NIST SP 800-53 Rev. 5 AC-6(9) is about logging the *use* of privileged functions, successful or not).
- **Hashing a timestamp you never publish.** A digest over a payload including `time.time()` cannot be recomputed by anyone, including you. Publish the exact UTC timestamp that went into the pre-image, or the "tamper-evident" hash is unfalsifiable decoration.
- **Hashing only the fields nobody would forge.** If the pre-image omits the secondary approver, the record of *who co-signed* can be rewritten while the stored hash still verifies. Bind every field that authorises the action, including the outcome.
- **Calling a bare SHA-256 chain immutable.** Unkeyed hashes are recomputable by whoever can rewrite the log. Tamper-*evidence* needs a key held elsewhere (HMAC-SHA-256) or an append-only/WORM sink; anything less is tamper-*resistant at best*.
- **Recording a TTL without implementing expiry.** `ttl_minutes=60` stored in a field expires nothing. Without a supervisory sweep, a "60-minute" halt is an indefinite one — the exact failure NIST SP 800-53 Rev. 5 AC-2(2) addresses for emergency accounts.
- **Comparing operator ids with `!=`.** Case and whitespace variants of one identity defeat the four-eyes rule using a single person's two consoles.
- **Check-then-act without a lock.** Two operators submitting the same `request_id` concurrently both pass a duplicate check that is not atomic with the state write, and the kill switch fires twice.
- **Leaving overrides active across a restart.** State here is in memory; a process restart silently drops every active override and its expiry. Persist active overrides, or fail closed on start-up.

## Verification

- Instantiate `EmergencyOverrideAccessEngine(clock=FrozenClock())`. Submit `KILL_SWITCH_ALL_ALGOS` with a single `RISK_OFFICER`: expect `is_approved=False` and `rejection_code == "DUAL_SIGN_OFF_REQUIRED"`. Resubmit with `RISK_OFFICER` + `HEAD_TRADER`: expect approval, `approval_mode == "DUAL_SIGN_OFF"`, a 64-hex-character chained hash, and `expires_at_utc` exactly `ttl_minutes` after `decision_timestamp_utc`.
- Submit the same request with a `JUNIOR_DEVELOPER` role: expect `UNAUTHORIZED_ROLE`. Submit with the secondary id differing only in case: expect `SELF_APPROVAL`.
- Present an unregistered 8-character break-glass token: expect `BREAK_GLASS_INVALID`, not approval. Present a valid token twice: expect the second attempt to be rejected as consumed.
- Mutate the archived request's `secondary_operator_id` and rerun `verify_audit_chain()`: expect `(False, 0)`. Drop a middle record: expect the break reported at the following index.
- Advance the clock past `expires_at` and call `expire_due_overrides()`: expect the override returned once and removed, and `is_override_active()` false at the expiry instant itself.
- Run `python -m unittest discover -s skills/emergency-manual-override-access-control/scripts` (47 tests) and confirm a 100% pass rate.

## Related Skills

- `execution-algorithm-kill-switch-integration`
- `strategy-level-kill-switch-vs-portfolio-level-kill-switch`
- `risk-control-bypass-audit-logging`
- `risk-control-configuration-change-approval-workflow`
- `segregation-of-duties-for-custody-operations`
