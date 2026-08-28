# Standards — synthetic-continuous-futures-contract-construction

## There is no mandated standard

No exchange, regulator or standards body defines how a continuous futures series must
be built. Roll dates and adjustment methods are **vendor conventions**, which is why
two providers' "ES continuous" series disagree on both the roll date and every
adjusted price before it. Everything below is either a published convention with a
citable source, or this library's default with its rationale stated.

## Sourced conventions

| Convention | What the source says | Source |
|---|---|---|
| **Back-adjustment direction** | "Looking backward in time, the new contract price minus the past contract price on roll-from day represents the delta price difference that is added to the past contract prices"; "the new contract prices remain unaffected by the back-adjustment splicing process." Worked example: December at 100, September at 95 on roll-backward day "would elevate all prices for the September contract by five." | [CSI Data — Back-Adjusted Charts](https://www.csidata.com/custserv/onlinehelp/OnlineManual/back_adjustedcharts.htm) |
| **Ratio adjustment exists and behaves differently** | Unfair Advantage will "proportionately adjust the history of a commodity by percentage or ratio terms" instead of by fixed dollar amounts. | [CSI Data — Back Adjusted Contracts](https://www.csidata.com/custserv/onlinehelp/OnlineManual/backadjustedoverview.htm) |
| **Additive series can go negative; ratio series generally cannot** | "Back- and forward-adjusted contracts can include negative numbers"; "Ratio adjusted series prepared through ratio multiplications are unlikely to go negative." | [CSI Data — Back Adjusted Contracts](https://www.csidata.com/custserv/onlinehelp/OnlineManual/backadjustedoverview.htm) |
| **Contract month codes** | The letter following the product root encodes the expiration month: F=January, G=February, H=March, J=April, K=May, M=June, N=July, Q=August, U=September, V=October, X=November, Z=December. | [CME Group — Contract Month Codes](https://www.cmegroup.com/month-codes.html); [CME Group — Understanding Contract Trading Codes](https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-contract-trading-codes) |

## Library defaults (calibrate before use)

| Parameter | Default | What it actually does |
|---|---|---|
| `roll_method` | `VOLUME_CROSSOVER` | Rolls when $V_{\text{next}} > V_{\text{front}}$ on a completed session. Strictly greater — equal volumes are not a crossover. |
| `adjustment_method` | `ADDITIVE_BACK_ADJUSTMENT` | $P_{\text{adj}}(j) = P_{\text{raw}} + \sum_{i>j} \text{gap}_i$, where segment $j$ is shifted by the gaps of every *later* roll. The newest segment is unshifted. |
| `PROPORTIONAL_RATIO` | — | $P_{\text{adj}}(j) = P_{\text{raw}} \times \prod_{i>j} \text{ratio}_i$, $\text{ratio}_i = P_{\text{next}}/P_{\text{front}}$ on the roll-from session. Requires strictly positive closes at every roll. |
| `days_before_expiry` | 5 **calendar** days | `DAYS_BEFORE_EXPIRY` threshold. Calendar, not business, days — a five-calendar-day window spanning a weekend is three trading sessions. Requires `contract_expiries`. |
| `min_confirmation_sessions` | 1 | Consecutive sessions the crossover must hold. 1 matches common vendor behaviour and rolls on the first crossover, including a spurious one. |
| Roll timing | Trigger on session *t*, effective session *t+1* | The trigger consumes *t*'s completed volume/open interest, so *t*'s own bar must still be priced off the front contract. Matches CSI's "roll-from day" as the last front-contract session. |
| Gap measurement | Both closes on the roll-from session | Taking the two prices from different sessions folds a day of market movement into the calendar spread. |

## Choosing the adjustment method

| Question being asked | Method | Why |
|---|---|---|
| Point-based signals, dollar P&L, stop distances in points | `ADDITIVE_BACK_ADJUSTMENT` | Preserves absolute price differences exactly. |
| Percentage returns, volatility, log prices, multi-decade series | `PROPORTIONAL_RATIO` | Preserves percentage changes; will not cross zero. |
| Auditing what the raw data actually looked like | `UNADJUSTED_CONCATENATED` | Leaves the discontinuities in, and reports the roll events that caused them. |

No method is correct on both absolute levels and returns simultaneously. Choose per
question and record the choice alongside the series.

## Known limitations

- **Two-digit year codes resolve into 2000-2099.** Pre-2000 history must supply
  `contract_expiries`; single-digit year codes are rejected as decade-ambiguous.
- **No exchange calendar.** `DAYS_BEFORE_EXPIRY` counts calendar days between session
  labels and the supplied expiration. Holiday and half-session handling is the
  caller's — see `global-exchange-holiday-calendar-handling`.
- **Front and second month only.** The engine walks one contract at a time and never
  skips an expiration, so it cannot build a "second nearby" or an N-month-deferred
  series.
- **Not point-in-time.** Each new roll restates the entire history. Store the roll
  schedule and adjustment method with any series you intend to reproduce — see
  `backtest-determinism-and-reproducibility`.
- **Volume and open interest are carried through unadjusted**, because they are
  quantities, not prices. Volume across a roll is therefore discontinuous by design.
