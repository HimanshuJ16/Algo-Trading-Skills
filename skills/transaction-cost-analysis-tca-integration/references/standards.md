# Backtesting Methodology Standards — transaction-cost-analysis-tca-integration

## Cost components

Sign convention: **positive is adverse** on both sides, with $d = +1$ for a buy and
$d = -1$ for a sell. All figures are basis points of $P_{\text{decision}}$.

| TCA Component | Calculation Formula | Notes |
|---|---|---|
| Delay Cost | $d \cdot \dfrac{P_{\text{arrival}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10^4$ | Signal-to-venue latency. High for latency-sensitive signals. |
| Half Spread Cross | $\dfrac{0.5 \cdot \text{Spread}}{P_{\text{decision}}} \times 10^4$ | Cost of *demanding* liquidity, not a fee. Charged unconditionally by the model, so it over-charges passive fills; a resting order that earns the spread is priced correctly only by the realized figure. |
| Market Impact | $\gamma \sqrt{\dfrac{\text{OrderSize}}{\text{ADV}}}$ | Concave in size everywhere, not only above 1% of ADV. See the calibration caveats below. |
| Commissions & Fees | Broker/exchange/regulatory rate in bps | Only the portion not already netted into the fill price. |
| Opportunity Cost | $d \cdot \dfrac{P_{\text{end}} - P_{\text{decision}}}{P_{\text{decision}}} \times 10^4$ on the unfilled remainder | Perold's fourth component. Reported as `None` when $P_{\text{end}}$ is unavailable — never as zero. |

## Realized vs estimated

| Quantity | Uses `p_fill`? | Purpose |
|---|---|---|
| `estimated_shortfall_bps` | No | Ex-ante model: delay + half-spread + impact + commission. What a backtest charges. |
| `realized_shortfall_bps` | Yes | Ex-post measurement against the decision price (Perold 1988). What execution actually cost. |
| `model_error_bps` | Yes | `realized - estimated`. Positive means the model under-predicts cost. The calibration signal. |

The two totals are **differenced, never summed** — realized shortfall already
subsumes delay, spread and impact as they actually occurred.

## Market impact model caveats

The canonical square-root law in the literature is

$$I = Y \cdot \sigma \cdot \sqrt{Q/V}$$

with $Q$ the metaorder size, $V$ average daily volume, $\sigma$ the **daily
volatility**, and $Y$ a dimensionless prefactor of order one. This skill's
$\gamma$ is a basis-point constant folding $Y\sigma$ into one number, which makes
it **instrument-specific and volatility-regime-specific**. Refit per liquidity
bucket and per regime; there is no transferable default.

The exponent is contested. Almgren, Thum, Hauptmann and Li (2005) reject $1/2$ for
temporary impact in favour of $3/5$ on Citigroup desk data; published fits span
roughly 0.4–0.7. The law also has a bounded validity range — impact crosses over
from linear to square-root as size grows, and empirical fits are typically quoted
for participation above $\sim 10^{-5}$ on metaorders small relative to ADV; 10%
participation is the conventional upper cut-off. Outside $[10^{-5}, 0.1]$ the engine flags
`participation_out_of_model_range` and logs; it does **not** clamp, because
clamping made a 100×-ADV order price identically to a 1×-ADV order.

## Sources

| Claim | Source | Verified |
|---|---|---|
| IS is measured from the decision price and includes opportunity cost on unexecuted shares | Perold, A. (1988), "The Implementation Shortfall: Paper Versus Reality", *Journal of Portfolio Management* 14(3), 4–9 | Yes — decomposition into delay, explicit, implicit and opportunity cost confirmed |
| Temporary impact exponent is 3/5, not 1/2; coefficients depend on volatility and ADV | Almgren, R., Thum, C., Hauptmann, E., Li, H. (2005), "Direct Estimation of Equity Market Impact", *Risk* 18(7) — https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf | Yes — 3/5 power law rejection of the square root confirmed |
| Canonical form $I = Y\sigma\sqrt{Q/V}$ with $\sigma$ daily volatility and $Y$ of order one | Tóth, B., Lempérière, Y., Deremble, C., de Lataillade, J., Kockelkoren, J., Bouchaud, J.-P. (2011), "Anomalous Price Impact and the Critical Nature of Liquidity in Financial Markets", *Physical Review X* 1, 021006 — https://link.aps.org/doi/10.1103/PhysRevX.1.021006 | Yes — volatility is an explicit factor in the canonical law |
| Square-root regime has a lower bound and a linear crossover at small size | Bucci, F., Benzaquen, M., Lillo, F., Bouchaud, J.-P. (2019), "Crossover from Linear to Square-Root Market Impact", *Physical Review Letters* 122, 108302 — https://arxiv.org/abs/1811.05230 | Yes — linear-to-square-root crossover in order volume confirmed |
| Impact scaling follows from microstructure invariance | Kyle, A., Obizhaeva, A. (2016), "Market Microstructure Invariance: Empirical Hypotheses", *Econometrica* 84(4), 1345–1404 | Yes — square-root law arises as a special case |

No regulatory requirement is asserted by this skill. Best-execution reporting
obligations differ by jurisdiction and change over time; consult
`best-execution-record-keeping-global` and `mifid-ii-algo-trading-compliance-eu`
rather than inferring one from these formulas.

## Category

`backtesting-methodology` — see top-level `mappings/` directory.
