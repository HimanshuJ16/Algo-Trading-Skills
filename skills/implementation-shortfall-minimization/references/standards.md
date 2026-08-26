# Standards for Implementation Shortfall Minimization

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Benchmark Price | Implementation Shortfall MUST be benchmarked against the Decision Price ($P_0$) — the price when the PM decided, not when the algorithm started. If only an arrival price is available, the report MUST say so rather than substituting it silently. |
| Cost Decomposition | Total IS MUST decompose into execution cost (on filled shares), opportunity cost (on unfilled shares) and explicit fees, and the components MUST sum to the reported total. |
| Delay / Impact Split | The executed leg MUST NOT be labelled "market impact" unless an arrival price was supplied and the split was actually computed. Without one, report the combined execution cost. |
| Sign Convention | Costs MUST be signed so positive is money lost, for buys and sells alike, so a buy filled above $P_0$ and a sell filled below it both read positive. |
| Measurement Unit | Total IS MUST be reported in both currency and basis points, with basis points quoted on the **intended** notional $Q \times P_0$, not the executed notional. |
| Horizon Price | The horizon used to mark unexecuted shares MUST be fixed by convention before measurement and recorded with the report; opportunity cost is linear in it. |
| Trajectory Sign | An Almgren-Chriss schedule MUST NOT contain a negative slice — a sell program never buys and a buy program never sells (Almgren & Chriss 2000, Sec. 3). |
| Input Integrity | Non-finite or non-positive prices, non-positive quantities, non-finite fees, duplicate fill identifiers and executed quantity exceeding the parent quantity MUST raise, never silently produce a NaN or clamped shortfall. |

## Model reference — Almgren-Chriss (2000)

Almgren, R. and Chriss, N. (2000). "Optimal Execution of Portfolio Transactions."
*Journal of Risk* 3(2), 5–39. https://www.smallake.kr/wp-content/uploads/2016/03/optliq.pdf

Discrete formulation over $N$ intervals of length $\tau$, $T = N\tau$:

| Quantity | Definition | Source |
|---|---|---|
| Difference equation | $\tau^{-2}(x_{j-1} - 2x_j + x_{j+1}) = \tilde\kappa^2 x_j$ | Eq. (16) |
| Adjusted temporary impact | $\tilde\eta = \eta\left(1 - \frac{\gamma\tau}{2\eta}\right) = \eta - \frac{\gamma\tau}{2}$ | following Eq. (16) |
| Decay parameter | $\tilde\kappa^2 = \lambda\sigma^2/\tilde\eta$ | following Eq. (16) |
| Exact discrete root | $2\tau^{-2}\left(\cosh(\kappa\tau) - 1\right) = \tilde\kappa^2$, i.e. $\kappa = \tau^{-1}\operatorname{arccosh}\!\left(1 + \tilde\kappa^2\tau^2/2\right)$ | following Eq. (16) |
| Holdings trajectory | $x_j = \dfrac{\sinh\left(\kappa(T - t_j)\right)}{\sinh(\kappa T)}\,X$ | Eq. (17) |
| Trade list | $n_j = 2\,\dfrac{\sinh(\tfrac{1}{2}\kappa\tau)}{\sinh(\kappa T)}\cosh\!\left(\kappa(T - t_{j-\frac{1}{2}})\right)X$, equivalently $x_{j-1} - x_j$ | Eq. (18) |
| Small-$\tau$ approximation | $\kappa \sim \tilde\kappa \sim \sqrt{\lambda\sigma^2/\eta} + O(\tau)$ | Eq. (19) |

Eq. (19) is an **approximation valid as $\tau \to 0$**; the exact root is used here so the
schedule is optimal on the interval grid actually traded. Sec. 3 states $n_j > 0$ for all
$j$ whenever $X > 0$, which is why a negative slice indicates an implementation defect
rather than an aggressive parameterisation.

Units: $\lambda$ is risk aversion (1/currency), $\sigma$ volatility per $\sqrt{\text{time}}$
in price units, $\eta$ and $\gamma$ are temporary and permanent impact coefficients (price
per unit trading rate), so $\lambda\sigma^2/\tilde\eta$ carries units of $1/\text{time}^2$
and $\kappa$ of $1/\text{time}$.

## Model reference — Perold (1988)

Perold, A. F. (1988). "The Implementation Shortfall: Paper vs. Reality."
*Journal of Portfolio Management* 14(3), 4–9. doi:10.3905/jpm.1988.409150

IS is the return difference between the paper portfolio (the whole order filled instantly
at $P_0$) and the implemented portfolio. For a buy of $Q$ shares of which $Q_f$ filled at
quantity-weighted average $P_e$, marked at horizon price $P_{\text{end}}$:

$$\text{IS} = Q(P_{\text{end}} - P_0) - \left[Q_f(P_{\text{end}} - P_e) - \text{fees}\right]
            = \underbrace{Q_f(P_e - P_0)}_{\text{execution}} + \underbrace{(Q - Q_f)(P_{\text{end}} - P_0)}_{\text{opportunity}} + \text{fees}$$

The expanded four-component form (delay, trading/impact, opportunity, explicit) follows
Perold (1988) together with Wagner, W. H. and Edwards, M. (1993), "Best Execution,"
*Financial Analysts Journal* 49(1), 65–71, which splits the delay-related cost out of the
executed leg at the arrival price.

## Documented limitation — impact is not causal

Decomposing realised cost into "market drift" and "market impact" is not identifiable from
post-trade data: the price path that would have obtained had the order not traded is
unobservable. Quantitative Brokers, "A Brief History of Implementation Shortfall"
(https://www.quantitativebrokers.com/blog/a-brief-history-of-implementation-shortfall)
makes the same point and notes that vendors commonly measure from arrival price rather
than decision time because decision time is not observable to the broker. Report the
impact term as attribution, never as an estimated impact coefficient.
