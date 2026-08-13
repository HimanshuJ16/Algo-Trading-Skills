# Standards for Arrival Price / Implementation Shortfall Algos

## Urgency Calibration

`kappa` encodes the trader's risk aversion `lambda` via `kappa = sqrt(lambda * sigma^2 / eta_tilde)` (Almgren-Chriss), where `sigma` is volatility and `eta_tilde` the effective temporary-impact coefficient. This skill fixes `kappa` per urgency level; production systems should derive it from live `sigma` and a calibrated impact model (see `execution-cost-model-recalibration-cadence`).

| Urgency Level | Alpha Decay | `kappa` | Trajectory Profile | Market Impact | Timing Risk |
|---|---|---|---|---|---|
| **HIGH** | Minutes | 1.0 | Steeply front-loaded (sinh decay) | High | Low |
| **MEDIUM** | Hours | 0.5 | Moderate front-loading | Medium | Medium |
| **LOW** | Days | 0.0 (linear limit) | Uniform (Flat / TWAP) | Low | High |

Over a 10-bin horizon, `kappa = 1.0` front-loads ~63% of the order into the first bin; `kappa = 0.5` front-loads ~40%. Both decay smoothly and monotonically with no degenerate single-bin dump.

## Closed-Form Trajectory (Almgren & Chriss, 1999)

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

`sinh(x)` overflows near `x = 710` (float64 max). When `kappa * T` exceeds that threshold the schedule is already in the immediate-execution regime, so the generator short-circuits and places the entire parent in bin 0 rather than calling `sinh`.

### Integer Apportionment

Fractional trade sizes are converted to integers with the **largest-remainder (Hamilton) method**: floor every bin, then distribute leftover shares to the bins with the largest fractional remainders. This preserves the curve shape and guarantees a non-negative, sum-exact schedule. Residual ties break toward earlier bins to keep the front-loading intent.

## Expected Shortfall Cost Model

For validating live performance against theory, the Almgren-Chriss expected shortfall and variance of the optimal strategy are:

```
E(X) = 0.5 * gamma * X^2 + (eta_tilde * kappa * X^2 / 2) * (tanh(0.5*kappa*T)*sinh(kappa*T) - kappa*T) / sinh^2(kappa*T)
V(X) = 0.5 * sigma^2 * X^2 * (sinh(kappa*T)*cosh(kappa*T) - kappa*T) / sinh^2(kappa*T)
U(X) = E(X) + lambda * V(X)      # the objective being minimized
```

A live shortfall that persistently exceeds `E(X) + lambda * V(X)` by more than a pre-set tolerance indicates the impact/volatility assumptions feeding `kappa` are miscalibrated.

## Arrival Price Convention

- Use the **median 1-second top-of-book mid-quote** at parent-order submission. A single tick is noisy; a 1-second median is statistically stable (Talos / BestEx Research practice).
- **Freeze it immutably** at decision time. Never recompute against "current mid" mid-execution — that hides real shortfall.
- For buy/sell sign convention: `IS = (Arrival Price - Avg Execution Price) * Shares` for a buy (positive = cost / underperformance); invert for a sell.

## Category

`execution-algorithms`

## References

- Almgren, R. & Chriss, N. (1999). "Optimal Execution of Portfolio Transactions". Closed-form `sinh` trajectory and `E/V` cost formulas.
- Perold, A. (1988). "The Implementation Shortfall: Paper Versus Reality". Definition of IS as the benchmark for execution quality.
- BestEx Research, "Designing Optimal Implementation Shortfall Algorithms" — adaptive IS, adverse selection, A/B testing.
