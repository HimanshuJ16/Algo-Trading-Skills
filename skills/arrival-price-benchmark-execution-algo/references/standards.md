# Standards for Arrival Price / Implementation Shortfall Algos

## Urgency Calibration

`kappa` encodes the trader's risk aversion `lambda`. Almgren-Chriss (2000) define it in two steps: `kappa_tilde^2 = lambda * sigma^2 / eta_tilde` (following Eq. 16), and `kappa` itself as the exact root of `2 * (cosh(kappa*tau) - 1) / tau^2 = kappa_tilde^2`. The familiar `kappa = sqrt(lambda * sigma^2 / eta)` is Eq. (19), an **approximation valid only as `tau -> 0`** — accurate for short bins, drifting for coarse ones. Here `sigma` is volatility, `eta` the temporary-impact coefficient, and `eta_tilde = eta - gamma*tau/2` its permanent-impact-adjusted form.

This skill fixes `kappa` per urgency level rather than deriving it; production systems should solve for it from live `sigma` and a calibrated impact model (see `execution-cost-model-recalibration-cadence`).

`kappa` carries units of `1/time`, and its reciprocal `1/kappa` is the trade's **half-life** — the time to deplete the position by a factor of `e` (Sec. 2.3). The half-life is a property of the security and the trader's risk aversion, **not of the horizon `T`**. Lengthening `T` at fixed `kappa` therefore does not spread the order out: it appends near-empty tail bins while the leading bins stay put. At `kappa = 1` the first bin is ~63.2% of the parent whether the horizon is 10 bins or 10,000.

| Urgency Level | Alpha Decay | `kappa` | Trajectory Profile | Market Impact | Timing Risk |
|---|---|---|---|---|---|
| **HIGH** | Minutes | 1.0 | Steeply front-loaded (sinh decay) | High | Low |
| **MEDIUM** | Hours | 0.5 | Moderate front-loading | Medium | Medium |
| **LOW** | Days | 0.0 (linear limit) | Uniform (Flat / TWAP) | Low | High |

Over a 10-bin horizon, `kappa = 1.0` front-loads ~63% of the order into the first bin; `kappa = 0.5` front-loads ~40%. Both decay smoothly and monotonically with no degenerate single-bin dump.

## Closed-Form Trajectory (Almgren & Chriss, 2000, Eqs. 17-18)

Remaining shares at the start of bin `t`:

```
x_t = X * sinh(kappa * (T - t)) / sinh(kappa * T)        t = 0..T
```

Child order size for bin `t`:

```
n_t = x_t - x_{t+1}
```

Properties:
- `x_0 = X` (full parent at start) and `x_T = 0` (fully executed at end), so `sum(n_t) = X` exactly.
- `n_t >= 0` and monotonically non-increasing for `kappa > 0` (front-loaded).
- `kappa -> 0` limit: `sinh(z) -> z`, so `x_t -> X * (T - t) / T` (linear), giving uniform `n_t = X / T` (TWAP).
- `kappa -> infinity` limit: schedule degenerates to immediate full execution at `t = 0`.

### Numerical Safety

`math.sinh(x)` overflows above `x ~= 710.48` (float64 max), and the intermediate `total_size * sinh(...)` product overflows to `inf` at a *lower* threshold that depends on the order size — after which `inf - inf` yields `NaN`.

Neither can be handled by short-circuiting to an immediate dump. A large `kappa * T` does **not** mean the schedule has degenerated to immediate execution: as noted above the half-life `1/kappa` is independent of `T`, so at `kappa = 1` the first bin is ~63.2% of the parent no matter how long the horizon. Collapsing it to 100% would be the maximum-market-impact outcome, not an approximation of the correct one.

The generator instead evaluates the ratio in a scaled form that never materialises `sinh(kappa*T)`:

```
sinh(a)/sinh(b) = exp(a - b) * (1 - exp(-2a)) / (1 - exp(-2b)),   0 <= a <= b
```

This is exact for arbitrarily long horizons, and `expm1` keeps the small-argument end accurate too.

### Integer Apportionment

Fractional trade sizes are converted to integers with the **largest-remainder (Hamilton) method**: floor every bin, then distribute leftover shares to the bins with the largest fractional remainders. This preserves the curve shape and guarantees a non-negative, sum-exact schedule. Residual ties break toward earlier bins to keep the front-loading intent.

## Expected Shortfall Cost Model

Two equivalent routes. Prefer the **definitional sums**, which is what `forecast_shortfall` implements.

### Definitional form — Eqs. (5) and (8)

For *any* schedule `n_k` (bin sizes) with `x_k` shares outstanding after bin `k`:

```
E(x) = 0.5*gamma*X^2 + epsilon*sum|n_k| + (eta_tilde/tau) * sum n_k^2       # Eq. (8)
V(x) = sigma^2 * tau * sum_{k=1..N} x_k^2                                    # Eq. (5)
U(x) = E(x) + lambda * V(x)      # the objective being minimized
```

with `eta_tilde = eta - gamma*tau/2`, and `epsilon` the fixed per-share cost (half-spread plus fees). The `V` sum runs `k = 1..N`, excluding the full parent `x_0`, which is never exposed to post-decision drift.

These hold for the integer schedule actually sent, not just the idealised optimum, and are free of the cancellation and overflow that afflict the closed form. `E` is in currency and `V` in currency **squared** — compare realised shortfall against `sqrt(V)`, never `V`.

### Closed form for the optimal trajectory — Eq. (20)

```
E(X) = 0.5*gamma*X^2 + epsilon*X
       + eta_tilde * X^2 * tanh(0.5*kappa*tau) * (tau*sinh(2*kappa*T) + 2*T*sinh(kappa*tau))
         / (2 * tau^2 * sinh^2(kappa*T))

V(X) = 0.5 * sigma^2 * X^2 * (tau*sinh(kappa*T)*cosh(kappa*(T-tau)) - T*sinh(kappa*tau))
         / (sinh^2(kappa*T) * sinh(kappa*tau))
```

Note `tanh(0.5*kappa*tau)` and `sinh(kappa*tau)` take the **bin length `tau`**, not the horizon `T`; substituting `T` there produces a *negative* expected cost at small `kappa`, which is impossible for a one-sided schedule. The unit tests assert both forms agree, and assert `E > 0` across a range of `kappa`.

### Sanity limits

Any implementation should reproduce these (the unit tests do):

| Regime | `E` | `V` | Source |
|---|---|---|---|
| Uniform / TWAP (`kappa -> 0`) | `0.5*gamma*X^2 + epsilon*X + eta_tilde*X^2/T` | `(1/3)*sigma^2*X^2*T*(1-1/N)*(1-1/(2N))` | Eqs. (10), (11) |
| Single-bin dump (`kappa -> inf`) | `epsilon*X + eta*X^2/tau` | `0` | Eqs. (12), (13) |

A live shortfall that persistently exceeds `E + lambda*V` by more than a pre-set tolerance — measured in units of `sqrt(V)` — indicates the impact/volatility assumptions feeding `kappa` are miscalibrated.

## Arrival Price Convention

- Use the **median 1-second top-of-book mid-quote** at parent-order submission. A single tick is noisy; a 1-second median is more stable. This exact definition is published by Talos (see References); it is one vendor's stated convention rather than a rule mandated by any regulator, so confirm what your own TCA provider uses before reconciling against their numbers.
- **Freeze it immutably** at decision time. Never recompute against "current mid" mid-execution — that hides real shortfall.
- **Sign and quantity convention.** With `s = +1` for a buy and `s = -1` for a sell, `Q` the parent quantity, `Q_f` the filled quantity at average price `P_exec`, `P_0` the arrival price and `P_end` the price at horizon end:

```
IS = s * [ Q_f * (P_exec - P_0)  +  (Q - Q_f) * (P_end - P_0) ] + fees
          \_____ execution _____/    \______ opportunity ______/
```

  Positive means underperformance (a cost). Two mistakes to avoid: writing the execution term as `(P_0 - P_exec)` — that reports a buy filled *above* arrival as a gain — and multiplying by the parent `Q` rather than the filled `Q_f`. The latter matters precisely because this skill's give-up policy can end with `Q_f < Q`, and the unfilled remainder is measured against `P_end`, not against a price at which nothing traded. See `implementation-shortfall-minimization` for the full four-component Perold decomposition and a reference implementation.

## References

- Almgren, R. and Chriss, N. (2000). "Optimal Execution of Portfolio Transactions." *Journal of Risk* 3(2), 5-39. https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf — Eq. (5) variance, Eq. (8) expected cost, Eqs. (10)-(13) limiting regimes, Eq. (16) and following for `kappa`/`kappa_tilde`/`eta_tilde`, Eq. (17) trajectory, Eq. (18) trade list, Eq. (19) small-`tau` approximation, Eq. (20) closed-form `E`/`V`, Sec. 2.3 half-life. (Dated December 2000; an earlier and more limited treatment appeared as Almgren and Chriss, "Value Under Liquidation," *Risk* 12(12), 1999 — the closed forms used here are from the 2000 paper.)
- Perold, A. F. (1988). "The Implementation Shortfall: Paper Versus Reality." *Journal of Portfolio Management* 14(3), 4-9. doi:10.3905/jpm.1988.409150 — definition of IS as the benchmark for execution quality.
- Hoch, E. (2025). "Execution Insights Through Transaction Cost Analysis (TCA): Benchmarks and Slippage." Talos Global, Inc., 3 April 2025. https://www.talos.com/insights/execution-insights-through-transaction-cost-analysis-tca-benchmarks-and-slippage — defines arrival price as "the median 1-second mid-point top-of-book quoted price at the parent order submission time."
- BestEx Research. "Designing Optimal Implementation Shortfall Algorithms with the BestEx Research Adaptive Optimal (IS) Framework." https://www.bestexresearch.com/insights/adaptive — adaptive IS, schedule-based vs opportunistic designs.
