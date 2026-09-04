---
name: risk-reporting-for-external-stakeholders
description: >-
  Use when risk figures are about to leave the firm to an investor letter, a prime
  broker feed or a supervisor, and position-level detail must be aggregated away.
  Regulatory filings such as Form PF need the detail this withholds.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: external-risk-reporting, lp-reporting, information-barrier, position-redaction, sec-form-pf, aifmd-annex-iv, report-integrity
  brokers_frameworks: "SEC Form PF (17 CFR 275.204(b)-1); AIFMD Annex IV (Reg. (EU) 231/2013); FCA SUP 16.18; NIST FIPS 198-1 (HMAC); Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when aggregate risk figures are about to cross the firm's
information barrier and you are building the step that decides what goes and
what stays. Four channels are modelled:

- **Limited partners / institutional investors** — the monthly or quarterly risk
  letter. Scope is set by the LPA and side letters, not by rule.
- **Regulators** — a supervisory information request answered in aggregate.
- **Prime brokers** — the daily gross/net exposure and liquidity feed that
  drives margin and credit monitoring.
- **Auditors** — the aggregate risk appendix to an audit.

What the engine actually does is narrow and worth stating plainly: it computes
two leverage ratios, validates that the risk figures you hand it are internally
consistent and expressible, ranks and truncates the sector breakdown according
to the recipient's policy, **checks** that no withheld position identifier
reached the disclosed aggregates, and seals the result so the recipient can
detect alteration.

## When NOT to Use

- **You are producing a Form PF or AIFMD Annex IV filing.** This is the most
  important boundary in the skill, and it is easy to get backwards. Those
  regimes require exactly the detail this module withholds. Form PF Question 35 (section
  2b): "For each open position of the reporting fund that represents 5% or more
  of the reporting fund's net asset value, provide the information requested
  below" — monthly, with sub-asset class. Questions 22 and 23 require the five
  largest counterparties by mark-to-market exposure in each direction. AIFMD
  Annex IV requires principal exposures and most-traded instruments per AIF.
  Redacting positions out of a statutory filing is not information-barrier
  hygiene, it is an incomplete filing. Those go through PFRD (Form PF) or the
  AIFM's national competent authority, and are protected there by the regime's
  own confidentiality provisions — the SEC "does not intend to make public
  information reported on Form PF that is identifiable to any particular adviser
  or private fund".
- **You want the risk metrics computed.** The engine calculates no VaR, no
  Sharpe, no drawdown, no sector exposure, no days-to-liquidate. It consumes
  what your risk system produces. See `value-at-risk-var-live-monitoring` and
  `real-time-liquidity-risk-monitoring` for the upstream.
- **You need AIFMD leverage.** `gross_leverage` and `net_leverage` here are
  exposure ÷ NAV. AIFMD leverage is the gross method (Art. 7) and the commitment
  method (Art. 8) of Regulation (EU) No 231/2013, which require
  instrument-by-instrument conversion this module does not perform. Do not put
  these numbers in an Annex IV leverage field.
- **You need non-repudiation — proof to a third party that the firm issued this
  report.** An unkeyed digest cannot do that and neither can an HMAC, which is
  symmetric: anyone who can verify it could have produced it. Use an asymmetric
  signature (NIST FIPS 186-5) if a recipient must be able to prove authorship to
  someone else.
- **You want a delivery mechanism.** The engine returns a dataclass. Rendering,
  encryption in transit, and the dispatch log are yours.

## Prerequisites

- **Risk figures for one fund, for one period end**, from your risk system:
  `fund_name`, `report_date_iso` (`YYYY-MM-DD`), `total_aum_usd`,
  `net_asset_value_usd` (strictly positive), `gross_exposure_usd`,
  `net_exposure_usd` (signed), `annualized_sharpe_ratio`, `max_drawdown_pct`,
  `top_sector_concentrations`, `liquidity_days_to_liquidate_pct`.
- **VaR with its parameters, not on its own.** `var_pct_of_nav` (a positive
  percentage of NAV), `var_confidence_pct` and `var_horizon_days` are all
  required. Form PF Q40(b) makes the filer state the confidence interval and the
  time horizon precisely because they vary between filers; VaR is "the loss over
  a target horizon that will not be exceeded at some specified confidence
  level", and a number without those two is not comparable to anyone else's.
- **A declared liquidity convention.** `LiquidityConvention.BUCKETED` (the
  default, matching Form PF Q32's non-overlapping buckets summing to ~100%) or
  `CUMULATIVE` (running totals, ascending horizon order).
- **The position list you are withholding**, in `proprietary_positions`. Optional
  but strongly recommended: the engine never copies it into the report, and
  having it is the only thing that lets the engine *verify* redaction rather
  than merely assert it.
- Python 3.10+ (dataclasses, ordered dicts). Standard library only.

## Workflow

1. **Build and validate the state first; a rejected state is a failed report.**
   - `PortfolioRiskState` validates at construction and is frozen, so an
     invalid state cannot exist to be reported. NaN and infinity are rejected
     before they reach a ratio, non-positive NAV is rejected outright, VaR
     outside `[0, 100]` percent of NAV is rejected, and `gross_exposure_usd`
     below `|net_exposure_usd|` is rejected because gross = |long| + |short|
     is never smaller than |long − short|.
   - Treat `ReportInputError` as a failed disclosure, not one to send with a
     caveat.

2. **Declare the liquidity convention rather than letting the recipient guess.**
   - Under `BUCKETED`, buckets must sum to 100% ± 1pp. Under `CUMULATIVE`, the
     series must be non-decreasing in the order supplied and end at or below
     100%. An empty mapping means "not reported" and passes.
   - The two are indistinguishable from the numbers alone. Version 1's own
     example, `{"1_DAY": 85.0, "7_DAYS": 100.0}`, reads as cumulative but sums
     to 185% as buckets — and the engine had no idea which was meant.

3. **Let the engine rank the sector breakdown. Do not pre-slice it.**
   - `rank_concentrations` sorts by **absolute** exposure, descending, ties
     broken on sector name. Absolute because a −40% net short is a larger
     concentration than a +5% long, and ranking on the signed value drops the
     short off the bottom of a top-5.
   - Version 1 took `list(concentrations.items())[:5]` — the first five in
     *insertion order*. Given the unsorted mapping in this skill's own test
     fixture it disclosed ENERGY/TECH/MISC/HEALTHCARE/FINANCE, keeping MISC at
     2.0% and dropping CRYPTO at 5.0%, under a heading that says "largest five".

4. **Route through a policy that fails closed.**
   - `resolve_policy` raises `DisclosurePolicyError` for any recipient without an
     explicit entry. Version 1 routed unknown recipients into an `else` branch
     that disclosed the **full** breakdown, so a newly added
     `PROSPECTIVE_INVESTOR` member would have silently received the widest
     disclosure in the module. Verified: six of six sectors.
   - Adding a recipient type means writing its policy, not inheriting a default.

5. **Read `redaction_verified`, not `are_proprietary_positions_redacted`.**
   - `are_proprietary_positions_redacted` is structurally always `True` — no code
     path copies positions into a report. It carries no information.
   - `redaction_verified` is `True` only when the engine actually checked
     supplied identifiers against every disclosed concentration key, liquidity
     key and the fund name, and found none. `False` means **not checked**, never
     "checked and clean"; `redaction_note` says which.
   - A leak raises `RedactionError` and no report is returned. An information
     barrier that logs a warning and sends the document anyway is not a barrier.

6. **Say which envelope you actually have.**
   - With no key: `content_digest` is an unkeyed SHA-256. It detects corruption
     and nothing else — anyone can recompute it over content they altered — and
     it is only useful if the recipient gets the expected value over a channel
     you authenticate separately. `authentication` says so in words.
   - With `hmac_key`: an HMAC-SHA256 tag (NIST FIPS 198-1) that authenticates the
     report to any holder of the key. Manage the key through
     `centralized-secrets-management-vault-integration`.
   - The digest covers every reported field except `report_id` (derived from it)
     and the envelope itself. `report.digest_covers` lists them; send it, because
     a digest whose coverage is unstated is a digest the recipient cannot rely on.

7. **Persist `report_id` and `audit_notes` to a controlled dispatch log.**
   - `report_id` ends in the digest prefix, so a restatement of any figure gets a
     new id while a byte-identical regeneration is idempotent. Version 1's id was
     `RPT_{stakeholder}_{date}` — two funds on the same date collided.
   - The engine logs only the id, recipient, digest prefix and redaction status.
     NAV, VaR and exposures stay on the returned object so you decide where they
     land, rather than spraying fund financials into whatever handlers the host
     application has attached. Retention: `record-retention-periods-by-jurisdiction`.

> Full step-by-step procedure: see `references/workflows.md`.
> Source behind every claim: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Treating a redacted aggregate report as a regulatory filing.** The costliest
  error available here. Form PF Q35 and Q22/Q23, and AIFMD Annex IV, require the
  position- and counterparty-level detail this report withholds by design. A
  filing satisfied with this artefact is an incomplete filing.
- **Calling an unkeyed SHA-256 a signature.** Version 1's field was
  `report_signature`. A hash with no key authenticates nobody; an attacker who
  alters the figures simply recomputes it. Authentication needs a key.
- **Sealing a digest that does not cover the payload.** Version 1 hashed five
  fields — fund, stakeholder, date, NAV, gross leverage. Verified: changing VaR
  from 1.85% to 0.10% left the "signature" byte-identical. Every risk metric the
  report exists to communicate was outside the seal.
- **Slicing insertion order and calling it "top N".** Ranking is not free; a dict
  that happens to be sorted in your test fixture will not be sorted in
  production.
- **Guarding a NAV denominator with `max(nav, 1.0)`.** Version 1 did. A zero-NAV
  fund reported gross leverage of 125,000,000x — exposure denominated in one
  dollar — and a NaN NAV propagated NaN into a sealed, dispatched report. The
  situation where the guard fires is the situation where the report matters most.
- **Reporting VaR without its confidence level and horizon.** A field named
  `daily_var_99_pct` hard-codes two parameters that Form PF Q40(b) requires the
  filer to state, and silently mislabels a 10-day 95% figure as a 1-day 99% one.
- **Publishing a liquidity profile without saying whether it is bucketed or
  cumulative.** 85 and 100 mean very different things under the two readings, and
  the difference is the whole answer to "how fast can this book be wound down".
- **Ranking sector concentration on the signed value.** A large net short is a
  large concentration. Ranking without `abs()` hides exactly the exposure a
  stressed LP is asking about.
- **Letting an unknown recipient fall through to a default.** In a disclosure
  path, the fail-open default is the wrong one. Silence is not consent.
- **Reading `redaction_verified=False` as "no positions leaked".** It means the
  check did not run. Supply `proprietary_positions`, and extend
  `identifier_fields` if your position records name instruments under a key the
  module does not know — every field left out is a field the check cannot cover.
- **Assuming the redaction check is exhaustive.** It compares identifiers you
  supplied against strings the engine emits. It cannot detect a leak through an
  identifier you did not supply, or through a sector label that reveals a single
  holding without naming it (`"SPECIAL_SITUATION_1"` in a two-position book).
- **Logging the audit note at INFO in a shared application logger.** It contains
  NAV, VaR and leverage for a fund that has not published them.

## Verification

- Run the unit suite:
  `python -m unittest discover -s skills/risk-reporting-for-external-stakeholders/scripts`
  — all tests must pass.
- Feed `gross_exposure_usd=125_000_000`, `net_exposure_usd=15_000_000`,
  `net_asset_value_usd=50_000_000` and confirm `gross_leverage == 2.5` and
  `net_leverage == 0.3` by hand.
- **The ranking regression.** Pass sectors in unsorted order —
  `{"ENERGY": 8, "TECH": 25, "MISC": 2, "HEALTHCARE": 10, "FINANCE": 15,
  "CRYPTO": 5}` — request a `LIMITED_PARTNER` report, and confirm the disclosure
  is `TECH, FINANCE, HEALTHCARE, ENERGY, CRYPTO`. If `MISC` appears, you are
  slicing insertion order.
- **The digest-coverage regression.** Generate a report, alter `var_pct_of_nav`
  on a copy, and confirm `verify_report` returns `False`. Under version 1 the
  digest was unchanged.
- Confirm `net_asset_value_usd` of `0.0`, `-1.0` and `float("nan")` each raise
  `ReportInputError` rather than producing a leverage figure.
- Confirm a `BUCKETED` profile of `{"1_DAY": 85.0, "7_DAYS": 100.0}` is rejected
  (185%), and that the same numbers are accepted under `CUMULATIVE`.
- Confirm a sector breakdown keyed with a withheld ticker raises `RedactionError`
  and returns no report, and that a report generated with
  `proprietary_positions=None` reports `redaction_verified=False`.
- Confirm an unknown recipient raises `DisclosurePolicyError` rather than
  receiving the full breakdown.
- With `hmac_key` set, confirm `verify_report(report, hmac_key=wrong)` is `False`
  and that `verify_report(report)` with no key **raises** rather than returning a
  misleading `True`.
- Against your own fund: reproduce the leverage and VaR figures on last period's
  filed or issued report from the same inputs before trusting the engine on this
  period's. A discrepancy is either a modelling error here or a definitional
  disagreement with your risk system, and both are worth finding before an LP does.

## Related Skills

- `multi-strategy-reporting-consolidation-for-stakeholders`
- `regulatory-capital-requirement-tracking`
- `value-at-risk-var-live-monitoring`
- `real-time-liquidity-risk-monitoring`
- `risk-model-backtesting-against-realized-outcomes`
- `concentration-risk-single-name-limits`
- `record-retention-periods-by-jurisdiction`
- `centralized-secrets-management-vault-integration`
- `cross-border-data-transfer-restrictions-for-trade-data`
