# Workflows for Risk Reporting for External Stakeholders

The full procedure behind `SKILL.md`. Every threshold and quotation is sourced in
`references/standards.md`.

## 0. Decide which channel you are on — before anything else

Ask one question: **is this a statutory filing?**

- **Yes** (Form PF, AIFMD Annex IV, an FCA AIF001/AIF002 return) — stop. This
  skill is the wrong tool. Those regimes require position-level detail (Form PF
  Q35: every open position at or above 5% of NAV, monthly) and counterparty-level
  detail (Q22/Q23: the five largest counterparties in each direction). Route to
  PFRD or your national competent authority. Redacting a filing does not make it
  compliant, it makes it incomplete.
- **No** (LP risk letter, prime broker exposure feed, auditor risk appendix, a
  supervisory information request answered in aggregate) — continue. On this
  channel withholding positions is correct and the failure mode is leaking them.

The engine records the answer for you: `report.disclosure_notice` carries the
statutory boundary text for the chosen recipient, and for `REGULATOR` it says in
capitals that the artefact is not a filing.

## 1. Snapshot the portfolio risk state

Pull one period-end snapshot from the risk system. Everything the report says
comes from here; the engine computes no risk of its own.

```python
from external_risk_reporter import (
    LiquidityConvention, PortfolioRiskState, StakeholderType,
    RiskReportingForExternalStakeholdersEngine, verify_report,
)

state = PortfolioRiskState(
    fund_name="ALPHA_QUANT_FUND_LP",
    report_date_iso="2026-08-05",
    total_aum_usd=50_000_000.0,
    net_asset_value_usd=50_000_000.0,
    gross_exposure_usd=125_000_000.0,      # 2.5x gross
    net_exposure_usd=15_000_000.0,         # 0.3x net, signed
    var_pct_of_nav=1.85,                   # % of NAV, positive loss magnitude
    var_confidence_pct=99.0,               # Form PF Q40(b)(i)
    var_horizon_days=1,                    # Form PF Q40(b)(ii)
    annualized_sharpe_ratio=2.1,
    max_drawdown_pct=6.5,
    top_sector_concentrations={            # % of NAV, unsorted is fine
        "ENERGY": 8.0, "TECH": 25.0, "MISC": 2.0,
        "HEALTHCARE": 10.0, "FINANCE": 15.0, "CRYPTO": 5.0,
    },
    liquidity_days_to_liquidate_pct={      # Form PF Q32 buckets, sum ~100%
        "1 day or less": 60.0,
        "2 days - 7 days": 25.0,
        "8 days - 30 days": 10.0,
        "31 days - 90 days": 5.0,
    },
    liquidity_convention=LiquidityConvention.BUCKETED,
    proprietary_positions=[                # withheld, but supply it anyway
        {"symbol": "SECRETCO", "qty": 10_000},
        {"ticker": "ZXQV", "qty": -4_000},
    ],
)
```

Construction validates and freezes. What it rejects, and why each one matters:

| Rejected | Why |
|---|---|
| `net_asset_value_usd <= 0` | It is the denominator of both leverage ratios. Version 1's `max(nav, 1.0)` turned a zero-NAV fund into 125,000,000x gross leverage — a figure denominated in one dollar, produced silently, in the exact situation where the report matters most. |
| NaN or infinity anywhere | NaN passes range checks written as `if v < lo or v > hi`, because every comparison against NaN is False. Version 1 propagated a NaN NAV straight into a sealed report. |
| `var_pct_of_nav` outside `[0, 100]` | Form PF reports VaR as a positive percentage of NAV. A negative value means a sign-convention mismatch upstream. |
| `var_horizon_days < 1`, or not an `int` | A horizon is a whole number of days and there is no zero-day VaR. |
| `max_drawdown_pct` outside `[0, 100]` | Peak-to-trough loss as a positive magnitude. |
| `gross_exposure_usd < abs(net_exposure_usd)` | gross = \|long\| + \|short\| ≥ \|long − short\| = \|net\|, always. A violation is the two fields swapped or computed on different bases — and it would report as a plausible leverage pair. |
| Σ\|sector %\| > gross exposure as % of NAV | Sector percentages are of NAV, so gross exposure bounds them. Catches percentages-of-gross mislabelled as percentages-of-NAV. |
| A liquidity profile contradicting its declared convention | See step 2. |
| `report_date_iso` that is not `YYYY-MM-DD` | `date.fromisoformat` alone accepts `20260805` and week dates on Python 3.11+. A period a recipient has to guess at is a defect in a document that will be compared against others. |
| Non-string mapping keys | A key whose representation changes between generation and verification breaks the digest, and the recipient cannot distinguish that from tampering. |

`ReportInputError` is a **failed** report. Do not send it with a caveat.

## 2. Declare the liquidity convention

The two conventions are indistinguishable from the numbers alone, and the
difference is the entire answer to "how fast can this book be wound down".

- **`BUCKETED`** (default) — Form PF Q32's schema. Each investment in exactly one
  period; buckets sum to 100% ± `LIQUIDITY_SUM_TOLERANCE_PCT` (1pp). Assign on
  the shortest period in which the position "could reasonably be liquidated at or
  near its carrying value", with no fire-sale discount, and group contingent legs
  under the least-liquid leg.
- **`CUMULATIVE`** — running totals. Non-decreasing in the order supplied (so
  supply horizons ascending), each value in `[0, 100]`.
- An empty mapping means "not reported" and passes both.

Version 1's own example, `{"1_DAY": 85.0, "7_DAYS": 100.0}`, reads naturally as
cumulative — and sums to 185% of the portfolio if read as buckets. It carried no
convention field, so neither the engine nor the recipient could tell.

## 3. Generate under a fail-closed policy

```python
engine = RiskReportingForExternalStakeholdersEngine(
    firm_name="Northwind Capital",
    hmac_key=load_secret("external-report-hmac"),   # optional; see step 5
)
report = engine.generate_external_report(state, StakeholderType.LIMITED_PARTNER)
```

| Recipient | Sectors disclosed | Basis |
|---|---|---|
| `LIMITED_PARTNER` | Top 5 by absolute exposure | LPA and side letters |
| `REGULATOR` | All | Supervisory request, aggregate — **not a filing** |
| `PRIME_BROKER` | Top 3 | Prime brokerage agreement |
| `AUDITOR` | All | Audit engagement scope |

Two behaviours to understand:

- **Ranking, not slicing.** `rank_concentrations` sorts by `abs(value)`
  descending, ties broken on sector name ascending for reproducibility. Version 1
  used `list(items)[:5]`, the first five in *insertion order*: given the mapping
  above it disclosed ENERGY/TECH/MISC/HEALTHCARE/FINANCE — keeping MISC at 2.0%
  and dropping CRYPTO at 5.0% — under a "top five" heading. Absolute value
  matters because a −40% net short is a bigger concentration than a +5% long.
- **Unknown recipients raise.** `DisclosurePolicyError`, no report. Version 1's
  `else` branch gave any unrecognised recipient the *full* breakdown: adding a
  `PROSPECTIVE_INVESTOR` enum member would have silently granted it the widest
  disclosure in the module. Adding a recipient means writing its policy.

## 4. Read the redaction result correctly

Two flags, and only one of them carries information:

- `are_proprietary_positions_redacted` — structurally always `True`. No code path
  copies `proprietary_positions` into a report. It tells you nothing about
  whether the *aggregates* are clean.
- `redaction_verified` — `True` only when the engine extracted identifiers from
  the positions you supplied and found none of them in any disclosed
  concentration key, any liquidity key, or the fund name. `False` means **not
  checked**. `redaction_note` says which of the two reasons applies:
  no positions supplied, or none of the supplied positions carried a key in
  `DEFAULT_IDENTIFIER_FIELDS`.

A hit raises `RedactionError` and returns nothing. A sector breakdown keyed
`{"AAPL": 25.0}` is a position disclosure wearing a sector label; version 1 would
have shipped it while asserting `are_proprietary_positions_redacted=True`.

Matching is whole-word and case-insensitive, so a ticker `F` is caught in a
sector label `"F"` but not inside `"FINANCE"`. If your position records name
instruments under a key the module does not know:

```python
engine = RiskReportingForExternalStakeholdersEngine(
    identifier_fields=[*DEFAULT_IDENTIFIER_FIELDS, "internal_code", "book_symbol"],
)
```

**Know the limits of this check.** It compares identifiers *you supplied* against
strings *the engine emits*. It cannot detect a leak through an identifier you did
not supply, nor a sector label that reveals a holding without naming it —
`"SPECIAL_SITUATION_1"` in a two-position book identifies a position perfectly
well. Human review of the disclosed labels is still the control; this is a
backstop under it.

## 5. Seal, and describe the seal honestly

| No key | With `hmac_key` |
|---|---|
| `content_digest`: unkeyed SHA-256 | `content_digest` **plus** `authentication_tag`: HMAC-SHA256 |
| Detects accidental corruption | Authenticates the report to any holder of the key |
| Authenticates nothing — an attacker who alters the figures recomputes the hash | Symmetric, so no non-repudiation: a verifier could have produced it |
| Useful only if the recipient gets the expected digest over a separately authenticated channel | Manage the key as a secret; rotate it |

Version 1 called its unkeyed hash a "cryptographic report signature" and covered
five fields with it: fund name, stakeholder, date, NAV, gross leverage. Changing
VaR from 1.85% to 0.10% left it byte-identical. Every metric the report exists to
communicate was outside the seal.

The digest now covers every reported field except `report_id` (derived from it)
and the envelope. Send `report.digest_covers` with the report — a digest whose
coverage is unstated is a digest the recipient cannot rely on.

Recipient side:

```python
if not verify_report(report, hmac_key=shared_key):
    raise RuntimeError("report altered in transit or key mismatch")
```

`verify_report` compares in constant time. If the report carries an HMAC tag and
you pass no key, it **raises** rather than quietly downgrading to an
integrity-only check and returning a misleading `True`.

Mapping key order is deliberately outside the seal: the canonical form sorts
keys, so a recipient who round-trips the payload through their own JSON tooling
still verifies. The ranked order of `disclosed_concentrations` is a presentation
property of the returned object, not part of the seal.

## 6. Dispatch and log

```python
dispatch_log.write(
    report_id=report.report_id,
    stakeholder=report.stakeholder_type.value,
    digest=report.content_digest,
    authentication=report.authentication,
    redaction_verified=report.redaction_verified,
    audit_notes=report.audit_notes,          # confidential: controlled log only
)
```

- `report_id` is `RPT-{recipient}-{date}-{fund}-{digest[:12]}`. A restatement of
  any figure produces a new id; a byte-identical regeneration is idempotent.
  Version 1's `RPT_{stakeholder}_{date}` collided across funds and across
  restatements — breaking the audit trail the skill claims to provide.
- The engine's own `logger.info` line carries only the id, recipient, digest
  prefix, authentication mode and redaction status. NAV, VaR, exposures and the
  audit note stay on the returned object, so **you** decide where fund financials
  land rather than having them sprayed into whatever handlers the host
  application has attached.
- Retention period: `record-retention-periods-by-jurisdiction`. Where the
  recipient is in another jurisdiction, check
  `cross-border-data-transfer-restrictions-for-trade-data` before dispatch.

## 7. Reconcile before you trust it

Reproduce the previous period's issued leverage and VaR figures from the same
inputs before relying on the engine for the current one. A discrepancy is either
a modelling error here or a definitional disagreement with your risk system —
both worth finding before an LP or an examiner finds it.
