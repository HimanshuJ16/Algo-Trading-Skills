# Standards for Capital Allocation

There is no regulator-issued or industry-body standard governing Kelly sizing across
trading strategies. The thresholds below are **team-chosen engineering defaults**, not
external requirements — adopt or override them deliberately. Only the Kelly formula
itself is a cited result.

## Cited results

| Result | Statement | Source |
|---|---|---|
| Binary-bet Kelly | $f^* = (bp - q)/b = p - q/b$, where $p$ is the win probability, $q = 1-p$, and $b$ the net reward/risk ratio. | Thorp, E. O. (2007), *The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market* — [PDF](https://web.williams.edu/Mathematics/sjmiller/public_html/341/handouts/Thorpe_KellyCriterion2007.pdf) |
| Fractional Kelly growth | Under the standard quadratic (Gaussian) growth approximation, betting $c\times$ Kelly retains $2c - c^2$ of the optimal growth rate: $c=0.5 \to 75\%$, $c=0.25 \to 43.75\%$, $c=2 \to 0\%$, and growth is negative beyond $c=2$. | Direct consequence of $g(f) = f\mu - f^2\sigma^2/2$ maximised at $f^*=\mu/\sigma^2$. |
| Multi-asset Kelly | For simultaneous correlated bets the optimal vector is $f^* = \Sigma^{-1}\mu$, not the vector of independent per-asset Kelly fractions. | Standard log-utility portfolio result; see Thorp (2007), "The theory for a portfolio of securities". |

## Engineering defaults (conventions, not standards)

| Parameter | Default | Rationale |
|---|---|---|
| Reallocation frequency | Weekly or monthly. | Shorter intervals reallocate on sampling noise rather than edge. Intraday reallocation on live PnL is performance chasing. |
| Kelly multiplier | Max $0.5$ (Half-Kelly); $0.25$ recommended for volatile crypto/equity strategies. | Caps the growth penalty at ~25% while roughly halving exposure and drawdown. The engine hard-rejects values above $1.0$. |
| Capacity ceiling | Mandatory per strategy, sourced from liquidity analysis. | The one constraint that must never be inferred from performance — a high Kelly weight is not evidence a strategy can absorb more capital. |
| Minimum trade sample | Enough closed trades that the win-rate standard error is small relative to the edge. $\mathrm{SE}(W) = \sqrt{W(1-W)/n}$; at $W=0.6$, $n=100$ gives $\approx 4.9$pp. | Kelly amplifies win-rate estimation error directly into position size. |
| Allocation floor | **None.** | A minimum-allocation floor forces capital into a strategy the model has judged to have no edge. Hold the remainder as cash instead. |
| Hurdle rate | Optional; if used, define it as an explicit gate applied *before* sizing. | A Sharpe or edge hurdle is a governance choice, not a property of the Kelly rule. The engine implements no hurdle — a strategy with positive Kelly edge receives capital. |

## Invariants the engine guarantees

1. $\sum_s \text{target}_s \le \text{total fund capital}$ — the engine never levers.
2. $\text{target}_s \le \min(\text{max\_capacity}_s,\ \text{fund} \times k_s \times \text{kelly\_fraction})$ — no strategy is funded above its capacity or above its own fractional Kelly target.
3. $\text{target}_s \ge 0$ — strategies are never shorted.
4. Output depends only on the inputs, never on dictionary iteration order.
5. Halving `kelly_fraction` halves every target, unless a capacity ceiling binds.
