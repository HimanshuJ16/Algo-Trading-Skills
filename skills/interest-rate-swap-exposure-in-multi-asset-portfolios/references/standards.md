# Standards for IRS Risk Management

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Duration multiplier | Swap DV01 MUST use the fixed-leg annuity `A = Σ δ_i·DF_i`, never a `tenor / 2` heuristic. Under a flat curve `A(y, n, f) = (1/y)·(1 − (1 + y/f)^(−n·f))`, with `A(0, n, f) = n`. |
| Pay-Fixed DV01 sign | Pay-Fixed swaps MUST carry a positive DV01 in this module's convention (short duration, gains on a rate rise). |
| Receive-Fixed DV01 sign | Receive-Fixed swaps MUST carry a negative DV01 (long duration, gains on a rate drop). |
| Bond DV01 input | MUST be supplied as signed P&L per +1 bps rise — negative for a long bond book. The opposite convention silently doubles exposure. |
| Tenor | MUST be the remaining tenor. Non-positive tenors MUST be rejected, never floored to a minimum duration. |
| Currency | DV01 MUST only be aggregated within a single curve. Non-USD positions MUST be rejected rather than summed into a USD total. |
| Hedge sizing | Required hedge notional MUST be derived from the hedge instrument's own annuity at the live par rate, and MUST report an explicit side (`PAY_FIXED` / `RECEIVE_FIXED` / `NONE`) rather than relying on the sign of a notional. |
| Neutrality claim | A zero net DV01 MUST NOT be described as risk-neutral: it is neutral only to a parallel shift, at first order, ignoring gross notional and counterparty exposure. |

## Valuation basis

For a vanilla fixed-vs-float swap the par rate `S` satisfies `S·A = 1 − P(t, T)` per unit notional, so the value to the payer of fixed is `V = N·A·(S − s_fix)` and `∂V/∂S = N·A`. DV01 is therefore `±N·A·0.0001`, positive for the payer. This is the same annuity/PVBP quantity the market calls PV01: "a dollar value of a basis point ... a change in swap present value resulting from a one basis point shift in a swap curve", computed as notional × 1 bp × day-count × discount factor summed across periods.

- *Mechanics and Definitions of DV01 in Derivatives* — Actrix Financial Technology. DV01/PV01/PVBP as the change in swap PV per 1 bp curve shift, aggregated across legs and periods. https://actrixft.com/mechanics-and-definitions-of-dv01-in-derivatives/
- *Basis Point Value (BPV, DV01)* — Barbican Consulting. Per-period DV01 as notional × 0.01% × day count × discount factor. https://www.barbicanconsulting.co.uk/bpv
- Par-swap relation `S·Σδ_k·P(t,τ_k) = 1 − P(t,T)`, with the floating leg carrying near-zero duration away from a reset, so swap duration is essentially fixed-leg duration.

## Market conventions

| Convention | Detail | Effect in this module |
|---|---|---|
| USD SOFR fixed-vs-float | Annual payments on **both** legs, ACT/360 day count. | `payment_frequency_per_year` defaults to `1`. |
| Legacy USD LIBOR | Semi-annual 30/360 fixed leg vs quarterly ACT/360 floating. | Set `payment_frequency_per_year = 2` for the fixed leg. |
| ACT/360 accrual | An annual ACT/360 period accrues ≈ 365/360 = 1.0139 years. | Not modelled: accruals are idealised at `1/f`, understating the annuity by ~1.4% for USD SOFR. |

- *SOFR Swap Nuances* — Clarus Financial Technology: "A Fixed-Float SOFR swap trades with annual payments on each side. The annual payments are calculated using an Act/360 DCC." https://www.clarusft.com/sofr-swap-nuances/
- *Pricing and Hedging USD SOFR Interest Rate Swaps with SOFR Futures* — CME Group (2025). https://www.cmegroup.com/articles/2025/price-and-hedging-usd-sofr-interest-swaps-with-sofr-futures.html

## Out of scope

Bootstrapped discount curves, key-rate/bucketed DV01, convexity, forward-starting and amortising structures, swaptions, CSA discounting, initial/variation margin and CVA are **not** modelled here. Regulatory margin and clearing-house risk figures must come from a curve-based engine, not from this estimator.
