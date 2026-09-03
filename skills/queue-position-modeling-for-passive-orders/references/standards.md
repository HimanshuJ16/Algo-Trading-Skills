# Standards for Queue Position Modeling for Passive Orders

Queue position is not a published field. No exchange disseminates it and no
regulator defines it — it is an *estimate* reconstructed from a level snapshot
plus the message traffic that follows. Everything below is therefore sourced
either to an exchange rule that constrains what the estimate can mean, to the
research literature, or to this repository's own engineering conventions, and is
labelled accordingly. Nothing here imposes a compliance obligation.

## Precondition: the matching algorithm must be price-time

The model's core claim — *volume resting ahead of me executes before I do* — is
a property of the venue's allocation algorithm, not a law of order books. It
must be verified per product before the model is used at all.

| Algorithm | Behaviour | Queue model valid? |
|---|---|---|
| FIFO / price-time | "distributes quantity to resting orders on a first in, first out basis with earlier timestamped orders receiving fills before later timestamped orders" | Yes |
| Pro-Rata | "quantity to be matched is multiplied by each resting order's pro-rated percentage"; the percentage is "order quantity divided by total quantity at a certain price" | **No** — allocation is by size, not arrival time |
| Split FIFO/Pro-Rata (Configurable K) | Hybrid; "currently set at 40% FIFO and is used for grain and oilseed futures and spreads and livestock spreads" | Partially, for the FIFO fraction only |
| Allocation / Threshold Pro-Rata | Enhanced pro-rata with a TOP priority step | **No** |

Source: CME Group Client Systems Wiki, *Supported Matching Algorithms* and
*CME Globex Matching Algorithms*
(<https://www.cmegroup.com/confluence/display/EPICSANDBOX/Supported+Matching+Algorithms>).
Product-level algorithm assignment is published by CME per product group and
changes over time; check it rather than assuming.

### TOP priority breaks volume-ahead even on a partly-FIFO book

CME grants priority to "a TOP order [which] has a price that betters the market
at the time the order is received". Only one buy and one sell order can hold TOP
at a time, and TOP orders "match first regardless of size". A TOP order that
arrives *after* ours executes *before* ours, so it adds to effective volume ahead
without ever appearing in the level's fill/cancel tally. Two parameters govern
it: `TOP Min` (minimum displayed quantity to qualify — 1 in most CME markets,
10 for grain options) and `TOP Percentage` (the fraction of aggressing quantity
routed to the TOP order). Same source.

## Definitions used by this skill

| Quantity | Definition | Notes |
|---|---|---|
| $Q_{\text{ahead}}$ | Volume resting at our limit price with strictly better time priority | Estimated, never observed directly from L2 |
| $Q_{\text{total}}$ | Total volume at the price level at order entry, **including** our own $q_{\text{our}}$ | Constraint: $Q_{\text{total}} \ge Q_{\text{ahead}} + q_{\text{our}}$ |
| Other resting volume | $Q_{\text{total}} - q_{\text{our}}$ | The only volume a third-party cancellation can come from |
| Queue rank | $\lceil Q_{\text{ahead}} / \bar{S}_{\text{order}} \rceil + 1$ | Ceiling, not floor: a part-consumed order ahead still blocks us |
| $P_{\text{full}}$ | $\Pr(N \ge \lceil (Q_{\text{ahead}} + q_{\text{our}}) / \bar{S}_{\text{trade}} \rceil)$, $N \sim \text{Poisson}(\mu)$ | Complete fill within the horizon |
| $P_{\text{partial}}$ | $\Pr(N \ge \lfloor Q_{\text{ahead}} / \bar{S}_{\text{trade}} \rfloor + 1)$ | At least one share; always $\ge P_{\text{full}}$ |
| $\mu$ | $\lambda \Delta t / \bar{S}_{\text{trade}}$ | Expected trade count over the horizon |

## Model rules enforced by the engine

| Rule | Statement | Status |
|---|---|---|
| Execution subtraction | Volume executed at our limit price on **our venue** is subtracted from $Q_{\text{ahead}}$ in full. | Exact under price-time priority. |
| Cancellation allocation | $V_{\text{cancel}}^{\text{ahead}} = \min\big(Q_{\text{ahead}},\, V_{\text{cancel}} \cdot \frac{Q_{\text{ahead}}}{Q_{\text{total}} - q_{\text{our}}} \cdot \alpha\big)$ | **Modelling assumption**, not a standard. See below. |
| Denominator excludes our own order | A cancellation originates from another participant's resting volume; including $q_{\text{our}}$ dilutes the share. | Definitional. |
| Non-negativity | $Q_{\text{ahead}}$ is clamped at zero, and credited cancellations can never exceed the volume actually ahead. | Engineering rule. |
| Front-of-queue | $Q_{\text{ahead}} \le$ `front_of_queue_tolerance` (default $10^{-9}$). Front-of-queue means the *next* execution at this price reaches us — it is not a guarantee of a fill. | Engineering rule. |
| Fail-closed input | Every numeric input is checked for type, finiteness and sign before any arithmetic; a bad value raises rather than being clamped. | Engineering rule. |

### Why the cancellation rule is an assumption, and why $\alpha < 1$

A uniform-cancellation model says a cancellation is equally likely to come from
any unit of resting volume, so the ahead-share is
$Q_{\text{ahead}} / (Q_{\text{total}} - q_{\text{our}})$. It is the standard
tractable choice — the queue-position literature derives its fluid and diffusion
limits under exactly this assumption, with an order's position shrinking
proportionally when the queue is cancelled into (Guo, Ruan & Zhu, *Dynamics of
Order Positions and Related Queues in a Limit Order Book*, arXiv:1505.04810;
Moallemi & Yuan, *A Model for Queue Position Valuation in a Limit Order Book*,
SSRN 2996221).

It is also known to be optimistic. Queue position is itself a strong empirical
determinant of cancellation, and limit orders later in the queue are cancelled
more frequently, because they face higher expected waiting costs (Dahlström &
Nordén, *The determinants of limit order cancellations*, **Financial Review**,
2024, DOI 10.1111/fire.12363). Uniform allocation therefore credits *too many*
cancellations to the volume ahead of us and flatters our priority.

`cancellation_share_alpha` is the haircut that corrects for this direction.
$\alpha = 1$ reproduces the pure uniform model; $\alpha = 0.5$ is the module
default and is an **uncalibrated pessimistic prior**, not a measured value. No
source establishes a transferable figure. Calibrate it per venue and instrument
against reconstructed L3 data
(`historical-order-book-reconstruction-from-message-logs`).

### Known approximations in the cancellation term

The ahead-share denominator is the entry-time snapshot. It ignores both the
depletion of the level by the fills already credited (which would *raise* the
true ahead-share) and any new volume joining behind us (which would *lower* it).
The sign of the resulting error is therefore not fixed, and the term is applied
once to a cumulative cancellation total rather than incrementally per event.
This is a stated simplification of the model, not a property of order books.

## Fill probability

| Claim | Status | Source |
|---|---|---|
| Fill probability depends on state-dependent order flow and is non-trivial to compute; simple static expressions are inadequate | Supported | Lokin & Yu, *Fill Probabilities in a Limit Order Book with State-Dependent Stochastic Order Flows*, arXiv:2403.02572 |
| A Poisson trade-count model is a *simplification* of that, not a replication of it | Stated limitation | This skill |
| A clamped ratio $\min(1, \lambda \Delta t / (Q_{\text{ahead}} + q_{\text{our}}))$ is a probability | **Not supported.** It carries no dispersion, and returns exactly 1.0 whenever expected volume merely reaches required volume. | — |
| Any specific value of $\alpha$, $\bar{S}_{\text{order}}$ or $\bar{S}_{\text{trade}}$ is broadly applicable | **Not supported.** The module defaults are placeholders. | — |

The Poisson model assumes trades at the level arrive independently at a constant
rate with a constant average size, that our order neither jumps nor is jumped,
and that no hidden or TOP liquidity intercepts flow. All four are false to some
degree in a real book, and all four bias the result *upward*. Treat the reported
probability as an upper bound.

### Numerical note

The survival function is summed exactly for $\mu \le 500$ and switches to a
normal approximation with continuity correction above it, because
`math.exp(-mu)` underflows to `0.0` near $\mu = 745$ — which would silently turn
$P(N \ge k)$ into a constant `1.0` for every $k$. Measured against an exact
log-space evaluation, the approximation is within $\approx 3 \times 10^{-3}$
absolute in the central region ($\mu = 1000$, $k = 1000$: 0.5063 vs 0.5042) and
closer in the tails. The far upper tail short-circuits to `0.0` beyond 40
standard deviations, which also bounds the summation loop.

## Input-integrity rules

| Condition | Result | Why |
|---|---|---|
| Any numeric input non-finite, non-numeric, or `bool` | `QueuePositionValidationError` | `max(0.0, nan)` returns `0.0` and `min(1.0, nan)` returns `1.0` in CPython — a `NaN` fails *open*, reporting front-of-queue with a certain fill. `bool` is an `int` subclass and would pass as `1.0`. |
| Negative fills or cancellations | `QueuePositionValidationError` | A negative fill grows $Q_{\text{ahead}}$ past the total volume at the level, which cannot happen. |
| `our_quantity`, `price` or `time_horizon_sec` $\le 0$ | `QueuePositionValidationError` | Not a resting order. |
| `side` outside `{'BUY', 'SELL'}` | `QueuePositionValidationError` | An arbitrary string would otherwise be upper-cased and reported as a side. |
| `total_level_volume` $< Q_{\text{ahead}} + q_{\text{our}}$ | `QueuePositionValidationError` | The snapshot was assembled from two different moments. |
| A derived product or quotient overflows to $\pm\infty$ | `QueuePositionValidationError` | Individually finite inputs can still combine badly: a finite rate times a finite horizon can reach `inf`, which then divides to `NaN` and would be reported as a probability. `math.ceil(inf)` also raises a bare `OverflowError` that a caller guarding on `QueuePositionError` would not catch. |
| `cancellation_share_alpha` outside $[0, 1]$; non-positive average sizes | `QueuePositionConfigurationError` | Rejected at construction, before any order is tracked. |

## Data-source constraint (US equities)

Executed volume must be **venue-local and price-specific**. Roughly half of US
equity share volume executes away from the exchanges and is reported through a
FINRA Trade Reporting Facility — Cboe reported the off-exchange share exceeding
50% of consolidated volume for the first time in January 2025, and the figure
moves month to month
(<https://www.cboe.com/us/equities/market_statistics/venue/market/tapec/>).
None of that volume consumes your venue's queue. Substituting consolidated-tape
volume drains the modelled queue far faster than the real one and produces a
confident, wrong `FRONT_OF_QUEUE`.

## Hidden and non-displayed liquidity

On Nasdaq, orders with a Display Attribute are ranked in time priority among
themselves, and non-displayed interest — including the non-displayed portion of
an order with Reserve Size — has "lower priority within the System than an
equally priced Displayed Order, regardless of time stamp" (Nasdaq Equity 4,
Rule 4757). So hidden size at *our* price does not sit ahead of a displayed
order. It does absorb aggressing flow at *better* prices, which lowers the rate
at which volume reaches our level — a fill-rate effect, not a queue-ahead
effect. Priority rules differ by venue; verify before generalising.

## Out of scope

Book construction and maintenance (`order-book-depth-processing-l2-l3`), L3
reconstruction (`historical-order-book-reconstruction-from-message-logs`),
realistic fill simulation (`execution-realistic-simulation`), markout and
toxicity measurement (`adverse-selection-measurement-for-passive-orders`), and
matching-engine congestion (`exchange-matching-engine-behavior-under-load`).
