# Standards & Sources for Order Book Microstructure Signal Research

## Nobody publishes an IC floor, a hit-ratio floor, or a sample-size gate

**No regulator, exchange, standards body or vendor publishes a threshold at which a
microstructure signal becomes "predictive alpha".** `MIN_IC_FOR_ALPHA = 0.05`,
`MIN_HIT_RATIO_PCT = 53.0` and `MIN_EFFECTIVE_OBSERVATIONS = 30` are this skill's
engineering choices. An earlier revision of this file presented them in a table headed
"Engineering Standard", which overstated them; they are defaults you should calibrate
against your own instrument, horizon and cost model, and nothing downstream should
quote them as authority.

The floors are also not comparable across horizons. Grinold's fundamental law,
$IR = IC \cdot \sqrt{BR}$, makes the same IC worth wildly different amounts depending on
how many *independent* decisions it is applied to. An IC of 0.05 on a monthly
cross-sectional factor and an IC of 0.05 on a 5-tick microstructure signal are not the
same finding, and the second is only meaningful if the breadth it implies survives the
overlap correction below.

## Order Flow Imbalance — the published definition

| Claim | Source | What it fixes here |
|---|---|---|
| $e_n = \mathbb{1}_{\{P^B_n \ge P^B_{n-1}\}} q^B_n - \mathbb{1}_{\{P^B_n \le P^B_{n-1}\}} q^B_{n-1} - \mathbb{1}_{\{P^A_n \le P^A_{n-1}\}} q^A_n + \mathbb{1}_{\{P^A_n \ge P^A_{n-1}\}} q^A_{n-1}$ | Cont, Kukanov & Stoikov, "The Price Impact of Order Book Events", *Journal of Financial Econometrics* 12(1), 2014, pp. 47–88, §2.1 ([arXiv:1011.6402](https://arxiv.org/abs/1011.6402), [OUP](https://academic.oup.com/jfec/article-abstract/12/1/47/816163)) | The canonical formula. v1.0.0 implemented four of its six unrolled branches. |
| "If $P^B$ increases, we let $e_n = q^B_n$, representing the size of a price-improving limit order. **If $P^B$ decreases, we let $e_n = q^B_{n-1}$, representing the size that was removed, whether due to a market order or a cancellation.** The same classification is done for events on the ask side, with signs reversed." | Cont, Kukanov & Stoikov (2014), §2.1 | Explicit authority for the two branches v1.0.0 zeroed. The paper states the magnitude; the sign comes from the formula, giving $-q^B_{n-1}$ on a falling bid and $+q^A_{n-1}$ on a rising ask. |
| $OFI_k = \sum_{n=N(t_{k-1})+1}^{N(t_k)} e_n$ | Cont, Kukanov & Stoikov (2014), §2.1 | **OFI is a sum over an interval.** The module's per-tick value is $e_n$, not $OFI_k$; `ofi_window_ticks > 1` produces the published variable. |
| "This variable treats a market sell and a cancel buy of the same size as equivalent, since they have the same effect on the size of the bid queue." | Cont, Kukanov & Stoikov (2014), §1.1 | Why OFI is preferred to trade-based imbalance: it is computable from Level 1 quote updates without a trade/quote matching step. |

## The 65% figure is contemporaneous, and the scope is narrow

| Claim | Source | Scope caveat |
|---|---|---|
| "We find that this aggregate variable explains mid-price changes over short time scales in a linear fashion, for a large sample of stocks, with an average $R^2$ of 65%." The estimated model is $\Delta P_k = \hat\alpha_i + \hat\beta_i OFI_k + \hat\epsilon_k$, over the **same** interval $[t_{k-1}, t_k]$. | Cont, Kukanov & Stoikov (2014), §1.1 and §3.2 eq. (4) | **Contemporaneous, not predictive.** This is a price-impact relation, not a forward-return relation, and it must never be cited in support of a forward IC. |
| Data: one calendar month (**April 2010**) of TAQ consolidated quotes and trades for **50 stocks randomly selected from the S&P 500**, on a uniform **10-second** time grid. Robustness checked from 10 quote updates up to 10 minutes. | Cont, Kukanov & Stoikov (2014), §3.1 | US large-cap equities, one month, 2010, decimalised one-cent tick. Do not present the result as universal across venues, asset classes, tick regimes or eras. |
| "The fit of our model generally increases with $\Delta t$." | Cont, Kukanov & Stoikov (2014), §3.1 | The headline $R^2$ is horizon-dependent. A per-event or per-tick study is at the low-$\Delta t$ end where the fit is weakest. |
| Adding a quadratic term raises $R^2$ from 65% to 68% on average and the coefficient "is statistically insignificant in most samples". Residuals are heteroscedastic; White standard errors were used. | Cont, Kukanov & Stoikov (2014), §3.2 | Linearity is well supported at that horizon, so Pearson correlation is an appropriate IC estimator here rather than a rank correlation. |
| $\beta_i = c / AD_i^{\lambda} + \nu_i$ — the price impact coefficient is inversely related to market depth. | Cont, Kukanov & Stoikov (2014), §2.3 eq. (3) | An IC measured on one liquidity regime does not transfer to another. Depth is the scaling variable, and it has strong intraday seasonality. |

## Weighted mid-price vs. micro-price

These are two different objects, and this module computes the first.

| Quantity | Definition | Source |
|---|---|---|
| Volume imbalance $I$ (this module's `voi`) | $I = \dfrac{q^b - q^a}{q^b + q^a}$ | Pulido, Rosenbaum & Sfendourakis, "Understanding the worst-kept secret of high-frequency trading", [arXiv:2307.15599](https://arxiv.org/abs/2307.15599), §1 |
| Weighted mid-price $P^w$ (this module's `micro_price`) | $P^w := \frac{I+1}{2}P^a + \frac{1-I}{2}P^b$, algebraically identical to $\frac{q^b P^a + q^a P^b}{q^b + q^a}$ | Pulido, Rosenbaum & Sfendourakis (2024), §1 |
| Micro-price (**not** computed here) | A martingale limit of a sequence of expected mid-prices, expressed as $P^{micro} = M + g(I, S)$ in the mid-price, imbalance and spread. | Stoikov, "The Micro-Price: A High Frequency Estimator of Future Prices", 2018 ([SSRN 2970694](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694)) |

> "Stoikov (2018) introduces the more sophisticated notion of 'micro-price' ... The
> micro-price provides a nice measure of the efficient price because it is a martingale
> and it is generally less noisy than the weighted mid-price."
> — Pulido, Rosenbaum & Sfendourakis (2024), §1

The field is named `micro_price` for backward compatibility. That name overstates what
it holds: the weighted mid is the non-martingale approximation the micro-price was
constructed to improve on.

### The deviation is not independent information

Substituting $P^w = \frac{q^b P^a + q^a P^b}{q^b + q^a}$ and $M = \frac{P^b + P^a}{2}$:

$$P^w - M = \frac{q^b - q^a}{2(q^b + q^a)}(P^a - P^b) = \frac{I}{2}\,S$$

exactly, for any book with positive total depth. `micro_price_dev` is therefore `voi`
rescaled by the spread, and where the spread is constant — most large-tick instruments,
nearly always — `ic_micro_price_dev_return` and `ic_voi_forward_return` are the same
number. The module raises `CONSTANT_SPREAD_COLLINEARITY` and reports both ICs so the
duplication is visible rather than left to be inferred.

## Overlapping forward returns

A $k$-tick forward return sampled at every tick shares $k-1$ ticks with its neighbour.

| Area | What the literature says | Source |
|---|---|---|
| Overlapping returns mechanically accumulate autocorrelation and can materially overstate the strength of a measured signal. | Long-horizon predictability literature; see the overview in Boudoukh, Israel & Richardson, "Long-Horizon Predictability: A Cautionary Tale", *Financial Analysts Journal* 75(1), 2019 ([Taylor & Francis](https://www.tandfonline.com/doi/full/10.1080/0015198X.2018.1547056)) | Why a naive t-statistic over N overlapping observations is not usable. |
| Newey-West and Hansen-Hodrick HAC standard errors are the standard remedy, but both are biased downwards when the horizon is long relative to the sample. | Hansen & Hodrick (1980); Newey & West (1987); see also "Improved Inference and Estimation in Regression With Overlapping Observations" ([Warwick WBS](https://warwick.ac.uk/fac/soc/wbs/subjects/finance/faculty1/anthony_neuberger/improved.pdf)) | Why this module does **not** claim to implement HAC inference. |
| $IR = IC \cdot \sqrt{BR}$, where breadth is the number of **independent** decisions. | Grinold (1989), "The Fundamental Law of Active Management", *Journal of Portfolio Management* 15(3) | Overlapping observations are not independent decisions, so they cannot be counted as breadth. |

**What this module does instead.** It reports the t-statistic on the non-overlapping
effective sample, $n_{\text{eff}} = \lfloor N/k \rfloor$, via
$t = IC\sqrt{(n_{\text{eff}} - 2)/(1 - IC^2)}$. This *discards* the information in the
overlapping samples rather than modelling their autocorrelation. It is deliberately
conservative and is not a HAC estimator; where the overlapping information matters,
Newey-West or Hansen-Hodrick standard errors are the right tool, with the caveat above.

## This skill's engineering rules

Everything below is a choice made by this skill. **None of it is published by a
regulator, an exchange, or a standards body.**

| Rule | Requirement | Why |
|---|---|---|
| $e_n$ completeness | All six unrolled branches MUST be implemented, including $-q^B_{n-1}$ on a falling bid and $+q^A_{n-1}$ on a rising ask. | Those are the queue-depletion events. Zeroing them silences the most informative ticks while leaving the common case correct, so the output still looks plausible. |
| Undefined first event | $e_0$ MUST be excluded from the research sample. | It has no predecessor; emitting 0.0 and counting it inserts a fabricated observation. |
| Window warm-up | Partial rolling windows MUST be excluded. | A sum over 2 events is a different random variable from a sum over 5; mixing them into one sample changes what is being estimated. |
| Feature precision | Features MUST NOT be rounded. | OFI rounded to 2 dp is identically zero in fractional units; a mid rounded to 4 dp is identically constant on a 5-decimal FX cross. |
| Return endpoints | Both endpoints of a forward return MUST be at full precision. | Rounding one endpoint and not the other biases every return in the same direction. |
| Hit-ratio sample | Zero signals and zero forward returns MUST be excluded from both numerator and denominator, and counted separately. | v1.0.0 scored the zero/zero pair as a hit, so a static book reported a perfect hit ratio. |
| Overlap discount | The reported t-statistic MUST use $\lfloor N/k \rfloor$, never $N$. | The naive statistic is inflated by roughly $\sqrt{k}$. |
| Approval gate | An approval MUST require $n_{\text{eff}} \ge$ `MIN_EFFECTIVE_OBSERVATIONS`; a shortfall MUST report `INSUFFICIENT_SAMPLES`, not `WEAK_SIGNAL`. | "Not enough data to say" and "measured and found weak" are different findings and must not collapse into one. |
| IC sign | The IC floor MUST be signed, not absolute, and a materially negative IC MUST raise `IC_SIGN_INVERTED`. | The published model predicts a positive coefficient. A large negative IC is evidence of a side-mapping, sign or alignment error before it is evidence of a contrarian edge. |
| Non-finite input | NaN/Inf MUST be rejected, not filtered. | NaN compares `False` against every threshold, so a corrupted capture reports a clean `WEAK_SIGNAL`. |
| Crossed books | A crossed top of book MUST reject the series. | It flips the sign of `micro_price_dev` relative to `voi` on exactly those ticks, silently inverting the signal. |
| Non-positive prices | A non-positive price MUST reject the series. | Simple returns are undefined through zero and sign-flip through a negative denominator. |
| Ordering and identity | Out-of-order timestamps and mixed symbols MUST reject the series. | $e_n$ is defined by differencing consecutive observations of *one* book. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `forward_horizon_ticks` | `5` | Placeholder. Must be at least as long as your tick-to-trade path or the signal is unreachable. |
| `ofi_window_ticks` | `1` | Tests the per-event $e_n$. Set above 1 for the published interval-summed $OFI_k$. |
| `MIN_IC_FOR_ALPHA` | `0.05` | This skill's floor. Not published by anyone; not comparable across horizons. |
| `MIN_HIT_RATIO_PCT` | `53.0` | This skill's floor. Meaningless without reading `directional_predictions` alongside it. |
| `MIN_EFFECTIVE_OBSERVATIONS` | `30` | This skill's gate on non-overlapping observations. A floor on noise, not a guarantee of power. |

## Scope boundary

This module reads no feed and places no orders. It audits a captured top-of-book series,
and every guarantee it offers concerns arithmetic over those samples. A positive IC is a
statistical statement about mid-price movement, not a claim that the move is capturable
after spread, fees, queue position and latency. It is not a compliance artifact, asserts
no regulatory requirement, and its thresholds carry no authority beyond the operator who
sets them.

## Category

`real-time-architecture` / research — see top-level `mappings/` directory.
