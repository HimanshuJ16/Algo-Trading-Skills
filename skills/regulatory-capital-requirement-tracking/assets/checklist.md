# Pre-Flight Checklist — Regulatory Capital Requirement Tracking

Sign-off before this computation is relied on for a filing or a trading decision.

## Requirement

- [ ] Is every applicable requirement component present, named after the rule it
      comes from, and current with the firm's permissions?
- [ ] Is aggregation set to `GREATER_OF`? 15c3-1(a) says "the greater of";
      MIFIDPRU 4.3.2R says "the highest of". If `SUM` is set, is the regime
      genuinely stacked, and is the reason recorded?
- [ ] Is the dollar minimum the one for *these* permissions — not USD 250,000
      assumed for an introducing broker whose floor is USD 50,000?
- [ ] Were the ratio components (aggregate indebtedness, aggregate debit items,
      fixed overheads, K-factors) computed for the current period, not carried
      over?

## Balance sheet

- [ ] Is `total_assets` the total, with non-allowable assets deducted **once**
      by the engine rather than pre-filtered out and deducted twice?
- [ ] Are securities haircuts on the **capital** side (`securities_haircuts`),
      not folded into the requirement?
- [ ] Does subordinated debt appear on exactly one side — in
      `qualifying_subordinated_debt` **or** in `total_liabilities`, never both,
      never neither?
- [ ] Does every subordination agreement counted as capital actually satisfy
      Appendix D (executed, and repayment outside the notice period)?
- [ ] Are all amounts in one currency and rounded to reporting precision before
      construction?

## Thresholds and alerting

- [ ] Is `early_warning_pct` at least 1.0, and is 1.20 being cited correctly —
      as 17a-11(b)(3) for a US broker-dealer, or as a house buffer for any other
      regime?
- [ ] Does a `WARNING_BUFFER_BREACHED` result page a human within the 24-hour
      notice window, and a `CAPITAL_DEFICIT` result page one the same day?
- [ ] Is `CapitalInputError` treated as a **failed** check that blocks, rather
      than caught and logged past?
- [ ] Is a `None` `regulatory_notice` understood as "unmapped jurisdiction", not
      "nothing to file"?

## Operations

- [ ] Does the computation run at least daily at end of day, and more often when
      inside the warning band? 15c3-1(a) requires the minimum "at all times".
- [ ] Is `audit_notes` persisted verbatim with its inputs, for the retention
      period applicable in the jurisdiction?
- [ ] Has the engine reproduced a previously filed net capital figure from the
      same inputs before being relied on?
- [ ] Is `binding_component` trended, so a floor migrating from PMR to FOR is
      noticed as a business change rather than a surprise?
