# Standards — participation-of-volume-pov-execution

## The participation identity

| Quantity | Definition |
|---|---|
| $V_{\text{away}}$ | Volume traded by every participant **except** this parent order. |
| $R$ | Participation rate, as a fraction of **total** volume — own prints included. |
| Cumulative target | $Q_{\text{target}} = \left\lfloor \frac{R}{1-R} \times V_{\text{away, cum}} \right\rfloor$ |
| Realized rate | $\text{RealizedRate} = \frac{Q_{\text{filled}}}{V_{\text{away, cum}} + Q_{\text{filled}}}$, computed from **fills**, never from quantity sent. |

The $\frac{R}{1-R}$ factor exists because your own executions print to the tape and
therefore enter the denominator you are measured against. Solving
$R = Q_{\text{own}} / (V_{\text{away}} + Q_{\text{own}})$ for $Q_{\text{own}}$ gives the
identity above; at $R = 0.15$ you must trade ~17.65% of away volume to *be* 15% of
total volume, and at $R = 1/3$ you must match away volume at 50%.

Both formulas are undefined at $R = 1$ and meaningless at $R \le 0$, which is why the
parent order rejects a rate outside $(0, 1)$ rather than clamping it.

## Protocol convention — FIX

| Field | Status | Bearing on this skill |
|---|---|---|
| `TargetStrategy(847) = 2` — *"Participate (aim to be x percent of the market volume)"* | Added FIX 4.4, current | The canonical statement that the rate is a percentage of **market** volume, not of away volume. Source of the denominator convention used throughout this skill. ([FIXimate tag 847](https://fiximate.fixtrading.org/en/FIX.Latest/tag847.html)) |
| `ParticipationRate(849)`, datatype Percentage — *"For a TargetStrategy=Participate order specifies the target participation rate. For other order types this is a volume limit (i.e. do not be more than this percent of the market volume)"* | Added FIX 4.4, **deprecated in FIX 5.0** | Carries the rate on the wire in FIX 4.4 venues, including NYSE Pillar. From FIX 5.0 the `StrategyParametersGrp` repeating group is used instead. Do not build a new FIX 5.0 integration on tag 849. ([FIXimate tag 849](https://fiximate.fixtrading.org/en/FIX.Latest/tag849.html), [OnixS FIX 4.4 dictionary](https://www.onixs.biz/fix-dictionary/4.4/tagnum_849.html)) |

Note the second sentence of the 849 definition: outside a Participate order the same
field means a **volume limit**, not a target. The two readings size orders differently.
Confirm which one a counterparty implements before relying on it.

## Broker parameter ranges — no common cap exists

| Venue / API | Parameter | Documented range | Source |
|---|---|---|---|
| Interactive Brokers, TWS API `PctVol` strategy | `pctVol` | **0.1 (10%) – 0.5 (50%)** | [TWS API — IB Algorithms](https://interactivebrokers.github.io/tws-api/ibalgos.html) |
| Binance Futures Algo, `POST /sapi/v1/algo/futures/newOrderVp` | `urgency` | Enum **LOW / MEDIUM / HIGH** — no numeric rate is exposed at all. Notional must exceed 10,000 USDT and stay below 1,000,000 USDT; maximum 10 open algo orders. | [Binance Open Platform — Futures Algo](https://developers.binance.com/docs/algo/future-algo) |
| FIX `ParticipationRate(849)` | Percentage | Up to 99.99% is representable | As above |

**The commonly quoted "30% maximum participation" figure is not a standard.** It is a
common institutional risk-policy default. No regulator, venue
or protocol reviewed here imposes it: IBKR documents a 50% ceiling, FIX permits values
approaching 100%, and Binance does not expose a rate. Set `max_rate` from your own
market-impact analysis and record who signed it off. Do not present it as a rule.

Note also that Binance's VP endpoint returns `success: true` on acceptance, and its
documentation states plainly that this "does not guarantee execution" — query the order
endpoints for final status. That is the protocol-level statement of why this engine
separates sent quantity from filled quantity.

## Regulatory touchpoints

Jurisdiction is stated per row. **None of these prescribe a participation rate for
ordinary agency or proprietary execution.** They govern the controls around the
algorithm, or they cap a specific corporate-action context.

### Where a percentage-of-volume cap *is* regulatory: buy-back safe harbours

Both are conditions of a **safe harbour for issuer share repurchases**, not general
execution rules. Both are stated against **average daily volume measured over prior
sessions**, which a live participation rate does not measure.

| Jurisdiction | Instrument | Condition |
|---|---|---|
| **US** | SEC Rule 10b-18, 17 CFR 240.10b-18 **(b)(4)** — *Volume of purchases* | *"The total volume of Rule 10b-18 purchases effected by or for the issuer and any affiliated purchasers effected on any single day must not exceed 25 percent of the ADTV for that security"*, with a once-weekly block-purchase alternative if no other Rule 10b-18 purchases are effected that day and the block is excluded from the four-week ADTV. ([17 CFR 240.10b-18](https://www.law.cornell.edu/cfr/text/17/240.10b-18)) |
| **EU** | Commission Delegated Regulation (EU) 2016/1052, Art. 3(3), supplementing MAR (Reg. (EU) 596/2014) Art. 5 | Issuers shall not purchase on any trading day more than **25% of the average daily volume** of the shares on the trading venue where the purchase is carried out. ADV is taken from either the month preceding the disclosure under Art. 2(1) (fixed for the programme's duration) or the **20 trading days** preceding the purchase. ([EUR-Lex CELEX:32016R1052](https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R1052)) |

Practical consequence: a POV algorithm targeting 20% of *today's* volume can breach a
25%-of-prior-ADV limit outright on a quiet day. If a repurchase programme is in scope,
the binding constraint is a daily share budget derived from ADV, computed outside this
engine and passed in as `total_qty`. Confirm applicability with counsel.

### EU — MiFID II RTS 6 (Commission Delegated Regulation (EU) 2017/589)

Mandatory for investment firms engaged in algorithmic trading in the EU. It constrains
the controls, testing and records around a POV algorithm, not its rate.
([EUR-Lex](https://eur-lex.europa.eu/eli/reg_del/2017/589/oj/eng))

| Article | Title | Bearing on this skill |
|---|---|---|
| Art. 12 | Kill functionality | Outstanding child orders of a working parent must be cancellable as a unit and immediately — see `execution-algorithm-kill-switch-integration`. |
| Art. 15 | Pre-trade controls on order entry | Maximum order value and volume limits sit **outside** this engine. `max_slice_qty` is a scheduling bound, not a risk control. |
| Art. 16 | Real-time monitoring | A working parent order must be monitored by the responsible trader and by an independent risk function. A POV order that has stalled at zero fills for hours is exactly what real-time monitoring is meant to surface. |

See `mifid-ii-algo-trading-compliance-eu` for the full obligation set.

### US — SEC Rule 15c3-5 (17 CFR 240.15c3-5)

Binds the **broker-dealer providing market access**, not a buy-side firm running its own
algorithm. Its pre-trade controls filter your child orders regardless of what this engine
schedules; a slice that breaches them produces a rejection, which must be routed to
`record_unfilled` so the quantity returns to the schedule rather than being lost. See
`sec-rule-15c3-5-risk-controls-us`.

## Benchmarks this engine does not compute

**PWP (Participation-Weighted Price)** is a post-trade TCA benchmark, not a scheduling
input: PWP-*X*% is the volume-weighted average price of the first $Q/X$ shares that trade
from order arrival. Measuring against it requires the full trade tape from arrival, which
this engine does not retain — it consumes interval volume and a last price. Earlier
revisions of this skill listed "PWP monitoring" as a workflow step; no such calculation
existed. Use `transaction-cost-analysis-tca-integration` for benchmark measurement, and
`implementation-shortfall-minimization` for the cost of the shares a POV order leaves
unfilled.
