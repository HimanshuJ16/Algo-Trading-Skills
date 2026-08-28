# Workflows — risk-control-bypass-audit-logging

## 0. Decide what the record is for, before writing any

Two different artefacts get called an "override log", and conflating them is the
most common way this goes wrong:

- **The evidence trail** — a small number of genuine emergency overrides, each one
  exceptional, each one expected to be read by a human. That is what this engine
  is for.
- **The configuration change history** — scheduled recalibrations, model-driven
  limit updates, new-instrument onboarding. High volume, routine, approved through
  a change process. Logging these here buries the emergencies. Route them to
  `risk-control-configuration-change-approval-workflow` instead.

If your override log has hundreds of entries a day, you are recording the second
thing and calling it the first.

## 1. Configure the engine to the firm, not to the defaults

```python
engine = RiskControlBypassAuditEngine(
    authorized_principals={"a.patel", "j.okafor", "cro"},   # real designated individuals
    critical_controls={"KILL_SWITCH", "DAILY_LOSS_LIMIT", "HOUSE_MARGIN_HALT"},
    high_severity_controls={"PRICE_COLLAR", "MAX_ORDER_VALUE", "MSG_RATE_CAP"},
    min_justification_chars=5,          # no regulatory basis; calibrate or ignore
    require_risk_function_verification=True,   # RTS 6 Art. 15(6) firms
    require_expiry_for_critical=True,          # RTS 6 Art. 15(6) "temporary basis"
)
```

Register the firm's actual control names. The `"LIMIT"`/`"CAP"` substring fallback
exists so an unregistered name does not silently vanish into `MEDIUM`; it is not a
classification policy. `MAX_ORDER_VALUE` — a control RTS 6 Article 15(1) requires —
contains neither substring, which is why the mandated four are seeded explicitly.

## 2. Capture at the point of override

Call `log_bypass` on the same code path that applies the override, not from a
nightly reconciliation job.

```python
entry = engine.log_bypass(
    RiskBypassEvent(
        event_id=f"BYP-{uuid4()}",
        timestamp_iso=override_applied_at.isoformat(),   # tz-aware
        bypassed_control="DAILY_LOSS_LIMIT",
        original_limit_value="50000",
        override_value="75000",
        requested_by="desk.trader",
        authorized_by="cro",
        risk_function_verifier="risk.control.desk",
        justification="Hedge unwind for the 14:30 index roll; CRO approval ref RM-2291.",
        expires_at_iso=(override_applied_at + timedelta(hours=1)).isoformat(),
        strategy_id="STRAT_INDEX_ROLL",
        instrument="ESZ4",
    ),
    recorded_at=datetime.now(timezone.utc),
)
```

Pass `recorded_at` explicitly. The engine stores it separately from
`timestamp_iso`, and the gap between the two is forensic information: a record
written forty minutes after the override tells a reader something a single
timestamp cannot.

## 3. Handle the failure modes deliberately

| Situation | Engine behaviour | What the caller must do |
|---|---|---|
| Missing `event_id`, blank control, unparseable or naive timestamp | Raises `RiskBypassAuditError`; nothing is appended | Fix the record and resubmit. Never catch-and-continue: the bypass happened whether or not it was recorded |
| Same `event_id`, identical content | Returns the original entry; no second record | Nothing — this is a retried write being absorbed correctly |
| Same `event_id`, different content | Raises `RiskBypassAuditError` | Investigate. An audit record is never restated; log a new corrective event that references the original |
| Blank `authorized_by` | Recorded and flagged `"No authorising principal recorded."` | Escalate. The record is deliberately kept rather than rejected — an unattributed bypass is evidence, not noise |

The asymmetry is the point: structural defects that make a record unusable raise,
because a record nobody can address or order proves nothing. Governance defects
that make a record *damning* are flagged and kept.

## 4. Read the flags

| Flag | What it means | Typical response |
|---|---|---|
| `Unauthorized principal '<x>' bypassed control.` | Authoriser not on the allowlist | Immediate escalation; check whether the control was actually bypassed or the record is misattributed |
| `No authorising principal recorded.` | Nobody named | Immediate escalation |
| `Missing or insufficient justification.` | Below `min_justification_chars` | Obtain the justification while the context is fresh; a written justification obtained a week later is a reconstruction |
| `Self-authorised: requester and authoriser are the same individual.` | Fails RTS 6 Art. 1(c) separation | Escalation regardless of the authoriser's seniority |
| `No risk management function verification recorded.` | RTS 6 Art. 15(6) verification missing | Escalation for in-scope firms |
| `Open-ended bypass of a critical control (no expiry recorded).` | No `expires_at_iso` | Set an expiry or convert to a tracked configuration change |
| `Override expiry is not after the bypass timestamp.` | Expiry at or before the event | Data error, or a backfilled record — check which |
| `Event timestamp is ahead of the recording clock...` | Forward-dated, or host clock skew | Check host clock sync first; if clocks are good, the record was forward-dated |

## 5. Verify, then report

```python
ok, reason = engine.verify_integrity()
report = engine.generate_audit_report()   # also sets report.integrity_verified
```

`generate_audit_report()` reads the verdicts fixed at log time. It does not
re-derive severity or suspicion — a report that recomputed them could contradict
the entry it summarises, and the hash covers the verdict so a later reclassification
shows up as an integrity failure rather than passing silently.

## 6. Anchor the chain externally

The chain is tamper-*evident* only relative to a chain head someone else holds.
On a defined cadence:

1. Serialise new entries and append them to storage the trading host cannot rewrite
   (WORM, object-lock bucket, write-only log sink).
2. Publish `engine.chain_head_hash` to that same storage, or to a separate system —
   compliance, an internal ledger, a timestamping service.
3. On the next run, verify the newly loaded prefix still hashes to the previously
   published head.

Without step 2 the chain proves only internal consistency, which a tamperer would
also arrange.

## 7. Review on a cadence, by someone who did not authorise

Have flagged entries reviewed by a function independent of the authorisers, and
record the disposition of each. The cadence is firm policy — no cited rule
prescribes daily. What matters is that every flagged entry ends in one of three
states: legitimate and explained, a data error corrected by a new record, or an
incident.
