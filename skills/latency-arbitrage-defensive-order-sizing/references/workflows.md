# Workflows for Latency-Arbitrage Defensive Order Sizing

Scope: sizing **one passive quote on one side of one instrument**. The engine never sees
the contra side, never models the cancel race, and never sends an order.

## 0. Calibrate before deploying (do this first, once per instrument)

The shipped `lambda_scaling = 0.50` is a placeholder. Deploying it unchanged sets your
quoting cut-off at a latency you did not choose.

1. Collect your own passive fills and compute forward markouts on them
   (`adverse-selection-measurement-for-passive-orders`). The realized rate at which
   resting quotes are picked off is the quantity $h = \lambda\sigma$ is standing in for.
2. Fit $\lambda$ so that $1 - e^{-\lambda\sigma\Delta\tau}$ reproduces the observed
   pick-off frequency at your measured $\sigma$ and $\Delta\tau$. Refit per liquidity tier;
   a single $\lambda$ across a mixed universe is not meaningful.
3. Sanity-check the implied cut-off: the engine stops quoting entirely at
   $\Delta\tau^{*} = \ln(1/(1-\theta)) / (\lambda\sigma)$ for threshold $\theta$. At the
   defaults ($\lambda = 0.5$, $\theta = 0.5$) that is **6.93 ms** at $\sigma = 0.20$ and
   **1.73 ms** at $\sigma = 0.80$. If your venue's cancel path exceeds that, the engine
   will simply never quote — which is a calibration failure, not a risk finding.
4. Record the calibration date, sample window, and instrument set. Recalibrate when the
   venue's latency profile or your colocation changes.

## 1. Measure the exposure window

- $\Delta\tau$ is **lead-event-to-sweep-arrival minus your cancel's delivery time**, on one
  synchronized clock domain (`cross-datacenter-clock-sync-validation`).
- Take a measured **high percentile**, not the mean. A gap averaging 2 ms that reaches
  20 ms at p99 is a 20 ms problem: the tail is what picks you off
  (`latency-monitoring-percentile-based-slas`).
- A **negative** $\Delta\tau$ is legitimate and means the cancel wins — zero exposure, full
  size. It is not an error and must not be clamped to an error path.
- If the probe is stale, dropped, or corrupt, pass the non-finite value straight through.
  Do **not** substitute a last-known-good or a zero: the engine's fail-closed path exists
  precisely so that "I cannot measure the gap" never becomes "there is no gap".

## 2. Construct the spec

`MarketStateSpec` validates its **structural** fields on construction and raises
`ValueError`: non-positive or non-integer `base_quote_qty`, `min_lot_size < 1`,
`lot_increment < 1`, negative or non-finite `spread_bps`, empty `symbol`.

It deliberately does **not** validate `latency_gap_ms` or `volatility_annualized`. Those are
market measurements; a stale sample is an expected production event and must produce an
auditable cancel report rather than an exception thrown inside the quoting path.

Set `min_lot_size` to the instrument's real round lot. For NMS stocks call
`round_lot_for_nms_price(average_closing_price)` — the round lot is price-tiered
(100 / 40 / 10 / 1 shares) under 17 CFR 242.600(b)(93), and the default of 100 is correct
only at or below \$250.00 per share.

## 3. Score the hazard

$$h = \lambda \sigma \ \text{(events/ms)}, \qquad P_{\text{snipe}} = 1 - e^{-h \Delta\tau}$$

- Reported to 4 dp. The rounding is applied **before** the cancel threshold is tested, so a
  borderline case can only ever move toward cancelling, never away from it.
- $P_{\text{snipe}}$ is an ordinal risk score, not a calibrated probability. Do not price
  anything off it.
- Zero volatility or zero/negative latency ⇒ $P_{\text{snipe}} = 0$.
- Non-finite input, or negative volatility ⇒ $P_{\text{snipe}} = 1.0$ and
  `INVALID_INPUT_CANCEL`.

## 4. Size, floor, widen

1. $Q_{\text{raw}} = \lfloor Q_0 (1 - P_{\text{snipe}}) \rfloor$ — truncation, so 904.8
   becomes 904, not 905.
2. Floor $Q_{\text{raw}}$ to a multiple of `lot_increment`. **Down, never up**: rounding a
   defensive size up would show more risk than the model just authorised. The default
   increment of 1 leaves the size unrounded.
3. $W = 1 + 2 P_{\text{snipe}}$; `defensive_spread_bps` = `spread_bps` $\times W$, returned
   already applied so no caller has to recompute it.

   **Known model gap:** $P_{\text{snipe}}$ never reads the spread, so widening does not feed
   back into the risk score. In Budish–Cramton–Shim a wider spread lowers
   $\Pr(J > s/2)$ and reduces sniping intensity directly. Here $W$ is a directive to the
   quoter; the risk reduction it buys is *not* already banked in $P_{\text{snipe}}$, which
   is therefore conservative once the quote widens.

## 5. Apply the cancel precedence

Evaluated in this order; each returns a complete report:

| Order | Condition | Status | Rationale |
|---|---|---|---|
| 1 | Non-finite measurement, or negative volatility | `INVALID_INPUT_CANCEL` | Checked first so a corrupt probe can never reach the arithmetic and emerge as an approved full-size quote. |
| 2 | $P_{\text{snipe}} \ge$ threshold (**inclusive**) | `HIGH_SNIPING_RISK_CANCEL` | Above the threshold the answer is "not on the book", not "smaller". At exact equality the safe answer is still to pull. |
| 3 | Surviving size $<$ `min_lot_size` | `MIN_LOT_CANCEL` | A sub-lot residual pays the fee without holding a usable quote, and on an NMS stock it is odd-lot information (Rule 600(b)(69)) rather than a protected quotation (Rule 600(b)(16), (81)). |
| 4 | otherwise | `QUOTE_DEFENSIVELY_SIZED` | |

## 6. Check comparable size before sending (EU market makers)

The engine sizes one quote and cannot enforce two-sided parity, so it reports the
divergence its own reduction creates:

- `size_divergence_ratio` = $(Q_0 - Q_{\text{def}}) / Q_{\text{def}}$ — measured against the
  **smaller** quote, the conservative reading, because RTS 8 Art. 1(2)(c) does not name a
  denominator. A pulled quote reports `inf`, not a small number.
- `breaches_comparable_size_one_sided` is `True` once that exceeds 50%.

If the firm is inside a market making agreement, either apply the same reduction to both
sides or treat the flag as blocking. Art. 3's exceptional-circumstances list does not
include elevated sniping risk, so a routine defensive pull does not suspend the obligation.
Track the cumulative time spent cancelled against the Art. 1(1)(b) 50%-of-continuous-hours
presence floor, and the cancel traffic against your order-to-trade budget
(`order-to-trade-ratio-fee-penalty-avoidance`).

## 7. Emit and retain

Every path returns a `DefensiveSizingReport` carrying the directive
(`defensive_quote_qty`, `spread_multiplier`, `defensive_spread_bps`), the inputs to the
decision (`sniping_probability`, `sniping_hazard_per_ms`), the compliance diagnostics
(`size_divergence_ratio`, `breaches_comparable_size_one_sided`), and `audit_notes`. Retain
them: a cancel with no record of why is not reviewable after the fact.
