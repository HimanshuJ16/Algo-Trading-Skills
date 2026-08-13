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

- **NYSE Rule 7.35(B)(A)**: "A LOC Order is a Limit Order that is to be traded
  only during a closing auction."
- **Nasdaq Equity Rule 4**: the closing-cross LOC equivalent requires a limit
  price; the order executes only if the closing cross price is at or better than
  the specified limit.

When `reference_price` and `slippage_tolerance_bps` are supplied to
`generate_routing_plan`, the engine populates `suggested_limit_price`:

- Buy: `reference_price * (1 + tolerance_bps / 10000)`
- Sell: `reference_price * (1 - tolerance_bps / 10000)`

When omitted, `suggested_limit_price` is `None` and the caller MUST set a limit
price before submitting the LOC order.

## Closing-Auction Cutoffs (US Equities)

| Exchange | New MOC/LOC entry | Modify / Cancel | Source |
|---|---|---|---|
| NYSE | until **3:50 p.m. ET** (contra-side only after) | frozen at **3:50 p.m. ET** | NYSE Rule 7.35B |
| Nasdaq | until **3:58 p.m. ET** | frozen at **3:50 p.m. ET** | Nasdaq Equity Rule 4 |

The conservative, exchange-portable cutoff encoded as
`CLOSING_AUCTION_CUTOFF_ET = 15:50` should be used for both entry and
cancel/modify planning. Nasdaq-only callers may relax entry to
`NASDAQ_LOC_ENTRY_CUTOFF_ET = 15:58`. IO (Imbalance-Only) orders may be entered
until 4:00 p.m. ET on both exchanges and are out of scope for this skill.

## Category
`execution-algorithms`
