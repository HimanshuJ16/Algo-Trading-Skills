---
name: fx-forward-and-swap-position-tracking
description: >-
  Treasury valuation engine for FX outright forwards and FX swaps: Covered Interest Rate Parity forward pricing on per-currency day-count bases, forward/swap points at the pair's own pip size, discounted mark-to-market by quote currency, and net exposure by currency and maturity bucket.
domain: Global Market Integration & FX
subdomain: FX Forwards, Swaps & Treasury Risk
tags: ["fx-forward", "fx-swap", "covered-interest-parity", "swap-points", "mark-to-market", "fx-exposure", "treasury-risk", "day-count-convention"]
brokers_frameworks: ["Covered Interest Parity (CIRP)", "BIS Triennial FX Survey conventions", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill on institutional FX desks, multi-currency treasury platforms, and cross-border hedging systems that hold **outright forwards** (a rate locked for settlement more than two business days out) and **FX swaps** (a near leg plus a reversing far leg, used to roll a hedge without taking directional risk). The engine prices the theoretical forward under **Covered Interest Rate Parity**,

$$F = S \times \frac{1 + r_q \cdot (T/B_q)}{1 + r_b \cdot (T/B_b)}$$

marks each position to present value, and aggregates net exposure by currency and by maturity bucket. Note the two denominators: $B_b$ and $B_q$ are the *base* and *quote* currency money-market bases, and they are frequently different.

## When NOT to Use

- **As the source of a tradable forward rate.** The CIRP output is a theoretical benchmark. Covered interest parity has not held since 2008; the residual is the cross-currency basis, which is persistent, currency-specific, and sizeable (BIS Working Paper 590). Where an outright is quotable, pass it as `market_forward_rate` and the engine marks to it instead — the CIRP rate then becomes your basis diagnostic, not your mark.
- **As a hedge-accounting engine.** It computes an economic mark. It does not split the spot element from the forward element, so it cannot on its own feed an IFRS 9 cost-of-hedging designation, and it performs no effectiveness testing. See the accounting touchpoint note in `references/standards.md`, which is flagged as unverified against the primary text.
- **On non-deliverable forwards without adjustment.** An NDF cash-settles against a fixing in one currency; the two-sided currency commitment this engine reports as `net_exposure_by_currency` is not what an NDF creates.
- **As a curve.** One `market_rates` entry per pair carries one spot and one rate pair, so a book with 1M and 1Y positions in the same pair is being marked off a single tenor point. Audit distinct tenors separately, or key them separately, until you have a real term structure — see `multi-currency-pnl-and-fx-conversion`.
- **For settlement, credit, or funding risk.** Net currency exposure is market risk. Herstatt-style settlement risk and counterparty exposure are different problems — see `counterparty-credit-risk-for-otc-derivatives`.

## Prerequisites

- Spot rate $S$ quoted as **units of quote currency per one unit of base currency**, consistent with the `BASE/QUOTE` pair string.
- Base- and quote-currency money-market simple rates for the tenor, as decimals. Negative rates are supported.
- Per-contract terms: `currency_pair`, `base_currency`, `quote_currency`, `contract_type`, `position_side`, `notional_base_currency` (positive, in base currency), `agreed_forward_rate` (all-in outright, not points), and `days_to_maturity`.
- `days_to_maturity` must be the **remaining** calendar days to settlement at the valuation date, not the original tenor. Feeding the original tenor freezes the position at trade date and prevents the mark from ever converging to spot.
- For an FX swap: **two rows** sharing one `contract_id`, one `swap_leg='NEAR'` and one `swap_leg='FAR'`, in opposite directions.

## Workflow

1. **Resolve the day-count basis for each currency separately.**
   - The engine looks up each currency in a verified table (USD 360, EUR 360, GBP 365, JPY 365) before using `default_day_count_basis`, and logs at WARNING every currency it had to fall back on.
   - **Decision point — a single denominator is not a shortcut.** On a 6-month GBP/USD forward ($S=1.2500$, GBP $4.5\%$, USD $5.0\%$), the correct mixed basis gives $F = 1.253434$ (34.34 points) while forcing Act/360 on both legs gives $1.253056$ (30.56 points). That is a 3.78-pip pricing error on every sterling trade. Passing an integer as `day_count_basis` raises `TypeError` for exactly this reason.
2. **Price the CIRP forward and decide what to mark against.**
   - If `market_forward_rate` is present for the pair, that is the mark (`mtm_basis = OBSERVED_MARKET_FORWARD`); otherwise CIRP is (`CIRP_THEORETICAL`).
   - **Decision point — the gap between the two is the cross-currency basis, not a bug.** If `valuation_forward_rate` and `cirp_forward_rate` differ materially, that spread is information about funding conditions. Do not "fix" it by discarding the observed rate.
3. **Scale forward points to the pair's own pip size.**
   - `pip_factor` is 10,000 for four-decimal pairs and **100 where the quote currency is yen**. On USD/JPY a $-1.6692$ rate move is $-166.92$ points, not $-16{,}692$.
4. **Mark to market as a present value.**
   - Maturity cash flow, in quote currency: $\text{Notional}_{\text{base}} \times (F_{\text{valuation}} - F_{\text{contract}})$, negated for a `SELL`.
   - Discount on the **quote currency's own basis**: $DF = 1 / (1 + r_q \cdot T/B_q)$, because the cash flow is denominated in the quote currency.
   - Both figures are reported: `undiscounted_mtm_quote` and `mtm_pv_quote`. Report the PV.
5. **Aggregate exposure in both currencies of every pair, and by maturity bucket.**
   - A long 1mm EUR/USD forward at 1.1050 is $+1{,}000{,}000$ EUR **and** $-1{,}105{,}000$ USD. Tracking only the base side hides half the commitment.
   - **Decision point — netted exposure is not flat exposure.** An FX swap nets to zero at book level while carrying a full notional of gap risk in each of two buckets. Read `net_exposure_by_maturity_bucket`, not just `net_exposure_by_currency`.
6. **Consolidate P&L only with explicit conversion rates.**
   - P&L is returned per quote currency. Asking for a `reporting_currency` without a `reporting_fx_rates` entry for every quote currency in the book raises, rather than adding USD to JPY.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Applying one day-count basis to both legs**: USD and EUR money markets accrue Actual/360, sterling and yen Actual/365. GBP/USD and USD/JPY have *mixed* legs, and a single denominator misprices them systematically in one direction — 3.78 pips on a 6-month GBP/USD forward.
- **Assuming JPY is still Actual/360**: it was under JPY LIBOR, which has ceased. The domestic yen money market and its successor benchmarks TONA and TORF accrue Actual/365 (Fixed). A stale table silently misprices every yen forward.
- **Reporting an undiscounted mark**: $N \times (F_{\text{mkt}} - F_{\text{ctr}})$ is a cash flow *at settlement*. On the worked EUR/USD example it is \$459.06 at maturity but \$453.39 today — the difference is the quote-currency carry, and it grows with tenor and rates.
- **Summing P&L across quote currencies**: a book of EUR/USD and USD/JPY forwards generates USD and JPY cash flows. Adding 453 and −6,683,794 and labelling the result "USD" produces a number that is wrong by five orders of magnitude.
- **Using a 10,000 pip factor on yen pairs**: overstates USD/JPY swap points by exactly 100×, which will pass a plausibility check on a small move and fail catastrophically on a large one.
- **Treating an FX swap as one directional row**: a swap is a near leg and a reversing far leg. Booked as a single row it looks like an outright and misstates both the maturity gap and the direction. Two same-direction legs under one `contract_id` double the exposure instead of rolling it — the engine rejects that shape.
- **Defaulting an unrecognised `position_side` to SELL**: silently inverts the sign of a typo'd row. Any side that is not `BUY` or `SELL` raises.
- **Tracking only base-currency exposure**: every forward commits *two* currencies. Netting EUR/USD against USD/JPY on the base side alone leaves the USD leg invisible.
- **Marking a whole book off one tenor point**: one spot and one rate pair per currency pair is a single point, not a curve. Positions at genuinely different tenors need their own market data.
- **Passing original tenor instead of remaining life**: the mark never converges to spot and the position never leaves its original maturity bucket.

## Verification

- Instantiate `FxForwardSwapTrackingEngine()`. Price EUR/USD with $S=1.1000$, $r_{\text{EUR}}=3.0\%$, $r_{\text{USD}}=5.0\%$, $T=90$ days: verify $F_{\text{fair}} = 1.10545906$ and **54.59** forward points (not 55). For a `BUY` of €1,000,000 at a contract rate of 1.1050, verify `undiscounted_mtm_quote` $= +\$459.06$, `quote_discount_factor` $= 0.98765432$, and `mtm_pv_quote` $= +\$453.39$. A `SELL` of the same contract must return the exact negatives.
- Mixed-basis regression: price GBP/USD with $S=1.2500$, $r_{\text{GBP}}=4.5\%$, $r_{\text{USD}}=5.0\%$, $T=180$ days. Verify $F = 1.25343407$ with `base_day_count_basis=365` and `quote_day_count_basis=360`. Forcing Act/360 on both legs yields $1.25305623$ — 3.78 pips lower.
- Pip-factor regression: price USD/JPY with $S=150.00$, $r_{\text{USD}}=5.0\%$, $r_{\text{JPY}}=0.5\%$, $T=90$ days. Verify $F = 148.33079655$ and **−166.92** points at `pip_factor=100.0`; the same move at 10,000 would read −16,692.03.
- Multi-currency check: audit the EUR/USD and USD/JPY positions together and verify `unrealized_mtm_pv_by_quote_currency` has separate `USD` and `JPY` entries and that `net_unrealized_mtm_pv_reporting_currency` is `None` until `reporting_currency` **and** `reporting_fx_rates` are both supplied.
- Negative checks — each must raise: an empty book, `position_side='LONG'`, a non-positive notional, a negative `days_to_maturity`, a `currency_pair` that disagrees with base/quote, a duplicated row, missing market data, an FX swap with one leg, an FX swap whose legs share a direction, a far leg that matures no later than its near leg, `day_count_basis=360` passed as an integer, and a `reporting_currency` with no rate for one of the book's quote currencies.
- Run `python -m unittest discover -s skills/fx-forward-and-swap-position-tracking/scripts` and confirm all tests pass.

## Related Skills

- `multi-currency-pnl-and-fx-conversion`
- `currency-pair-quoting-convention-normalization`
- `cross-asset-hedge-execution-synchronization`
- `multi-currency-var-aggregation`
- `counterparty-credit-risk-for-otc-derivatives`
- `global-exchange-holiday-calendar-handling`
