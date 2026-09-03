# Standards — exchange-fee-tier-and-rebate-structure-analysis

## Jurisdiction

Everything in the "Regulatory requirements" section below is **US-specific**, applying to
NMS stocks on US national securities exchanges. It does not apply to crypto venues, to
non-US exchanges, or to off-exchange US venues, which remain free to use rolling
current-window volume tiers. Do not universalize it.

## Regulatory requirements (verified against primary sources)

| Requirement | Rule | Status | What it means here |
|---|---|---|---|
| An exchange "shall not impose, nor permit to be imposed, any fee or fees, or provide, or permit to be provided, any rebate or other remuneration, for the execution of an order in an NMS stock that cannot be determined at the time of execution." | Reg NMS **Rule 610(d)** | **In force.** Compliance date 2 February 2026 (originally 3 November 2025, extended by temporary exemptive relief). Adopted in Release [34-101070](https://www.sec.gov/rules/final/2024/34-101070.pdf) (18 Sept 2024). | Volume tiers on US equity venues MUST be qualified on a **completed prior period**, not on current-period volume. Model with `TierQualificationBasis.PRIOR_PERIOD`. |
| Access (taker) fee cap: **$0.0030/share** for NMS stocks priced ≥ $1.00; 0.3% of quotation price below $1.00. | Reg NMS **Rule 610(c)** | **In force** — this is the operative cap today. | `REG_NMS_610C_ACCESS_FEE_CAP_USD`; enforced by `check_reg_nms_access_fee_cap()`. |
| Amended access fee cap: **$0.0010/share** ≥ $1.00; 0.1% of quotation price below $1.00. | Reg NMS **Rule 610(c)** as amended | **Adopted but not yet in force.** Compliance deferred to the first business day of **November 2027** by SEC exemptive order of 11 June 2026 (Rel. 34-105656), having previously been November 2026. | `REG_NMS_610C_AMENDED_CAP_USD`; pass it to `check_reg_nms_access_fee_cap()` to pre-test a schedule. |

The 2027 date is a moving target: it has already been extended twice, and the SEC
separately proposed rescinding Rule 611 in June 2026 (Rel. 34-105655). Re-verify against
the SEC before relying on it. Nothing in this module hard-codes a compliance date.

### How exchanges implemented Rule 610(d)

Cboe's US equities fee schedules state that "unless otherwise indicated, all volume
figures will be derived from quoting or trading activity in the **prior month**"
([EDGA](https://www.cboe.com/us/equities/membership/fee_schedule/edga/),
[BYX](https://www.cboe.com/us/equities/membership/fee_schedule/byx/)). IEX, Cboe
BZX/BYX/EDGX/EDGA and NYSE Arca all filed conforming fee-schedule changes ahead of the
February 2026 date (e.g. SEC Rel. 34-104541, 5 Jan 2026, 91 FR 737).

**Consequence for this skill:** the month-end "push volume to hit the tier" play does not
exist on US equity venues any more. Volume traded today changes *next* period's tier.

## Venue pricing model — verify, do not remember

Venue orientation changes. **Cboe EDGA replaced its inverted (taker-maker) model with a
maker-taker model effective 1 November 2024** (Cboe release note C2024100100; SEC filing
[89 FR 87953](https://www.federalregister.gov/documents/2024/11/06/2024-25732/), 6 Nov
2024). Its published schedule now shows an add-liquidity rebate and a remove-liquidity
fee. Any material that still cites EDGA as the canonical inverted venue is stale.

| Orientation | Maker | Taker | Current US examples |
|---|---|---|---|
| Maker-taker | credited (rebate) | charged (fee) | Nasdaq, Cboe BZX, Cboe EDGX, Cboe EDGA |
| Inverted (taker-maker) | charged (fee) | credited (rebate) | Cboe BYX, Nasdaq BX |

Confirm orientation against the venue's own published fee schedule before routing.
Specific per-share rates are deliberately not reproduced here: they change by rule filing,
often monthly, and a stale rate in a reference file is worse than no rate.

## Engineering standards

| Metric | Engineering standard |
|---|---|
| Sign convention | Every rate and every reported amount is signed: negative = credited to the member, positive = charged. Net cost is a single signed sum, so it is correct on both venue orientations without branching. |
| Maker-taker netting | Net transaction cost MUST net maker rebates against taker fees. Gross taker fees alone mis-rank venues. |
| Inverted venue logic | Inverted venues MUST be modelled as charging makers and crediting takers. A schedule contradicting its declared pricing model is rejected, not silently priced. |
| Tier qualification basis | MUST be explicit. There is no safe default, so the engine requires it rather than assuming one. |
| Base tier | A schedule MUST define a tier at threshold 0, so no volume can be assigned a tier it does not qualify for. |
| Tier jump economics | The reported benefit MUST net the cost of the incremental volume required to qualify. Gross savings on existing volume is an upper bound, not a decision input. |
| Savings sign | Tier-jump savings MUST NOT be clamped at zero — a higher tier can be strictly worse. |
| Numerical handling | Arithmetic is carried in full precision and rounded once at the reporting boundary; non-finite rates are rejected at construction. |

## Known limitations

- **Absolute share thresholds only.** Real US equity tiers frequently qualify on a
  percentage of Total Consolidated Volume or consolidated ADV (Nasdaq's price list carries
  criteria such as "Add 0.65% or greater of TCV or 70M shares ADV"). Converting those
  requires a consolidated-volume forecast this module does not make.
- **Single-criterion tiers only.** Schedules requiring simultaneous add *and* remove
  criteria, or cross-asset conditions, cannot be expressed as one scalar threshold.
- **No fill probability, queue position, market impact, or adverse selection.** Fee is one
  term of execution cost and on inverted venues usually the smaller one.
- **Per-share rates only.** Sub-$1.00 US securities are capped as a percentage of
  quotation price and are out of scope.
