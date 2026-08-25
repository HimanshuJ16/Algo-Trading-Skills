# Workflows — exchange-fee-tier-and-rebate-structure-analysis

## 0. Sign convention

Fix this before anything else, because every downstream figure depends on it:

```
rate < 0  ->  rebate: the venue CREDITS the member
rate > 0  ->  fee:    the venue CHARGES the member
```

A signed cost is always `shares * rate`. Net cost positive means money leaving the desk;
negative means net rebate capture. The same convention applies to every USD field in
`FeeTierAnalysisReport`, so an inverted venue needs no special-case arithmetic.

## 1. Fee schedule ingestion

Transcribe the venue's published schedule into `FeeTierDefinition` rows.

- Include an explicit tier at threshold `0`. The engine rejects a schedule without one,
  because otherwise volume below the lowest threshold falls through to a tier it does not
  qualify for.
- Convert the venue's own notation into the sign convention above. Cboe publishes rebates
  in parentheses — `($0.0027)` is a **rebate**, i.e. `-0.0027` here.
- Thresholds must be absolute share counts. If the venue qualifies on a percentage of
  consolidated volume, convert it yourself using your own ADV forecast and record that the
  result is conditional on that forecast.
- Rates must be finite. A NaN rate is rejected at construction rather than silently
  producing a NaN net cost that reads as a valid float downstream.

## 2. Choose the tier qualification basis

This is a correctness decision, not a configuration preference, and the engine requires it
explicitly.

| Basis | Use for | Tier fixed by |
|---|---|---|
| `PRIOR_PERIOD` | **US NMS stocks** (mandatory, Reg NMS Rule 610(d), in force since 2 Feb 2026) | A completed prior period, supplied as `qualifying_volume_shares` |
| `ROLLING_CURRENT` | Crypto venues (rolling 30-day volume), some non-US venues | The rolling window that includes the volume being priced |

Under `PRIOR_PERIOD` the engine refuses to fall back to the priced volume. That is the
whole point of Rule 610(d): the volume being priced is exactly the volume that may not
determine its own rate.

## 3. Validate the schedule against the declared pricing model

Performed at construction:

- **Raises** when the schedule contradicts the declared model — a `MAKER_TAKER` venue whose
  maker rate is worse than its taker rate is an inverted schedule and has been mislabelled.
- **Warns** when a `MAKER_TAKER` tier charges makers (or an inverted tier charges takers).
  That is legal and common in base tiers, but it means passive flow earns nothing there.
- **Raises** on duplicate thresholds, which would make tier assignment ambiguous.

Tiers are sorted by threshold internally, so input order does not matter.

## 4. Tier classification

Compare the qualifying volume against thresholds; the highest tier whose threshold is met
wins. Thresholds are inclusive (`volume >= threshold`), so exactly 10,000,000 shares
reaches a 10,000,000-share tier.

## 5. Net cost calculation

```
maker_side = maker_shares * active_tier.maker_rate_per_share
taker_side = taker_shares * active_tier.taker_rate_per_share
net_cost   = maker_side + taker_side
effective_cost_per_share = net_cost / priced_volume     # 0.0 when no volume is priced
```

Carried in full precision and rounded once at the reporting boundary, so rounding never
compounds into the net.

`gross_taker_fees_usd` and `gross_maker_rebates_usd` are retained for maker-taker
reporting. On an inverted venue they are 0.0 by construction and the report carries a
warning saying so — read `maker_side_cost_usd` / `taker_side_cost_usd` instead.

## 6. Tier jump evaluation

```
gap            = max(0, next_tier.threshold - qualifying_volume)
gross_savings  = net_cost - (priced mix repriced at next tier's rates)
incremental    = gap, split by the maker mix, priced at the BILLING tier
net_benefit    = gross_savings - incremental
```

The billing tier for the incremental volume differs by basis, and this is the substantive
difference between the two regimes:

- `ROLLING_CURRENT` → **next tier**. Crossing the threshold reprices the whole window, so
  the incremental shares get the better rate too. Benefit lands in `CURRENT_PERIOD`.
- `PRIOR_PERIOD` → **current tier**. Today's volume cannot reprice today's fills, so the
  incremental shares are billed at today's rate and the better rate applies from next
  period. Benefit lands in `NEXT_PERIOD`.

`incremental_maker_fraction` defaults to the observed maker mix; override it when the
volume you would add to close the gap has a different passive/aggressive profile than your
current flow. With no volume priced there is no mix to observe, and it defaults to 0.0
(all-taker), the conservative assumption on a maker-taker venue.

`gross_savings` is deliberately **not** clamped at zero. A higher tier can be worse — a
smaller rebate paired with a smaller fee is a net loss for a maker-heavy desk — and a clamp
would report "no downside" on a strictly losing move.

**Act on `net_tier_jump_benefit_usd`, not on gross savings.** Even that excludes adverse
selection and market impact on the forced volume, so treat a marginal positive as
negative. When the net benefit is non-positive the report says so in `warnings`.

## 7. Access fee cap check

`check_reg_nms_access_fee_cap(tiers)` flags taker rates above the Rule 610(c) cap for US
NMS stocks priced at or above $1.00. The default is the cap in force ($0.0030/share); pass
`REG_NMS_610C_AMENDED_CAP_USD` ($0.0010) to pre-test a schedule against the amended cap
ahead of its November 2027 compliance date. Sub-$1.00 securities are capped as a
percentage of quotation price and are out of scope.

## 8. Report

`FeeTierAnalysisReport` carries the tier, qualifying vs priced volume, the basis used,
signed per-side and net economics, the tier-jump analysis with its benefit period, and
`warnings`. **Read `warnings`** — schedule inconsistencies and uneconomic tier jumps are
reported there rather than by raising, so an unread list is a silently ignored finding.
