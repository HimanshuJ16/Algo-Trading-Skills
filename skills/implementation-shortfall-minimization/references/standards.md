# Standards for Implementation Shortfall Minimization

## Engineering standards

| Metric | Engineering Standard |
|---|---|
| Benchmark Price | Implementation Shortfall MUST be benchmarked against the Decision Price ($P_0$) — the price when the PM decided, not when the algorithm started. If only an arrival price is available, the report MUST say so rather than substituting it silently. |
| Arrival Price Capture | The arrival price ($P_a$) MUST be the median top-of-book mid over the one-second window at parent-order submission, captured once and stored immutably with the order. It MUST NOT be recomputed from a later mid, and a crossed quote MUST be rejected rather than averaged in. |
| Cost Decomposition | Total IS MUST decompose into execution cost (on filled shares), opportunity cost (on unfilled shares) and explicit fees, and the components MUST sum to the reported total. |
| Delay / Impact Split | The executed leg MUST NOT be labelled "market impact" unless an arrival price was supplied and the split was actually computed. Without one, report the combined execution cost. |
| Sign Convention | Costs MUST be signed so positive is money lost, for buys and sells alike, so a buy filled above $P_0$ and a sell filled below it both read positive. |
| Measurement Unit | Total IS MUST be reported in both currency and basis points, with basis points quoted on the **intended** notional $Q \times P_0$, not the executed notional. |
| Horizon Price | The horizon used to mark unexecuted shares MUST be fixed by convention before measurement and recorded with the report; opportunity cost is linear in it. |
| Trajectory Sign | An Almgren-Chriss schedule MUST NOT contain a negative slice — a sell program never buys and a buy program never sells (Almgren & Chriss 2000, Sec. 3). |
| Horizon Independence | Lengthening the horizon at fixed urgency MUST NOT be presented as trading more patiently. The half-life $1/\kappa$ is independent of $T$ (ibid., Sec. 2.3); patience comes from lowering $\lambda$. |
| Forecast Comparison | A realised shortfall MUST be compared against the forecast $E(x)$ in units of $\sqrt{V(x)}$. $V$ is in currency squared; an alert written against $V$ compares incommensurable units. |
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
| Expected cost of a schedule | $E(x) = \frac{\gamma X^2}{2} + \epsilon\sum_j \lvert n_j \rvert + \frac{\tilde\eta}{\tau}\sum_j n_j^2$ | Eq. (8) |
| Variance of a schedule | $V(x) = \sigma^2\tau\sum_{j=1}^{N} x_j^2$ | Eq. (5) |
| Uniform (TWAP) limit | $E = \frac{\gamma X^2}{2} + \epsilon X + \tilde\eta\frac{X^2}{T}$; $V = \frac{1}{3}\sigma^2 X^2 T\left(1 - \frac{1}{N}\right)\left(1 - \frac{1}{2N}\right)$ | Eqs. (10), (11) |
| Immediate-dump limit | $E = \epsilon X + \eta X^2/\tau$, $V = 0$ | Eq. (13) |
| Closed form for the optimum | $E$, $V$ of the exact $\sinh$ trajectory | Eq. (20) |

`forecast_shortfall` evaluates Eqs. (5) and (8) as sums over the schedule rather than
Eq. (20), so it prices the **integer** schedule the algo will actually send and avoids the
catastrophic cancellation that makes Eq. (20) awkward at small or large $\kappa T$. The two
agree to floating-point precision on the exact trajectory, which this skill's tests assert
directly against all four closed forms above.

**Half-life independence.** $1/\kappa$ is set by the security's dynamics and the impact
parameters, not by the horizon (ibid., Sec. 2.3). Raising $T$ at fixed $\kappa$ leaves the
leading intervals unchanged: at $\kappa = 1$ the first interval holds $1 - e^{-1} = 63.2\%$
of the parent whether $T$ is 10 intervals or 10,000. The practical consequence is numerical
as well as economic — evaluating $\sinh(\kappa T)$ directly overflows float64 past
$\kappa T \approx 710$, and an implementation that guards by short-circuiting to "everything
in interval 0" produces the maximum-impact schedule precisely where the correct answer is
unchanged. Evaluate the ratio in exponential form instead.

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

## Benchmark reference — the arrival price

The **decision price** $P_0$ is the price when the PM decided; the **arrival price** $P_a$
is the price when the order reached the venue. Their difference *is* the delay cost, so the
two are not interchangeable — substituting $P_a$ for $P_0$ deletes the component a trading
desk can usually fix.

The industry convention for $P_a$ is the **median top-of-book mid-quote over the one-second
window at parent-order submission**, not a single tick. A single print is one draw from the
quote-flicker distribution, and one stale or crossed quote relocates the entire benchmark;
the median over ~1s of quotes is robust to both while remaining a decision-time measurement.
`median_mid_arrival_price` implements exactly that reduction, and rejects a crossed book
($\text{bid} > \text{ask}$) rather than averaging a feed artefact into the number the desk is
graded on. A locked market ($\text{bid} = \text{ask}$) has a well-defined mid and is accepted.

Capture it **once, at submission, and store it immutably** alongside the order. Recomputing
"current mid" partway through execution makes the benchmark chase the price the order is
itself moving, and the resulting report shows no shortfall regardless of how the execution
actually went.

Note that vendors differ on where the delay/opportunity boundary sits — some measure
opportunity cost from $P_a$ rather than $P_0$, which moves money between buckets while
leaving the total unchanged. Confirm the convention before comparing components across
systems.

## Documented limitation — impact is not causal

Decomposing realised cost into "market drift" and "market impact" is not identifiable from
post-trade data: the price path that would have obtained had the order not traded is
unobservable. Quantitative Brokers, "A Brief History of Implementation Shortfall"
(https://www.quantitativebrokers.com/blog/a-brief-history-of-implementation-shortfall)
makes the same point and notes that vendors commonly measure from arrival price rather
than decision time because decision time is not observable to the broker. Report the
impact term as attribution, never as an estimated impact coefficient.
