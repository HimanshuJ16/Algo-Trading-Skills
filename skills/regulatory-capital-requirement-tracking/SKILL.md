---
name: regulatory-capital-requirement-tracking
description: >-
  Use when a trading entity must stay above a prudential capital floor such as SEC Rule
  15c3-1 net capital or FCA MIFIDPRU, and you are building the daily computation that
  proves it did at all times.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: risk-management
  tags: regulatory-capital, net-capital-rule, sec-15c3-1, sec-17a-11, fca-mifidpru, capital-adequacy, financial-compliance
  brokers_frameworks: "SEC Rule 15c3-1 (Net Capital); SEC Rule 17a-11 (Notification); FCA MIFIDPRU (IFPR); Python Standard Library"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Invoke this skill when a trading entity must hold capital above a floor set by a
regulator, and you are building the daily computation that proves it does.
17 CFR 240.15c3-1(a) is the archetype: "Every broker or dealer must **at all
times** have and maintain net capital no less than the greater of the highest
minimum requirement applicable to its ratio requirement under paragraph (a)(1)
of this section, or to any of its activities under paragraph (a)(2) of this
section". "At all times" is what makes this a monitoring problem rather than a
month-end reporting one.

Two regimes are modelled directly:

- **SEC Rule 15c3-1** — US broker-dealers. Net capital per (c)(2), against the
  greater of the (a)(2) dollar minimum and the (a)(1) ratio requirement.
- **FCA MIFIDPRU** — UK investment firms. Own funds against MIFIDPRU 4.3.2R,
  "the highest of" the permanent minimum capital requirement, the fixed
  overheads requirement, or the K-factor requirement.

The engine also reports **which component binds** — for a UK firm dealing on own
account, whether the GBP 750,000 PMR (MIFIDPRU 4.4) or a growing fixed overheads
requirement is the thing actually constraining the business.

## When NOT to Use

- **You are a bank under Basel III.** Basel III ¶50 requires that "Common Equity
  Tier 1 must be at least 4.5% of risk-weighted assets at all times", Tier 1
  "at least 6.0%" and Total Capital "at least 8.0%", plus "a capital
  conservation buffer of 2.5%, comprised of Common Equity Tier 1" (¶129). Those
  are three simultaneous ratio tests against three different definitions of
  eligible capital. A single net-capital scalar cannot represent them. You can
  test one tier at a time by passing `ratio × RWA` as a component and that
  tier's own funds as the capital figure, but the answer then speaks only to the
  tier you translated. Version 1 of this skill claimed Basel III support it did
  not have; that claim is withdrawn.
- **You want the requirement computed for you.** The engine does not compute
  aggregate indebtedness, aggregate debit items, fixed overheads, or K-factors,
  and does not look up haircut percentages. Those come from your books, your
  accountants, and the rule text. It compares the numbers you produce.
- **You want an accounting classification of allowable vs. non-allowable
  assets.** 15c3-1(c)(2)(iv) covers "fixed assets and assets which cannot be
  readily converted into cash". Deciding what falls in that set is a FinOps and
  audit judgement, not a function call.
- **You want intraday enforcement of trading limits.** This reports a capital
  position; it does not stop an order. Wire the result into
  `kill-switch-and-drawdown-circuit-breakers` or
  `capital-preservation-mode-for-degraded-conditions` if you need it to act.
- **You are tracking broker margin rather than firm capital.** Maintenance
  margin at your executing broker is
  `broker-account-margin-call-handling`; this skill is the entity's own
  prudential floor.

## Prerequisites

- **Balance-sheet figures** from your books and records, in one currency (the
  engine performs no FX conversion): total assets, total liabilities,
  non-allowable assets (15c3-1(c)(2)(iv)), securities haircuts
  ((c)(2)(vi)), and qualifying subordinated debt ((c)(2)(ii), Appendix D).
- **The requirement components that actually apply to your permissions.** There
  is no safe default and the engine has none — `CapitalRequirementSpec` is a
  required constructor argument. 15c3-1(a)(2) alone runs from USD 250,000 for a
  firm carrying customer accounts, to USD 100,000 for a dealer, to USD 50,000
  for an introducing broker. MIFIDPRU 4.4 sets GBP 750,000 for a firm dealing on
  own account, GBP 150,000, or GBP 75,000 depending on permissions.
- **A daily cadence at minimum.** "At all times" in 15c3-1(a) is stricter than
  daily; daily end-of-day is the practical floor, and firms close to their
  warning band should compute more often.
- Python 3.7+ (ordered dicts and dataclasses). Standard library only.

## Workflow

1. **Build the requirement from the components that apply, and let greater-of
   pick the binder.**
   - Pass every applicable component by name:
     `{"MIN_DOLLAR_(a)(2)(i)": 250_000, "AI_RATIO_(a)(1)(i)": 280_000}`, or
     `{"PMR": 750_000, "FOR": 600_000, "KFR": 310_000}`.
   - `AGGREGATION_GREATER_OF` is the default because that is what both rules
     say. **Do not sum them.** Version 1 did, and for the MIFIDPRU firm above it
     reported a GBP 1,660,000 floor against a real one of GBP 750,000 —
     manufacturing a deficit, and with it a false wind-down trigger, out of a
     healthy balance sheet.
   - `AGGREGATION_SUM` exists for genuinely stacked regimes (a minimum with a
     conservation buffer on top) and must be asked for explicitly.

2. **Put risk-based deductions on the capital side, never the requirement side.**
   - 15c3-1(c)(2) defines net capital as "the net worth of a broker or dealer,
     adjusted by" — including (c)(2)(vi) "Deducting the percentages specified in
     paragraphs (c)(2)(vi)(A) through (M) of this section (or the deductions
     prescribed for securities positions set forth in Appendix A) of the market
     value of all securities". A haircut reduces capital. It does not raise the
     floor, and the two are not interchangeable once greater-of aggregation is
     in play.

3. **Pass total assets, not the liquid subset.**
   - `total_assets` means total. Non-allowable assets are deducted separately by
     the engine; handing it a pre-filtered figure deducts them twice and
     understates net capital, which fails in the safe direction but will have
     you raising capital you do not need.

4. **Keep subordinated debt on exactly one side of the ledger.**
   - 15c3-1(c)(2)(ii) excludes liabilities "subordinated to the claims of
     creditors pursuant to a satisfactory subordination agreement" from the
     liability side. Pass those in `qualifying_subordinated_debt` and exclude
     them from `total_liabilities`. Counting them in both nets to zero effect;
     counting them in neither inflates capital.
   - Subordinated debt that does **not** meet Appendix D is an ordinary
     liability. If the agreement is unsigned, or repayment falls inside the
     notice period, it does not qualify — and it is a liability precisely when
     you most want it to be capital.

5. **Read the status, then read the notification deadline attached to it.**
   - `CAPITAL_DEFICIT` — net capital below the floor. Under 17 CFR
     240.17a-11(a)(1) notice is due **the same day**, and continuing to conduct
     a securities business while deficient is a continuing violation of
     15c3-1(a).
   - `WARNING_BUFFER_BREACHED` — at or above the floor but below 120% of it.
     17a-11(b)(3) requires notice **within 24 hours** when "total net capital is
     less than 120 percent of the broker's or dealer's required minimum net
     capital". This is a rule, not a house buffer.
   - `COMPLIANT` — at or above 120%.
   - `report.regulatory_notice` carries the applicable text for mapped
     jurisdictions. `None` means *this module has no mapping for your
     jurisdiction*, never *no notice is due*.

6. **Treat `CapitalInputError` as a failed capital check, not a skipped one.**
   - Every input is validated: NaN and infinity are rejected before they reach a
     threshold comparison, negative liabilities and deductions are rejected,
     non-allowable assets exceeding total assets are rejected, requirement
     components must be positive, and `early_warning_pct` must be at least 1.0.
   - An unevaluable balance sheet is not an adequate one. Fail the gate.

7. **Branch on `status` and `is_compliant`, and log `audit_notes` verbatim.**
   - `audit_notes` names the status, both amounts, the binding component, the
     aggregation mode, the headroom, the ratio, the early-warning line, and the
     notification rule. That is the line an examiner will ask to see, so persist
     it — see `record-retention-periods-by-jurisdiction` for how long.

> Full step-by-step procedure: see `references/workflows.md`.
> Threshold-by-threshold sources: see `references/standards.md`.
> Printable sign-off checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Summing requirement components instead of taking the greater of them.** The
  single most consequential error here, and the one version 1 of this skill
  made. 15c3-1(a) says "the greater of"; MIFIDPRU 4.3.2R says "the highest of".
  Summing inflates the floor and fabricates deficits.
- **Adding haircuts to the requirement.** They are a deduction from capital
  under 15c3-1(c)(2)(vi). Moving a deduction to the other side of a greater-of
  comparison changes the answer, not just the presentation.
- **Passing already-filtered "liquid assets" as total assets.** The engine
  deducts non-allowable assets itself. Do it twice and the number is wrong even
  though it looks conservative.
- **Assuming a default minimum.** USD 250,000 is 15c3-1(a)(2)(i), for a
  broker-dealer *carrying customer accounts*. It is the wrong floor for an
  introducing broker (USD 50,000), a dealer (USD 100,000), or any UK firm. The
  engine now refuses to guess.
- **Treating the 120% line as a nice-to-have internal buffer.** For a US
  broker-dealer it is 17a-11(b)(3) and it carries a 24-hour notice obligation.
  Conversely, applying 120% to a MIFIDPRU firm is a house convention — a
  sensible one, but do not cite it as an FCA rule.
- **Rounding before comparing.** Version 1 rounded net capital to two decimals
  before testing it against the floor, so a shortfall of fractions of a cent
  could round into compliance. Report values are now exact; only the display
  string is formatted.
- **Reading a `None` notification as "nothing to file."** It means the
  jurisdiction is unmapped in this module. Your own regime's notification rules
  still apply in full.
- **Computing capital only at month-end because that is when the FOCUS report is
  due.** "At all times" is the standard in 15c3-1(a). A firm that was deficient
  on the 14th and healthy on the 30th was deficient.
- **Counting subordinated debt that does not satisfy Appendix D.** An
  unexecuted or short-notice subordination agreement is ordinary debt, and it
  reverts to being ordinary debt exactly when the firm is under stress.

## Verification

- Run the unit suite:
  `python -m unittest discover -s skills/regulatory-capital-requirement-tracking/scripts`
  — all tests must pass.
- Build a spec with `{"PMR": 750_000, "FOR": 420_000, "KFR": 310_000}` and
  confirm `calculate_required_capital()` returns `(750_000.0, "PMR")` — not
  `1_480_000.0`. This is the greater-of regression.
- Feed `total_assets=1_000_000, total_liabilities=500_000,
  non_allowable_assets=50_000, securities_haircuts=40_000,
  qualifying_subordinated_debt=100_000` and confirm net capital is `510_000.0`
  by hand from 15c3-1(c)(2).
- Against a requirement of 300,000: confirm net capital of 550,000 is
  `COMPLIANT`, 320,000 is `WARNING_BUFFER_BREACHED`, and 150,000 is
  `CAPITAL_DEFICIT`.
- Confirm the boundaries: exactly 300,000 is compliant but warning ("no less
  than"), exactly 360,000 is compliant and not warning ("less than 120
  percent"), and 299,999.99 is a deficit.
- Confirm `CapitalComponents(total_assets=float("nan"), total_liabilities=0)`
  raises `CapitalInputError` rather than silently classifying as a deficit.
- Confirm `early_warning_pct=0.9` is rejected — a warning line below the floor
  never fires.
- Against your own firm: reproduce last month's filed net capital figure from
  the same inputs before trusting the engine on today's. A discrepancy is either
  a modelling error here or a classification disagreement in your books, and
  both are worth finding before an examiner does.

## Related Skills

- `broker-account-margin-call-handling`
- `margin-utilization-circuit-breaker`
- `capital-preservation-mode-for-degraded-conditions`
- `kill-switch-and-drawdown-circuit-breakers`
- `algorithmic-trading-firm-licensing-thresholds`
- `sec-rule-15c3-5-risk-controls-us`
- `uk-fca-algorithmic-trading-systems-controls`
- `record-retention-periods-by-jurisdiction`
