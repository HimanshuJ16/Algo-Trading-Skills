---
name: dividend-futures-and-forward-modeling
description: >-
  Use when constructing an equity forward curve with discrete cash dividends, pricing
  dividend futures at fair value and detecting cash-and-carry arbitrage against the
  listed forward.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: multi-asset-derivatives
  tags: dividend-futures, forward-modeling, discrete-dividends, cash-and-carry, cost-of-carry, eurex-fexd, cme-sda
  brokers_frameworks: "Eurex FEXD; CME SDA; Python Math / Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill in equity index desk trading, forward curve construction, and index arbitrage strategies. Equities and stock indices (S&P 500, EURO STOXX 50) distribute discrete cash dividends, so the theoretical forward price $F(0, T)$ must account for the present value of expected discrete dividends $\text{PV}(D)$. Dividend futures — Eurex EURO STOXX 50 Index Dividend Futures (product ID **FEXD**) and CME S&P 500 Annual Dividend Index Futures (**SDA**) — isolate dividend risk so quants can trade dividend expectations independently of spot.

The engine computes **two different dividend measures**, and confusing them is the most common way to misprice this product:

| Measure | Basis | Why |
|---|---|---|
| $\text{PV}(D)$ / $\text{FV}(D)$ for the **forward price** | **Net of withholding tax** | The cash-and-carry arbitrageur holding physical shares receives only the net cash. |
| Fair value of the **dividend future** | **Gross ordinary** dividends, special dividends excluded | Eurex FEXD settles on "the cumulative total of the relevant gross dividends of the constituents"; CME SDA accumulates ordinary gross dividends and excludes special/extraordinary ones. |

## When NOT to Use

- **Single-stock American options with discrete dividends** — the escrowed-dividend forward here does not handle early-exercise boundaries. Use a dividend-aware binomial/PDE model.
- **Continuous-yield index approximations** — if you are pricing a broad index over a long horizon with a continuous $q$, this engine's discrete-schedule machinery adds precision you are not using. Its value is near-dated, where the exact ex-date placement matters.
- **Stochastic-dividend or stochastic-rate valuation** — the fair value here is the deterministic expected accrual. It carries no convexity adjustment and no dividend-volatility term, so it will not price options on dividend futures (Eurex OEXD).
- **Sizing a trade directly from the output** — all figures are per unit (per share, or per index point). They are not scaled by a contract multiplier.

## Prerequisites

- Spot price $S_0 > 0$, risk-free rate $r$ (decimal, e.g. `0.05` for 5%; negative rates are supported), time to maturity $T > 0$ in years.
- Expected discrete dividend events: gross amount $D_i$, **payment time** $t_i^{pay}$, and where they differ, the **ex-date** $t_i^{ex}$.
- Withholding tax rate per event in $[0, 1)$ where cross-border tax applies.
- Market forward price or dividend futures quote, in the same units and currency as the spot.
- Round-trip transaction cost estimates — separately for the forward and reverse legs.

## Workflow

1. **Ingest the dividend schedule with both dates.** A dividend has an ex-date (when eligibility is fixed) and a payment date (when cash arrives); they are different, and this engine keeps them apart. Supply `ex_time_years` whenever it differs from `payment_time_years` — omitting it makes the engine assume they coincide, which misplaces a dividend that goes ex just before expiry and pays after it. Flag special/extraordinary dividends with `is_special=True`.
2. **Bound the accrual window at both ends.** Filtering only on $t_i \le T$ is not enough. A dividend that has *already gone ex* must be excluded: including it overstates $\text{PV}(D)$, understates the theoretical forward, and manufactures a false `ARBITRAGE_SHORT_FORWARD_LONG_SPOT` against an honest quote. For a dividend future, set `accrual_start_years` to the contract's accrual start — the index resets to zero after the leading contract expires, so pre-window dividends belong to the *previous* contract. Check `excluded_dividend_ids` on the report to confirm the window did what you intended.
3. **Compute PV/FV on net dividends, accrual on gross.**
   - $\text{PV}(D) = \sum_i D_i (1 - \tau_i) e^{-r t_i^{pay}}$ over $t_i^{ex}$ in the window.
   - $\text{FV}(D) = \sum_i D_i (1 - \tau_i) e^{r (T - t_i^{pay})}$.
   - Dividend-future fair value $= \sum_i D_i$ over ordinary (non-special) events in the window, **gross**.
4. **Price the forward.** $F_{\text{theo}}(0, T) = (S_0 - \text{PV}(D)) e^{r T} = S_0 e^{r T} - \text{FV}(D)$. Both identities are computed and agree; if they diverge, the schedule has been mutated between calls.
5. **Audit the spread, then decide by direction — not by magnitude.** $\Delta = F_{\text{market}} - F_{\text{theo}}$.
   - $\Delta > \text{cost}_{\text{fwd}} \implies$ `ARBITRAGE_SHORT_FORWARD_LONG_SPOT` (sell the rich forward, buy and carry the shares).
   - $\Delta < -\text{cost}_{\text{rev}} \implies$ `ARBITRAGE_LONG_FORWARD_SHORT_SPOT`. **This leg is not the mirror image.** It requires borrowing the stock, so it carries a borrow fee, recall risk, and may be impossible in hard-to-borrow names; the short also pays *gross* manufactured dividends while a long holder receives them net of withholding. Set `reverse_arbitrage_cost_threshold_usd` explicitly — the symmetric default systematically overstates reverse-leg opportunities.
   - Otherwise `NO_ARBITRAGE`. The spread must *strictly exceed* the threshold.
6. **Read the report before acting.** `estimated_gross_profit_usd` is $|\Delta|$ before costs; `estimated_net_profit_usd` subtracts the threshold that actually applied, reported in `applied_cost_threshold_usd`. Treat a non-empty `warnings` list — especially a non-positive theoretical forward — as a data-quality stop, not a trade.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Netting withholding tax out of the dividend-futures fair value.** The dividend-point indices behind FEXD and SDA accumulate **gross** dividends. Applying a 15% withholding to the accrual understates the fair value of a $4.00 accrual by $0.60 — larger than most arbitrage thresholds, so the error alone can flip the signal.
- **Summing special dividends into the index accrual.** Special/extraordinary dividends are excluded from the dividend indices, but they *do* depress the forward, because the shareholder receives the cash. One schedule, two different filters.
- **Treating the payment date as the ex-date.** Eligibility is set on the ex-date; cash timing is the payment date. Collapsing them misprices any dividend that goes ex near expiry, and the error is largest exactly where near-dated forwards are most traded.
- **Leaving already-ex dividends in the feed.** A dividend paid last quarter is not a claim on a forward buyer. Including one at $2.00 cuts a $105.13 theoretical forward to $103.03 and reports a $2.10 "arbitrage" that does not exist.
- **Using a continuous dividend yield for individual stocks.** Applying a continuous $q$ to single-stock forwards instead of the discrete schedule misprices near-term contracts, where a single ex-date dominates.
- **Assuming symmetric arbitrage costs.** The reverse cash-and-carry needs stock borrow; pricing both legs off one threshold generates reverse-leg signals that cannot be executed.
- **Trading `estimated_gross_profit_usd` as if it were a P&L.** It is per unit and before costs. Multiply by the venue multiplier (Eurex FEXD: EUR 100/point; CME SDA: USD 250/point) and subtract the threshold before sizing.
- **Letting a NaN through.** A NaN spot or dividend used to yield a confident `NO_ARBITRAGE`, because every NaN comparison is False. Non-finite inputs are now rejected — do not catch and ignore that error.

## Verification

- Instantiate `DividendForwardModelingEngine`. Input $S_0 = 100.0$, $r = 5\%$, $T = 1.0$. Add two dividends of \$2.00 at $t_1 = 0.25$ and $t_2 = 0.75$. Expect $\text{PV}(D) \approx 3.9015$, $\text{FV}(D) \approx 4.1016$, and $F(0,T) \approx 101.0256$ — and confirm $(S_0 - \text{PV}(D))e^{rT}$ equals $S_0 e^{rT} - \text{FV}(D)$. Submit a market forward of \$104.00 and verify `ARBITRAGE_SHORT_FORWARD_LONG_SPOT` with gross profit $\approx 2.97$ and net $\approx 2.47$ at a \$0.50 threshold.
- Re-run the same schedule with `withholding_tax_pct=0.15` on both events: $\text{PV}(D)$ must fall to $\approx 3.3163$ while `fair_value_dividend_future_points` stays at **4.00** gross, and the theoretical forward must *rise* (a taxed holder loses less to dividends).
- Add a \$5.00 `is_special=True` dividend: it must enter $\text{PV}(D)$ but leave the accrual at 4.00.
- Confirm the window guards: a dividend at $t = 1.5$ and one at $t = -0.25$ must both land in `excluded_dividend_ids`, and the stale one must leave $\text{PV}(D) = 0$ rather than flagging arbitrage.
- Confirm invalid inputs raise `DividendModelError`: NaN/Inf spot or rate, $S_0 \le 0$, $T \le 0$, `withholding_tax_pct` outside $[0,1)$, negative dividend amounts, and an ex-date after its payment date.
- Run `python -m unittest discover -s skills/dividend-futures-and-forward-modeling/scripts`.

## Related Skills

- `synthetic-continuous-futures-contract-construction`
- `corporate-action-event-calendar-integration`
- `options-chain-expiry-cycle-conventions-by-exchange`
