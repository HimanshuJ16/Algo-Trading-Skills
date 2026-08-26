# Broker Integration Standards — multi-broker-consolidated-position-view

## Consolidated accounting definitions

All quantities are **signed**: negative is short. $b$ indexes position legs (one broker
may contribute more than one leg for a symbol — see hedge mode below). $m_b$ is the
contract multiplier, $P_b$ the mark price, $C_b$ the reported average cost, and
$X_{c}$ the FX rate for leg currency $c$.

| Metric | Calculation | Description |
|---|---|---|
| Net Quantity | $Q_{\text{net}} = \sum_b Q_b$ | Algebraic sum of long and short holdings |
| Gross Quantity | $Q_{\text{gross}} = \sum_b \|Q_b\|$ | Total contract count before offsetting. **Not** a capital figure — quantities of instruments with different multipliers and currencies are not commensurable |
| Leg Market Value | $MV_b = Q_b \cdot P_b \cdot m_b \cdot X_{c(b)}$ | Signed, in base currency |
| Net Market Value | $MV_{\text{net}} = \sum_b MV_b$ | Signed. Collapses toward zero on an offset book |
| Gross Market Value | $MV_{\text{gross}} = \sum_b \|MV_b\|$ | **The exposure magnitude.** What a gross-market-value cap must consume |
| Cost Basis | $\text{Cost} = \sum_b Q_b \cdot C_b \cdot m^{*}_b \cdot X_{c(b)}$ | $m^{*}_b = 1$ when the broker already folded the multiplier into $C_b$, else $m_b$ |
| Unrealized P&L | $MV_{\text{net}} - \text{Cost}$ | Signed, base currency |
| Weighted Avg Cost | $\text{Cost} / Q_{\text{net}}$, undefined when $\|Q_{\text{net}}\| \le \epsilon$ | Base currency **per contract**. Indicative only — see cost-basis caveat below |
| FX Convention | $X_c$ = units of base currency per one unit of $c$; $X_{\text{base}} \equiv 1.0$ | No default table; an unknown $c$ raises |
| Base Currency | Configurable, default USD | Every `*_base` field is denominated in it |
| Discrepancy Threshold | $\|Q_{\text{actual}} - Q_{\text{expected}}\| > \epsilon$, $\epsilon$ default $10^{-5}$ | Absolute, per-symbol overridable. Inclusive at the boundary |

## Broker feed conventions relied on

| Behavior | Broker/source | Implication for this skill |
|---|---|---|
| Short positions carry a negative quantity | IBKR `position`; Alpaca `qty` (with a matching negative `market_value`); Binance USD-M `positionAmt` | Legs are consumed as signed quantities; no `side` field is needed or accepted |
| `avgCost` reflects the contract multiplier for derivatives, unlike per-share `avgPrice` | Interactive Brokers Web API, Portfolio → Positions | `average_cost_includes_multiplier` exists so the multiplier is not applied twice |
| `avg_entry_price` uses weighted average for intraday positions and compressed FIFO end-of-day | Alpaca, "Position Average Entry Price Calculation" | Cost basis blended across brokers mixes conventions; the aggregate is indicative, not an accounting record |
| Hedge mode returns separate LONG and SHORT rows for one symbol in one account | Binance USD-M Futures, Position Information | Both legs are ingested and netted; `broker_breakdown` holds the net per broker, and intra-broker offsetting surfaces in `gross_quantity` / `is_internally_offset` |
| A standard equity option contract covers 100 shares | OCC, Equity Options product specifications | Multiplier-bearing instruments must carry an explicit `contract_multiplier` |
| Corporate actions can leave an adjusted contract delivering other than 100 shares while retaining a 100 premium multiplier | OIC, "Splits, Mergers, Spinoffs & Bankruptcies" | The multiplier must come from the contract definition, never be assumed from instrument type or ticker |
| Currency codes are three-letter alphabetic | ISO 4217 (maintenance agency: SIX Financial Information) | `currency` is format-validated as `^[A-Z]{3}$`; membership of the live code list is **not** checked, so a well-formed retired code fails later at the FX lookup instead |

## Scope boundary

This module performs no cash, margin, or limit accounting, and holds no position state
between calls. Firm-wide gross-market-value caps and margin utilization belong to
`cross-account-aggregate-risk-view`, which consumes canonical symbols and
base-currency values produced here.

## Category

`broker-integration` — see top-level `mappings/` directory.
