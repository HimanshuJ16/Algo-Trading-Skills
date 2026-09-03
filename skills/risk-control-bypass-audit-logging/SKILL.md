---
name: risk-control-bypass-audit-logging
description: >-
  Use when a human can override a pre-trade or intra-trade risk control and the override
  must leave evidence: who requested, authorised and verified it, what was bypassed and
  until when. Parameter changes are audit-logging-for-configuration-changes.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: risk-bypass, audit-logging, risk-override, compliance-audit, kill-switch, position-limits, tamper-evident, segregation-of-duties
  brokers_frameworks: "SEC Rule 15c3-5; SEC Rule 17a-4(f); MiFID II RTS 6 Article 15(6); Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a trading system has pre-trade or intra-trade risk controls (position limits, daily loss limits, price collars, message-rate limits, kill switches) that a human can override, and you need the override to leave an evidence trail rather than a shrug. The engine records each bypass in an append-only SHA-256 chain, classifies its severity, and flags the patterns that make a bypass suspicious on its face: an unauthorised principal, a missing justification, an authoriser who is also the requester, an override of a critical control with no expiry, and a record whose event time runs ahead of the recording clock.

It is worth being precise about *whose* obligation this serves, because the skill's own domain is one where vague appeals to "the regulators" are common and wrong:

- **US broker-dealers with market access** are subject to SEA Rule 15c3-5. The rule requires a documented system of risk management controls under the broker-dealer's "direct and exclusive control". SEC Division of Trading and Markets FAQ No. 18 addresses the override case directly: where a threshold is reached and orders are rejected, the firm may raise the threshold in accordance with supervisory procedures, and "the reasons for such modifications should be documented and retained as part of the broker-dealer's books and records."
- **EEA and UK investment firms engaged in algorithmic trading** are subject to RTS 6. Article 15(6) is the override provision, and it is stricter than an allowlist: override arrangements apply "in relation to a specific trade on a temporary basis and in exceptional circumstances" and are "subject to verification by the risk management function and authorisation by a designated individual" — two distinct actors, a bounded scope, and a bounded duration.

Everyone else — a proprietary trader, a fund's internal system, an individual running an algorithm — has no override-logging rule pointed at them, and should use this as good operational hygiene rather than as compliance evidence.

## When NOT to Use

- **As an enforcement gate.** This engine records that a bypass happened; it does not decide whether one may happen. Authorisation enforcement belongs in the risk control itself, upstream. A system that lets `log_bypass` return before checking whether the principal was allowed has already permitted the trade.
- **As the system of record.** The hash chain is held in process memory. It is tamper-*evident*, not immutable: anything that can rewrite the process can recompute the chain. Persist entries and publish the chain head to append-only storage — see **Common Pitfalls**.
- **For automated risk-limit recalibration.** A scheduled or model-driven limit change is a configuration change with its own approval path, not an emergency override. Logging it here buries the genuine emergencies in noise. See `risk-control-configuration-change-approval-workflow`.
- **As a substitute for the escalation itself.** Recording a CRITICAL bypass is not the same as telling anyone. Wire the flags into an alerting path — see `risk-limit-breach-escalation-matrix`.
- **To generate compliance assertions for a regime the firm is not in scope of.** The engine cannot tell whether you are a broker-dealer with market access or an EEA investment firm; it will happily produce an authoritative-looking report either way.

## Prerequisites

- Bypass event details: `event_id`, timezone-aware ISO-8601 `timestamp_iso`, `bypassed_control`, `original_limit_value`, `override_value`, `authorized_by`, `justification`. Timestamps must carry an explicit UTC offset; naive local times are rejected because they cannot be ordered across a DST transition.
- Ideally also `requested_by`, `risk_function_verifier`, and `expires_at_iso` — the three fields that let the record answer the questions RTS 6 Article 15(6) actually asks. They default to empty for backward compatibility, which means an incomplete record still logs; it simply proves less.
- The firm's own authorised-principal list. The module default (`risk_officer`, `cro`, `head_of_trading`, `system_admin`) is an illustrative example, not a policy.
- Calibrated engine options. `min_justification_chars` defaults to 5 and has **no regulatory basis**; `require_risk_function_verification` and `require_expiry_for_critical` default to `False` because they encode jurisdiction-specific RTS 6 expectations. Firms in scope of RTS 6 should turn both on.
- Append-only storage for the persisted chain (WORM, an object store with object-lock, or a write-only sink the trading host cannot delete from).

## Workflow

1. **Capture the Bypass at the Point It Happens, Not Afterwards**: Call `log_bypass` on the same code path that applies the override, and pass `recorded_at` explicitly so the result is reproducible. Recording later loses exactly what a forensic reader needs: the gap between when the bypass occurred and when it was written down. That gap is itself a signal, which is why the engine stores `timestamp_iso` and `recorded_at_iso` separately and flags an event timestamp that runs ahead of the recording clock.
2. **Let Structural Failures Raise — Never Swallow Them**: A missing `event_id`, an unparseable or timezone-naive timestamp, or a resubmitted id carrying different content raises `RiskBypassAuditError`. Fix the record and resubmit. Catching the exception and continuing produces the one outcome the trail exists to prevent: a bypass that happened and was never recorded.
3. **Treat a Retry as a Retry, Not a Second Bypass**: Resubmitting an identical `event_id` with identical content returns the original entry unchanged. One override that was written twice because an acknowledgement was lost must appear once, or the counts a regulator reads are wrong.
4. **Classify Severity from Registered Control Names**: `CRITICAL` for controls whose bypass removes a capital-protection or halt mechanism (kill switch, loss limits, VaR limit, margin-call halt); `HIGH` for the RTS 6 Article 15(1) mandated pre-trade controls (price collars, maximum order values, maximum order volumes, message limits). The `"LIMIT"`/`"CAP"` substring rule is only a fallback for names you have not registered — register the firm's real control names rather than relying on it.
5. **Flag the Governance Failures, Not Just the Unauthorised Ones**: An override authorised by someone on the allowlist can still be improper. Self-authorisation (requester equals authoriser) defeats the separation RTS 6 Article 1(c) requires "to ensure that unauthorised trading activity cannot be concealed", and an open-ended override of a critical control is a permanent disablement wearing an override's clothes.
6. **Verify and Publish the Chain**: Call `verify_integrity()` before relying on a report, and publish `chain_head_hash` to append-only storage on a cadence. A chain nobody anchors externally proves only that the entries are consistent with each other, which is exactly what a tamperer would also arrange.
7. **Generate the Report — Which Reads Verdicts, Never Re-derives Them**: `generate_audit_report()` reproduces the severity and suspicion verdict fixed at log time, together with `integrity_verified` and the chain head.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling a Hash Chain "Immutable"**: A SHA-256 chain makes an edit *detectable* by anyone holding an earlier chain head. It does nothing against an attacker who edits a record and recomputes every hash after it — which is trivial when the whole chain lives in one process. Immutability comes from where the record is written, not from how it is hashed. Under SEA Rule 17a-4(f)(2)(i)(A) the electronic record must be preserved either in a non-rewriteable, non-erasable (WORM) format or under the audit-trail alternative, which requires a complete time-stamped trail of all modifications and deletions, the date and time of each create/modify/delete action, and the identity of the individual responsible. Publish chain heads out to that storage; do not claim the in-memory object satisfies it.
- **Re-deriving the Verdict at Report Time**: If the report recomputes severity or suspicion instead of reading what was decided at log time, one event can carry two different verdicts inside one regulatory record. That is worse than either verdict alone, and it is invisible until a regulator reads both. The engine computes once, stores, and reports the stored value — and the hash covers the verdict, so a later reclassification is detectable rather than silent.
- **Treating an Allowlist as Segregation of Duties**: An allowlist answers "may this person authorise overrides?" It does not answer "did the person who wanted the override approve their own request?" A head of trading who requests and authorises their own limit increase passes an allowlist check and fails RTS 6 Article 1(c). Populate `requested_by`; without it the self-authorisation check has nothing to compare.
- **Logging the Override but Not Its Duration**: RTS 6 Article 15(6) permits overrides "in relation to a specific trade on a temporary basis". An entry with no `expires_at_iso` cannot distinguish a five-minute exception for one block trade from a kill switch that has been off since March. Set `require_expiry_for_critical=True` and record the expiry.
- **Accepting a Justification Because It Is Long Enough**: `min_justification_chars` catches an empty field and nothing else. "Need more room" clears the default threshold and explains nothing. No length check can assess adequacy; the control is human review of the flagged entries, on a cadence, by someone who did not authorise them.
- **Swallowing `RiskBypassAuditError` in the Order Path**: The temptation under load is to wrap the logging call in a bare `except` so a malformed audit record can never block a trade. That converts a data-entry bug into a missing audit record on precisely the events most likely to be malformed — the rushed, unusual, suspicious ones. Route the failure to an operator instead.
- **Timezone-Naive Timestamps**: An audit trail is evidence because it can be ordered. Naive local timestamps are ambiguous for one hour every autumn, and that hour is not reliably the quiet one. The engine rejects them.
- **Assuming the Retention Period**: RTS 6 Article 28(3)'s five years applies to the HFT *order* records of Article 28, not to override records; those fall under the firm's general MiFID retention (Delegated Regulation (EU) 2017/565 Article 72 — five years, extendable to seven at a competent authority's request). A US broker-dealer's periods come from SEA Rule 17a-4 instead. Confirm the applicable period with counsel rather than copying a number from a code comment.

## Verification

- Log a `SPREAD_VETO` bypass and confirm `log_bypass` returns `MEDIUM` **and** `generate_audit_report().entries[0].severity` is also `MEDIUM` — the two must never diverge.
- Log a bypass by `unknown_user` with an empty justification and confirm the report entry still carries a non-`None` `flag_reason` naming both defects, and that `justification=None` produces a report rather than an `AttributeError`.
- Log `MAX_ORDER_VALUE` and confirm `HIGH`, not `MEDIUM` — it is an RTS 6 Article 15(1) mandated control whose name contains neither "LIMIT" nor "CAP".
- Log two events, mutate a stored event in place, and confirm `verify_integrity()` returns `(False, reason)` naming the affected `event_id` and the report sets `integrity_verified=False`.
- Resubmit an identical event and confirm the total stays at 1; resubmit the same `event_id` with a different `override_value` and confirm `RiskBypassAuditError`.
- Set `requested_by` equal to `authorized_by` and confirm the self-authorisation flag fires even though the principal is on the allowlist.
- Submit a timezone-naive `timestamp_iso` and confirm `RiskBypassAuditError`, and that the rejected event did not advance `chain_head_hash`.
- Run `python -m unittest discover -s skills/risk-control-bypass-audit-logging/scripts` and confirm a 100% pass rate.

## Related Skills

- `risk-control-configuration-change-approval-workflow`
- `kill-switch-and-drawdown-circuit-breakers`
- `sec-rule-15c3-5-risk-controls-us`
- `mifid-ii-algo-trading-compliance-eu`
- `risk-limit-breach-escalation-matrix`
- `structured-logging-for-post-incident-forensics`
- `record-retention-periods-by-jurisdiction`
- `emergency-manual-override-access-control`
