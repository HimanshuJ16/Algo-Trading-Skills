# Deep Workflow Reference — regulatory-custody-requirements-by-jurisdiction

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Establish jurisdiction *and* asset scope

`CustodySetup.jurisdiction` alone is not enough to select a rule. The engine
resolves `"<JURISDICTION>:<ASSET_SCOPE>"`, where scope is `SECURITIES` or
`CRYPTO`, because the same regulator applies materially different rules to
different asset classes:

- `US:SECURITIES` and `US:CRYPTO` share Rule 206(4)-2 but only the crypto regime
  admits the conditional state-chartered-trust route.
- `EU:CRYPTO` is MiCA. EU securities custody is MiFID II and AIFMD Art. 21, which
  is **not modelled** and returns `UNSUPPORTED_REGIME`.
- `UK:SECURITIES` is CASS 6 today. `UK:CRYPTO` is CASS 17 and does not commence
  until 2027-10-25.
- `SG:CRYPTO` is the Payment Services Act DPT regime, not the Securities and
  Futures Act capital markets regime.

Pass `regime_id` explicitly to override resolution. An unknown jurisdiction
returns `UNKNOWN_JURISDICTION`; a known jurisdiction with an unmodelled scope
returns `UNSUPPORTED_REGIME`. Both are non-compliant outcomes, because "we do not
model this" must never read as "this passed".

### 2. Populate evidence honestly

Every evidence attribute is `Optional[bool]` and defaults to `None`:

| Outcome | Meaning | Reported as |
|---|---|---|
| `True` | Evidenced and satisfied | `satisfied_requirements` |
| `False` | Checked and breached | violation, severity `MANDATORY` |
| `None` | Not evidenced | violation, severity `UNEVIDENCED` |

The `UNEVIDENCED` severity exists so a reviewer can tell "we have a problem" from
"we have not looked". Both block compliance; only one is a remediation item for
the custodian rather than for the reviewer.

`has_annual_audit` means different things in different regimes, and the finding
says which: an Advisers Act **surprise examination** under `US:*`, an auditor's
**client assets report** under `UK:*`. Do not carry a value across regimes
without re-reading what it is asserting.

### 3. Let codified exceptions run before findings

The engine checks each requirement's exception first. Where one applies, the
requirement is skipped and the exception is recorded verbatim in
`exemptions_applied` — a report has to show not just that a requirement was not
flagged but why.

| Requirement | Exception | Provision |
|---|---|---|
| `ANNUAL_SURPRISE_EXAMINATION` | `custody_solely_for_fee_deduction` | 206(4)-2(b)(3) |
| `ANNUAL_SURPRISE_EXAMINATION` | `pooled_vehicle_audited_within_120_days` | 206(4)-2(b)(4) |

Separately, some requirements are **conditional** — they are not engaged at all
unless the facts call for them:

| Requirement | Engaged when |
|---|---|
| `INTERNAL_CONTROL_REPORT` (206(4)-2(a)(6)) | `custody_type == AFFILIATED_CUSTODIAN` or `custodian_is_related_person` |
| `STATE_TRUST_NO_ACTION_CONDITIONS` | `custody_type == STATE_CHARTERED_TRUST` (US crypto only) |

A conditional requirement demanded of everyone generates noise; one never
demanded generates a blind spot. Both are failures.

### 4. Evaluate the MiCA Article 67 higher-of test properly

```
required = max(EUR 125,000 (Annex IV Class 2),
               0.25 * fixed overheads of the preceding year)
```

The safeguard may be own funds, a qualifying insurance policy, or a comparable
guarantee — `prudential_safeguard_eur` is whichever the CASP relies on.

The check deliberately returns **not evidenced** rather than "satisfied" when
`fixed_overheads_prior_year_eur` is missing and the safeguard clears the Annex IV
floor: passing on the floor alone would let a CASP with EUR 4m of fixed overheads
present EUR 125,000 as compliance. It returns a definite **breach** when the
safeguard is below the floor, because no overheads figure can rescue that.

### 5. Keep guidance out of the violations list

`CustodyRequirement.mandatory=False` marks a supervisory expectation. MAS's 90%
cold-storage expectation is the modelled example: a shortfall lands in
`advisories`, and `is_compliant` stays true. The distinction is load-bearing —
a report where a guidance shortfall and an unconstituted statutory trust look
identical is a report nobody triages.

### 6. Date every audit

Pass `as_of`. It defaults to today only as a convenience.

For a regime that is made but not in force at `as_of` — currently `UK:CRYPTO`
before 2027-10-25 — the status is `PRE_COMMENCEMENT_READINESS`, findings move to
`advisories`, and `audit_notes` states the commencement date and the number of
readiness gaps. `is_compliant` still tracks whether those gaps are closed, so a
forward-looking audit of an unprepared firm cannot be mistaken for a pass.

### 7. Reject malformed input instead of scoring it

`CustodyRegimeError` is raised for: a blank jurisdiction or custodian name, an
unrecognised `custody_type` or `asset_scope`, a `cold_storage_pct` outside
[0, 100] or non-finite, a negative or non-finite monetary amount, and a non-date
`as_of`. The `custody_type` case matters most: silently treating
`"QUALIFED_CUSTODIAN"` as a non-qualifying arrangement would turn a typo into a
regulatory finding.

## Production Implementation Reference

- Reference code: `scripts/regulatory_custody_requirements_by_jurisdiction.py`
  (`RegulatoryCustodyRequirementsByJurisdictionEngine`, `CustodySetup`,
  `CustodyRuleSpec`, `CustodyRequirement`, `CustodyViolation`,
  `JurisdictionalCustodyAuditReport`, `CustodyRegimeError`).
- Automated unit tests:
  `scripts/test_regulatory_custody_requirements_by_jurisdiction.py`.
- Extend with `custom_rules={"<REGIME_ID>": CustodyRuleSpec(...)}`; the dict key
  must equal the spec's `regime_id`, and mismatches raise rather than registering
  a regime nothing will ever resolve to.

## Known Limitations

- **Not a legal determination.** Qualified custodian status, and whether a trust
  is validly constituted, are conclusions for counsel.
- **Six regimes only.** US broker-dealer customer protection (15c3-3), EU custody
  of financial instruments (MiFID II, AIFMD Art. 21), and SG capital markets
  services custody under the Securities and Futures Act are out of scope and
  reported as such.
- **Self-asserted inputs.** The engine cannot detect a custodian misrepresenting
  its own licence status, trust arrangements, or capital position. It audits what
  it is told, and says when it has been told nothing.
- **MAS licensee audit obligations are not modelled**, because their precise
  scope was not verified in this review. Their absence from the ruleset is a gap,
  not a finding that no obligation exists.
- **Dated content.** The UK cryptoasset regime commences on a known future date;
  MiCA's Member State transitional positions vary; the 2025-09-30 US staff
  no-action relief is revocable. Re-verify `references/standards.md` before
  relying on a report.
