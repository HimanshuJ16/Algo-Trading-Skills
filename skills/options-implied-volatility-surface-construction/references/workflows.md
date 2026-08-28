# Workflows for Options Implied Volatility Surface Construction

## 1. Market quote ingestion and Black-Scholes inversion

`implied_volatility_from_price(option_type, strike, tte, market_price, spot=None, r=None, q=None)`

1. Normalize `option_type` to `CALL` or `PUT`. Anything else raises — treating an
   unrecognized string as a put silently inverts the wrong formula.
2. Compute the no-arbitrage price bounds:

   ```
   call:  max(S e^{-qt} - K e^{-rt}, 0)  <  C  <  S e^{-qt}
   put:   max(K e^{-rt} - S e^{-qt}, 0)  <  P  <  K e^{-rt}
   ```

   A quote at or outside them has no implied volatility. Raise. Do not clamp:
   a fabricated wing volatility survives into the fit and into every price
   derived from it.
3. Bisect on `[1e-6, 5.0]`. The BS price is strictly increasing in sigma, so the
   bracket is valid and bisection converges unconditionally. Newton-Raphson's
   step is `residual / vega`; vega collapses deep ITM/OTM and at short expiry,
   where the step is unbounded.
4. After converging, estimate the identification resolution as
   `max(|price|, 1) * 1e-15 / vega(solution)`. Above `1e-6`, warn: the price is
   flat in sigma over a band wider than the answer's apparent precision.
   Exclude those quotes from the fit.

Ordering matters. Checking bounds *before* solving means the failure is reported
as "this quote is not invertible" rather than as a solver that ran out of
iterations.

## 2. Per-expiration smile calibration

`calibrate_smile_from_quotes(quotes, spot=None) -> SmileCalibrationResult`

1. Require all quotes to share one `tte_years`. A smile is one expiration;
   mixing tenors fits a surface cross-section as if it were a slice.
2. Require at least three quotes.
3. Invert each quote (step 1) to an implied volatility.
4. Least-squares fit `sigma(x) = atm + alpha*x + beta*x^2` where `x = K/S - 1`,
   solving the 3x3 normal equations by Gaussian elimination with partial
   pivoting. A singular system means the quotes do not span three distinct
   moneyness levels and the coefficients are not identified — raise rather than
   return an arbitrary solution.
5. Report the RMS residual in volatility units. A large residual means the
   quadratic does not describe this chain; do not proceed on the fitted numbers
   without looking at it.

Fitting each expiration independently gives **no** guarantee that the resulting
surface is calendar-arbitrage-free. Step 3 is not optional.

## 3. Surface grid evaluation

`construct_surface_grid(strikes, expirations_tte, atm_vol_by_tte=None)`

1. Deduplicate and sort both axes. Validate every value is finite and strictly
   positive.
2. Resolve the ATM level per expiration. With `atm_vol_by_tte`, every expiration
   must be present — substituting the flat config level for a missing tenor
   fabricates a term structure and can turn a real calendar violation into a
   clean report.
3. For each `(K, tau)` evaluate `sigma(K/S)`, and record the forward
   `F = S e^{(r-q)tau}`, the log-forward moneyness `k = ln(K/F)`, and the total
   variance `w = sigma^2 tau`. Nothing is rounded.

## 4. Calendar spread audit

Condition: `d/dtau w(k, tau) >= 0` at **fixed log-forward moneyness**
(Gatheral & Jacquier Lemma 2.1 / Definition 2.2).

1. Derive the audit levels from the input strikes at the **front** expiration:
   `k_i = ln(K_i / F_{tau_0})`.
2. For each `k_i`, walk the expirations in ascending order. At each, re-strike:
   `K = F_tau * e^{k}`. Evaluate the smile there and compute `w`.
3. Flag any step where `w` falls by more than the floating-point tolerance.

**Auditing at fixed strike is a different, wrong comparison.** Whenever `r != q`
the forward drifts between expirations, so the same strike sits at a different
`k` at each one. Worked counterexample, with `alpha = -2.0`, `beta = 0`,
`S = 100`, `r = 5%`, `q = 0`:

```
strikes (98, 100, 102), expirations (0.5, 1.0)

fixed-strike scan (wrong):
  K= 98:  w(0.5)=0.028800  ->  w(1.0)=0.057600   monotone
  K=100:  w(0.5)=0.020000  ->  w(1.0)=0.040000   monotone
  K=102:  w(0.5)=0.012800  ->  w(1.0)=0.025600   monotone
  verdict: clean

fixed-k scan (correct), at k = ln(102 / F_0.5) = -0.005197:
  tau=0.50:  F=102.5315, K=102.000, m=1.0200, sigma=0.16000, w=0.012800
  tau=1.00:  F=105.1271, K=104.582, m=1.0458, sigma=0.10836, w=0.011741
  verdict: CALENDAR_ARBITRAGE_VIOLATION
```

## 5. Butterfly audit

Condition: `d^2 C / dK^2 >= 0`, equivalently a non-negative risk-neutral density
(Breeden & Litzenberger 1978; Gatheral & Jacquier Definition 2.3).

For each expiration, price a call at every strike off the surface, then for each
consecutive triple `K1 < K2 < K3`:

```
w1 = (K3 - K2) / (K3 - K1)
w3 = (K2 - K1) / (K3 - K1)
butterfly = w1*C(K1) + w3*C(K3) - C(K2)     must be >= 0
```

Use the spacing weights, not `0.5/0.5`. Listed chains are not equally spaced: on
strikes `(90, 95, 150)` with a benign surface the equal-weighted value is
negative while the correctly weighted one is positive.

Calls are used, but the condition is not call-specific — put-call parity is
linear in `K`, so put convexity in strike is the same statement.

## 6. Report generation

`IVSurfaceConstructionReport` carries the grid, the violation lists, and
`calendar_audit_performed` / `butterfly_audit_performed`.

Status values:

| Status | Meaning |
|---|---|
| `ARBITRAGE_FREE_SURFACE` | Both audits ran on this grid; neither found a violation. |
| `CALENDAR_ARBITRAGE_VIOLATION` | Total variance fell with maturity at some `k`. |
| `BUTTERFLY_ARBITRAGE_VIOLATION` | Call price concave in strike somewhere; negative density. |
| `STATIC_ARBITRAGE_VIOLATION` | Both. |
| `UNAUDITED_SURFACE` | The grid was too sparse to run an audit (calendar needs >= 2 expirations, butterfly >= 3 strikes). |

`is_arbitrage_free` is `True` only when both audits ran **and** both were clean.
An unaudited surface is not a clean one.
