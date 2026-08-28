# Pre-Flight Checklist — Risk Reporting for External Stakeholders

Sign-off before risk figures leave the firm. One pass per report, per recipient.

## Channel

- [ ] Is this a **discretionary** disclosure (LP letter, prime broker feed,
      auditor appendix, supervisory request answered in aggregate) rather than a
      statutory filing?
- [ ] If a filing is also due, is it going through its own channel with the
      position- and counterparty-level detail the regime requires — Form PF Q35
      (every open position ≥5% of NAV, monthly), Q22/Q23 (five largest
      counterparties each way), AIFMD Annex IV principal exposures? A redacted
      report does not satisfy any of them.
- [ ] Has `report.disclosure_notice` been read and, where the recipient is a
      regulator, does the covering note repeat that this is not a filing?

## Inputs

- [ ] Is `net_asset_value_usd` strictly positive and the same NAV the fund
      administrator struck for this period end?
- [ ] Are `gross_exposure_usd` and `net_exposure_usd` on the same basis, with
      gross ≥ |net|, and is `net_exposure_usd` signed rather than absolute?
- [ ] Is `var_pct_of_nav` a **percentage of NAV**, positive, and are
      `var_confidence_pct` and `var_horizon_days` the ones actually used to
      compute it — not defaults inherited from a previous period?
- [ ] Do the VaR parameters on this report match those on the last one, and if
      they changed, does the covering note say so? A VaR that halved because the
      horizon changed is not a risk reduction.
- [ ] Is `liquidity_convention` the one the numbers are actually in — `BUCKETED`
      buckets summing to ~100%, or `CUMULATIVE` running totals in ascending
      horizon order?
- [ ] Are sector percentages expressed as a percentage of NAV (not of gross), and
      are net-short sectors carried with their sign?
- [ ] Is `total_aum_usd` on a stated basis the recipient will read the same way?
- [ ] Did the state construct without raising? A `ReportInputError` is a failed
      report, not one to send with a caveat.

## Disclosure policy

- [ ] Does the recipient have an explicit policy entry — no report was generated
      under a fallback?
- [ ] Does the sector count disclosed match what the LPA, side letter or prime
      brokerage agreement actually permits for *this* counterparty, rather than
      the repository's default top-5 / top-3?
- [ ] Is the disclosure ranked by size (the engine's job) rather than pre-sliced
      by the caller?

## Redaction

- [ ] Was `proprietary_positions` supplied, so the check could run at all?
- [ ] Is `redaction_verified` **True**? `False` means not checked — never
      "checked and clean".
- [ ] If `redaction_note` reports unrecognised identifier fields, has
      `identifier_fields` been extended to the keys your position records use?
- [ ] Has a human read the disclosed concentration and liquidity **labels**? The
      automated check cannot catch a label that identifies a holding without
      naming it — `SPECIAL_SITUATION_1` in a two-position book identifies it
      perfectly well.
- [ ] Does `positions_withheld_count` match the position count you expected for
      this period?

## Integrity envelope

- [ ] Does the covering note describe the seal accurately — an unkeyed
      `content_digest` is **integrity only**, not a signature and not proof of
      sender?
- [ ] If authenticity matters to this recipient, was an `hmac_key` supplied and
      is `authentication` set to `HMAC-SHA256`?
- [ ] If the recipient must be able to prove authorship to a third party, has the
      report been signed asymmetrically outside this module? HMAC is symmetric
      and gives no non-repudiation.
- [ ] Was `report.digest_covers` sent to the recipient alongside the digest?
- [ ] Does the recipient have the expected digest over a channel that does not
      carry the report itself?
- [ ] Is the HMAC key held in a secrets manager, scoped to this use, and on a
      rotation schedule?

## Dispatch and retention

- [ ] Is `report_id` unique against the dispatch log — no collision with another
      fund, another period, or an earlier version of this report?
- [ ] If this restates an earlier report, is the superseded `report_id` linked to
      the new one in the log?
- [ ] Are `report_id`, `content_digest`, recipient, timestamp and `audit_notes`
      persisted to a controlled dispatch log with the retention period the
      jurisdiction requires?
- [ ] Is `audit_notes` — which carries NAV, VaR, leverage and drawdown — going to
      a controlled log rather than a shared application logger?
- [ ] Where the recipient is in another jurisdiction, have cross-border transfer
      restrictions on fund data been cleared?

## Reconciliation

- [ ] Do the leverage and VaR figures reconcile to the previous period's issued
      report from the same inputs?
- [ ] Do the aggregate figures reconcile to any statutory filing covering the
      same period? Two different numbers for the same fund and the same date is a
      question you want to answer before an examiner asks it.
