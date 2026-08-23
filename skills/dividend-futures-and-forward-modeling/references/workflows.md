# Workflows for Dividend Futures and Forward Modeling

## 1. Dividend schedule ingestion

- Collect the **gross** declared amount $D_i$, the **payment date** $t_i^{pay}$, and the **ex-date** $t_i^{ex}$ for each expected dividend. Supply `ex_time_years` whenever it differs from `payment_time_years`; omitting it makes the engine treat them as identical.
- Tag special/extraordinary dividends with `is_special=True` — they are excluded from the dividend-index accrual but still depress the forward.
- Record the per-event withholding tax rate in $[0, 1)$ for cross-border names.
- Registration validates eagerly: non-finite values, negative amounts, a tax outside $[0, 1)$, or an ex-date after its own payment date all raise `DividendModelError`.

## 2. Accrual window selection

- Bound the window at both ends: `accrual_start_years < ex_date <= maturity_years`.
- For a **forward price**, the window normally starts at 0 — anything already ex is not a claim on a forward buyer.
- For a **dividend future**, set `accrual_start_years` to the contract's accrual start. The dividend index resets to zero after the leading contract expires, so earlier dividends belong to the previous contract.
- Inspect `excluded_dividend_ids` and `warnings` on the report to confirm the filter did what you intended, rather than assuming it did.

## 3. Present and future value computation

- $\text{PV}(D) = \sum_i D_i (1 - \tau_i) e^{-r t_i^{pay}}$ — **net** of withholding, discounted from the payment date.
- $\text{FV}(D) = \sum_i D_i (1 - \tau_i) e^{r (T - t_i^{pay})}$.
- Dividend-future fair value $= \sum_i D_i$ over ordinary events in the window — **gross**, untaxed, specials excluded.
- A dividend that goes ex before $T$ but pays after it stays in the window and is discounted from its later payment date.

## 4. Theoretical forward pricing

- $F_{\text{theo}}(0, T) = (S_0 - \text{PV}(D)) e^{r T}$, equivalently $S_0 e^{r T} - \text{FV}(D)$.
- A non-positive theoretical forward means $\text{PV}(D) \ge S_0$. The engine warns rather than raising, because both a deep-discount name and a mis-scaled or wrong-currency feed land here. Treat it as a data-quality stop.

## 5. Arbitrage auditing

- Spread $\Delta = F_{\text{market}} - F_{\text{theo}}$, which must **strictly exceed** the applicable cost threshold.
- $\Delta > \text{cost}_{\text{fwd}}$: sell the forward, buy and carry the shares (`ARBITRAGE_SHORT_FORWARD_LONG_SPOT`).
- $\Delta < -\text{cost}_{\text{rev}}$: buy the forward, short the shares (`ARBITRAGE_LONG_FORWARD_SHORT_SPOT`). Configure `reverse_arbitrage_cost_threshold_usd` separately — this leg needs stock borrow and pays gross manufactured dividends, so the symmetric default overstates it.
- Read `estimated_gross_profit_usd` ($|\Delta|$, before costs), `estimated_net_profit_usd` (after the threshold), and `applied_cost_threshold_usd` (which threshold was used).

## 6. Sizing and units

- Every figure is **per unit** — per share, or per index point. Apply the venue multiplier before sizing: Eurex FEXD is EUR 100 per point, CME SDA is USD 250 per point.
- Confirm the spot, market forward, and dividend amounts share one currency and one quotation basis before comparing them.
