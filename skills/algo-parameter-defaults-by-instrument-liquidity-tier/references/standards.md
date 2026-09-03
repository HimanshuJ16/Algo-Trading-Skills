# Standards for Algo Parameter Defaults by Instrument Liquidity Tier

## Scope

Liquidity-tier defaults are calibration starting points. They are not universal market rules, regulatory limits, best-execution proof, or authorization to route an order. Independent market-access and risk controls must remain active.

| Tier | Illustrative default | Max participation | Profile crossing capability | Required live gate |
|---|---|---:|---|---|
| `HIGH` | TWAP | 5% | Crossing may be permitted by profile | Current spread/depth, volatility, order size, venue, and risk checks |
| `MEDIUM` | VWAP | 10% | Passive by default | Current spread/depth, volume curve, impact, venue, and risk checks |
| `LOW` | IS | 20% | Passive by default | Current spread/depth, urgency, price protection, venue, and risk checks |

These values are examples from the package calibration and must be validated with post-trade analysis. A high ADV observation does not establish that the current spread is tight, depth is executable, or a child order can cross safely.

## Reading the Default Calibration

Two properties of the shipped defaults run against the intuition that "tiers loosen as liquidity falls". Both are deliberate, and both are integration decisions rather than settled answers.

### The participation ceiling rises as liquidity falls — it is not a discount

`max_participation_rate` goes 5% → 10% → 20% as ADV drops. That is a **fill-feasibility allowance**: an order that is large relative to a thin name's daily volume cannot be worked at 5% participation inside a reasonable horizon, so the ceiling is raised to keep completion attainable. It is emphatically **not** a statement that participating harder in an illiquid name is cheaper.

The empirical impact literature says the opposite. The square-root law of market impact holds that a metaorder of size $Q$ worked in an instrument with average daily volume $V$ and daily volatility $\sigma$ moves the price by approximately

$$I(Q) \;\approx\; Y \, \sigma \sqrt{Q / V}, \qquad Y = O(1)$$

so impact depends on $Q/V$ — the footprint **as a fraction of ADV** — and scales linearly in volatility. Two consequences bear directly on this table:

- Raising the ceiling from 10% to 20% of ADV raises expected impact by roughly $\sqrt{2} \approx 1.41\times$ in volatility units. The tier boundary does not offset this; nothing in a low ADV number makes a 20% footprint cheaper.
- Low-ADV instruments generally carry **higher** $\sigma$, so the same $Q/V$ costs more in basis points there than in a deep name. The LOW tier is the most impact-sensitive row in the table, not the most forgiving.

Treat the LOW-tier figure as a ceiling you are permitted to approach only when completion risk demands it, and size against `liquidity-adjusted-position-sizing` and `strategy-capacity-estimation-before-scaling-capital` before doing so.

| Claim | Source | Status |
|---|---|---|
| $I(Q) \propto \sigma\sqrt{Q/V}$, with $Y$ of order 1 | Square-root law of market impact, restated across equities, futures, FX and options datasets ([Bouchaud, *The Square-Root Law of Market Impact*](https://bouchaud.substack.com/p/the-square-root-law-of-market-impact); [Tóth et al., option markets, arXiv:1602.03043](https://arxiv.org/abs/1602.03043)) | Widely replicated empirical regularity, not a regulatory or venue rule |
| The exponent is $1/2$ within statistical error at both stock and trader level, i.e. it does **not** vary with a stock's liquidity | [Complete survey of the Tokyo Stock Exchange, arXiv:2411.13965](https://arxiv.org/abs/2411.13965) | Verified against the paper's stated conclusion; the dataset covers liquid TSE names, so universality across genuinely illiquid instruments is an extrapolation |
| A directly estimated equity impact model fits a 3/5 power law on trade rate rather than 1/2 | Almgren, Thum, Hauptmann & Li, *Direct Estimation of Equity Market Impact*, **Risk** 18(7):58–62 (2005) ([preprint](https://www.cis.upenn.edu/~mkearns/finread/costestim.pdf)) | Reported from the paper's abstract and secondary summaries; the full text was **not** read for this note. Recorded to show the exponent is model- and dataset-dependent — do not treat $1/2$ as exact |

The practical reading: the *direction* (impact grows with participation, and grows faster in a volatile thin name) is robust; the exact exponent is not. Calibrate against your own TCA, per `transaction-cost-analysis-tca-integration`.

### The LOW tier pairs `IS` with `cross_spread_allowed=False`

An Implementation Shortfall schedule trades timing risk off against impact and front-loads as risk aversion rises (see `implementation-shortfall-minimization`). A schedule that may **never** take liquidity cannot front-load, so this pairing does not describe a complete IS algorithm — it describes an IS-shaped *starting posture* for a name whose spread is wide enough that crossing it is the expensive half of the trade-off.

The consequence is an integration obligation, not a bug: **an IS-configured tier with `cross_spread_allowed=False` must be given an explicit urgency-escalation policy outside this module** — who may cross, on what residual quantity, at what point in the horizon, and under whose authority. Without one, the parent order can sit passive and accumulate unbounded timing risk while every metric in this package still reads healthy. Conversely, the HIGH tier permits crossing precisely because a tight spread in a deep name makes taking liquidity cheap.

## Data Contract

- **ADV definition**: record whether ADV is shares/day, currency/day, contracts/day, or another unit.
- **Lookback and calendar**: record session calendar, lookback length, half-days, halted sessions, and missing observations.
- **Corporate actions**: use split-consistent volume and document whether the input is raw or adjusted.
- **Freshness**: record the ADV as-of timestamp and reject observations older than the configured maximum for the strategy. Freshness is enforced only when an age is supplied; set `require_adv_age=True` on the manager to make the age mandatory and turn an omitted timestamp into a rejection rather than an unchecked classification.
- **Zero ADV**: a zero observation is a data-quality signal (suspended, never traded, or a broken feed), not a genuine LOW-tier instrument. The manager logs a warning and still classifies, so the caller keeps a single rejection path; the integration must treat the warning as a routing stop.
- **Calibration**: version thresholds and profiles; persist the version with every parent-order decision.

## Profile Invariants

Enforced in `ExecutionProfile.__post_init__`, so they hold for any profile an integrator constructs directly, not only for profiles that pass through a manager:

- `0 < max_participation_rate <= 1`.
- `default_algo_type` is one of `TWAP`, `VWAP`, or `IS`.
- `cross_spread_allowed` and `requires_live_market_check` are strictly `bool`.
- `passive_buffer_bps >= 0`, `tier` is a `LiquidityTier`, `calibration_version` is non-empty, and all numeric values are finite.
- Profiles are immutable after construction (frozen dataclass).

Enforced by `ExecutionParameterManager`:

- `high_adv_threshold > medium_adv_threshold > 0`.
- Custom profiles must define all three tiers and their mapping keys must match `profile.tier`.
- The calibrated set is exposed through the read-only `manager.profiles` mapping proxy and copied from the caller's mapping at construction, so an approved calibration cannot be swapped in place or mutated through the mapping that was passed in.
- `requires_live_market_check=True` means `cross_spread_allowed` is not sufficient authorization to cross.

### `passive_buffer_bps` semantics

The field is a **passive placement tolerance**: how far *behind* the same-side touch a passive child order may rest, in basis points (1 bps = 0.01%) of the current same-side touch price, expressed as a non-negative magnitude. With a 5.0 bps buffer against a touch of 100.00, a buy may rest at or above 99.95 and a sell at or below 100.05. The sign is supplied by the order's side, never by the field, and `0.0` means "join the touch".

It is **not** a limit price, a price collar, a slippage budget, or a marketable offset. Those are independent risk controls (`sec-rule-15c3-5-risk-controls-us`) and a wider passive buffer must never be read as authorization to pay more.

## Regulatory Context

No regulator or venue reviewed here prescribes execution-algorithm participation caps or algorithm selection by liquidity tier. Two adjacent regimes are commonly confused with this skill's tiers and are not the same thing:

- **The EU liquid/illiquid determination is a transparency classification, not an execution parameter.** ESMA publishes an annual liquidity assessment per instrument under Articles 1 to 5 of Commission Delegated Regulation (EU) 2017/567, distributed through FITRS, and that determination drives MiFID II/MiFIR transparency obligations alongside large-in-scale, standard-market-size and tick-size regime inputs ([ESMA — annual transparency calculations for equity and equity-like instruments](https://www.esma.europa.eu/press-news/esma-news/esma-publishes-results-annual-transparency-calculations-equity-and-equity)). An instrument ESMA marks "liquid" may still be a `LOW` tier here, and the reverse. Never map one onto the other, and never cite this package's tier as evidence of a regulatory liquidity status. Note also that ESMA's average daily *turnover* input is a currency-per-day measure — feeding it into an ADV threshold calibrated in shares/day is exactly the unit mismatch this skill warns about.
- **Percentage-of-volume caps that *are* regulatory belong to issuer buy-back safe harbours**, are stated against average daily volume measured over prior sessions, and cap 25% of ADV under both SEC Rule 10b-18(b)(4) and Commission Delegated Regulation (EU) 2016/1052 Art. 3(3). Those figures, and the absence of any common institutional cap across broker APIs, are documented in `participation-of-volume-pov-execution/references/standards.md`; they are not repeated here. A tier's participation ceiling is not evidence of compliance with either.

For the controls that *are* mandatory around an algorithm — kill functionality, pre-trade order-entry limits, and real-time monitoring — see `mifid-ii-algo-trading-compliance-eu` (EU, MiFID II RTS 6) and `sec-rule-15c3-5-risk-controls-us` (US). Those obligations sit outside this module and a tier lookup neither satisfies nor relaxes them.

## Execution Controls

Before applying a profile, the EMS must independently evaluate:

- Current protected bid/offer, spread, depth, and quote freshness.
- Child quantity and notional relative to displayed/expected liquidity and parent limits.
- Volatility, price collars, venue trading status, auctions, halts, and rejects.
- Credit, position, rate, concentration, and kill-switch controls.
- Expected implementation shortfall, fill probability, and signaling/adverse-selection risk.

## Calibration and Monitoring

Retune thresholds and profile values through versioned walk-forward analysis and TCA. Monitor by tier and instrument:

- Implementation shortfall and arrival-price slippage.
- Participation, fill rate, reject/cancel rate, and residual quantity.
- Spread capture/crossing cost, volatility, and quote/depth conditions.
- Data age, ADV revisions, tier migrations, and risk-control overrides.

If a calibration underperforms or data quality degrades, roll back to the last approved version and pause affected instruments until reviewed.
