# Standards for Illiquid Execution

| Order Size (% of ADV) | Liquidity Tier | Target Venue / Order Type | Reason |
|---|---|---|---|
| **< 1%** | Liquid | Continuous (VWAP) | Low risk of market impact. |
| **1% - 5%** | Moderate | Hybrid (VWAP + LOC) | Balance impact across the day and the close. |
| **>= 5%** | Severe | 100% LOC Auction | Continuous trading would cause unacceptable slippage. |

*Note: MOC (Market-on-Close) is banned for Severe illiquidity due to the lack of price protection against auction imbalances.*

## LOC Limit-Price Requirement

An LOC (Limit-on-Close) order is a **limit order** designated for the closing
auction and therefore **requires a limit price** at submission.

- **NYSE Rule 7.31(c)(2)(A)**: a LOC Order is a Limit Order that is to be traded
  only in a closing auction. (NYSE Rule 7.35B governs the DMM-facilitated
  Closing Auction itself; the order-type definitions live in Rule 7.31.)
- **Nasdaq Equity 4 Rule 4702(b)(12)(A)**: a Limit On Close Order is "an Order
  Type entered with a price that may be executed only in the Nasdaq Closing
  Cross, and only if the price determined by the Nasdaq Closing Cross is equal
  to or better than the price at which the LOC Order was entered."

When `reference_price` and `slippage_tolerance_bps` are supplied to
`generate_routing_plan`, the engine populates `suggested_limit_price`:

- Buy: `reference_price * (1 + tolerance_bps / 10000)`, rounded **down** to `tick_size`
- Sell: `reference_price * (1 - tolerance_bps / 10000)`, rounded **up** to `tick_size`

Rounding away from the aggressive side keeps the tolerance a hard bound rather
than an approximation. When `reference_price` is omitted,
`suggested_limit_price` is `None` and the caller MUST set a limit price before
submitting the LOC order.

## Minimum Price Variation (SEC Rule 612)

A limit price that is not a permissible minimum increment is rejected. Under
**17 CFR 242.612** (Reg NMS Rule 612, the "sub-penny rule") the minimum pricing
increment for an NMS stock is **$0.01** at or above $1.00 per share and
**$0.0001** below $1.00. The engine exposes these as `DEFAULT_TICK_SIZE` and
`SUB_DOLLAR_TICK_SIZE`.

The exchange applies the same constraint to its own auction prices: Nasdaq Rule
4702(b)(12)(A) re-prices a late LOC to a Reference Price only after rounding
that price "to the nearest permitted minimum increment."

**Forward-looking:** the SEC's September 2024 Reg NMS amendments add a **$0.005**
increment for NMS stocks at or above $1.00 whose Time Weighted Average Quoted
Spread over a three-month Evaluation Period is $0.015 or less, assigned for a
six-month period. The original compliance date was the first business day of
November 2025; an SEC exemptive order dated **October 31, 2025** moved it to the
**first business day of November 2026**. Because the applicable increment then
becomes per-security and time-varying, `tick_size` is a caller input rather than
a constant — see `exchange-tick-size-regime-tracking`.

## Closing-Auction Cutoffs (US Equities)

Times below are for a regular session ending at 4:00 p.m. ET.

| Exchange | New MOC entry | New LOC entry | Free cancel / modify | Source |
|---|---|---|---|---|
| NYSE | until **3:50 p.m.** (contra-side to a published Significant Closing Imbalance only, thereafter) | until **3:50 p.m.** (same carve-out) | frozen at **3:50 p.m.**, no legitimate-error exception | NYSE Rule 7.35B; Rule 7.35(a)(8) |
| Nasdaq | rejected at or after **3:55 p.m.** | rejected at or after **3:58 p.m.**; from 3:55 p.m. accepted only against a First/Second Reference Price | frozen at **3:50 p.m.**; legitimate-error corrections only until 3:58 p.m., none thereafter | Nasdaq Equity 4 Rules 4702(b)(11), 4702(b)(12) |

`CLOSING_AUCTION_CUTOFF_ET = 15:50` is the conservative, exchange- and
order-type-portable cutoff and is the default for both entry and cancel/modify
planning. Nasdaq-only callers may relax entry to `NASDAQ_MOC_ENTRY_CUTOFF_ET`
(15:55) or `NASDAQ_LOC_ENTRY_CUTOFF_ET` (15:58), accepting the reprice/rejection
risk documented above.

### Cutoffs move on early-close days

NYSE defines the deadline as the **Closing Auction Imbalance Freeze Time**, i.e.
ten minutes before the *scheduled* end of Core Trading Hours (NYSE Rule
7.35(a)(8)). NYSE Regulation states it explicitly: "On days when the NYSE is
scheduled to close at a time other than 4 p.m., such deadlines — tied to the
scheduled end of Core Trading Hours — move accordingly" (NYSE RM-26-03, §II.A
n.2). On a 1:00 p.m. half day the MOC/LOC deadline is therefore **12:50 p.m.**

The module encodes every cutoff as an offset from `market_close_et` for this
reason. Nasdaq's rule text states absolute clock times against a 4:00 p.m.
close; representing them as offsets is a deliberately conservative
extrapolation, because on an early close the offset-derived deadline is always
earlier than the absolute one. Confirm the venue's published half-day schedule
before relying on the Nasdaq relaxations on such a day.

### Out of scope

Imbalance-only interest is not handled here: Nasdaq **IO Orders**
(Rule 4702(b)(13)) may be entered from 4:00 a.m. until the cross executes, and
NYSE **Closing IO Orders** may be entered on both sides up to 4:00 p.m.
(NYSE RM-26-03, §II.A). Both are contra-liquidity instruments — see
`close-auction-participation-strategy`.

## Sources

- 17 CFR 242.612 (Reg NMS Rule 612, minimum pricing increments).
- SEC, "SEC Adopts Rules to Amend Minimum Pricing Increments and Access Fee Caps
  and to Enhance the Transparency of Better Priced Orders," Press Release
  2024-137 (Sept. 18, 2024); SEC exemptive order of Oct. 31, 2025 extending the
  Rule 612 compliance date to the first business day of November 2026.
- SEC Release No. 34-84454 (Oct. 19, 2018), File No. SR-NASDAQ-2018-068 —
  extension of Nasdaq Closing Cross cutoff times (MOC entry to 3:55 p.m., LOC
  entry to 3:58 p.m., Order Imbalance Indicator from 3:55 p.m.).
- SEC Release No. 34-86642, File No. SR-NASDAQ-2019-064, Exhibit 5 — current
  text of Nasdaq Rules 4702(b)(11) and 4702(b)(12), including the 3:50 p.m.
  cancel/modify freeze and the Second Reference Price.
- NYSE Regulation, Regulatory Memo NYSE RM-26-03 (Mar. 20, 2026), "Quarterly
  Expiration Day," §II.A-B — MOC/LOC entry and cancellation under NYSE Rule
  7.35B, Significant Closing Imbalance publication, and the early-close rule.
- NYSE Rules 7.31(c)(2)(A) (LOC Order), 7.35(a)(8) (Closing Auction Imbalance
  Freeze Time), 7.35(a)(13) (Legitimate Error), 7.35B(j)(2)(B).
