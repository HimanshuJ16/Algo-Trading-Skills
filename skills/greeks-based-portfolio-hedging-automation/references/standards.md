# Standards — greeks-based-portfolio-hedging-automation

## Risk limits

The two delta numbers do different jobs. The **limit** decides *whether* to hedge;
the **minimum rebalance size** decides whether the resulting order is worth sending.
Using the minimum size as the trigger reintroduces exactly the fee drag it exists to
prevent.

| Parameter | Role | Illustrative value | Behaviour |
|---|---|---|---|
| `max_allowed_delta_usd` | Hedge **trigger** on beta-weighted delta | $\pm \$50{,}000$ | Above it, size an underlying/futures hedge |
| `max_allowed_vega_usd` | Hedge **trigger** on net dollar vega | $\pm \$10{,}000$ | Above it, size an options overlay or escalate |
| `min_rebalance_delta_usd` | **Floor on order size** | $\$10{,}000$ | Orders below it are suppressed and reported |

These values are illustrative defaults, not a standard or a regulatory requirement.
Real limits are set from the book's capital, mandate and liquidity, and should be
calibrated rather than inherited — see `risk-limit-calibration-against-historical-drawdowns`.

## Contract multipliers (verified)

| Product | Multiplier | Source |
|---|---|---|
| US equity / ETP option (standard) | 100 shares | Cboe, *Equity Options Specifications*: "generally, 100 shares of one of the exchange-traded products" |
| US equity option (OCC-adjusted) | **Varies** — read the OCC memo | OCC Infomemo #26853, *Contract Adjustments*: adjusted contracts carry non-standard deliverables |
| CME E-mini S&P 500 futures (ES) | $\$50$ per index point | CME Group, E-mini S&P 500 contract specifications |
| NSE index derivatives (NIFTY et al.) | **Revised periodically** — read the current NSE contract specification | NSE revises index-derivative lot sizes to keep contract value above SEBI's minimum; any hard-coded figure goes stale silently |

Never hard-code a multiplier in hedging code. Read it per position from the contract
master, and treat a missing multiplier as an error rather than defaulting to 100. The
same applies to the hedge instrument: a forgotten `multiplier=50` on an E-mini is a
50x oversized hedge that raises no error unless the field is mandatory.

## Greek conventions

| Quantity | Convention | Source |
|---|---|---|
| Delta | Per unit of the deliverable, $[-1, +1]$; sign of the position carried by quantity | Standard listed-options convention |
| Vega | Per unit of the deliverable, per **one percentage point** of implied volatility | OIC, *Vega*: "an absolute change in option value for a 1% change in volatility" |
| Beta-weighted delta | $\beta_i \times$ position dollar delta, measured against the hedge instrument's underlying | Cboe Insights, *How to Right-size Hedges Via Beta Weighting with XSP Options* |

## Operational note

This engine emits hedge *recommendations*. Anything that routes them to a venue
inherits the full obligations of order origination — pre-trade risk controls,
client-order-ID idempotency, kill-switch coverage, and short-sale locate checks
where a SELL hedge is in cash equity. Those are out of scope here; see
`order-placement-idempotency`, `execution-algorithm-kill-switch-integration`, and
`us-reg-sho-short-sale-locate-requirements`.

## Category

`risk-management`
