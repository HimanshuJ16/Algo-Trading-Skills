# Workflows — portfolio-stress-test-including-liquidity-crunch-scenarios

Full procedure behind the summary in `SKILL.md`. Symbols: $Q_i$ signed quantity, $P_i$
price, $\Delta_i$ scenario return, $\alpha$ participation rate, $\sigma_i$ stressed daily
volatility, $Y$ the impact coefficient.

## 1. Prepare the book

1. **Net per instrument.** One row per symbol. Days-to-Liquidate is a property of the
   aggregate holding: two 50,000-share rows against a 50,000-share ADV each report 20
   days, while the real 100,000-share position takes 40. The engine raises on a duplicate
   symbol rather than reporting the flattering number.
2. **Align units.** `current_price` and `adv_shares` must measure the same thing. For
   futures and options pass price per contract with ADV in contracts; a raw quote against
   contract volume stresses the position at a fraction of its size.
3. **Choose the ADV window and record it.** ADV is an input, not a forecast. A mean
   spanning a holiday stretch, an expiry, or one index-rebalance print overstates
   continuously available volume before any stress is applied.
4. **Reject, do not repair, bad reference data.** Non-finite or non-positive prices and
   ADVs raise. A zero ADV floored to one share a day converts an untradeable instrument
   into a large-but-finite horizon derived from a volume that does not exist.

## 2. Define the scenario

1. **Set the shock vector.** `price_shock_pct` maps symbol to return; a `DEFAULT` key
   covers unlisted symbols. A symbol matching neither raises — a stress test must not
   invent a shock nobody chose and then omit it from the report.
2. **Set `liquidity_drop_pct` as a capacity haircut.** This is the fraction of absorbable
   size lost, not a forecast of tape volume. Crash-period volume typically *rises*; depth
   is what collapses (FSB 2020: 10-year UST depth $-93\%$ in March 2020). Calibrating
   against observed volume declines understates the crunch. Values in $[0, 1)$; a total
   capacity loss is a trading halt, not a DTL question, and raises.
3. **Set `spread_expansion_factor`.** ESMA34-39-897 para. 45 characterises stress by
   "higher volatility, lower liquidity (e.g. higher bid-ask spread) and longer time to
   liquidate", and para. 45 also warns against relying only on historical observations —
   run hypothetical severities alongside historical ones.
4. **Supply `daily_volatility` where the impact term matters.** Use a *stressed*
   volatility consistent with the shock, not a calm-period estimate.

## 3. Compute the stressed liquidity state

$$\text{StressedADV}_i = \text{ADV}_i\,(1 - \text{LiquidityDrop}), \qquad
  \text{Capacity}_i = \alpha \cdot \text{StressedADV}_i$$
$$DTL_i = \frac{|Q_i|}{\text{Capacity}_i}, \qquad
  \phi_i = \frac{|Q_i|}{\text{StressedADV}_i}$$

$DTL$ uses the magnitude, so a short is measured exactly like the mirror long: covering a
short in a name that cannot absorb the flow is at least as hard as selling the long.

## 4. Revalue at shocked prices

$$\text{PriceShockLoss} = -\sum_i Q_i P_i \Delta_i$$

Positive is a loss. The signed quantity does the work: a long gains on a rally, a short
gains on a crash, and offsetting legs net. Taking $|\Delta_i|$ instead — as version 1.0.0
did — makes every long lose on a rally and prevents any book from ever netting.

Report `net_exposure_usd` alongside `total_portfolio_value_usd` (gross). A market-neutral
book has zero net and substantial gross, and the gross figure is the one the liquidity
haircut is charged against.

## 5. Price the liquidation

**Exogenous — half the spread, once per share** (Bangia et al., 1999):

$$\text{SpreadCost}_i = \frac{1}{2}\cdot\frac{\text{Spread}_i \cdot \text{Expansion}}{10^4}\cdot |Q_i| P_i$$

Independent of $DTL$. A liquidation crosses the spread once; slicing over more sessions
does not make each share pay it repeatedly.

**Endogenous — the square-root law** (Tóth et al., 2011, Eq. 1):

$$\text{Impact}_i = Y \sigma_i \sqrt{\phi_i}\cdot |Q_i| P_i$$

Priced only where `daily_volatility` is supplied; the rest are listed in
`positions_missing_volatility` and the haircut is then an explicit lower bound.

$$\text{Haircut} = \sum_i \left(\text{SpreadCost}_i + \text{Impact}_i\right), \qquad
  \text{TotalStressedLoss} = \text{PriceShockLoss} + \text{Haircut}$$

## 6. Audit the bottlenecks

1. Flag $DTL_i > \text{MaxAllowedDTL}$ — strictly greater; exactly at the limit passes.
2. Any flag sets `LIQUIDITY_CRUNCH_ILLIQUID_WARNING`. The status is about the *horizon*,
   not the loss: a book can pass the P&L test and fail this one.
3. Check `positions_outside_impact_calibration` before quoting any impact figure. Beyond
   $\phi = 0.10$ the square-root law is being extrapolated well past its fitted range of
   "a few $10^{-4}$ to a few %"; the honest reading is "untradeable on this horizon", not
   a dollar amount.
4. Check `positions_missing_volatility`. Those positions contributed no impact cost.

## 7. Report and escalate

1. Persist the full `StressTestReport`, including the per-position `positions` list, so
   any aggregate can be traced to its drivers.
2. Escalate on the bottleneck flag, not only on the loss figure. The remedies differ:
   a large stressed loss is a sizing or hedging question; a long $DTL$ is a capacity
   question, and the response is to cap the position — see
   `liquidity-adjusted-position-sizing`.
3. Re-run across a range of severities. A single scenario is a data point, not a
   distribution; ESMA34-39-897 para. 45 expects "a significant number and variety of
   market stresses".
4. Record the scenario parameters with the result. A stressed loss without the shock,
   capacity haircut and spread expansion that produced it cannot be reviewed later.
