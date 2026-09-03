# Standards for Algo Wheel Execution Quality

The wheel should be treated as a controlled TCA decision, not as an automatic
claim that the lowest historical average is always the best broker.

## What This Metric Is, and Is Not

Perold (1988) defines implementation shortfall as the gap between a paper
portfolio filled instantly at the decision price and the real portfolio,
decomposed into delay cost, explicit cost, implicit (market impact) cost, and
the **opportunity cost of the quantity never executed**.

The reference implementation computes the executed-quantity component only:
signed slippage on filled shares plus explicit fees. It has no view of unfilled
or cancelled residual, so it is strictly a partial implementation shortfall.
The practical consequence is a selection bias in the broker's favour — a broker
that executes the liquid part of an order and cancels the rest is scored on its
best fills alone. Track fill rate, cancelled residual, and reject rate over the
same window and segment, and treat an IS improvement accompanied by a fill-rate
decline as a deterioration.

## Measurement Standards

| Metric | Standard | Engineering implication |
|---|---|---|
| Decision price | Capture before routing using a documented timestamp and price source. | Reject stale or missing benchmarks; retain the benchmark with the execution record. |
| Order assignment | Randomise which broker receives each comparable order, according to the published weights. | Discretionary assignment confounds the comparison; record the assignment decision with the order. |
| Buy IS | `(fill - decision) / decision * 10000 + fee_bps`. | Positive values are costs; negative values are price improvement. |
| Sell IS | `(decision - fill) / decision * 10000 + fee_bps`. | Use the decision price denominator, not the fill price denominator. |
| Explicit fees | `fees / decision_notional * 10000`. | Normalize fee currency and include commissions, taxes, exchange fees, and documented rebates. |
| Completion | Filled quantity / intended quantity, measured separately. | Not computed by this module; required before the score drives allocation. |
| Broker score | Notional-weighted average IS. | Prevent many small fills from outweighing a small number of large fills. |
| Ranking | Lowest score first; deterministic tie-break required. | Use broker ID as the reference implementation tie-breaker and retain the raw scores. |
| Data sufficiency | Minimum executions and minimum notional per broker before promotion. | `min_observations` / `min_notional`; ineligible brokers keep the canary share but cannot lead. |
| Canary flow | Every non-leading observed broker receives the configured minimum. | Reject a floor configuration that leaves the leader below the floor itself. |

## Data Quality and Comparability

- Validate broker identity, side, finite prices, positive quantity, and positive
  prices before calculating IS. Guard the decision notional against floating
  point overflow as well as against zero.
- Keep partial fills linked to the parent order and document whether the
  decision notional or executed notional is used for aggregation.
- Segment or control for instrument, order size, urgency, venue, spread,
  volatility, and market regime before using broker scores operationally.
- Track sample count and notional coverage; a low-sample broker should not be
  promoted solely because of one favorable fill. Industry practitioners are
  consistent that a wheel needs enough flow to produce a statistically
  meaningful data set before allocations are moved.
- Retain raw observations and the exact fee, FX, benchmark, and filtering
  configuration used to produce the ranking.

## Allocation Policy

The reference implementation defaults to a 10% minimum for every non-leading
broker and assigns the residual to the leader. With three brokers this produces
80% / 10% / 10%; with one broker it produces 100%. This is a canary policy, not
a universal optimal allocation. Desks may use another approved policy, but it
must preserve a valid probability distribution, define tie handling, and retain
an auditable rationale.

Two invariants are enforced rather than assumed:

- the leading broker must receive at least the canary minimum, so a floor that
  would route the best broker less flow than the brokers it beat is rejected;
- shares are ordinary floats and sum to 1.0 only within `ALLOCATION_TOLERANCE`
  (1e-9). Four brokers at a 10% floor sum to 0.9999999999999999 if the leader
  share is not derived from the residual.

## Best-Execution Context

Broker-wheel output is one input to a broader best-execution framework, not
evidence that discharges an obligation by itself.

| Obligation | Jurisdiction | Status | Relevance here |
|---|---|---|---|
| MiFID II Art. 27(1) — all sufficient steps to obtain the best possible result | EU | Mandatory, in force | The wheel's output is one factor among price, costs, speed, likelihood of execution and settlement, size and nature |
| MiFID II Art. 27(7) with Art. 66 of Del. Reg. (EU) 2017/565 — monitor execution arrangements, review the policy at least annually | EU | Mandatory, in force | This is the clause a broker wheel actually serves |
| MiFID II Art. 27(3) / RTS 27 (Del. Reg. (EU) 2017/575) — venue execution-quality reports | EU | **Deleted** by Directive (EU) 2024/790 | Do not generate |
| MiFID II Art. 27(6) / RTS 28 (Del. Reg. (EU) 2017/576) — annual top-five-venue reports | EU | **Deleted** by Directive (EU) 2024/790; ESMA told NCAs to deprioritise supervision from 13 February 2024 | Do not generate |
| FINRA Rule 5310 with Supplementary Material .09 — regular and rigorous review of execution quality | US (FINRA members) | Mandatory, in force | Review at least quarterly; must weigh price improvement and disimprovement, likelihood of execution of limit orders, speed, size of execution, transaction costs, customer needs, and any internalisation or payment-for-order-flow arrangement |

Directive (EU) 2024/790 was published in the Official Journal on 8 March 2024
with a Member State transposition deadline of 29 September 2025, now past.

FINRA Rule 5310 is the point of greatest tension with this module: three of the
review factors it names — likelihood of execution of limit orders, speed of
execution, and size of execution — are absent from an executed-fill cost score.
A regular and rigorous review evidenced only by this ranking is incomplete.

## Sources

Consulted September 2026.

- ESMA, "ESMA clarifies certain best execution reporting requirements under
  MiFID II" (13 February 2024), and Public Statement ESMA35-335435667-5871 on
  the deprioritisation of supervisory actions on RTS 28 reporting —
  <https://www.esma.europa.eu/press-news/esma-news/esma-clarifies-certain-best-execution-reporting-requirements-under-mifid-ii>
- Directive (EU) 2024/790 amending Directive 2014/65/EU —
  <https://eur-lex.europa.eu/eli/dir/2024/790/oj>
- FINRA Rule 5310 and Supplementary Material .09, *Best Execution and
  Interpositioning* —
  <https://www.finra.org/rules-guidance/rulebooks/finra-rules/5310>
- Perold, A. F. (1988), "The Implementation Shortfall: Paper versus Reality",
  *Journal of Portfolio Management* 14(3), 4–9.
- Markets Media, "Trading Smarter With Algo Wheels" — practitioner accounts of
  randomised broker selection and the need for a statistically significant
  sample before reallocating flow —
  <https://www.marketsmedia.com/trading-smarter-with-algo-wheels/>
