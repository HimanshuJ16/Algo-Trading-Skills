# Variance Swap & Volatility Derivative Pre-Trade / Sign-Off Checklist

Work top to bottom. Anything unchecked in **Units & Notional** stops the trade — that
section is where the 40x errors live.

## Units & Notional (blocking)

- [ ] **Strike units confirmed**: $K_{\text{vol}}$ in volatility *points* (20.0), not
      decimals (0.20). $K_{\text{var}} = K_{\text{vol}}^2 = 400.0$.
- [ ] **Notional type identified on the term sheet**: vega notional (dollars per
      volatility point) or variance notional (dollars per variance point)?
- [ ] **Conversion applied**:
      $N_{\text{var}} = \dfrac{N_{\text{vega}}}{2 K_{\text{vol}}}$. At a 20% strike
      these differ by **40x** — read `contract.variance_notional_usd` rather than
      dividing by hand.
- [ ] **Worst-case P&L sized off the variance notional**, since P&L is linear in
      variance, not volatility.

## Option Strip Hygiene

- [ ] **Forward computed from the valuation-date spot**, $F = S_0e^{rT}$, with the
      continuously compounded rate to *this swap's* maturity.
- [ ] **Reference strike $S^* = K_0$ identified**: the largest available strike at or
      below $F$. Confirm $K_0 \le F$ and note the gap.
- [ ] **One OTM quote per strike**: puts below $K_0$, calls above $K_0$, the average of
      the put and call at $K_0$; ITM quotes discarded.
- [ ] **$\Delta K_i$ built on the deduplicated grid**, interior
      $(K_{i+1}-K_{i-1})/2$, one-sided at both edges. Verify this was *not* computed
      over the raw two-sided chain.
- [ ] **Both wings present**: at least one selected strike either side of $K_0$. A
      one-sided strip returns a plausible number that is badly low.
- [ ] **Strike range checked against DDKZ Table 4**: 50%–200% of spot recovers the
      true fair variance; 75%–125% costs ~0.1 vol points at three months but ~2.0 vol
      points at one year. Record which side of that you are on.
- [ ] **No zero, crossed, or stale premiums** in the strip.

## Replication & Convexity

- [ ] **Anchor term included**:
      $\frac{2}{T}\left[rT - (F/S^* - 1) - \ln(S^*/S_0)\right]$. It is zero *only*
      when $S^* = F$; it is not optional on a discrete grid.
- [ ] **$K_{\text{var}} > 0$** and within a sane band of ATM implied variance. A
      downside skew should push it *above* ATM implied variance.
- [ ] **Diagnostics reviewed**: `reference_strike`, `min_strike`, `max_strike`,
      `num_options_used`, and any truncation warning in the log.
- [ ] **Volatility swap only**: `vol_of_vol_points` sourced from a model or market and
      documented. $K_{\text{vol}} = \sqrt{K_{\text{var}} - \sigma_\Sigma^2}$.
      **Striking at $\sqrt{K_{\text{var}}}$ is not arbitrage-free.**
- [ ] **Convexity adjustment recorded**:
      $K_{\text{var}} - K_{\text{vol}}^2 = \sigma_\Sigma^2 > 0$.
- [ ] **Jump haircut considered** for event risk in the accrual window (DDKZ Table 5:
      7.2 variance points per 10% one-year gap, 28.8 at three months).

## Realized Variance & Accrual

- [ ] **Price source matches the confirmation** — official exchange closing prices on
      the named schedule.
- [ ] **Annualization factor matches the term sheet** (252 or 260 — do not assume the
      default).
- [ ] **Zero-mean convention preserved**: $\sum r_i^2$ with no sample-mean
      subtraction (DDKZ page 2).
- [ ] **Divisor understood**: the engine divides by returns actually observed
      (accrual-to-date). Settlement divides by the *expected* count and applies the
      confirmation's disruption provisions.

## Mark-to-Market & Margin

- [ ] **`current_spot` and `current_risk_free_rate` passed explicitly.** Falling back
      to inception values misplaces the forward and the $K_0$ boundary.
- [ ] **Contract type is `VARIANCE_SWAP`.** A volatility swap marked on the
      variance-linear formula is overstated by the convexity bias.
- [ ] **No substituted data**: an accrued contract has price history; a contract with
      remaining time has a strip. Missing legs must surface as unmarked, never as
      flat.
- [ ] **Blend weights sum to one**: $t/T + (T-t)/T$.
- [ ] **Discounting over the remaining term only**, $e^{-r(T-t)}$.
- [ ] **Mark posted to the risk ledger and ISDA variation margin process**, with the
      strip snapshot retained for audit.
